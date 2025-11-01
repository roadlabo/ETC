"""split_by_opid_streaming.py

Scan ZIP archives for a specific inner CSV file and stream rows to per-operation
CSV outputs. Only one output file is kept open at any time so that the script
can run safely on systems with a low file descriptor limit. Configuration is
performed exclusively through the constants defined at the top of the module.
"""

from __future__ import annotations

import csv
import io
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set, Tuple

try:  # Optional dependency for friendly progress bars.
    from tqdm import tqdm  # type: ignore
except ImportError:  # pragma: no cover - tqdm is optional.
    tqdm = None

# ---------------------------------------------------------------------------
# Configuration constants (edit to suit your environment)
# ---------------------------------------------------------------------------
INPUT_DIR = r"D:\\01仕事\\05 ETC2.0分析\\生データ\\R7年2月_OUT1-2"  # folder with ZIPs
OUTPUT_DIR = r"D:\\01仕事\\05 ETC2.0分析\\生データ\\out(1st)"       # output folder
TERM_NAME = "R7_2"                                            # prefix for output
ZIP_DIGIT_KEYS = ["523357", "523347", "523450", "523440"]     # substrings to match
INNER_CSV = "data.csv"                                        # CSV inside each ZIP
ENC = "utf-8"                                                 # change to "cp932" if needed
DELIM = ","                                                   # input CSV delimiter
SHOW_PROGRESS = True                                           # use tqdm if available
WRITE_HEADER_PER_FILE = True                                   # keep CSV headers
BUFFER_SIZE = 1 << 20                                          # 1 MiB write buffer

# --- Final sort settings ---
DO_FINAL_SORT = True
TIMESTAMP_COL = 6
SORT_GLOB = f"{TERM_NAME}_*.csv"
CHUNK_ROWS = 200_000
TEMP_SORT_DIR = "_sort_tmp"

# ---------------------------------------------------------------------------

CsvRow = List[str]
ProgressCallback = Callable[[int], None]


def ensure_output_dir(path: Path) -> None:
    """Ensure the output directory exists."""

    path.mkdir(parents=True, exist_ok=True)


def _parse_ts_to_int(s: str) -> int:
    """Convert timestamp strings into sortable integers."""

    s = (s or "").strip()
    if not s.isdigit():
        return 10**20
    if len(s) >= 14:
        s = s[:14]
    elif len(s) == 12:
        s = f"{s}00"
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
) -> Tuple[List[Path], Optional[CsvRow]]:
    import csv as _csv

    temp_dir.mkdir(parents=True, exist_ok=True)
    chunk_files: List[Path] = []
    buf: List[Tuple[int, CsvRow]] = []
    header_row: Optional[CsvRow] = None
    header_is_data = False

    with src.open("r", encoding=encoding, newline="") as f:
        reader = _csv.reader(f, delimiter=delim)
        for row in reader:
            if not row:
                continue
            if header_row is None:
                header_row = row
                if len(row) > ts_col and _parse_ts_to_int(row[ts_col]) != 10**20:
                    header_is_data = True
                else:
                    header_is_data = False
                    continue
            if len(row) <= ts_col:
                key = 10**20
            else:
                key = _parse_ts_to_int(row[ts_col])
            buf.append((key, row))
            if len(buf) >= chunk_rows:
                buf.sort(key=lambda x: x[0])
                cpath = temp_dir / f"chunk_{len(chunk_files) + 1:05d}.csv"
                with cpath.open("w", encoding=encoding, newline="") as w:
                    wtr = _csv.writer(w, delimiter=delim, quoting=_csv.QUOTE_MINIMAL)
                    for _, r in buf:
                        wtr.writerow(r)
                chunk_files.append(cpath)
                buf.clear()

    if buf:
        buf.sort(key=lambda x: x[0])
        cpath = temp_dir / f"chunk_{len(chunk_files) + 1:05d}.csv"
        with cpath.open("w", encoding=encoding, newline="") as w:
            wtr = _csv.writer(w, delimiter=delim, quoting=_csv.QUOTE_MINIMAL)
            for _, r in buf:
                wtr.writerow(r)
        chunk_files.append(cpath)
        buf.clear()

    return chunk_files, (header_row if header_row is not None and not header_is_data else None)


def _merge_chunks(
    chunk_files: List[Path],
    dst: Path,
    encoding: str,
    delim: str,
    ts_col: int,
    header: Optional[CsvRow],
) -> None:
    import csv as _csv
    import heapq

    tmp = dst.with_suffix(".sorted.tmp")
    readers: List[_csv._reader] = []  # type: ignore[attr-defined]
    files = []
    try:
        for cf in chunk_files:
            f = cf.open("r", encoding=encoding, newline="")
            files.append(f)
            readers.append(_csv.reader(f, delimiter=delim))

        heap: List[Tuple[int, int, int, CsvRow]] = []
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
            wtr = _csv.writer(w, delimiter=delim, quoting=_csv.QUOTE_MINIMAL)
            if header is not None:
                wtr.writerow(header)
            while heap:
                _, _, idx, row = heapq.heappop(heap)
                wtr.writerow(row)
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


def _final_sort_one(
    path: Path,
    encoding: str,
    delim: str,
    ts_col: int,
    chunk_rows: int,
    temp_root: Path,
) -> None:
    import csv as _csv

    print(f"[FINAL-SORT] {path.name}")
    temp_dir = temp_root / path.stem
    try:
        chunks, header = _split_to_sorted_chunks(
            path, temp_dir, encoding, delim, ts_col, chunk_rows
        )
        if not chunks:
            if header is not None:
                tmp = path.with_suffix(".sorted.tmp")
                with tmp.open("w", encoding=encoding, newline="") as w:
                    wtr = _csv.writer(w, delimiter=delim, quoting=_csv.QUOTE_MINIMAL)
                    wtr.writerow(header)
                os.replace(tmp, path)
            return

        if len(chunks) == 1:
            tmp = path.with_suffix(".sorted.tmp")
            with tmp.open("w", encoding=encoding, newline="") as w:
                wtr = _csv.writer(w, delimiter=delim, quoting=_csv.QUOTE_MINIMAL)
                if header is not None:
                    wtr.writerow(header)
                with chunks[0].open("r", encoding=encoding, newline="") as r:
                    rdr = _csv.reader(r, delimiter=delim)
                    for row in rdr:
                        wtr.writerow(row)
            os.replace(tmp, path)
        else:
            _merge_chunks(chunks, path, encoding, delim, ts_col, header)
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
    output_dir: Path,
    pattern: str,
    encoding: str,
    delim: str,
    ts_col: int,
    chunk_rows: int,
    temp_dir_name: str,
) -> None:
    temp_root = output_dir / temp_dir_name
    temp_root.mkdir(parents=True, exist_ok=True)
    files = sorted(output_dir.glob(pattern))
    if not files:
        print(f"[FINAL-SORT] no files matched: {output_dir}\\{pattern}")
        try:
            temp_root.rmdir()
        except Exception:
            pass
        return
    for f in files:
        _final_sort_one(f, encoding, delim, ts_col, chunk_rows, temp_root)
    try:
        temp_root.rmdir()
    except Exception:
        pass


def iter_target_zips(directory: Path, digit_keys: Iterable[str]) -> List[Path]:
    """Return sorted ZIP files whose names contain any of the digit keys."""

    keys = list(digit_keys)
    candidates: List[Path] = []
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
    header: CsvRow,
    header_written: Set[str],
) -> tuple[io.TextIOWrapper, csv.writer, bool]:
    """Open an output writer for the given operation ID."""

    output_path = OUTPUT_DIR_PATH / f"{TERM_NAME}_{opid}.csv"
    file_exists = output_path.exists()
    file_obj = output_path.open(
        mode="a",
        encoding=ENC,
        newline="",
        buffering=BUFFER_SIZE,
    )
    writer = csv.writer(file_obj, delimiter=DELIM)
    header_already_written = file_exists or opid in header_written
    if WRITE_HEADER_PER_FILE and not header_already_written:
        writer.writerow(header)
        header_written.add(opid)
        header_already_written = True
    elif header_already_written:
        header_written.add(opid)
    return file_obj, writer, header_already_written


def close_current(current_fp: Optional[io.TextIOWrapper]) -> None:
    """Safely close the currently open output file."""

    if current_fp is not None:
        try:
            current_fp.close()
        except Exception:
            pass


def process_zip(
    zip_path: Path,
    header_written: Set[str],
    progress_cb: Optional[ProgressCallback],
) -> tuple[int, Set[str], List[str]]:
    """Stream rows from a single ZIP archive.

    Returns a tuple of (rows_processed, opids_encountered, log_messages).
    """

    rows_processed = 0
    opids: Set[str] = set()
    logs: List[str] = []
    current_opid: Optional[str] = None
    current_fp: Optional[io.TextIOWrapper] = None
    current_writer: Optional[csv.writer] = None

    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                info = zf.getinfo(INNER_CSV)
            except KeyError:
                logs.append(
                    f"WARNING: '{INNER_CSV}' not found in {zip_path.name}; skipping archive."
                )
                return rows_processed, opids, logs

            with zf.open(info, mode="r") as raw:
                text_stream = io.TextIOWrapper(
                    raw,
                    encoding=ENC,
                    newline="",
                    errors="strict",
                )
                reader = csv.reader(text_stream, delimiter=DELIM)

                header: Optional[CsvRow] = None
                row_number = 0

                while True:
                    try:
                        row = next(reader)
                    except StopIteration:
                        break
                    except UnicodeDecodeError as exc:
                        row_number += 1
                        logs.append(
                            f"ERROR: Encoding issue in {zip_path.name} at row {row_number}: {exc}"
                        )
                        continue
                    except csv.Error as exc:
                        row_number += 1
                        logs.append(
                            f"ERROR: Malformed CSV row in {zip_path.name} at row {row_number}: {exc}"
                        )
                        continue

                    row_number += 1

                    if header is None:
                        header = row
                        continue

                    if len(row) <= 3:
                        logs.append(
                            f"ERROR: Row {row_number} in {zip_path.name} lacks a 4th column; skipped."
                        )
                        continue

                    opid = row[3].strip()
                    if not opid:
                        logs.append(
                            f"WARNING: Empty operation ID at row {row_number} in {zip_path.name}; skipped."
                        )
                        continue

                    if header is None:
                        # Should never happen, but guard for mypy/static analysis.
                        continue

                    if opid != current_opid:
                        close_current(current_fp)
                        current_fp, current_writer, _ = open_writer(opid, header, header_written)
                        current_opid = opid

                    if current_writer is None or current_fp is None:
                        logs.append(
                            f"ERROR: Writer unavailable for operation ID '{opid}' in {zip_path.name}."
                        )
                        continue

                    try:
                        current_writer.writerow(row)
                    except Exception as exc:  # pragma: no cover - unexpected I/O issues.
                        logs.append(
                            f"ERROR: Failed to write row {row_number} for '{opid}' in {zip_path.name}: {exc}"
                        )
                        continue

                    rows_processed += 1
                    opids.add(opid)
                    if progress_cb is not None:
                        progress_cb(1)
    except zipfile.BadZipFile as exc:
        logs.append(f"ERROR: Bad ZIP file {zip_path.name}: {exc}")
    except Exception as exc:  # pragma: no cover - defensive catch-all with logging.
        logs.append(f"ERROR: Unexpected issue with {zip_path.name}: {exc}")
    finally:
        close_current(current_fp)

    return rows_processed, opids, logs


def manual_progress(zip_index: int, total: int, zip_name: str) -> None:
    """Print a simple progress line when tqdm is unavailable."""

    percent = (zip_index / total) * 100 if total else 100.0
    print(f"[{zip_index}/{total}] scanning {zip_name} ({percent:5.1f}% complete)")


def main() -> None:
    start_time = time.time()
    ensure_output_dir(OUTPUT_DIR_PATH)

    zip_paths = iter_target_zips(INPUT_DIR_PATH, ZIP_DIGIT_KEYS)
    total_zips = len(zip_paths)
    if not zip_paths:
        print("No matching ZIP files were found. Adjust ZIP_DIGIT_KEYS or INPUT_DIR.")
        return

    header_written: Set[str] = set()
    total_rows = 0
    all_opids: Set[str] = set()
    all_logs: List[str] = []

    use_tqdm = SHOW_PROGRESS and tqdm is not None
    outer_bar = None
    if use_tqdm:
        outer_bar = tqdm(total=total_zips, unit="zip", desc="ZIPs", ncols=100)

    for index, zip_path in enumerate(zip_paths, start=1):
        if use_tqdm and outer_bar is not None:
            outer_bar.set_description(f"[{index}/{total_zips}] {zip_path.name}")
        else:
            manual_progress(index, total_zips, zip_path.name)

        row_bar = None
        if use_tqdm and tqdm is not None:
            row_bar = tqdm(total=0, unit="row", leave=False, desc=f"Rows {zip_path.name}")

        zip_rows = 0

        def progress_cb(increment: int) -> None:
            nonlocal zip_rows
            zip_rows += increment
            if row_bar is not None:
                row_bar.update(increment)
            elif increment and zip_rows % 10000 == 0:
                print(f"    processed {zip_rows:,} rows in {zip_path.name}...")

        rows, opids, logs = process_zip(
            zip_path,
            header_written,
            progress_cb if SHOW_PROGRESS else None,
        )

        if row_bar is not None:
            row_bar.close()
        if use_tqdm and outer_bar is not None:
            outer_bar.update(1)

        total_rows += rows
        all_opids.update(opids)
        all_logs.extend(logs)

        if not use_tqdm:
            print(f"    completed {zip_path.name}: {rows} rows, {len(opids)} unique IDs")

    if outer_bar is not None:
        outer_bar.close()

    if DO_FINAL_SORT:
        print("\n[FINAL-SORT] Start chronological sorting by timestamp (col=7)...")
        _final_sort_all(
            output_dir=OUTPUT_DIR_PATH,
            pattern=SORT_GLOB,
            encoding=ENC,
            delim=DELIM,
            ts_col=TIMESTAMP_COL,
            chunk_rows=CHUNK_ROWS,
            temp_dir_name=TEMP_SORT_DIR,
        )
        print("[FINAL-SORT] Done.")

    end_time = time.time()
    duration = end_time - start_time

    print("\nSummary")
    print("-------")
    print(f"ZIP archives processed : {total_zips}")
    print(f"Rows written           : {total_rows}")
    print(f"Unique operation IDs   : {len(all_opids)}")
    print(f"Elapsed time           : {duration:.2f} seconds")

    if all_logs:
        print("\nWarnings / Errors")
        print("-----------------")
        for message in all_logs:
            print(message)


# Resolve string paths once at import time.
INPUT_DIR_PATH = Path(INPUT_DIR)
OUTPUT_DIR_PATH = Path(OUTPUT_DIR)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(1)
