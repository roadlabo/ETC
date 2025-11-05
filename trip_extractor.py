"""ETC trip extractor utility.

This module scans CSV files within a configured directory and extracts trip
segments whose routes overlap a given sample route. The
implementation follows the detailed specification provided in the user
instructions, including strict trip segmentation rules, haversine distance
checks, and rich command-line progress feedback.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Sequence, Tuple

import numpy as np


# Column indices (0-based)
LAT_INDEX = 14
LON_INDEX = 15
FLAG_INDEX = 12

EARTH_RADIUS_M = 6_371_000.0

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Edit these paths to match your environment when running the script without
# command-line arguments.  Both paths can still be overridden via --sample and
# --input-dir options if desired.
DEFAULT_SAMPLE_PATH: Path | None = Path("/path/to/sample_route.csv")
DEFAULT_INPUT_DIR: Path | None = Path("/path/to/input_directory")


@dataclass
class CSVRow:
    """Container for a CSV row preserving its original values."""

    values: List[str]

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.values)

    def __getitem__(self, item):  # pragma: no cover - trivial
        return self.values[item]


def read_sample_points(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Read sample latitude/longitude points and return radians arrays."""

    lat_list: List[float] = []
    lon_list: List[float] = []

    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) <= max(LAT_INDEX, LON_INDEX):
                continue
            try:
                lat = float(row[LAT_INDEX])
                lon = float(row[LON_INDEX])
            except (TypeError, ValueError):
                continue
            lat_list.append(math.radians(lat))
            lon_list.append(math.radians(lon))

    if not lat_list:
        raise ValueError(f"No valid sample points found in {path}")

    return np.asarray(lat_list, dtype=np.float64), np.asarray(lon_list, dtype=np.float64)


def haversine_min_to_sample(
    lat_deg: float,
    lon_deg: float,
    sample_lat_rad: np.ndarray,
    sample_lon_rad: np.ndarray,
) -> float:
    """Return the minimum haversine distance from a point to the sample points."""

    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)

    d_lat = lat_rad - sample_lat_rad
    d_lon = lon_rad - sample_lon_rad

    sin_dlat = np.sin(d_lat / 2.0)
    sin_dlon = np.sin(d_lon / 2.0)
    a = sin_dlat ** 2 + np.cos(lat_rad) * np.cos(sample_lat_rad) * sin_dlon ** 2
    c = 2.0 * np.arcsin(np.minimum(1.0, np.sqrt(a)))
    distances = EARTH_RADIUS_M * c
    return float(np.min(distances))


def read_csv_rows(path: Path) -> List[CSVRow]:
    """Read CSV rows (without headers) preserving original values."""

    rows: List[CSVRow] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(CSVRow(list(row)))
    return rows


def build_boundaries(rows: Sequence[CSVRow]) -> List[int]:
    """Build the boundary set B following the strict specification."""

    boundaries = {0, len(rows)}
    for idx, row in enumerate(rows):
        if len(row.values) <= FLAG_INDEX:
            continue
        flag = row.values[FLAG_INDEX]
        if flag == "0":
            boundaries.add(idx)
        elif flag == "1":
            boundaries.add(idx + 1)
    return sorted(boundaries)


def iter_segments_from_boundaries(boundaries: Sequence[int]) -> Iterator[Tuple[int, int]]:
    """Yield candidate segments from consecutive boundary pairs (length >= 2)."""

    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end - start >= 2:
            yield start, end


def trip_matches_route(
    rows: Sequence[CSVRow],
    start: int,
    end: int,
    sample_lat_rad: np.ndarray,
    sample_lon_rad: np.ndarray,
    thresh_m: float,
    min_hits: int,
) -> bool:
    """Return True if the segment [start, end) contains at least ``min_hits`` matches."""

    if sample_lat_rad.size == 0 or sample_lon_rad.size == 0:
        return False

    hits = 0
    for row in rows[start:end]:
        if len(row.values) <= max(LAT_INDEX, LON_INDEX):
            continue
        try:
            lat = float(row.values[LAT_INDEX])
            lon = float(row.values[LON_INDEX])
        except (TypeError, ValueError):
            continue

        distance = haversine_min_to_sample(lat, lon, sample_lat_rad, sample_lon_rad)
        if distance <= thresh_m:
            hits += 1
            if hits >= min_hits:
                return True

    return False


def save_trip(
    rows: Sequence[CSVRow],
    start: int,
    end: int,
    out_dir: Path,
    base_name: str,
    seq_no: int,
) -> Path:
    """Save the segment [start, end) into the output directory."""

    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{base_name}-{seq_no:02d}.csv"
    out_path = out_dir / filename
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for row in rows[start:end]:
            writer.writerow(row.values)
    return out_path


def list_csv_files(root: Path, recursive: bool = False) -> List[Path]:
    """Return a sorted list of CSV files under ``root``."""

    if recursive:
        return sorted(p for p in root.rglob("*.csv") if p.is_file())
    return sorted(p for p in root.glob("*.csv") if p.is_file())


def format_hms(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""

    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _clear_progress(last_len: int) -> None:
    if last_len:
        sys.stdout.write("\r" + " " * last_len + "\r")
        sys.stdout.flush()


def _update_progress(line: str, last_len: int) -> int:
    padding = max(0, last_len - len(line))
    sys.stdout.write("\r" + line + (" " * padding))
    sys.stdout.flush()
    return len(line)


def process_file(
    path: Path,
    sample_lat_rad: np.ndarray,
    sample_lon_rad: np.ndarray,
    out_dir: Path,
    thresh_m: float,
    min_hits: int,
    dry_run: bool,
    verbose: bool,
) -> Tuple[int, int, int]:
    """Process a single CSV file and return (candidate_trips, matched, saved)."""

    try:
        rows = read_csv_rows(path)
    except Exception as exc:
        if verbose:
            print(f"Failed to read {path.name}: {exc}")
        return 0, 0, 0

    if not rows:
        if verbose:
            print(f"{path.name}: empty file")
        return 0, 0, 0

    boundaries = build_boundaries(rows)
    segments = list(iter_segments_from_boundaries(boundaries))
    candidate_count = len(segments)
    matched_count = 0
    saved_count = 0

    for seg_idx, (start, end) in enumerate(segments, start=1):
        if not trip_matches_route(
            rows,
            start,
            end,
            sample_lat_rad,
            sample_lon_rad,
            thresh_m,
            min_hits,
        ):
            continue

        matched_count += 1
        if dry_run:
            saved_count += 1
            if verbose:
                print(
                    f"[DRY-RUN] {path.name}: match segment #{seg_idx} rows {start}-{end}"
                )
            continue

        try:
            save_trip(rows, start, end, out_dir, path.stem, saved_count + 1)
            saved_count += 1
            if verbose:
                print(
                    f"Saved {path.name} segment #{saved_count:02d} rows {start}-{end}"
                )
        except Exception as exc:
            if verbose:
                print(f"Failed to save segment from {path.name}: {exc}")

    return candidate_count, matched_count, saved_count


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract trips that match a sample route")
    parser.add_argument("--sample", type=Path, help="Path to sample CSV")
    parser.add_argument("--input-dir", type=Path, help="Directory containing trip CSV files")
    parser.add_argument("--thresh", type=float, default=10.0, help="Distance threshold in meters")
    parser.add_argument("--min-hits", type=int, default=2, help="Minimum match points required")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing files")
    parser.add_argument("--recursive", action="store_true", help="Recursively search CSV files")
    parser.add_argument("--verbose", action="store_true", help="Print verbose logs")
    return parser.parse_args(list(argv))


def resolve_paths(args: argparse.Namespace) -> Tuple[Path, Path]:
    """Resolve the sample file and input directory without GUI dialogs."""

    sample_path = args.sample or DEFAULT_SAMPLE_PATH
    input_dir = args.input_dir or DEFAULT_INPUT_DIR

    if sample_path is None or input_dir is None:
        raise SystemExit(
            "Specify --sample and --input-dir or set DEFAULT_SAMPLE_PATH and "
            "DEFAULT_INPUT_DIR in the script."
        )

    return Path(sample_path), Path(input_dir)


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    args = parse_args(argv)

    try:
        sample_path, input_dir = resolve_paths(args)
    except Exception as exc:
        print(f"Initialization failed: {exc}")
        return 1

    if not sample_path.exists():
        print(f"Sample CSV not found: {sample_path}")
        return 1

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}")
        return 1

    try:
        sample_lat_rad, sample_lon_rad = read_sample_points(sample_path)
    except Exception as exc:
        print(f"Failed to read sample CSV: {exc}")
        return 1

    files = list_csv_files(input_dir, recursive=args.recursive)
    total_files = len(files)
    if total_files == 0:
        print(f"No CSV files found in {input_dir}")
        return 0

    print(f"Total CSV files: {total_files}")

    out_root = input_dir / sample_path.stem
    total_trips = 0
    total_matches = 0
    total_saved = 0
    start_time = time.time()
    last_len = 0

    for index, file_path in enumerate(files, start=1):
        if args.verbose and last_len:
            _clear_progress(last_len)
            last_len = 0

        candidate_count, matched_count, saved_count = process_file(
            file_path,
            sample_lat_rad,
            sample_lon_rad,
            out_root,
            thresh_m=args.thresh,
            min_hits=args.min_hits,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

        total_trips += candidate_count
        total_matches += matched_count
        total_saved += saved_count

        elapsed = time.time() - start_time
        avg_time = elapsed / index
        eta = avg_time * (total_files - index)

        line = (
            f"[{index}/{total_files}] {file_path.name}  "
            f"trips:{candidate_count}  hits:{matched_count}  saved:{saved_count}  "
            f"(elapsed {format_hms(elapsed)}, eta {format_hms(eta)})"
        )
        last_len = _update_progress(line, last_len)

        if args.verbose:
            sys.stdout.write("\n")
            sys.stdout.flush()
            last_len = 0

    if last_len:
        sys.stdout.write("\n")
        sys.stdout.flush()

    print(
        "Processed {files} files, total trips {trips}, matched {matches}, saved {saved}".format(
            files=total_files, trips=total_trips, matches=total_matches, saved=total_saved
        )
    )

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())

