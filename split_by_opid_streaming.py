"""Stream ZIP archives and split rows by operation ID with optional final sort."""

from __future__ import annotations

import csv
import heapq
import io
import os
import sys
import zipfile
from pathlib import Path
from typing import Iterable, Optional

# Paths
INPUT_DIR = r"D:\\...\\R7年2月_OUT1-2"    # ZIP群の場所
OUTPUT_DIR = r"D:\\...\\out(1st)"          # 出力先
TERM_NAME = "R7_2"
INNER_CSV = "data.csv"                  # ZIP内部の対象CSV名
ZIP_DIGIT_KEYS = ["523357", "523347", "523450", "523440"]  # ZIP名フィルタ

# CSV I/O
ENCODING = "utf-8"    # 日本語CSVなら cp932 でも可
DELIM = ","

# Extraction behavior
BUFFER_SIZE = 8 << 20   # 8MiB
SHOW_PROGRESS = True      # 進捗表示ON（％のみ）

# Final sort settings
DO_FINAL_SORT = True
TIMESTAMP_COL = 6         # 0始まり: 7列目（GPS時刻）
SORT_GLOB = f"{TERM_NAME}_*.csv"
CHUNK_ROWS = 200_000   # メモリに応じて調整（100k~1M推奨）
TEMP_SORT_DIR = "_sort_tmp"

def print_progress(stage: str, done: int, total: int) -> None:
    if not SHOW_PROGRESS or total <= 0:
        return
    pct = min(100, int(done * 100 / total))
    sys.stdout.write(f"\r{stage}: {pct}%")
    sys.stdout.flush()


def end_progress_line() -> None:
    if SHOW_PROGRESS:
        sys.stdout.write("\n")
        sys.stdout.flush()


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def iter_target_zips(directory: Path, digit_keys: Iterable[str]) -> list[Path]:
    keys = list(digit_keys)
    candidates: list[Path] = []
    for entry in directory.iterdir():
        if not entry.is_file() or entry.suffix.lower() != ".zip":
            continue
        name = entry.name
        if any(key in name for key in keys):
            candidates.append(entry)
    candidates.sort(key=lambda p: p.name)
    return candidates


def open_writer(opid: str) -> tuple[io.TextIOWrapper, csv.writer]:
    output_path = OUTPUT_DIR_PATH / f"{TERM_NAME}_{opid}.csv"
    file_obj = output_path.open(
        mode="a",
        encoding=ENCODING,
        newline="",
        buffering=BUFFER_SIZE,
    )
    writer = csv.writer(file_obj, delimiter=DELIM, quoting=csv.QUOTE_MINIMAL)
    return file_obj, writer


def close_current(current_fp: Optional[io.TextIOWrapper]) -> None:
    if current_fp is not None:
        try:
            current_fp.close()
        except Exception:
            pass


def process_zip(zip_path: Path) -> None:
    current_fp: Optional[io.TextIOWrapper] = None
    current_writer: Optional[csv.writer] = None
    current_opid: Optional[str] = None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                info = zf.getinfo(INNER_CSV)
            except KeyError:
                return
            with zf.open(info, mode="r") as raw:
                text_stream = io.TextIOWrapper(
                    raw,
                    encoding=ENCODING,
                    newline="",
                    errors="strict",
                )
                reader = csv.reader(text_stream, delimiter=DELIM)
                while True:
                    try:
                        row = next(reader)
                    except StopIteration:
                        break
                    except (UnicodeDecodeError, csv.Error):
                        continue
                    if not row or len(row) <= 3:
                        continue
                    opid = row[3].strip()
                    if not opid:
                        continue
                    if opid != current_opid:
                        close_current(current_fp)
                        current_fp, current_writer = open_writer(opid)
                        current_opid = opid
                    if current_writer is None:
                        continue
                    current_writer.writerow(row)
    finally:
        close_current(current_fp)


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


def _split_to_sorted_chunks(src: Path, temp_dir: Path, encoding: str, delim: str,
                            ts_col: int, chunk_rows: int) -> list[Path]:
    temp_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Path] = []
    buf: list[tuple[int, list[str]]] = []
    with src.open("r", encoding=encoding, newline="") as f:
        rd = csv.reader(f, delimiter=delim)
        for row in rd:
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


def _merge_chunks(chunk_files: list[Path], dst: Path, encoding: str, delim: str, ts_col: int) -> None:
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


def _final_sort_one(path: Path, encoding: str, delim: str, ts_col: int,
                    chunk_rows: int, temp_root: Path) -> None:
    temp_dir = temp_root / path.stem
    try:
        chunks = _split_to_sorted_chunks(path, temp_dir, encoding, delim, ts_col, chunk_rows)
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
                        wr.writerow(row)
            os.replace(tmp, path)
        else:
            _merge_chunks(chunks, path, encoding, delim, ts_col)
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


def _final_sort_all(output_dir: Path, pattern: str, encoding: str, delim: str, ts_col: int,
                    chunk_rows: int, temp_dir_name: str) -> None:
    files = sorted(Path(output_dir).glob(pattern))
    total = len(files)
    if total == 0:
        return
    temp_root = Path(output_dir) / temp_dir_name
    temp_root.mkdir(parents=True, exist_ok=True)
    done = 0
    for f in files:
        _final_sort_one(f, encoding, delim, ts_col, chunk_rows, temp_root)
        done += 1
        print_progress("Sort", done, total)
    end_progress_line()
    try:
        temp_root.rmdir()
    except Exception:
        pass


def main() -> None:
    ensure_output_dir(OUTPUT_DIR_PATH)
    zip_paths = iter_target_zips(INPUT_DIR_PATH, ZIP_DIGIT_KEYS)
    total_zips = len(zip_paths)
    processed = 0
    for zip_path in zip_paths:
        process_zip(zip_path)
        processed += 1
        print_progress("Extract", processed, total_zips)
    if total_zips > 0:
        end_progress_line()
    if DO_FINAL_SORT:
        _final_sort_all(
            output_dir=OUTPUT_DIR_PATH,
            pattern=SORT_GLOB,
            encoding=ENCODING,
            delim=DELIM,
            ts_col=TIMESTAMP_COL,
            chunk_rows=CHUNK_ROWS,
            temp_dir_name=TEMP_SORT_DIR,
        )


INPUT_DIR_PATH = Path(INPUT_DIR)
OUTPUT_DIR_PATH = Path(OUTPUT_DIR)


if __name__ == "__main__":
    main()
