"""Route-based second screening for ETC trip CSV files.

This script scans first-screening trip CSV files and saves each trip once when
it passes near at least three sampled points of any route file.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Sequence, Tuple

FOLDER_ROUTE = "10_ルート(Route)データ"
FOLDER_OUT = "20_第２スクリーニング(ルート)"

DEFAULT_RADIUS_M = 30.0
MIN_ROUTE_POINTS = 3
PROGRESS_EMIT_SEC = 0.8

LON_INDEX = 14
LAT_INDEX = 15
FLAG_INDEX = 12
DATE_INDEX = 6
OP_DATE_INDEX = 2
OP_ID_INDEX = 3
VEHICLE_TYPE_INDEX = 4
VEHICLE_USE_INDEX = 5
TRIP_NO_INDEX = 8
EARTH_RADIUS_M = 6_371_000.0
TARGET_WEEKDAYS: set[int] = {1, 2, 3, 4, 5, 6, 7}
WEEKDAY_ABBR = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]


@dataclass
class CSVRow:
    values: List[str]


@dataclass(frozen=True)
class RoutePoint:
    route: str
    index: int
    lon: float
    lat: float


@dataclass
class RouteData:
    name: str
    points: list[RoutePoint]


def resolve_project_paths(project_dir: Path) -> tuple[Path, Path]:
    return project_dir / FOLDER_ROUTE, project_dir / FOLDER_OUT


def read_csv_rows(path: Path) -> list[CSVRow]:
    rows: list[CSVRow] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        for row in csv.reader(f):
            rows.append(CSVRow(list(row)))
    return rows


def _read_lon_lat(row: Sequence[str]) -> tuple[float, float] | None:
    if len(row) <= max(LON_INDEX, LAT_INDEX):
        return None
    try:
        lon = float(row[LON_INDEX])
        lat = float(row[LAT_INDEX])
    except (TypeError, ValueError):
        return None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return lon, lat


def load_routes(route_dir: Path) -> list[RouteData]:
    routes: list[RouteData] = []
    for path in sorted(route_dir.glob("*.csv")):
        points: list[RoutePoint] = []
        try:
            with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                for row in csv.reader(f):
                    lon_lat = _read_lon_lat(row)
                    if lon_lat is None:
                        continue
                    lon, lat = lon_lat
                    points.append(RoutePoint(path.stem, len(points), lon, lat))
        except Exception as exc:
            print(f"[WARN] route read failed: {path.name}: {exc}", flush=True)
            continue
        if points:
            routes.append(RouteData(path.stem, points))
            print(f"ROUTE: {path.stem} {len(points)}", flush=True)
    return routes


def iter_csv_files(root: Path, recursive: bool = False) -> Iterator[Path]:
    if recursive:
        yield from (p for p in root.rglob("*.csv") if p.is_file())
    else:
        yield from (p for p in root.glob("*.csv") if p.is_file())


def build_boundaries(rows: Sequence[CSVRow]) -> list[int]:
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


def iter_segments_from_boundaries(boundaries: Sequence[int]) -> Iterator[tuple[int, int]]:
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end - start >= 2:
            yield start, end


def _weekday_from_row(row: CSVRow) -> int | None:
    try:
        if len(row.values) <= DATE_INDEX:
            return None
        token = row.values[DATE_INDEX]
        if not token:
            return None
        dt = datetime.strptime(token[:8], "%Y%m%d")
        py = dt.weekday()
        return 1 if py == 6 else py + 2
    except Exception:
        return None


def _weekday_abbr_from_ymd(ymd: str) -> str | None:
    if len(ymd) != 8 or not ymd.isdigit():
        return None
    try:
        dt = datetime.strptime(ymd, "%Y%m%d")
    except Exception:
        return None
    py = dt.weekday()
    return "SUN" if py == 6 else WEEKDAY_ABBR[py + 1]


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_route_index(routes: Sequence[RouteData], radius_m: float) -> tuple[dict[tuple[int, int], list[RoutePoint]], float]:
    cell_deg = max(radius_m / 111_000.0, 0.000001)
    grid: dict[tuple[int, int], list[RoutePoint]] = {}
    for route in routes:
        for point in route.points:
            key = (math.floor(point.lon / cell_deg), math.floor(point.lat / cell_deg))
            grid.setdefault(key, []).append(point)
    return grid, cell_deg


def trip_matches_routes(
    rows: Sequence[CSVRow],
    start: int,
    end: int,
    route_index: dict[tuple[int, int], list[RoutePoint]],
    cell_deg: float,
    radius_m: float,
    min_route_points: int,
) -> list[str]:
    hits: dict[str, set[int]] = {}
    completed: set[str] = set()

    for row in rows[start:end]:
        if TARGET_WEEKDAYS:
            wd = _weekday_from_row(row)
            if wd is None or wd not in TARGET_WEEKDAYS:
                continue
        lon_lat = _read_lon_lat(row.values)
        if lon_lat is None:
            continue
        lon, lat = lon_lat
        cx = math.floor(lon / cell_deg)
        cy = math.floor(lat / cell_deg)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for point in route_index.get((cx + dx, cy + dy), ()):
                    if point.route in completed:
                        continue
                    if haversine_distance_m(lat, lon, point.lat, point.lon) <= radius_m:
                        route_hits = hits.setdefault(point.route, set())
                        route_hits.add(point.index)
                        if len(route_hits) >= min_route_points:
                            completed.add(point.route)
    return sorted(completed)


def _safe_name(text: str, max_len: int = 80) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" ._")
    return (safe or "route")[:max_len]


def save_trip(rows: Sequence[CSVRow], start: int, end: int, out_dir: Path, hit_routes: Sequence[str], seq_no: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_slice = rows[start:end]

    op_dates: set[str] = set()
    primary_date: str | None = None
    for row in rows_slice:
        if len(row.values) <= OP_DATE_INDEX:
            continue
        ymd = row.values[OP_DATE_INDEX].strip()[:8]
        if len(ymd) == 8 and ymd.isdigit():
            op_dates.add(ymd)
            primary_date = primary_date or ymd
    weekday_order = [abbr for abbr in WEEKDAY_ABBR if abbr in {_weekday_abbr_from_ymd(d) for d in op_dates}]
    weekday_part = "-".join(weekday_order) if weekday_order else "UNK"

    def first_token(index: int, default: str) -> str:
        for row in rows_slice:
            if len(row.values) > index and row.values[index].strip():
                return row.values[index].strip()
        return default

    opid12 = first_token(OP_ID_INDEX, "000000000000").zfill(12)
    primary_date = primary_date or "00000000"
    try:
        trip_tag = f"t{int(float(first_token(TRIP_NO_INDEX, '0'))):03d}"
    except ValueError:
        trip_tag = "t000"
    etype_tag = f"E{first_token(VEHICLE_TYPE_INDEX, '00').zfill(2)}"
    fuse_tag = f"F{first_token(VEHICLE_USE_INDEX, '00').zfill(2)}"
    route_part = _safe_name(hit_routes[0] if hit_routes else "route")
    if len(hit_routes) > 1:
        route_part = f"{route_part}_plus{len(hit_routes) - 1}"

    filename = f"2nd_route_{seq_no:06d}_{route_part}_{weekday_part}_ID{opid12}_{primary_date}_{trip_tag}_{etype_tag}_{fuse_tag}.csv"
    out_path = out_dir / filename
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for row in rows_slice:
            writer.writerow(row.values)
    return out_path


def format_hms(seconds: float) -> str:
    total = int(round(max(0.0, seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def process_file(
    path: Path,
    route_index: dict[tuple[int, int], list[RoutePoint]],
    cell_deg: float,
    output_dir: Path,
    radius_m: float,
    min_route_points: int,
    hits_per_route: Dict[str, int],
    save_seq: list[int],
    dry_run: bool,
) -> tuple[int, int]:
    try:
        rows = read_csv_rows(path)
    except Exception as exc:
        print(f"[WARN] read failed: {path.name}: {exc}", flush=True)
        return 0, 0
    boundaries = build_boundaries(rows)
    segments = list(iter_segments_from_boundaries(boundaries))
    matched = 0
    for start, end in segments:
        hit_routes = trip_matches_routes(rows, start, end, route_index, cell_deg, radius_m, min_route_points)
        if not hit_routes:
            continue
        matched += 1
        for name in hit_routes:
            hits_per_route[name] = hits_per_route.get(name, 0) + 1
            print(f"HIT: {name} {hits_per_route[name]}", flush=True)
        if not dry_run:
            save_seq[0] += 1
            save_trip(rows, start, end, output_dir, hit_routes, save_seq[0])
    return len(segments), matched


def run_second_screening(
    input_dir: Path,
    route_dir: Path,
    output_dir: Path,
    radius_m: float,
    min_route_points: int,
    recursive: bool,
    dry_run: bool,
) -> int:
    print(f"[INFO] Input : {input_dir}", flush=True)
    print(f"[INFO] Routes: {route_dir}", flush=True)
    print(f"[INFO] Output: {output_dir}", flush=True)
    print(f"[INFO] radius_m={radius_m} min_route_points={min_route_points}", flush=True)
    if not input_dir.is_dir():
        print(f"[ERROR] input dir not found: {input_dir}", flush=True)
        return 1
    if not route_dir.is_dir():
        print(f"[ERROR] route dir not found: {route_dir}", flush=True)
        return 1

    routes = load_routes(route_dir)
    if not routes:
        print(f"[ERROR] no valid route csv in: {route_dir}", flush=True)
        return 1
    route_index, cell_deg = build_route_index(routes, radius_m)
    hits_per_route = {route.name: 0 for route in routes}

    total_files = 0
    total_candidate = 0
    total_matched = 0
    save_seq = [0]
    started = time.time()
    last_progress_emit = time.monotonic()

    for trip_path in iter_csv_files(input_dir, recursive):
        total_files += 1
        cand, matched = process_file(
            trip_path,
            route_index,
            cell_deg,
            output_dir,
            radius_m,
            min_route_points,
            hits_per_route,
            save_seq,
            dry_run,
        )
        total_candidate += cand
        total_matched += matched
        now = time.monotonic()
        if now - last_progress_emit >= PROGRESS_EMIT_SEC:
            print(f"進捗ファイル: {total_files} files processed", flush=True)
            last_progress_emit = now

    print(f"進捗ファイル: {total_files} files processed", flush=True)
    for name in sorted(hits_per_route):
        print(f"HIT: {name} {hits_per_route[name]}", flush=True)
    print(f"TOTAL 所要時間 : {format_hms(time.time() - started)}", flush=True)
    print(f"TOTAL 候補セグメント数 : {total_candidate}", flush=True)
    print(f"TOTAL HITトリップ数 : {total_matched}", flush=True)
    print(f"TOTAL 保存ファイル数 : {save_seq[0]}", flush=True)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Route 第2スクリーニング（プロジェクト駆動）")
    parser.add_argument("--project", required=True, help="project001 のようなプロジェクトフォルダ")
    parser.add_argument("--input", required=True, help="第1スクリーニングデータフォルダ（CSV群）")
    parser.add_argument("--radius-m", type=float, default=DEFAULT_RADIUS_M, help="ルート点からの判定半径[m]")
    parser.add_argument("--min-route-points", type=int, default=MIN_ROUTE_POINTS, help="HITに必要な同一ルート上の点数")
    parser.add_argument("--recursive", action="store_true", help="入力フォルダ配下のサブフォルダも探索する")
    parser.add_argument("--dry-run", action="store_true", help="保存せずに判定だけ行う")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_dir = Path(args.project).resolve()
    input_dir = Path(args.input).resolve()
    if not project_dir.is_dir():
        print(f"[ERROR] project not found: {project_dir}", flush=True)
        return 1
    route_dir, output_dir = resolve_project_paths(project_dir)
    return run_second_screening(
        input_dir=input_dir,
        route_dir=route_dir,
        output_dir=output_dir,
        radius_m=args.radius_m,
        min_route_points=args.min_route_points,
        recursive=args.recursive,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
