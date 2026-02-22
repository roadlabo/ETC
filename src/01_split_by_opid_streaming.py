"""Stream ZIP archives and split rows by operation ID with optional final sort."""

from __future__ import annotations

import argparse
import csv
import heapq
import io
import os
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

# Legacy-compatible defaults
INPUT_DIR = r"D:\\...\\R7年2月_OUT1-2"
OUTPUT_DIR = r"D:\\...\\out(1st)"
TERM_NAME = "R7_2"
INNER_CSV = "data.csv"
ZIP_DIGIT_KEYS = ["523357", "523347", "523450", "523440"]
ENCODING = "utf-8"
DELIM = ","
BUFFER_SIZE = 8 << 20
SHOW_PROGRESS = True
DO_FINAL_SORT = True
TIMESTAMP_COL = 6
CHUNK_ROWS = 200_000
TEMP_SORT_DIR = "_sort_tmp"


@dataclass
class SplitConfig:
    input_dir: str
    output_dir: str
    term_name: str
    inner_csv: str = INNER_CSV
    zip_digit_keys: list[str] = field(default_factory=lambda: ZIP_DIGIT_KEYS.copy())

    encoding: str = ENCODING
    delim: str = DELIM

    buffer_size: int = BUFFER_SIZE
    do_final_sort: bool = DO_FINAL_SORT
    timestamp_col: int = TIMESTAMP_COL
    chunk_rows: int = CHUNK_ROWS
    temp_sort_dir: str = TEMP_SORT_DIR


ProgressCB = Optional[Callable[[str, int, int, dict], None]]


def print_progress(
    stage: str,
    done: int,
    total: int,
    *,
    extra: Optional[dict] = None,
    progress_cb: ProgressCB = None,
    show_progress: bool = SHOW_PROGRESS,
) -> None:
    payload = extra or {}
    if progress_cb is not None:
        progress_cb(stage, done, total, payload)
        return
    if not show_progress or total <= 0:
        return
    pct = min(100, int(done * 100 / total))
    sys.stdout.write(f"\r{stage}: {pct}%")
    sys.stdout.flush()


def end_progress_line(progress_cb: ProgressCB = None, show_progress: bool = SHOW_PROGRESS) -> None:
    if progress_cb is None and show_progress:
        sys.stdout.write("\n")
        sys.stdout.flush()


def _format_hms(sec: float) -> str:
    total = int(sec)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _timestamp() -> str:
    return time.strftime("%Y/%m/%d %H:%M:%S")


class RunLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg: str) -> None:
        self.lines.append(f"{_timestamp()} [INFO] {msg}")

    def warn(self, msg: str) -> None:
        self.lines.append(f"{_timestamp()} [WARN] {msg}")

    def error(self, msg: str) -> None:
        self.lines.append(f"{_timestamp()} [ERROR] {msg}")


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def iter_target_zips(directory: Path, digit_keys: Iterable[str]) -> list[Path]:
    keys = [k.strip() for k in digit_keys if k and k.strip()]
    candidates: list[Path] = []
    for entry in directory.iterdir():
        if not entry.is_file() or entry.suffix.lower() != ".zip":
            continue
        name = entry.name
        if any(key in name for key in keys):
            candidates.append(entry)
    candidates.sort(key=lambda p: p.name)
    return candidates


def open_writer(
    opid: str,
    *,
    output_dir_path: Path,
    term_name: str,
    encoding: str,
    delim: str,
    buffer_size: int,
) -> tuple[io.TextIOWrapper, csv.writer, bool]:
    output_path = output_dir_path / f"{term_name}_{opid}.csv"
    existed = output_path.exists()
    file_obj = output_path.open(mode="a", encoding=encoding, newline="", buffering=buffer_size)
    writer = csv.writer(file_obj, delimiter=delim, quoting=csv.QUOTE_MINIMAL)
    return file_obj, writer, existed


def close_current(current_fp: Optional[io.TextIOWrapper]) -> None:
    if current_fp is not None:
        try:
            current_fp.close()
        except Exception:
            pass


def process_zip(
    zip_path: Path,
    config: SplitConfig,
    *,
    output_dir_path: Path,
    cancel_flag=None,
    progress_cb: ProgressCB = None,
    zip_done: int = 0,
    zips_total: int = 0,
    total_rows_before: int = 0,
    total_out_files_before: int = 0,
    seen_opids: Optional[set[str]] = None,
) -> tuple[int, int, int, int, int]:
    current_fp: Optional[io.TextIOWrapper] = None
    current_writer: Optional[csv.writer] = None
    current_opid: Optional[str] = None
    rows_written = 0
    zip_new = 0
    zip_append = 0
    missing_inner_csv_count = 0
    decode_skip_count = 0
    rows_in_zip = 0
    last_emit = 0.0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                info = zf.getinfo(config.inner_csv)
            except KeyError:
                missing_inner_csv_count += 1
                return rows_written, zip_new, zip_append, missing_inner_csv_count, decode_skip_count
            total_bytes = max(1, info.file_size)
            with zf.open(info, mode="r") as raw:
                text_stream = io.TextIOWrapper(raw, encoding=config.encoding, newline="", errors="strict")
                reader = csv.reader(text_stream, delimiter=config.delim)
                while True:
                    if cancel_flag is not None and cancel_flag.is_set():
                        break
                    try:
                        row = next(reader)
                    except StopIteration:
                        break
                    except (UnicodeDecodeError, csv.Error):
                        decode_skip_count += 1
                        continue
                    if not row or len(row) <= 3:
                        continue
                    opid = row[3].strip()
                    if not opid:
                        continue
                    if seen_opids is not None:
                        seen_opids.add(opid)
                    if opid != current_opid:
                        close_current(current_fp)
                        current_fp, current_writer, existed = open_writer(
                            opid,
                            output_dir_path=output_dir_path,
                            term_name=config.term_name,
                            encoding=config.encoding,
                            delim=config.delim,
                            buffer_size=config.buffer_size,
                        )
                        current_opid = opid
                        if existed:
                            zip_append += 1
                        else:
                            zip_new += 1
                    if current_writer is None:
                        continue
                    current_writer.writerow(row)
                    rows_written += 1
                    rows_in_zip += 1

                    now = time.monotonic()
                    if now - last_emit >= 0.2:
                        zip_pct = min(100, int(raw.tell() * 100 / total_bytes))
                        print_progress(
                            "EXTRACT",
                            zip_done,
                            zips_total,
                            extra={
                                "zip": zip_path.name,
                                "zips_done": zip_done,
                                "zips_total": zips_total,
                                "zip_pct": zip_pct,
                                "rows_in_zip": rows_in_zip,
                                "zip_new": zip_new,
                                "zip_append": zip_append,
                                "rows_written": total_rows_before + rows_written,
                                "out_files": total_out_files_before + zip_new,
                                "opid_total": len(seen_opids) if seen_opids is not None else 0,
                            },
                            progress_cb=progress_cb,
                        )
                        last_emit = now
    finally:
        close_current(current_fp)
    return rows_written, zip_new, zip_append, missing_inner_csv_count, decode_skip_count


def _parse_ts_to_int(s: str) -> int:
    s = (s or "").strip()
    if not s.isdigit():
        return 10**20
    if len(s) >= 14:
        s = s[:14]
    elif len(s) == 12:
        s = s + "00"
    else:
        return 10**20
    try:
        return int(s)
    except Exception:
        return 10**20


def _split_to_sorted_chunks(
    src: Path,
    temp_dir: Path,
    encoding: str,
    delim: str,
    ts_col: int,
    chunk_rows: int,
    cancel_flag=None,
) -> list[Path]:
    temp_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Path] = []
    buf: list[tuple[int, list[str]]] = []
    with src.open("r", encoding=encoding, newline="") as f:
        rd = csv.reader(f, delimiter=delim)
        for row in rd:
            if cancel_flag is not None and cancel_flag.is_set():
                break
            if not row:
                continue
            key = _parse_ts_to_int(row[ts_col]) if len(row) > ts_col else 10**20
            buf.append((key, row))
            if len(buf) >= chunk_rows:
                buf.sort(key=lambda x: x[0])
                cpath = temp_dir / f"chunk_{len(chunks)+1:05d}.csv"
                with cpath.open("w", encoding=encoding, newline="") as w:
                    wr = csv.writer(w, delimiter=delim, quoting=csv.QUOTE_MINIMAL)
                    for _, r in buf:
                        wr.writerow(r)
                chunks.append(cpath)
                buf.clear()
    if buf:
        buf.sort(key=lambda x: x[0])
        cpath = temp_dir / f"chunk_{len(chunks)+1:05d}.csv"
        with cpath.open("w", encoding=encoding, newline="") as w:
            wr = csv.writer(w, delimiter=delim, quoting=csv.QUOTE_MINIMAL)
            for _, r in buf:
                wr.writerow(r)
        chunks.append(cpath)
        buf.clear()
    return chunks


def _merge_chunks(
    chunk_files: list[Path],
    dst: Path,
    encoding: str,
    delim: str,
    ts_col: int,
    cancel_flag=None,
) -> None:
    tmp = dst.with_suffix(".sorted.tmp")
    readers, files = [], []
    try:
        for cf in chunk_files:
            f = cf.open("r", encoding=encoding, newline="")
            files.append(f)
            readers.append(csv.reader(f, delimiter=delim))

        heap: list[tuple[int, int, int, list[str]]] = []
        counter = 0
        for i, rd in enumerate(readers):
            try:
                row = next(rd)
            except StopIteration:
                continue
            key = _parse_ts_to_int(row[ts_col]) if len(row) > ts_col else 10**20
            heap.append((key, counter, i, row))
            counter += 1
        heapq.heapify(heap)

        with tmp.open("w", encoding=encoding, newline="") as w:
            wr = csv.writer(w, delimiter=delim, quoting=csv.QUOTE_MINIMAL)
            while heap:
                if cancel_flag is not None and cancel_flag.is_set():
                    return
                _, _, idx, row = heapq.heappop(heap)
                wr.writerow(row)
                try:
                    row = next(readers[idx])
                except StopIteration:
                    continue
                k2 = _parse_ts_to_int(row[ts_col]) if len(row) > ts_col else 10**20
                heapq.heappush(heap, (k2, counter, idx, row))
                counter += 1

        os.replace(tmp, dst)
    finally:
        for f in files:
            try:
                f.close()
            except Exception:
                pass
        if cancel_flag is not None and cancel_flag.is_set() and tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _final_sort_one(
    path: Path,
    encoding: str,
    delim: str,
    ts_col: int,
    chunk_rows: int,
    temp_root: Path,
    cancel_flag=None,
) -> None:
    temp_dir = temp_root / path.stem
    try:
        chunks = _split_to_sorted_chunks(path, temp_dir, encoding, delim, ts_col, chunk_rows, cancel_flag=cancel_flag)
        if cancel_flag is not None and cancel_flag.is_set():
            return
        if not chunks:
            tmp = path.with_suffix(".sorted.tmp")
            with tmp.open("w", encoding=encoding, newline=""):
                pass
            os.replace(tmp, path)
        elif len(chunks) == 1:
            tmp = path.with_suffix(".sorted.tmp")
            with tmp.open("w", encoding=encoding, newline="") as w:
                wr = csv.writer(w, delimiter=delim, quoting=csv.QUOTE_MINIMAL)
                with chunks[0].open("r", encoding=encoding, newline="") as r:
                    rd = csv.reader(r, delimiter=delim)
                    for row in rd:
                        if cancel_flag is not None and cancel_flag.is_set():
                            return
                        wr.writerow(row)
            os.replace(tmp, path)
        else:
            _merge_chunks(chunks, path, encoding, delim, ts_col, cancel_flag=cancel_flag)
    finally:
        if temp_dir.exists():
            for p in sorted(temp_dir.glob("*.csv")):
                try:
                    p.unlink()
                except Exception:
                    pass
            try:
                temp_dir.rmdir()
            except Exception:
                pass


def _final_sort_all(
    config: SplitConfig,
    output_dir: Path,
    progress_cb: ProgressCB = None,
    cancel_flag=None,
    run_log: RunLog | None = None,
) -> None:
    pattern = f"{config.term_name}_*.csv"
    files = sorted(output_dir.glob(pattern))
    total = len(files)
    temp_root = output_dir / config.temp_sort_dir
    temp_root.mkdir(parents=True, exist_ok=True)
    done = 0
    try:
        for f in files:
            if cancel_flag is not None and cancel_flag.is_set():
                break
            _final_sort_one(
                f,
                config.encoding,
                config.delim,
                config.timestamp_col,
                config.chunk_rows,
                temp_root,
                cancel_flag=cancel_flag,
            )
            done += 1
            print_progress(
                "SORT",
                done,
                total,
                extra={"current_file": f.name, "total_files": total, "done_files": done},
                progress_cb=progress_cb,
            )
            if run_log is not None and total > 0:
                step = max(1, total // 10)
                if done == 1 or done == total or done % step == 0:
                    run_log.info(f"SORT: {done}/{total} current={f.name}")
        end_progress_line(progress_cb=progress_cb)
    finally:
        try:
            temp_root.rmdir()
        except Exception:
            pass


def run_split(config: SplitConfig, progress_cb: ProgressCB = None, cancel_flag=None) -> None:
    started = time.time()
    log = RunLog()
    log.info("=== 01 1st screening start ===")
    log.info(f"input_dir: {config.input_dir}")
    log.info(f"output_dir: {config.output_dir}")
    log.info(f"term_name: {config.term_name}")
    log.info(f"zip_keys: {','.join(config.zip_digit_keys)}")
    log.info(f"chunk_rows: {config.chunk_rows}")
    log.info(f"timestamp_col: {config.timestamp_col}")

    input_dir_path = Path(config.input_dir)
    output_dir_path = Path(config.output_dir)
    ensure_output_dir(output_dir_path)

    zip_paths = iter_target_zips(input_dir_path, config.zip_digit_keys)
    total_zips = len(zip_paths)
    log.info(f"zips_total: {total_zips}")
    print_progress(
        "SCAN",
        total_zips,
        total_zips if total_zips else 1,
        extra={"zips_total": total_zips, "zip_list": [p.name for p in zip_paths]},
        progress_cb=progress_cb,
    )
    end_progress_line(progress_cb=progress_cb)

    processed = 0
    total_rows = 0
    total_out_files = 0
    total_missing_inner = 0
    total_decode_skip = 0
    seen_opids: set[str] = set()

    try:
        for zip_path in zip_paths:
            if cancel_flag is not None and cancel_flag.is_set():
                break
            rows, zip_new, zip_append, miss_inner, decode_skip = process_zip(
                zip_path,
                config,
                output_dir_path=output_dir_path,
                cancel_flag=cancel_flag,
                progress_cb=progress_cb,
                zip_done=processed + 1,
                zips_total=total_zips,
                total_rows_before=total_rows,
                total_out_files_before=total_out_files,
                seen_opids=seen_opids,
            )
            processed += 1
            total_rows += rows
            total_out_files += zip_new
            total_missing_inner += miss_inner
            total_decode_skip += decode_skip
            log.info(
                f"ZIP done: {zip_path.name} rows={rows} new={zip_new} append={zip_append} "
                f"miss_inner={miss_inner} decode_skip={decode_skip}"
            )
            print_progress(
                "EXTRACT",
                processed,
                total_zips,
                extra={
                    "zip": zip_path.name,
                    "zip_pct": 100,
                    "rows_in_zip": rows,
                    "zip_new": zip_new,
                    "zip_append": zip_append,
                    "rows_written": total_rows,
                    "out_files": total_out_files,
                    "opid_total": len(seen_opids),
                    "zips_done": processed,
                    "zips_total": total_zips,
                },
                progress_cb=progress_cb,
            )
        if total_zips > 0:
            end_progress_line(progress_cb=progress_cb)

        if config.do_final_sort and (cancel_flag is None or not cancel_flag.is_set()):
            log.info("SORT start")
            _final_sort_all(config, output_dir_path, progress_cb=progress_cb, cancel_flag=cancel_flag, run_log=log)

        status = "CANCELLED" if cancel_flag is not None and cancel_flag.is_set() else "DONE"
        out_count = len(list(output_dir_path.glob(f"{config.term_name}_*.csv")))
        print_progress(
            "VERIFY",
            processed,
            total_zips if total_zips else processed,
            extra={"status": status, "rows_written": total_rows, "out_files": out_count},
            progress_cb=progress_cb,
        )
        end_progress_line(progress_cb=progress_cb)

        ended = time.time()
        log.info(f"status: {status}")
        log.info(f"zips_processed: {processed}/{total_zips}")
        log.info(f"rows_written: {total_rows}")
        log.info(f"out_files: {out_count}")
        log.info(f"missing_inner_csv: {total_missing_inner}")
        log.info(f"decode_skip: {total_decode_skip}")
        log.info(f"elapsed: {_format_hms(ended - started)}")
        log.info("=== DONE ===")
    except Exception as exc:
        log.error(f"run_split failed: {exc}")
        raise
    finally:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = output_dir_path / f"01_1stScr_log_{stamp}.txt"
        try:
            out_path.write_text("\n".join(log.lines) + "\n", encoding="utf-8")
        except Exception as exc:
            log.warn(f"log write failed: {exc}")



def _parse_zip_keys(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _build_config_from_args() -> SplitConfig:
    parser = argparse.ArgumentParser(description="Split ZIP data by opid with optional final sort.")
    parser.add_argument("--input_dir", default=INPUT_DIR)
    parser.add_argument("--output_dir", default=OUTPUT_DIR)
    parser.add_argument("--term_name", default=TERM_NAME)
    parser.add_argument("--inner_csv", default=INNER_CSV)
    parser.add_argument("--zip_digit_keys", default=",".join(ZIP_DIGIT_KEYS))
    parser.add_argument("--encoding", default=ENCODING)
    parser.add_argument("--delim", default=DELIM)
    parser.add_argument("--do_final_sort", action="store_true", default=DO_FINAL_SORT)
    parser.add_argument("--no_final_sort", action="store_false", dest="do_final_sort")
    parser.add_argument("--timestamp_col", type=int, default=TIMESTAMP_COL)
    parser.add_argument("--chunk_rows", type=int, default=CHUNK_ROWS)
    parser.add_argument("--temp_sort_dir", default=TEMP_SORT_DIR)
    args = parser.parse_args()

    return SplitConfig(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        term_name=args.term_name,
        inner_csv=args.inner_csv,
        zip_digit_keys=_parse_zip_keys(args.zip_digit_keys),
        encoding=args.encoding,
        delim=args.delim,
        do_final_sort=args.do_final_sort,
        timestamp_col=args.timestamp_col,
        chunk_rows=args.chunk_rows,
        temp_sort_dir=args.temp_sort_dir,
    )


def main() -> None:
    config = _build_config_from_args()
    if not config.input_dir or config.input_dir.startswith("D:\\..."):
        print("[ERROR] input_dir is not configured. Please pass --input_dir.")
        return
    if not Path(config.input_dir).exists():
        print(f"[ERROR] input_dir does not exist: {config.input_dir}")
        return
    run_split(config)


if __name__ == "__main__":
    main()
