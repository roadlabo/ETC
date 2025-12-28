"""Extract O/D coordinates, assign zones, and build an OD matrix.

This script ties together three inputs:

- First-screening split CSV files created by ``01_split_by_opid_streaming.py``
  (multiple trip numbers per file)
- 様式1-3 CSV files that contain O/D coordinates keyed by (運行ID1, トリップ番号)
- Zone polygon CSV exported from ``12_polygon_builder.html``

It produces four outputs under ``OUTPUT_DIR``:

- ``od_long.csv``: per (opid, trip_no) record with zones and coordinates
- ``od_matrix.csv``: zone-to-zone matrix (rows=zone_o, cols=zone_d)
- ``zone_production_attraction.csv``: production (row sum) and attraction
  (column sum) per zone
- ``zone_master.csv``: zone_id to zone_name mapping used in the matrix labels

Zone assignment is performed with a lightweight ray-casting point-in-polygon
implementation (no external GIS dependencies). Points outside any polygon are
assigned to a directional fallback zone (east/west/north/south) relative to the
津山市中心 point. When no 様式1-3 entry exists, the zone is ``MISSING`` and the
record is preserved in the outputs.
"""

from __future__ import annotations

import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Mapping, Sequence


# ---------------------------------------------------------------------------
# Configuration (edit before running)
# ---------------------------------------------------------------------------

# 集計対象データセット（分割CSVと様式1-3CSVのペアを複数指定可能）
# 必要に応じて追加・削除してください。
DATASETS = [
    {"name": "R7_02", "split_dir": Path(r"D:\path\to\01_split_output"), "style13_dir": Path(r"D:\path\to\style1-3_csvs")},
]

# ゾーンポリゴンCSV（12_polygon_builder.html 出力）
POLYGON_CSV = Path(r"D:\path\to\zones.csv")

# 津山市中心（基準点）座標（東西南北ゾーンの判定に使用）
TSUYAMA_CENTER_LON = 133.93
TSUYAMA_CENTER_LAT = 35.07

# 出力先フォルダ
OUTPUT_DIR = Path(r"D:\path\to\od_output")

# 集計対象曜日（分割CSVの運行日YYYYMMDDから判定）
TARGET_WEEKDAYS = {"火", "水", "木"}

# (opid, trip_no) を一意にカウントするかどうか
COUNT_UNIQUE_TRIP = True

# 入力で試行するエンコーディング（順に試す）
ENCODINGS = ("utf-8-sig", "utf-8", "cp932")

# ---------------------------------------------------------------------------
# Directional zones
# ---------------------------------------------------------------------------

EAST_ZONE = "東方面ゾーン"
WEST_ZONE = "西方面ゾーン"
NORTH_ZONE = "北方面ゾーン"
SOUTH_ZONE = "南方面ゾーン"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PolygonZone:
    name: str
    points: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


class ProgressTracker:
    def __init__(self, total_files: int) -> None:
        self.total_files = total_files
        self.done_files = 0
        self.hits = 0
        self.missing = 0
        self.phase = ""
        self.start_time = time.time()
        self.start_label = datetime.now().strftime("%H:%M:%S")
        self.last_print = 0.0

    def _format_time(self, seconds: float) -> str:
        if seconds < 0 or seconds != seconds:
            return "--:--:--"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def update(self, *, phase: str | None = None, increment_done: int = 0, hits: int | None = None, missing: int | None = None, force: bool = False) -> None:
        if phase is not None:
            self.phase = phase
        self.done_files += increment_done
        if hits is not None:
            self.hits = hits
        if missing is not None:
            self.missing = missing

        now = time.time()
        if not force and now - self.last_print < 0.5:
            return
        self.last_print = now

        elapsed = now - self.start_time
        if self.done_files > 0 and self.total_files > 0:
            eta = elapsed * (self.total_files / self.done_files - 1)
        else:
            eta = float("nan")
        percent = (self.done_files / self.total_files * 100) if self.total_files else 0

        line = (
            f"\r[{self.start_label}] elapsed:{self._format_time(elapsed)} "
            f"ETA:{self._format_time(eta)} {percent:5.1f}% "
            f"HIT:{self.hits} MISSING:{self.missing} "
            f"phase:{self.phase} files:{self.done_files}/{self.total_files}"
        )
        print(line, end="", flush=True)

    def finalize(self) -> None:
        self.update(force=True)
        print()


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def iter_csv_rows(path: Path, encodings: Sequence[str]) -> Iterator[list[str]]:
    for enc in encodings:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    yield row
            return
        except UnicodeDecodeError:
            continue


def normalize_trip_no(value: str) -> int | None:
    s = (value or "").strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return int(s)
        except Exception:
            return None
    return None


def parse_float(value: str) -> float | None:
    try:
        return float((value or "").strip())
    except Exception:
        return None


def get_weekday_jp(date_text: str) -> str | None:
    s = (date_text or "").strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y%m%d")
    except Exception:
        return None
    return "月火水木金土日"[dt.weekday()]


# ---------------------------------------------------------------------------
# Polygon handling
# ---------------------------------------------------------------------------


def point_in_polygon(lon: float, lat: float, points: Sequence[tuple[float, float]]) -> bool:
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]
        intersect = (yi > lat) != (yj > lat) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-20) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def load_polygons(csv_path: Path) -> list[PolygonZone]:
    polygons: list[PolygonZone] = []
    if not csv_path.exists():
        log(f"[WARN] Polygon CSV not found: {csv_path}")
        return polygons

    for row in iter_csv_rows(csv_path, ENCODINGS):
        if not row:
            continue
        zone_name = (row[0] or "").strip()
        if not zone_name:
            continue
        coords = row[1:]
        if len(coords) < 6:
            continue
        points: list[tuple[float, float]] = []
        for i in range(0, len(coords), 2):
            if i + 1 >= len(coords):
                break
            lon = parse_float(coords[i])
            lat = parse_float(coords[i + 1])
            if lon is None or lat is None:
                continue
            points.append((lon, lat))
        if len(points) < 3:
            continue
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        bbox = (min(lons), min(lats), max(lons), max(lats))
        polygons.append(PolygonZone(zone_name, points, bbox))

    log(f"Loaded polygons: {len(polygons)} zones")
    return polygons


def directional_zone(lon: float, lat: float) -> str:
    dx = lon - TSUYAMA_CENTER_LON
    dy = lat - TSUYAMA_CENTER_LAT
    if abs(dx) >= abs(dy):
        return EAST_ZONE if dx > 0 else WEST_ZONE
    return NORTH_ZONE if dy > 0 else SOUTH_ZONE


def assign_zone(lon: float | None, lat: float | None, polygons: Sequence[PolygonZone]) -> str:
    if lon is None or lat is None:
        return "MISSING"
    for poly in polygons:
        min_lon, min_lat, max_lon, max_lat = poly.bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if point_in_polygon(lon, lat, poly.points):
            return poly.name
    return directional_zone(lon, lat)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def list_csv_files(directory: Path) -> list[Path]:
    if not directory.exists():
        log(f"[WARN] Directory not found: {directory}")
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".csv")


def collect_trip_counts(
    datasets: Sequence[Mapping[str, Path]],
    split_files: Mapping[str, list[Path]],
    progress: ProgressTracker,
) -> tuple[dict[tuple[str, int], int], int]:
    counts: dict[tuple[str, int], int] = {}
    invalid_date_count = 0
    for ds in datasets:
        name = ds["name"]
        files = split_files.get(name, [])
        for csv_path in files:
            for row in iter_csv_rows(csv_path, ENCODINGS):
                if len(row) <= 8:
                    continue
                op_date = (row[2] or "").strip()
                weekday = get_weekday_jp(op_date)
                if weekday is None:
                    invalid_date_count += 1
                    continue
                if weekday not in TARGET_WEEKDAYS:
                    continue

                opid = (row[3] or "").strip()
                trip_no = normalize_trip_no(row[8])
                if not opid or trip_no is None:
                    continue
                key = (opid, trip_no)
                counts[key] = counts.get(key, 0) + 1
            progress.update(phase="split収集", increment_done=1)
    log(f"Collected trip keys: {len(counts)} unique pairs")
    return counts, invalid_date_count


def load_od_lookup(
    datasets: Sequence[Mapping[str, Path]],
    style_files: Mapping[str, list[Path]],
    progress: ProgressTracker,
) -> dict[tuple[str, int], tuple[float, float, float, float]]:
    lookup: dict[tuple[str, int], tuple[float, float, float, float]] = {}
    for ds in datasets:
        name = ds["name"]
        files = style_files.get(name, [])
        for csv_path in files:
            for row in iter_csv_rows(csv_path, ENCODINGS):
                if len(row) <= 14:
                    continue
                opid = (row[1] or "").strip()
                trip_no = normalize_trip_no(row[7])
                if not opid or trip_no is None:
                    continue
                o_lon = parse_float(row[11])
                o_lat = parse_float(row[12])
                d_lon = parse_float(row[13])
                d_lat = parse_float(row[14])
                if None in (o_lon, o_lat, d_lon, d_lat):
                    continue
                lookup[(opid, trip_no)] = (o_lon, o_lat, d_lon, d_lat)
            progress.update(phase="style1-3検索", increment_done=1)
    log(f"Loaded STYLE1-3 records: {len(lookup)} keys")
    return lookup


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_od_outputs(
    trip_counts: Mapping[tuple[str, int], int],
    od_lookup: Mapping[tuple[str, int], tuple[float, float, float, float]],
    polygons: Sequence[PolygonZone],
    output_dir: Path,
    progress: ProgressTracker,
) -> None:
    ensure_output_dir(output_dir)
    od_long_path = output_dir / "od_long.csv"
    od_matrix_path = output_dir / "od_matrix.csv"
    prod_attr_path = output_dir / "zone_production_attraction.csv"
    zone_master_path = output_dir / "zone_master.csv"

    matrix: dict[str, dict[str, float]] = {}
    col_sums: dict[str, float] = {}
    zones_set: set[str] = set()

    unique_total = len(trip_counts)
    found_keys = 0
    missing_keys = 0

    with od_long_path.open("w", encoding="utf-8-sig", newline="") as f_long:
        writer = csv.writer(f_long)
        writer.writerow(["opid", "trip_no", "zone_o", "zone_d", "o_lon", "o_lat", "d_lon", "d_lat", "weight"])

        for opid, trip_no in sorted(trip_counts.keys()):
            occurrences = trip_counts[(opid, trip_no)]
            weight = 1 if COUNT_UNIQUE_TRIP else occurrences

            coords = od_lookup.get((opid, trip_no))
            if coords is None:
                missing_keys += 1
                zone_o = zone_d = "MISSING"
                o_lon = o_lat = d_lon = d_lat = ""
            else:
                found_keys += 1
                o_lon, o_lat, d_lon, d_lat = coords
                zone_o = assign_zone(o_lon, o_lat, polygons)
                zone_d = assign_zone(d_lon, d_lat, polygons)

            zones_set.update([zone_o, zone_d])
            matrix.setdefault(zone_o, {})[zone_d] = matrix.get(zone_o, {}).get(zone_d, 0) + weight
            col_sums[zone_d] = col_sums.get(zone_d, 0) + weight
            progress.update(phase="style1-3検索", hits=found_keys, missing=missing_keys)

            writer.writerow([
                opid,
                trip_no,
                zone_o,
                zone_d,
                o_lon,
                o_lat,
                d_lon,
                d_lat,
                weight,
            ])

    zones = sorted(zones_set)
    if "MISSING" in zones:
        zones.remove("MISSING")
        zones.append("MISSING")

    zone_master: list[tuple[int, str]] = []
    for idx, name in enumerate(zones, start=1):
        zone_master.append((idx, name))
    zone_to_id = {name: idx for idx, name in zone_master}

    with zone_master_path.open("w", encoding="utf-8-sig", newline="") as f_master:
        writer = csv.writer(f_master)
        writer.writerow(["zone_id", "zone_name"])
        for idx, name in zone_master:
            writer.writerow([f"{idx:03d}", name])

    with od_matrix_path.open("w", encoding="utf-8-sig", newline="") as f_mat:
        writer = csv.writer(f_mat)
        header = ["zone_o_id:zone_o_name \\ zone_d_id:zone_d_name"]
        header.extend(f"{zone_to_id[z]:03d}:{z}" for z in zones)
        writer.writerow(header)
        for zo in zones:
            row_counts = [matrix.get(zo, {}).get(zd, 0) for zd in zones]
            writer.writerow([f"{zone_to_id[zo]:03d}:{zo}", *row_counts])

    with prod_attr_path.open("w", encoding="utf-8-sig", newline="") as f_pa:
        writer = csv.writer(f_pa)
        writer.writerow(["zone", "production", "attraction"])
        for z in zones:
            production = sum(matrix.get(z, {}).values())
            attraction = col_sums.get(z, 0)
            writer.writerow([z, production, attraction])

    log(f"Unique (opid, trip_no): {unique_total}")
    log(f"STYLE1-3 found: {found_keys} / missing: {missing_keys}")
    log("Outputs written:")
    log(f"  - {od_long_path}")
    log(f"  - {od_matrix_path}")
    log(f"  - {prod_attr_path}")
    log(f"  - {zone_master_path}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    split_files = {ds["name"]: list_csv_files(ds["split_dir"]) for ds in DATASETS}
    style_files = {ds["name"]: list_csv_files(ds["style13_dir"]) for ds in DATASETS}

    total_split_files = sum(len(v) for v in split_files.values())
    total_style_files = sum(len(v) for v in style_files.values())
    total_files = total_split_files + total_style_files + 1  # +1 for output phase

    log(f"Datasets: {len(DATASETS)}")
    log(f"Split CSV files: {total_split_files}")
    log(f"STYLE1-3 CSV files: {total_style_files}")

    progress = ProgressTracker(total_files)
    polygons = load_polygons(POLYGON_CSV)

    trip_counts, invalid_date_count = collect_trip_counts(DATASETS, split_files, progress)
    if invalid_date_count:
        log(f"[WARN] Invalid or unparsable dates in split files: {invalid_date_count} rows")

    od_lookup = load_od_lookup(DATASETS, style_files, progress)
    progress.update(phase="出力", increment_done=1, force=True)

    build_od_outputs(trip_counts, od_lookup, polygons, OUTPUT_DIR, progress)
    progress.finalize()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"[ERROR] {exc}")
        sys.exit(1)
