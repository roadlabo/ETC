"""Trip extractor utility.

This script scans CSV files in a selected directory and extracts trips that
match a sample route defined by a separate CSV file. The GUI prompts are used
to select both the sample file and the directory containing the trip logs.

The implementation follows the requirements provided in the user request,
including robust trip segmentation, route matching via haversine distance, and
safe handling of malformed data rows.
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Sequence, Tuple

import numpy as np

try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:  # pragma: no cover - tkinter not available in some envs
    tk = None
    filedialog = None


EARTH_RADIUS_M = 6371000.0
LAT_INDEX = 14
LON_INDEX = 15
FLAG_INDEX = 12


@dataclass
class CSVRow:
    """Container for parsed CSV row along with original values."""

    values: List[str]

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.values)

    def __getitem__(self, item):  # pragma: no cover - trivial
        return self.values[item]


def read_sample_points(path: Path) -> np.ndarray:
    """Read sample latitude/longitude points from a CSV file.

    The returned array contains radians for both latitude and longitude.
    Rows with missing or invalid data are skipped.
    """

    points: List[Tuple[float, float]] = []
    try:
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
                points.append((math.radians(lat), math.radians(lon)))
    except Exception as exc:  # pragma: no cover - safety catch
        logging.error("Failed to read sample CSV %s: %s", path, exc)
        raise

    if not points:
        raise ValueError(f"No valid sample points found in {path}")

    return np.asarray(points, dtype=np.float64)


def haversine_batch(lat: float, lon: float, sample_lat_rad: np.ndarray, sample_lon_rad: np.ndarray) -> float:
    """Return the minimum haversine distance between a point and sample points."""

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)

    d_lat = lat_rad - sample_lat_rad
    d_lon = lon_rad - sample_lon_rad
    sin_dlat = np.sin(d_lat / 2.0)
    sin_dlon = np.sin(d_lon / 2.0)
    a = sin_dlat ** 2 + np.cos(lat_rad) * np.cos(sample_lat_rad) * sin_dlon ** 2
    c = 2.0 * np.arcsin(np.minimum(1.0, np.sqrt(a)))
    distances = EARTH_RADIUS_M * c
    return float(np.min(distances))


def build_boundaries(rows: Sequence[CSVRow]) -> List[int]:
    """Construct trip boundaries based on flag transitions and file edges."""

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
    """Yield candidate segments as half-open intervals from sorted boundaries."""

    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end - start >= 2:
            yield start, end


def trip_matches_route(
    rows: Sequence[CSVRow],
    sample_points: np.ndarray,
    thresh_m: float = 10.0,
    min_hits: int = 2,
) -> bool:
    """Determine if a trip segment matches the sample route."""

    if sample_points.size == 0:
        return False

    sample_lat = sample_points[:, 0]
    sample_lon = sample_points[:, 1]

    hits = 0
    for row in rows:
        if len(row.values) <= max(LAT_INDEX, LON_INDEX):
            continue
        try:
            lat = float(row.values[LAT_INDEX])
            lon = float(row.values[LON_INDEX])
        except (TypeError, ValueError):
            continue

        distance = haversine_batch(lat, lon, sample_lat, sample_lon)
        if distance <= thresh_m:
            hits += 1
            if hits >= min_hits:
                return True

    return False


def save_trip(rows: Sequence[CSVRow], out_dir: Path, base_name: str, seq_no: int) -> Path:
    """Save a trip segment to a CSV file and return the path."""

    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{base_name}-{seq_no:02d}.csv"
    out_path = out_dir / filename
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row.values)
    return out_path


def read_csv_rows(path: Path) -> List[CSVRow]:
    """Read CSV rows preserving their values for later writing."""

    rows: List[CSVRow] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(CSVRow(list(row)))
    return rows


def process_file(
    path: Path,
    sample_points: np.ndarray,
    out_dir: Path,
    thresh_m: float,
    min_hits: int,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Process a single CSV file and return the number of saved trips."""

    try:
        rows = read_csv_rows(path)
    except Exception as exc:
        logging.warning("Failed to read %s: %s", path, exc)
        return 0

    if not rows:
        return 0

    boundaries = build_boundaries(rows)
    segments = list(iter_segments_from_boundaries(boundaries))
    saved = 0

    for start, end in segments:
        segment_rows = rows[start:end]
        if not trip_matches_route(segment_rows, sample_points, thresh_m, min_hits):
            continue

        if dry_run:
            saved += 1
            if verbose:
                logging.info("[DRY-RUN] Trip match in %s rows %d-%d", path.name, start, end)
            continue

        try:
            save_trip(segment_rows, out_dir, path.stem, saved + 1)
            saved += 1
            if verbose:
                logging.info("Saved trip %s #%02d (%d-%d)", path.name, saved, start, end)
        except Exception as exc:
            logging.warning("Failed to save segment from %s: %s", path, exc)

    return saved


def collect_csv_files(directory: Path, recursive: bool = False) -> List[Path]:
    """Collect CSV files from a directory."""

    if recursive:
        files = sorted(p for p in directory.rglob("*.csv") if p.is_file())
    else:
        files = sorted(p for p in directory.glob("*.csv") if p.is_file())
    return files


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract trips matching a sample route")
    parser.add_argument("--sample", type=Path, help="Path to sample CSV (optional)")
    parser.add_argument("--input-dir", type=Path, help="Directory containing trip CSV files (optional)")
    parser.add_argument("--thresh", type=float, default=10.0, help="Distance threshold in meters")
    parser.add_argument("--min-hits", type=int, default=2, help="Minimum matching points required")
    parser.add_argument("--dry-run", action="store_true", help="Identify matches without writing files")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--recursive", action="store_true", help="Recursively search for CSV files")
    return parser.parse_args(argv)


def init_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def request_path_via_gui(args: argparse.Namespace) -> Tuple[Path, Path]:
    """Prompt the user via GUI dialogs for sample file and input directory."""

    if args.sample and args.input_dir:
        return args.sample, args.input_dir

    if tk is None or filedialog is None:
        raise RuntimeError("Tkinter is not available; please provide --sample and --input-dir")

    root = tk.Tk()
    root.withdraw()

    sample_path = args.sample
    if sample_path is None:
        sample_file = filedialog.askopenfilename(title="Select sample CSV", filetypes=[["CSV files", "*.csv"]])
        if not sample_file:
            raise SystemExit("Sample CSV selection cancelled")
        sample_path = Path(sample_file)

    input_dir = args.input_dir
    if input_dir is None:
        directory = filedialog.askdirectory(title="Select directory containing trip CSV files")
        if not directory:
            raise SystemExit("Input directory selection cancelled")
        input_dir = Path(directory)

    root.destroy()
    return sample_path, input_dir


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    args = parse_args(argv)
    init_logging(args.verbose)

    try:
        sample_path, input_dir = request_path_via_gui(args)
    except Exception as exc:
        logging.error("Initialization failed: %s", exc)
        return 1

    if not sample_path.exists():
        logging.error("Sample CSV does not exist: %s", sample_path)
        return 1

    if not input_dir.exists() or not input_dir.is_dir():
        logging.error("Input directory does not exist or is not a directory: %s", input_dir)
        return 1

    try:
        sample_points = read_sample_points(sample_path)
    except Exception as exc:
        logging.error("Failed to process sample CSV: %s", exc)
        return 1

    files = collect_csv_files(input_dir, recursive=args.recursive)
    if not files:
        logging.info("No CSV files found in %s", input_dir)
        return 0

    out_dir = input_dir / sample_path.stem
    total_saved = 0

    for file_path in files:
        saved = process_file(
            file_path,
            sample_points,
            out_dir,
            thresh_m=args.thresh,
            min_hits=args.min_hits,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        total_saved += saved

    logging.info("Finished processing %d files, saved %d trips", len(files), total_saved)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

