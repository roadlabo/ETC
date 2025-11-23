"""ETC trip extractor for crossroad points.

This script scans trip CSV files and extracts trips that pass near specified
crossroad points. It follows the structure of ``15_trip_extractor_route.py``
while adapting the matching logic to point/segment distance checks against
crossroad centers.
"""

from __future__ import annotations

import csv
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# 入力トリップCSVディレクトリ / 出力ディレクトリ
DEFAULT_INPUT_DIR: Path | None = Path(r"/path/to/input_directory")
DEFAULT_OUTPUT_DIR: Path | None = Path(r"/path/to/output_directory")

# 交差点CSVファイルを複数指定
CROSSROAD_CSV_LIST: list[Path] = [
    Path(r"/path/to/crossroad001.csv"),
    Path(r"/path/to/crossroad002.csv"),
    # 必要に応じて追加
]

THRESH_M = 20.0      # 交差点中心からの判定距離[m]
MIN_HITS = 1         # HITとみなす最小ヒット数（点＋線分の合計）
DRY_RUN = False
VERBOSE = False
RECURSIVE = False
AUDIT_MODE = False   # （必要なら距離計算回数などの統計用）

# 曜日フィルタは 15_trip_extractor_route.py と同様の TARGET_WEEKDAYS を流用
TARGET_WEEKDAYS: set[int] = {1, 2, 3, 4, 5, 6, 7}


# Column indices (0-based)
LAT_INDEX = 14
LON_INDEX = 15
FLAG_INDEX = 12
DATE_INDEX = 6  # G列。例: 20250224161105（YYYYMMDDHHMMSS）
OP_DATE_INDEX = 2  # C列: 運行日 (YYYYMMDD)
OP_ID_INDEX = 3    # D列: 運行ID (12桁数字)
VEHICLE_TYPE_INDEX = 4  # E列: 自動車種別 (2桁数字)
VEHICLE_USE_INDEX = 5   # F列: 自動車用途 (2桁数字)
TRIP_NO_INDEX = 8       # I列: トリップ番号 (数値)

EARTH_RADIUS_M = 6_371_000.0


@dataclass
class CSVRow:
    """Container for a CSV row preserving its original values."""

    values: List[str]

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.values)

    def __getitem__(self, item):  # pragma: no cover - trivial
        return self.values[item]


@dataclass
class CrossroadPoint:
    """Crossroad center point loaded from a sampler CSV."""

    name: str
    lon: float
    lat: float


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def read_csv_rows(path: Path) -> List[CSVRow]:
    """Read CSV rows (without headers) preserving original values."""

    rows: List[CSVRow] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(CSVRow(list(row)))
    return rows


def load_crossroad_points(paths: list[Path]) -> list[CrossroadPoint]:
    """Load crossroad center points from sampler CSV files."""

    points: list[CrossroadPoint] = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) <= 2:
                        continue
                    try:
                        lon = float(row[1])
                        lat = float(row[2])
                    except (TypeError, ValueError):
                        continue
                    points.append(CrossroadPoint(name=path.stem, lon=lon, lat=lat))
                    break  # use only first data row
        except Exception:
            continue
    return points


# ---------------------------------------------------------------------------
# Weekday utilities
# ---------------------------------------------------------------------------

def _weekday_from_row(row: "CSVRow") -> int | None:
    """
    G列（DATE_INDEX）の先頭8桁 YYYYMMDD から曜日番号を返す。
    戻り値: 1=SUN, 2=MON, ... , 7=SAT。パース失敗時は None。
    """

    try:
        if len(row.values) <= DATE_INDEX:
            return None
        token = row.values[DATE_INDEX]
        if not token:
            return None
        ymd = token[:8]  # "YYYYMMDD"
        dt = datetime.strptime(ymd, "%Y%m%d")
        py = dt.weekday()  # Mon=0, Tue=1, ..., Sun=6
        return 1 if py == 6 else py + 2
    except Exception:
        return None


WEEKDAY_ABBR = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]  # 1: SUN ... 7: SAT に対応


def _weekday_abbr_from_ymd(ymd: str) -> str | None:
    """
    YYYYMMDD 文字列から曜日の英語3文字表記を返す。
    例: "20250224" -> "MON"
    パース失敗時は None。
    """

    if len(ymd) != 8 or not ymd.isdigit():
        return None
    try:
        dt = datetime.strptime(ymd, "%Y%m%d")
    except Exception:
        return None
    py = dt.weekday()  # Mon=0, ... Sun=6
    if py == 6:
        return "SUN"
    return WEEKDAY_ABBR[py + 1]  # MON=1, TUE=2, ... SAT=6


# ---------------------------------------------------------------------------
# Trip segmentation helpers
# ---------------------------------------------------------------------------

def build_boundaries(rows: Sequence[CSVRow]) -> List[int]:
    """Build the boundary set B following the strict specification."""

    boundaries: set[int] = {0, len(rows)}
    prev_trip_no: int | None = None

    for idx, row in enumerate(rows):
        if len(row.values) > FLAG_INDEX:
            flag = row.values[FLAG_INDEX]
            if flag == "0":
                boundaries.add(idx)
            elif flag == "1":
                boundaries.add(idx + 1)

        trip_no_val: int | None = None
        if len(row.values) > TRIP_NO_INDEX:
            token = row.values[TRIP_NO_INDEX].strip()
            if token:
                try:
                    trip_no_val = int(float(token))
                except (TypeError, ValueError):
                    trip_no_val = None

        if trip_no_val is not None:
            if prev_trip_no is None:
                prev_trip_no = trip_no_val
            elif trip_no_val != prev_trip_no:
                boundaries.add(idx)
                prev_trip_no = trip_no_val

    return sorted(boundaries)


def iter_segments_from_boundaries(boundaries: Sequence[int]) -> Iterator[Tuple[int, int]]:
    """Yield candidate segments from consecutive boundary pairs (length >= 2)."""

    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end - start >= 2:
            yield start, end


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def haversine_distance_m(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float:
    """Return haversine distance between two points in meters."""

    lat1 = math.radians(lat1_deg)
    lon1 = math.radians(lon1_deg)
    lat2 = math.radians(lat2_deg)
    lon2 = math.radians(lon2_deg)

    d_lat = lat2 - lat1
    d_lon = lon2 - lon1

    sin_dlat = math.sin(d_lat / 2.0)
    sin_dlon = math.sin(d_lon / 2.0)
    a = sin_dlat * sin_dlat + math.cos(lat1) * math.cos(lat2) * sin_dlon * sin_dlon
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_RADIUS_M * c


def _to_local_xy(lon_deg: float, lat_deg: float, lon0_deg: float, lat0_deg: float) -> Tuple[float, float]:
    """Convert lon/lat to local tangent plane coordinates (meters)."""

    lat0_rad = math.radians(lat0_deg)
    k = (math.pi / 180.0) * EARTH_RADIUS_M
    x = (lon_deg - lon0_deg) * math.cos(lat0_rad) * k
    y = (lat_deg - lat0_deg) * k
    return x, y


def _segment_distance_to_origin(p0: Tuple[float, float], p1: Tuple[float, float]) -> float:
    """Return shortest distance from segment p0-p1 to origin in meters."""

    x0, y0 = p0
    x1, y1 = p1
    dx = x1 - x0
    dy = y1 - y0
    if dx == 0 and dy == 0:
        return math.hypot(x0, y0)

    t = -(x0 * dx + y0 * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x0 + t * dx
    proj_y = y0 + t * dy
    return math.hypot(proj_x, proj_y)


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

def trip_matches_point(
    rows: Sequence[CSVRow],
    start: int,
    end: int,
    cross_lat_deg: float,
    cross_lon_deg: float,
    thresh_m: float,
    min_hits: int,
    target_weekdays: set[int],
) -> bool:
    """Return True if the segment [start, end) passes near the crossroad point."""

    point_hits = 0
    segment_hits = 0

    coords: list[Tuple[float, float]] = []

    for row in rows[start:end]:
        if target_weekdays:
            wd = _weekday_from_row(row)
            if wd is None or wd not in target_weekdays:
                continue

        if len(row.values) <= max(LAT_INDEX, LON_INDEX):
            continue
        try:
            lat = float(row.values[LAT_INDEX])
            lon = float(row.values[LON_INDEX])
        except (TypeError, ValueError):
            continue

        coords.append((lon, lat))
        distance = haversine_distance_m(lat, lon, cross_lat_deg, cross_lon_deg)
        if distance <= thresh_m:
            point_hits += 1
            if point_hits + segment_hits >= min_hits:
                return True

    if point_hits > 0 or len(coords) < 2:
        return point_hits + segment_hits >= min_hits

    # Segment-based check only when no point hit and at least two points exist.
    lon0 = cross_lon_deg
    lat0 = cross_lat_deg
    last_x, last_y = _to_local_xy(coords[0][0], coords[0][1], lon0, lat0)

    for lon, lat in coords[1:]:
        x, y = _to_local_xy(lon, lat, lon0, lat0)
        if max(abs(last_x), abs(last_y), abs(x), abs(y)) > thresh_m * 3:
            if math.hypot(last_x, last_y) > thresh_m * 3 and math.hypot(x, y) > thresh_m * 3:
                last_x, last_y = x, y
                continue

        dist = _segment_distance_to_origin((last_x, last_y), (x, y))
        if dist <= thresh_m:
            segment_hits += 1
            if point_hits + segment_hits >= min_hits:
                return True
        last_x, last_y = x, y

    return point_hits + segment_hits >= min_hits


# ---------------------------------------------------------------------------
# Saving trips and formatting
# ---------------------------------------------------------------------------

def save_trip(
    rows: Sequence[CSVRow],
    start: int,
    end: int,
    out_dir: Path,
    route_name: str,
    seq_no: int,
) -> Path:
    """Save the segment [start, end) into the output directory."""

    out_dir.mkdir(parents=True, exist_ok=True)

    rows_slice = rows[start:end]

    op_dates: set[str] = set()
    primary_date: str | None = None
    for row in rows_slice:
        if len(row.values) <= OP_DATE_INDEX:
            continue
        token = row.values[OP_DATE_INDEX].strip()
        if len(token) < 8:
            continue
        ymd = token[:8]
        if not ymd.isdigit():
            continue
        op_dates.add(ymd)
        if primary_date is None:
            primary_date = ymd

    weekdays: set[str] = set()
    for ymd in op_dates:
        abbr = _weekday_abbr_from_ymd(ymd)
        if abbr:
            weekdays.add(abbr)
    weekday_order = [abbr for abbr in WEEKDAY_ABBR if abbr in weekdays]
    weekday_part = "-".join(weekday_order) if weekday_order else "UNK"

    opid12 = "000000000000"
    for row in rows_slice:
        if len(row.values) <= OP_ID_INDEX:
            continue
        token = row.values[OP_ID_INDEX].strip()
        if not token:
            continue
        opid12 = token.zfill(12)
        break

    trip_tag = "t000"
    for row in rows_slice:
        if len(row.values) <= TRIP_NO_INDEX:
            continue
        token = row.values[TRIP_NO_INDEX].strip()
        if not token:
            continue
        try:
            trip_no = int(float(token))
        except (TypeError, ValueError):
            trip_no = None
        if trip_no is not None:
            trip_tag = f"t{trip_no:03d}"
            break

    etype_tag = "E00"
    for row in rows_slice:
        if len(row.values) <= VEHICLE_TYPE_INDEX:
            continue
        token = row.values[VEHICLE_TYPE_INDEX].strip()
        if token:
            etype_tag = f"E{token.zfill(2)}"
            break

    fuse_tag = "F00"
    for row in rows_slice:
        if len(row.values) <= VEHICLE_USE_INDEX:
            continue
        token = row.values[VEHICLE_USE_INDEX].strip()
        if token:
            fuse_tag = f"F{token.zfill(2)}"
            break

    if primary_date is None:
        primary_date = "00000000"

    filename = (
        f"2nd_{route_name}_{weekday_part}_ID{opid12}_{primary_date}_{trip_tag}_{etype_tag}_{fuse_tag}.csv"
    )
    out_path = out_dir / filename
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for row in rows_slice:
            writer.writerow(row.values)
    return out_path


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

def process_file_for_crossroad(
    path: Path,
    cross: CrossroadPoint,
    out_dir: Path,
    thresh_m: float,
    min_hits: int,
    dry_run: bool,
    verbose: bool,
) -> Tuple[int, int, int]:
    """Process a single CSV file for one crossroad point."""

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
        if not trip_matches_point(
            rows,
            start,
            end,
            cross.lat,
            cross.lon,
            thresh_m,
            min_hits,
            TARGET_WEEKDAYS,
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
            save_trip(rows, start, end, out_dir, cross.name, saved_count + 1)
            saved_count += 1
            if verbose:
                print(
                    f"Saved {path.name} segment #{saved_count:02d} rows {start}-{end}"
                )
        except Exception as exc:
            if verbose:
                print(f"Failed to save segment from {path.name}: {exc}")

    return candidate_count, matched_count, saved_count


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str]) -> Dict[str, Path | None]:
    if not argv:
        return {}

    import argparse

    parser = argparse.ArgumentParser(
        description="Extract trips that pass near specified crossroad points"
    )
    parser.add_argument("--input-dir", type=Path, help="Directory containing trip CSV files")
    parser.add_argument("--output-dir", type=Path, help="Directory to store extracted trips")
    return vars(parser.parse_args(list(argv)))


def resolve_paths(args: Dict[str, Path | None]) -> Tuple[Path, Path]:
    """Resolve the input and output directories."""

    input_dir = args.get("input_dir")
    output_dir = args.get("output_dir")

    input_dir = input_dir or DEFAULT_INPUT_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    if input_dir is None or output_dir is None:
        raise SystemExit(
            "Specify --input-dir and --output-dir or set DEFAULT_INPUT_DIR and DEFAULT_OUTPUT_DIR in the script."
        )

    return Path(input_dir), Path(output_dir)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_crossroad_header(cross: CrossroadPoint, file_count: int) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[CROSS] {cross.name}  開始: {now_str}  対象CSV: {file_count} files")


def _print_crossroad_footer(cross: CrossroadPoint, start_ts: float, hits: int) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elapsed = format_hms(time.time() - start_ts)
    print(
        f"[CROSS] {cross.name}  終了: {now_str}  所要時間: {elapsed}  HIT件数: {hits}"
    )
    print("")


def run_crossroad(
    cross: CrossroadPoint,
    trip_files: Sequence[Path],
    output_dir: Path,
    thresh_m: float,
    min_hits: int,
    dry_run: bool,
    verbose: bool,
) -> int:
    """Process all trip files for a single crossroad and return hit count."""

    _print_crossroad_header(cross, len(trip_files))
    start_ts = time.time()
    hits = 0
    last_len = 0

    for idx, trip_path in enumerate(trip_files, start=1):
        _, matched, _ = process_file_for_crossroad(
            trip_path, cross, output_dir, thresh_m, min_hits, dry_run, verbose
        )
        hits += matched

        elapsed = time.time() - start_ts
        percent = (idx / len(trip_files)) * 100 if trip_files else 100.0
        eta = (elapsed / idx) * (len(trip_files) - idx) if idx else 0.0
        line = (
            f"[{cross.name}]  {percent:3.0f}% ({idx}/{len(trip_files)} files)  hits: {hits}  "
            f"elapsed {format_hms(elapsed)}  eta {format_hms(eta)}"
        )
        last_len = _update_progress(line, last_len)

    _clear_progress(last_len)
    _print_crossroad_footer(cross, start_ts, hits)
    return hits


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    args = parse_args(argv)

    try:
        input_dir, output_dir = resolve_paths(args)
    except Exception as exc:
        print(f"Initialization failed: {exc}")
        return 1

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}")
        return 1

    if not CROSSROAD_CSV_LIST:
        print("No crossroad CSV files configured.")
        return 1

    crossroads = load_crossroad_points(CROSSROAD_CSV_LIST)
    if not crossroads:
        print("No valid crossroad points could be loaded.")
        return 1

    trip_files = list_csv_files(input_dir, recursive=RECURSIVE)
    if not trip_files:
        print(f"No trip CSV files found under {input_dir}")
        return 0

    print(f"Target trip CSV files: {len(trip_files)}")
    print(f"Target crossroads    : {len(crossroads)}")
    print("Crossroad list:")
    for cp in crossroads:
        print(f"  - {cp.name}")

    overall_start = time.time()
    overall_hits = 0

    for cp in crossroads:
        hits = run_crossroad(
            cp,
            trip_files,
            output_dir,
            THRESH_M,
            MIN_HITS,
            DRY_RUN,
            VERBOSE,
        )
        overall_hits += hits

    total_elapsed = format_hms(time.time() - overall_start)
    print(f"TOTAL 所要時間: {total_elapsed}")
    print(f"TOTAL HIT件数 : {overall_hits}")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
