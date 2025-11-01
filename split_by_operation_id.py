"""split_by_operation_id
=================================

Two-phase external sort for ETC CSV archives.

This module scans ZIP archives located in :data:`INPUT_DIR` and extracts the
rows contained in the ``INNER_CSV_NAME`` member of each archive. Rows are
partitioned by the operation ID (column index ``OPID_COLUMN_INDEX``) and
sorted chronologically according to the timestamp found in column
``TIME_COLUMN_INDEX``. Sorting is performed using a two-phase external sort so
that memory usage stays bounded even for very large datasets:

* Phase 1 (chunking) streams rows from the ZIP archives, buffering rows per
  operation ID. When the buffer for an operation exceeds either
  :data:`CHUNK_MAX_ROWS` rows or :data:`CHUNK_MAX_BYTES` estimated bytes, the
  rows are sorted by timestamp and flushed to ``TEMP_DIR/opid/chunk_#.csv``.
* Phase 2 (merge) performs a k-way merge of the chunk files for each operation
  ID. Intermediate multi-pass merges are performed automatically when the
  number of chunk files exceeds :data:`MAX_OPEN_MERGE_FILES`. The final output
  files are written to ``OUTPUT_DIR`` with a ``TERM_NAME`` prefix.

All configuration lives in the constants below; no command-line arguments are
required. The script strives to be robust in environments with low file
descriptor limits by limiting the number of concurrently open files and by
keeping only one output file open at a time during the merge phase.
"""

from __future__ import annotations

import csv
import datetime as dt
import heapq
import io
import itertools
import logging
import sys
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

try:  # Optional dependency for user-friendly progress bars.
    from tqdm import tqdm  # type: ignore
except ImportError:  # pragma: no cover - tqdm is optional.
    tqdm = None

# ---------------------------------------------------------------------------
# Configuration constants (edit as needed for your environment)
# ---------------------------------------------------------------------------
INPUT_DIR = r"D:\\01仕事\\05 ETC2.0分析\\生データ\\⑨Ｒ７年２月＿OUT1-2"  # folder containing ZIP archives
OUTPUT_DIR = r"D:\\01仕事\\05 ETC2.0分析\\生データ\\out(1st)"        # output folder for per-opid CSVs
TERM_NAME = "R7_2"                                                        # prefix for output CSV filenames
ZIP_DIGIT_KEYS = ["523450", "523347", "523357", "523440"]               # substrings to match ZIP filenames
INNER_CSV_NAME = "data.csv"                                               # member to extract from each ZIP
CSV_ENCODING = "utf-8"                                                   # change to "cp932" if necessary
CSV_DIALECT = {"delimiter": ",", "quotechar": '"', "quoting": csv.QUOTE_MINIMAL}
HAS_HEADER = True                                                          # input CSV contains a header row
TIME_COLUMN_INDEX = 6                                                      # 0-based index of timestamp column
OPID_COLUMN_INDEX = 3                                                      # 0-based index of operation ID column
BUFFER_SIZE = 1 << 20                                                      # 1 MiB write buffer for chunk/output files

# Phase 1 chunking thresholds
CHUNK_MAX_ROWS = 200_000                                                   # flush buffer after this many rows
CHUNK_MAX_BYTES = 200 * 1024 * 1024                                        # or roughly 200 MiB of text data
TEMP_DIR = Path("temp")                                                   # base directory for chunk files

# Phase 2 merge controls
MAX_OPEN_MERGE_FILES = 32                                                  # max files to open simultaneously in merge
MERGE_BATCH_PREFIX = "merge_pass"                                         # prefix for intermediate merge files

# Logging / progress configuration
LOG_LEVEL = logging.INFO
PROGRESS_BAR = True                                                        # enable tqdm progress if available

# Timestamp parsing patterns (in addition to datetime.fromisoformat)
TIME_FORMATS: Sequence[str] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
)


# ---------------------------------------------------------------------------
# Data classes and helper structures
# ---------------------------------------------------------------------------


@dataclass(order=True)
class RowRecord:
    """Represents a buffered row tagged with its timestamp and sequence."""

    sort_index: tuple = field(init=False, repr=False)
    timestamp: dt.datetime
    sequence: int
    row: List[str]

    def __post_init__(self) -> None:  # pragma: no cover - trivial field init
        self.sort_index = (self.timestamp, self.sequence)


@dataclass
class OpidBuffer:
    """Buffer rows for an operation ID prior to flushing to disk."""

    opid: str
    rows: List[RowRecord] = field(default_factory=list)
    estimated_bytes: int = 0
    chunk_count: int = 0

    def add(self, record: RowRecord) -> None:
        self.rows.append(record)
        # Rough byte estimate for flush heuristics (cells + commas + newline)
        self.estimated_bytes += sum(len(cell) for cell in record.row) + len(record.row)

    @property
    def needs_flush(self) -> bool:
        return len(self.rows) >= CHUNK_MAX_ROWS or self.estimated_bytes >= CHUNK_MAX_BYTES

    def reset(self) -> None:
        self.rows.clear()
        self.estimated_bytes = 0


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    """Configure basic logging suitable for console use."""

    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def ensure_directory(path: Path) -> None:
    """Create *path* if it does not already exist."""

    path.mkdir(parents=True, exist_ok=True)


def discover_zip_files(directory: Path, digit_keys: Iterable[str]) -> List[Path]:
    """Return a sorted list of ZIP files whose names contain any digit key."""

    keys = list(digit_keys)
    matches: List[Path] = []
    for entry in directory.iterdir():
        if not entry.is_file() or entry.suffix.lower() != ".zip":
            continue
        name = entry.name
        if any(key in name for key in keys):
            matches.append(entry)
    matches.sort(key=lambda p: p.name)
    return matches


def parse_timestamp(raw: str) -> dt.datetime:
    """Parse a timestamp string using common ETC formats."""

    value = raw.strip()
    if not value:
        raise ValueError("empty timestamp")

    # Try ISO-8601 parsing first (Python 3.11+ handles offsets and fractions).
    try:
        # Handle trailing 'Z' (UTC) explicitly.
        if value.endswith("Z"):
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.datetime.fromisoformat(value)
    except ValueError:
        pass

    for pattern in TIME_FORMATS:
        try:
            return dt.datetime.strptime(value, pattern)
        except ValueError:
            continue

    raise ValueError(f"Unrecognised timestamp format: {value!r}")


def timestamp_for_sort(raw: str, *, context: str) -> dt.datetime:
    """Parse a timestamp with logging on failure.

    Rows with unparseable timestamps are assigned :func:`datetime.datetime.max`
    so they appear at the end of their respective outputs while preserving the
    row. This avoids silent data loss while signalling the issue to the user.
    """

    try:
        return parse_timestamp(raw)
    except ValueError as exc:
        logging.warning("%s -- %s", context, exc)
        return dt.datetime.max


def estimate_rows_per_second(start: float, count: int) -> float:
    """Return the processing throughput as rows per second."""

    elapsed = time.time() - start
    if elapsed <= 0:
        return 0.0
    return count / elapsed


# ---------------------------------------------------------------------------
# Phase 1: Chunk creation
# ---------------------------------------------------------------------------


def run_chunking_phase(
    zip_files: Sequence[Path],
    temp_base: Path,
    output_dir: Path,
) -> tuple[Dict[str, List[Path]], Optional[List[str]], Dict[str, int]]:
    """Stream rows from ZIP archives and create sorted chunk files per opid."""

    ensure_directory(temp_base)

    buffers: Dict[str, OpidBuffer] = {}
    chunk_paths: Dict[str, List[Path]] = {}
    opid_counts: defaultdict[str, int] = defaultdict(int)
    header_row: Optional[List[str]] = None
    total_rows = 0
    sequence_counter = itertools.count()

    progress: Optional[Iterable[Path]]
    if PROGRESS_BAR and tqdm is not None:
        progress = tqdm(zip_files, desc="Chunking", unit="zip", leave=True)
    else:
        progress = zip_files

    start_time = time.time()

    for zip_path in progress:  # type: ignore[assignment]
        logging.info("Scanning %s", zip_path.name)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                try:
                    info = zf.getinfo(INNER_CSV_NAME)
                except KeyError:
                    logging.warning("%s missing '%s'; skipping", zip_path.name, INNER_CSV_NAME)
                    continue

                with zf.open(info, mode="r") as raw:
                    text_stream = io.TextIOWrapper(
                        raw,
                        encoding=CSV_ENCODING,
                        newline="",
                        errors="strict",
                    )
                    reader = csv.reader(text_stream, **CSV_DIALECT)

                    local_row_number = 0
                    for row in reader:
                        local_row_number += 1

                        if HAS_HEADER and local_row_number == 1:
                            if header_row is None:
                                header_row = row
                                logging.info("Captured header with %d columns", len(row))
                            elif header_row != row:
                                logging.warning(
                                    "Header mismatch detected in %s; using first header encountered",
                                    zip_path.name,
                                )
                            continue

                        if len(row) <= max(OPID_COLUMN_INDEX, TIME_COLUMN_INDEX):
                            logging.error(
                                "Row %d in %s lacks required columns; skipped", local_row_number, zip_path.name
                            )
                            continue

                        opid = row[OPID_COLUMN_INDEX].strip()
                        if not opid:
                            logging.warning(
                                "Empty opid at row %d in %s; skipped", local_row_number, zip_path.name
                            )
                            continue

                        timestamp_value = row[TIME_COLUMN_INDEX]
                        timestamp = timestamp_for_sort(
                            timestamp_value,
                            context=f"{zip_path.name} row {local_row_number}",
                        )

                        sequence = next(sequence_counter)
                        record = RowRecord(timestamp=timestamp, sequence=sequence, row=row)

                        buffer = buffers.get(opid)
                        if buffer is None:
                            buffer = OpidBuffer(opid=opid)
                            buffers[opid] = buffer
                        buffer.add(record)
                        opid_counts[opid] += 1
                        total_rows += 1

                        if buffer.needs_flush:
                            chunk_path = flush_buffer(buffer, temp_base)
                            chunk_paths.setdefault(opid, []).append(chunk_path)

        except zipfile.BadZipFile as exc:
            logging.error("Unable to read ZIP %s: %s", zip_path.name, exc)
            continue

    # Flush remaining buffers after scanning all ZIPs
    for opid, buffer in buffers.items():
        if buffer.rows:
            chunk_path = flush_buffer(buffer, temp_base)
            chunk_paths.setdefault(opid, []).append(chunk_path)

    rows_per_second = estimate_rows_per_second(start_time, total_rows)
    logging.info(
        "Chunking complete: %d rows across %d operation IDs (%.2f rows/sec)",
        total_rows,
        len(chunk_paths),
        rows_per_second,
    )

    return chunk_paths, header_row, opid_counts


def flush_buffer(buffer: OpidBuffer, temp_base: Path) -> Path:
    """Sort and flush the buffer for an operation ID to disk."""

    if not buffer.rows:
        raise ValueError("Attempted to flush an empty buffer")

    buffer.rows.sort()  # RowRecord defines order via ``sort_index``

    opid_dir = temp_base / buffer.opid
    ensure_directory(opid_dir)
    buffer.chunk_count += 1
    chunk_path = opid_dir / f"chunk_{buffer.chunk_count:05d}.csv"

    logging.info(
        "Flushing %d rows for opid=%s to %s", len(buffer.rows), buffer.opid, chunk_path
    )

    with chunk_path.open("w", encoding=CSV_ENCODING, newline="", buffering=BUFFER_SIZE) as fp:
        writer = csv.writer(fp, **CSV_DIALECT)
        for record in buffer.rows:
            writer.writerow(record.row)

    buffer.reset()
    return chunk_path


# ---------------------------------------------------------------------------
# Phase 2: K-way merge
# ---------------------------------------------------------------------------


def merge_chunks_for_opid(
    opid: str,
    chunk_paths: Sequence[Path],
    output_dir: Path,
    header: Optional[List[str]],
) -> None:
    """Merge chunk files for a single operation ID into the final CSV."""

    if not chunk_paths:
        logging.info("No chunk files for opid=%s; skipping", opid)
        return

    work_paths = list(chunk_paths)
    pass_index = 0
    opid_temp_dir = chunk_paths[0].parent

    # Reduce the number of chunk files using intermediate merges if necessary.
    while len(work_paths) > MAX_OPEN_MERGE_FILES:
        logging.info(
            "opid=%s has %d chunk files; performing intermediate merge pass %d",
            opid,
            len(work_paths),
            pass_index,
        )
        next_paths: List[Path] = []
        for batch_index, start in enumerate(range(0, len(work_paths), MAX_OPEN_MERGE_FILES)):
            batch = work_paths[start : start + MAX_OPEN_MERGE_FILES]
            merge_output = opid_temp_dir / f"{MERGE_BATCH_PREFIX}{pass_index:02d}_{batch_index:03d}.csv"
            merge_files(batch, merge_output, header=None)
            for path in batch:
                try:
                    path.unlink()
                except OSError:
                    logging.warning("Failed to delete temporary chunk %s", path)
            next_paths.append(merge_output)
        work_paths = next_paths
        pass_index += 1

    final_output = output_dir / f"{TERM_NAME}_{opid}.csv"
    logging.info(
        "Merging %d chunk files for opid=%s into %s", len(work_paths), opid, final_output
    )
    merge_files(work_paths, final_output, header=header if HAS_HEADER else None, write_header=HAS_HEADER)

    # Clean up temporary chunk files and directory
    for path in work_paths:
        try:
            path.unlink()
        except OSError:
            logging.warning("Failed to delete temporary chunk %s", path)

    try:
        opid_temp_dir.rmdir()
    except OSError:
        # Directory may not be empty if intermediate passes produced nested files.
        logging.debug("Temporary directory %s not empty after merge", opid_temp_dir)


def merge_files(
    input_paths: Sequence[Path],
    output_path: Path,
    *,
    header: Optional[List[str]],
    write_header: bool = False,
) -> None:
    """Merge sorted CSV chunk files into ``output_path``."""

    ensure_directory(output_path.parent)

    readers: List[Iterator[List[str]]] = []
    file_handles: List[io.TextIOWrapper] = []

    try:
        for path in input_paths:
            fp = path.open("r", encoding=CSV_ENCODING, newline="")
            reader = csv.reader(fp, **CSV_DIALECT)
            file_handles.append(fp)
            readers.append(reader)

        with output_path.open("w", encoding=CSV_ENCODING, newline="", buffering=BUFFER_SIZE) as out_fp:
            writer = csv.writer(out_fp, **CSV_DIALECT)

            if write_header and header is not None:
                writer.writerow(header)

            push_initial_rows(readers, writer)

    finally:
        for fp in file_handles:
            try:
                fp.close()
            except Exception:
                pass


def push_initial_rows(readers: Sequence[Iterator[List[str]]], writer: csv.writer) -> None:
    """Perform the heap-based k-way merge and stream rows to ``writer``."""

    heap: List[tuple[dt.datetime, int, int, List[str]]] = []
    row_counter = itertools.count()

    for reader_index, reader in enumerate(readers):
        try:
            row = next(reader)
        except StopIteration:
            continue

        timestamp = timestamp_for_sort(
            row[TIME_COLUMN_INDEX] if len(row) > TIME_COLUMN_INDEX else "",
            context=f"chunk reader {reader_index} initial row",
        )
        heapq.heappush(heap, (timestamp, next(row_counter), reader_index, row))

    while heap:
        timestamp, _, reader_index, row = heapq.heappop(heap)
        writer.writerow(row)

        reader = readers[reader_index]
        try:
            next_row = next(reader)
        except StopIteration:
            continue

        next_timestamp = timestamp_for_sort(
            next_row[TIME_COLUMN_INDEX] if len(next_row) > TIME_COLUMN_INDEX else "",
            context=f"chunk reader {reader_index} subsequent row",
        )
        heapq.heappush(heap, (next_timestamp, next(row_counter), reader_index, next_row))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()

    input_dir = Path(INPUT_DIR)
    output_dir = Path(OUTPUT_DIR)

    if not input_dir.exists():
        logging.error("INPUT_DIR does not exist: %s", input_dir)
        sys.exit(1)

    ensure_directory(output_dir)

    zip_files = discover_zip_files(input_dir, ZIP_DIGIT_KEYS)
    if not zip_files:
        logging.info("No ZIP files matched the provided digit keys. Nothing to do.")
        return

    logging.info("Discovered %d matching ZIP file(s)", len(zip_files))

    chunk_paths, header, opid_counts = run_chunking_phase(zip_files, TEMP_DIR, output_dir)

    if not chunk_paths:
        logging.info("No chunk files were generated. Exiting.")
        return

    opids = sorted(chunk_paths.keys())
    progress: Optional[Iterable[str]]
    if PROGRESS_BAR and tqdm is not None:
        progress = tqdm(opids, desc="Merging", unit="opid", leave=True)
    else:
        progress = opids

    for opid in progress:  # type: ignore[assignment]
        merge_chunks_for_opid(opid, chunk_paths[opid], output_dir, header)

    # Attempt to remove the temporary directory tree once all merges complete
    try:
        # Remove empty directories bottom-up
        for opid_dir in TEMP_DIR.iterdir():
            if opid_dir.is_dir():
                try:
                    opid_dir.rmdir()
                except OSError:
                    pass
        TEMP_DIR.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        logging.debug("Temporary directory %s not empty after cleanup", TEMP_DIR)

    total_rows = sum(opid_counts.values())
    logging.info(
        "Completed merge for %d operation IDs (%d total rows)", len(opid_counts), total_rows
    )


if __name__ == "__main__":
    main()

