"""split_by_operation_id.py

This script scans ZIP archives located in ``INPUT_DIR`` and routes the rows in each
``data.csv`` file to separate CSVs grouped by the operation ID found in the fourth
column (0-based index 3). All configuration values can be modified by editing the
constants in the section below. The script performs streaming reads without
extracting ZIP contents to disk and appends results to per-operation CSV files in
``OUTPUT_DIR``.
"""

from __future__ import annotations

import csv
import io
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from tqdm import tqdm  # type: ignore
except ImportError:  # pragma: no cover - tqdm is optional
    tqdm = None

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
INPUT_DIR = r"D:\\01仕事\\05 ETC2.0分析\\生データ\\⑨Ｒ７年２月＿OUT1-2"
OUTPUT_DIR = r"D:\\01仕事\\05 ETC2.0分析\\生データ\\out(1st)"
TERM_NAME = "R7_2"
ZIP_DIGIT_KEYS = ["523450", "523347", "523357", "523440"]
INNER_CSV_NAME = "data.csv"
CSV_ENCODING = "utf-8"  # change to "cp932" if needed
WRITE_HEADER_PER_FILE = True
BUFFER_SIZE = 1 << 20  # 1 MiB
CSV_DIALECT = {
    "delimiter": ",",
    "quotechar": '"',
    "quoting": csv.QUOTE_MINIMAL,
}
# ---------------------------------------------------------------------------

class OpidWriterManager:
    """Manage a single writable CSV handle for the current operation ID."""

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir
        self._current_opid: Optional[str] = None
        self._file_obj: Optional[io.TextIOWrapper] = None
        self._writer: Optional[csv.writer] = None

    def write_row(self, opid: str, row: List[str]) -> None:
        if self._current_opid != opid:
            self._switch_writer(opid)

        if self._writer is None:
            raise RuntimeError("Writer not initialized for operation ID")

        self._writer.writerow(row)

    def close_current(self) -> None:
        if self._file_obj is not None:
            try:
                self._file_obj.close()
            finally:
                self._file_obj = None
                self._writer = None
                self._current_opid = None

    def _switch_writer(self, opid: str) -> None:
        self.close_current()

        output_path = self._output_dir / f"{TERM_NAME}_{opid}.csv"

        file_obj = open(
            output_path,
            mode="a",
            encoding=CSV_ENCODING,
            newline="",
            buffering=BUFFER_SIZE,
        )
        writer = csv.writer(file_obj, **CSV_DIALECT)

        # ``data.csv`` does not contain a header row, so no header is written here.
        self._file_obj = file_obj
        self._writer = writer
        self._current_opid = opid


def ensure_output_dir(path: Path) -> None:
    """Create the output directory if it does not already exist."""
    path.mkdir(parents=True, exist_ok=True)


def discover_zip_files(directory: Path, digit_keys: Iterable[str]) -> List[Path]:
    """Return a sorted list of ZIP files whose names contain any of the digit keys."""
    digit_keys = list(digit_keys)
    matching: List[Path] = []
    for entry in directory.iterdir():
        if not entry.is_file() or entry.suffix.lower() != ".zip":
            continue
        filename = entry.name
        if any(key in filename for key in digit_keys):
            matching.append(entry)
    matching.sort(key=lambda p: p.name)
    return matching


def process_zip_file(
    zip_path: Path,
    output_dir: Path,
    writer_manager: OpidWriterManager,
    counters: Dict[str, int],
) -> Tuple[int, List[str]]:
    """Process a single ZIP file and return the number of rows processed and logs."""
    log_messages: List[str] = []
    row_count = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                info = zf.getinfo(INNER_CSV_NAME)
            except KeyError:
                log_messages.append(
                    f"WARNING: '{INNER_CSV_NAME}' not found in {zip_path.name}, skipping."
                )
                return row_count, log_messages

            with zf.open(info, mode="r") as raw:
                text_stream = io.TextIOWrapper(
                    raw,
                    encoding=CSV_ENCODING,
                    newline="",
                    errors="strict",
                )
                reader = csv.reader(text_stream, **CSV_DIALECT)
                row_number = 0

                while True:
                    try:
                        row = next(reader)
                    except StopIteration:
                        break
                    except UnicodeDecodeError as exc:
                        row_number += 1
                        log_messages.append(
                            f"ERROR: Encoding error in {zip_path.name} at row {row_number}: {exc}"
                        )
                        continue
                    except csv.Error as exc:
                        row_number += 1
                        log_messages.append(
                            f"ERROR: Malformed CSV row in {zip_path.name} at row {row_number}: {exc}"
                        )
                        continue

                    row_number += 1
                    try:
                        opid = row[3].strip()
                    except IndexError:
                        log_messages.append(
                            f"ERROR: Row {row_number} in {zip_path.name} has insufficient columns; skipped."
                        )
                        continue

                    try:
                        writer_manager.write_row(opid, row)
                    except Exception as exc:
                        log_messages.append(
                            f"ERROR: Failed writing row {row_number} for opid '{opid}' from {zip_path.name}: {exc}"
                        )
                        continue

                    row_count += 1
                    counters[opid] += 1
            writer_manager.close_current()
    except zipfile.BadZipFile as exc:
        log_messages.append(f"ERROR: Unable to read ZIP {zip_path.name}: {exc}")
    return row_count, log_messages


def main() -> None:
    input_dir = Path(INPUT_DIR)
    output_dir = Path(OUTPUT_DIR)

    if not input_dir.exists():
        print(f"ERROR: INPUT_DIR does not exist: {input_dir}")
        sys.exit(1)

    ensure_output_dir(output_dir)
    zip_files = discover_zip_files(input_dir, ZIP_DIGIT_KEYS)
    total_zips = len(zip_files)

    if total_zips == 0:
        print("No ZIP files matched the provided digit keys. Nothing to do.")
        return

    print(f"Discovered {total_zips} matching ZIP files in {input_dir}")

    start_time = time.time()
    writer_manager = OpidWriterManager(output_dir)
    opid_counts: Dict[str, int] = defaultdict(int)
    total_rows = 0

    progress_bar = None
    if tqdm is not None:
        progress_bar = tqdm(total=total_zips, unit="zip", desc="ZIPs", leave=True)

    try:
        for idx, zip_path in enumerate(zip_files, start=1):
            percent_complete = ((idx - 1) / total_zips) * 100
            message = (
                f"[{idx}/{total_zips}] {percent_complete:6.2f}% scanning {zip_path.name}"
            )
            if progress_bar is not None:
                progress_bar.set_description(
                    f"[{idx}/{total_zips}] {percent_complete:6.2f}%"
                )
                progress_bar.set_postfix_str(zip_path.name)
                progress_bar.write(message)
            else:
                print(message)

            rows_processed, logs = process_zip_file(
                zip_path,
                output_dir,
                writer_manager,
                opid_counts,
            )
            total_rows += rows_processed

            if progress_bar is not None:
                progress_bar.update(1)

            if progress_bar is not None:
                progress_bar.write(
                    f"    Processed {rows_processed} data rows from {zip_path.name}."
                )
                for log in logs:
                    progress_bar.write(f"    {log}")
            else:
                print(
                    f"    Processed {rows_processed} data rows from {zip_path.name}."
                )
                for log in logs:
                    print(f"    {log}")

        elapsed = time.time() - start_time
        unique_opids = len(opid_counts)
        print("-" * 72)
        print(
            f"Summary: processed {total_zips} ZIP(s), {total_rows} data rows, "
            f"{unique_opids} unique operation IDs."
        )
        print(f"Output directory: {output_dir}")
        print(f"Elapsed time: {elapsed:.2f} seconds")
    finally:
        if progress_bar is not None:
            progress_bar.close()
        writer_manager.close_current()


if __name__ == "__main__":
    main()
