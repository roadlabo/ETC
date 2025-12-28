"""Extract O/D coordinates, assign zones, and build an OD matrix.

This script ties together three inputs:

- First-screening split CSV files created by ``01_split_by_opid_streaming.py``
  (multiple trip numbers per file)
- 様式1-3 CSV files that contain O/D coordinates keyed by (運行ID1, トリップ番号)
- Zone polygon CSV exported from ``12_polygon_builder.html``

It produces three outputs under ``OUTPUT_DIR``:

- ``od_long.csv``: per (opid, trip_no) record with zones and coordinates
- ``od_matrix.csv``: zone-to-zone matrix (rows=zone_o, cols=zone_d)
- ``zone_production_attraction.csv``: production (row sum) and attraction
  (column sum) per zone

Zone assignment is performed with a lightweight ray-casting point-in-polygon
implementation (no external GIS dependencies). Points outside any polygon are
labeled ``OUT``. When no 様式1-3 entry exists, the zone is ``MISSING`` and the
record is preserved in the outputs.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence


# ---------------------------------------------------------------------------
# Configuration (edit before running)
# ---------------------------------------------------------------------------

# 第1スクリーニング分割ファイル群フォルダ（01_split_by_opid_streaming.py 出力先）
SPLIT_DIR = Path(r"D:\path\to\01_split_output")

# 様式1-3（CSV）フォルダ
STYLE13_DIR = Path(r"D:\path\to\style1-3_csvs")

# ゾーンポリゴンCSV（12_polygon_builder.html 出力）
POLYGON_CSV = Path(r"D:\path\to\zones.csv")

# 出力先フォルダ
OUTPUT_DIR = Path(r"D:\path\to\od_output")

# (opid, trip_no) を一意にカウントするかどうか
COUNT_UNIQUE_TRIP = True

# 入力で試行するエンコーディング（順に試す）
ENCODINGS = ("utf-8-sig", "utf-8", "cp932")


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


def assign_zone(lon: float | None, lat: float | None, polygons: Sequence[PolygonZone]) -> str:
    if lon is None or lat is None:
        return "MISSING"
    for poly in polygons:
        min_lon, min_lat, max_lon, max_lat = poly.bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if point_in_polygon(lon, lat, poly.points):
            return poly.name
    return "OUT"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def collect_trip_counts(directory: Path) -> dict[tuple[str, int], int]:
    counts: dict[tuple[str, int], int] = {}
    if not directory.exists():
        log(f"[WARN] Split directory not found: {directory}")
        return counts

    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
    for csv_path in files:
        for row in iter_csv_rows(csv_path, ENCODINGS):
            if len(row) <= 8:
                continue
            opid = (row[3] or "").strip()
            trip_no = normalize_trip_no(row[8])
            if not opid or trip_no is None:
                continue
            key = (opid, trip_no)
            counts[key] = counts.get(key, 0) + 1
    log(f"Collected trip keys: {len(counts)} unique pairs")
    return counts


def load_od_lookup(directory: Path) -> dict[tuple[str, int], tuple[float, float, float, float]]:
    lookup: dict[tuple[str, int], tuple[float, float, float, float]] = {}
    if not directory.exists():
        log(f"[WARN] STYLE1-3 directory not found: {directory}")
        return lookup

    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
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
) -> None:
    ensure_output_dir(output_dir)
    od_long_path = output_dir / "od_long.csv"
    od_matrix_path = output_dir / "od_matrix.csv"
    prod_attr_path = output_dir / "zone_production_attraction.csv"

    matrix: dict[str, dict[str, float]] = {}
    col_sums: dict[str, float] = {}
    zones_set: set[str] = set()

    unique_total = len(trip_counts)
    found_keys = 0
    missing_keys = 0
    out_origin = 0
    out_dest = 0

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
                if zone_o == "OUT":
                    out_origin += 1
                if zone_d == "OUT":
                    out_dest += 1

            zones_set.update([zone_o, zone_d])
            matrix.setdefault(zone_o, {})[zone_d] = matrix.get(zone_o, {}).get(zone_d, 0) + weight
            col_sums[zone_d] = col_sums.get(zone_d, 0) + weight

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
    # Keep OUT and MISSING at the end for readability.
    for special in ["MISSING", "OUT"]:
        if special in zones:
            zones.remove(special)
            zones.append(special)

    with od_matrix_path.open("w", encoding="utf-8-sig", newline="") as f_mat:
        writer = csv.writer(f_mat)
        writer.writerow(["zone_o/zone_d", *zones])
        for zo in zones:
            row_counts = [matrix.get(zo, {}).get(zd, 0) for zd in zones]
            writer.writerow([zo, *row_counts])

    with prod_attr_path.open("w", encoding="utf-8-sig", newline="") as f_pa:
        writer = csv.writer(f_pa)
        writer.writerow(["zone", "production", "attraction"])
        for z in zones:
            production = sum(matrix.get(z, {}).values())
            attraction = col_sums.get(z, 0)
            writer.writerow([z, production, attraction])

    log(f"Unique (opid, trip_no): {unique_total}")
    log(f"STYLE1-3 found: {found_keys} / missing: {missing_keys}")
    log(f"OUT count (origin): {out_origin}, (dest): {out_dest}")
    log("Outputs written:")
    log(f"  - {od_long_path}")
    log(f"  - {od_matrix_path}")
    log(f"  - {prod_attr_path}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    polygons = load_polygons(POLYGON_CSV)
    trip_counts = collect_trip_counts(SPLIT_DIR)
    od_lookup = load_od_lookup(STYLE13_DIR)
    build_od_outputs(trip_counts, od_lookup, polygons, OUTPUT_DIR)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"[ERROR] {exc}")
        sys.exit(1)
