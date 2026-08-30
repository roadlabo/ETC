"""Area 1.5 screening for ETC2.0 first-screening trip CSV files.

This step only clips trips to the analysis area and writes one CSV per
area subtrip so existing second-screening and performance tools can run after it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Sequence

FOLDER_OUT = "15_エリア第1.5スクリーニング"

LON_INDEX = 14
LAT_INDEX = 15
FLAG_INDEX = 12
DATE_INDEX = 6
OP_ID_INDEX = 3
TRIP_NO_INDEX = 8
EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class Point:
    lon: float
    lat: float


@dataclass
class TripPoint:
    values: list[str]
    lon: float
    lat: float
    timestamp: datetime | None
    timestamp_text: str
    synthetic: bool = False
    boundary_type: str = ""
    source_index: int = 0


@dataclass
class AreaDefinition:
    official_polygons: list[list[Point]]
    analysis_polygons: list[list[Point]]


@dataclass
class ScreeningConfig:
    input_path: Path
    area_geojson: Path
    output_dir: Path
    encoding: str = "utf-8-sig"
    recursive: bool = False
    boundary_tolerance_m: float = 0.0
    min_subtrip_distance_m: float = 10.0
    min_subtrip_duration_sec: float = 5.0
    merge_gap_sec: float = 10.0
    max_check_geojson_features: int = 5000


@dataclass
class ScreeningStats:
    files: int = 0
    original_trips: int = 0
    subtrips: int = 0
    excluded: int = 0
    multi_entry_trips: int = 0
    warnings: int = 0


ProgressCB = Callable[[str, int, int, dict], None] | None


def parse_timestamp(text: str) -> datetime | None:
    raw = (text or "").strip()
    if not raw:
        return None
    for fmt, n in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12), ("%Y/%m/%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M:%S", 19)):
        try:
            return datetime.strptime(raw[:n], fmt)
        except ValueError:
            pass
    return None


def format_timestamp(dt: datetime | None, fallback: str = "") -> str:
    return dt.strftime("%Y%m%d%H%M%S") if dt is not None else fallback


def _xy(point: Point, lon0: float, lat0: float) -> tuple[float, float]:
    k = (math.pi / 180.0) * EARTH_RADIUS_M
    return ((point.lon - lon0) * math.cos(math.radians(lat0)) * k, (point.lat - lat0) * k)


def distance_m(a: Point, b: Point) -> float:
    ax, ay = _xy(a, a.lon, a.lat)
    bx, by = _xy(b, a.lon, a.lat)
    return math.hypot(bx - ax, by - ay)


def polyline_distance_m(points: Sequence[TripPoint | Point]) -> float:
    return sum(distance_m(Point(a.lon, a.lat), Point(b.lon, b.lat)) for a, b in zip(points[:-1], points[1:]))


def point_segment_distance_m(p: Point, a: Point, b: Point) -> float:
    px, py = _xy(p, p.lon, p.lat)
    ax, ay = _xy(a, p.lon, p.lat)
    bx, by = _xy(b, p.lon, p.lat)
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_in_polygon(p: Point, polygon: Sequence[Point]) -> bool:
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        pi = polygon[i]
        pj = polygon[j]
        if point_segment_distance_m(p, pj, pi) <= 0.001:
            return True
        if ((pi.lat > p.lat) != (pj.lat > p.lat)) and (
            p.lon < (pj.lon - pi.lon) * (p.lat - pi.lat) / ((pj.lat - pi.lat) or 1e-30) + pi.lon
        ):
            inside = not inside
        j = i
    return inside


def point_in_any_polygon(p: Point, polygons: Sequence[Sequence[Point]]) -> bool:
    return any(point_in_polygon(p, poly) for poly in polygons)


def point_on_polygon_boundary(p: Point, polygon: Sequence[Point], tolerance_m: float = 0.01) -> bool:
    return any(point_segment_distance_m(p, a, b) <= tolerance_m for a, b in zip(polygon, polygon[1:] + polygon[:1]))  # type: ignore[operator]


def point_on_any_boundary(p: Point, polygons: Sequence[Sequence[Point]], tolerance_m: float = 0.01) -> bool:
    return any(point_on_polygon_boundary(p, poly, tolerance_m) for poly in polygons)


def polygon_valid(polygon: Sequence[Point]) -> tuple[bool, str]:
    if len(polygon) < 3:
        return False, "3点未満のポリゴンです"
    edges = list(zip(polygon, polygon[1:] + polygon[:1]))  # type: ignore[operator]
    for i, (a1, a2) in enumerate(edges):
        for j, (b1, b2) in enumerate(edges):
            if abs(i - j) <= 1 or {i, j} == {0, len(edges) - 1}:
                continue
            if segment_intersection_t(a1, a2, b1, b2) is not None:
                return False, "自己交差しているポリゴンです"
    return True, ""


def segment_intersection_t(a: Point, b: Point, c: Point, d: Point) -> float | None:
    rx = b.lon - a.lon
    ry = b.lat - a.lat
    sx = d.lon - c.lon
    sy = d.lat - c.lat
    denom = rx * sy - ry * sx
    qpx = c.lon - a.lon
    qpy = c.lat - a.lat
    if abs(denom) < 1e-15:
        return None
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if -1e-10 <= t <= 1 + 1e-10 and -1e-10 <= u <= 1 + 1e-10:
        return max(0.0, min(1.0, t))
    return None


def segment_polygon_crossings(a: Point, b: Point, polygon: Sequence[Point]) -> list[float]:
    values: list[float] = []
    for c, d in zip(polygon, polygon[1:] + polygon[:1]):  # type: ignore[operator]
        t = segment_intersection_t(a, b, c, d)
        if t is not None and all(abs(t - old) > 1e-8 for old in values):
            values.append(t)
    return sorted(values)


def interp_point(a: TripPoint, b: TripPoint, t: float, base_values: list[str] | None = None) -> TripPoint:
    lon = a.lon + (b.lon - a.lon) * t
    lat = a.lat + (b.lat - a.lat) * t
    values = list(base_values if base_values is not None else a.values)
    if len(values) <= max(LON_INDEX, LAT_INDEX, DATE_INDEX):
        values.extend([""] * (max(LON_INDEX, LAT_INDEX, DATE_INDEX) + 1 - len(values)))
    values[LON_INDEX] = f"{lon:.9f}"
    values[LAT_INDEX] = f"{lat:.9f}"
    dt: datetime | None = None
    if a.timestamp and b.timestamp:
        dt = a.timestamp + (b.timestamp - a.timestamp) * t
        values[DATE_INDEX] = format_timestamp(dt)
    return TripPoint(values, lon, lat, dt, values[DATE_INDEX], synthetic=True)


def read_trip_rows(path: Path, encoding: str = "utf-8-sig") -> list[list[str]]:
    with path.open("r", encoding=encoding, errors="ignore", newline="") as fh:
        return [list(row) for row in csv.reader(fh) if row]


def row_to_point(row: list[str], idx: int) -> TripPoint | None:
    if len(row) <= max(LON_INDEX, LAT_INDEX):
        return None
    try:
        lon = float(row[LON_INDEX])
        lat = float(row[LAT_INDEX])
    except (TypeError, ValueError):
        return None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    text = row[DATE_INDEX] if len(row) > DATE_INDEX else ""
    return TripPoint(list(row), lon, lat, parse_timestamp(text), text, source_index=idx)


def build_boundaries(rows: Sequence[list[str]]) -> list[int]:
    boundaries: set[int] = {0, len(rows)}
    prev_trip_no: int | None = None
    for idx, row in enumerate(rows):
        if len(row) > FLAG_INDEX:
            if row[FLAG_INDEX] == "0":
                boundaries.add(idx)
            elif row[FLAG_INDEX] == "1":
                boundaries.add(idx + 1)
        trip_no_val: int | None = None
        if len(row) > TRIP_NO_INDEX and row[TRIP_NO_INDEX].strip():
            try:
                trip_no_val = int(float(row[TRIP_NO_INDEX]))
            except ValueError:
                trip_no_val = None
        if trip_no_val is not None:
            if prev_trip_no is None:
                prev_trip_no = trip_no_val
            elif trip_no_val != prev_trip_no:
                boundaries.add(idx)
                prev_trip_no = trip_no_val
    return sorted(boundaries)


def iter_trip_slices(rows: Sequence[list[str]]) -> Iterator[tuple[int, int]]:
    bounds = build_boundaries(rows)
    for start, end in zip(bounds[:-1], bounds[1:]):
        if end - start >= 2:
            yield start, end


def original_trip_id(rows: Sequence[list[str]], fallback: str) -> str:
    opid = ""
    trip_no = ""
    for row in rows:
        if not opid and len(row) > OP_ID_INDEX:
            opid = row[OP_ID_INDEX].strip()
        if not trip_no and len(row) > TRIP_NO_INDEX:
            trip_no = row[TRIP_NO_INDEX].strip()
    return f"{opid or fallback}-t{trip_no or '000'}"


def _polygon_from_ring(ring: Sequence[Sequence[float]], role: str, name: str) -> list[Point]:
    poly = [Point(float(x), float(y)) for x, y, *_ in ring]
    if poly and poly[0] == poly[-1]:
        poly = poly[:-1]
    ok, reason = polygon_valid(poly)
    if not ok:
        raise ValueError(f"不正なポリゴン: {name or role}: {reason}")
    return poly


def load_area_definition(path: Path) -> AreaDefinition:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    official: list[list[Point]] = []
    analysis: list[list[Point]] = []

    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        role = (props.get("area15_role") or props.get("role") or props.get("type") or "").lower()
        geom = feature.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if role not in {"official_area", "analysis_area"}:
            continue
        target = official if role == "official_area" else analysis
        if gtype == "Polygon":
            target.append(_polygon_from_ring(coords[0] if coords else [], role, str(props.get("name") or "")))
        elif gtype == "MultiPolygon":
            for polygon_coords in coords:
                target.append(_polygon_from_ring(polygon_coords[0] if polygon_coords else [], role, str(props.get("name") or "")))

    if not official:
        raise ValueError("GeoJSONに official_area ポリゴンがありません")
    if not analysis:
        raise ValueError("GeoJSONに analysis_area ポリゴンがありません")
    return AreaDefinition(official, analysis)


def extract_subtrips(points: Sequence[TripPoint], area: AreaDefinition, config: ScreeningConfig, original_id: str) -> tuple[list[list[TripPoint]], list[str]]:
    notes: list[str] = []
    intervals: list[list[TripPoint]] = []
    current: list[TripPoint] = []
    last_inside = point_in_any_polygon(Point(points[0].lon, points[0].lat), area.analysis_polygons)

    for idx, (a, b) in enumerate(zip(points[:-1], points[1:])):
        pa = Point(a.lon, a.lat)
        pb = Point(b.lon, b.lat)
        crossings: list[float] = []
        for poly in area.analysis_polygons:
            crossings.extend(segment_polygon_crossings(pa, pb, poly))
        ts = sorted({0.0, 1.0, *crossings})
        if len(ts) == 2 and not current and last_inside:
            current.append(a)
        for t0, t1 in zip(ts[:-1], ts[1:]):
            if abs(t1 - t0) < 1e-10:
                continue
            mid_t = (t0 + t1) / 2.0
            mid = Point(pa.lon + (pb.lon - pa.lon) * mid_t, pa.lat + (pb.lat - pa.lat) * mid_t)
            inside = point_in_any_polygon(mid, area.analysis_polygons) and not point_on_any_boundary(mid, area.analysis_polygons, config.boundary_tolerance_m)
            p0 = a if t0 == 0.0 else interp_point(a, b, t0)
            p1 = b if t1 == 1.0 else interp_point(a, b, t1)
            if inside:
                if not current:
                    if p0.synthetic:
                        p0.boundary_type = "entry"
                    current.append(p0)
                elif distance_m(Point(current[-1].lon, current[-1].lat), Point(p0.lon, p0.lat)) > 0.01:
                    current.append(p0)
                current.append(p1)
            elif current:
                endp = current[-1]
                if not endp.synthetic and t0 > 0.0:
                    endp = p0
                    current.append(endp)
                if endp.synthetic:
                    endp.boundary_type = "exit"
                intervals.append(current)
                current = []
        last_inside = point_in_any_polygon(pb, area.analysis_polygons)
        if idx == len(points) - 2 and current:
            intervals.append(current)

    cleaned: list[list[TripPoint]] = []
    for sub in intervals:
        if len(sub) < 2:
            continue
        if sub[0].synthetic and sub[-1].synthetic and distance_m(Point(sub[0].lon, sub[0].lat), Point(sub[-1].lon, sub[-1].lat)) <= max(0.01, config.boundary_tolerance_m):
            notes.append("接線または境界付近のみのサブトリップを除外")
            continue
        dist = polyline_distance_m(sub)
        duration = (sub[-1].timestamp - sub[0].timestamp).total_seconds() if sub[0].timestamp and sub[-1].timestamp else 0.0
        if dist < config.min_subtrip_distance_m:
            notes.append(f"短距離サブトリップ除外({dist:.1f}m)")
            continue
        if duration and duration < config.min_subtrip_duration_sec:
            notes.append(f"短時間サブトリップ除外({duration:.1f}s)")
            continue
        cleaned.append(sub)

    merged: list[list[TripPoint]] = []
    for sub in cleaned:
        if (
            merged
            and merged[-1][-1].timestamp
            and sub[0].timestamp
            and (sub[0].timestamp - merged[-1][-1].timestamp).total_seconds() <= config.merge_gap_sec
        ):
            merged[-1].extend(sub)
            notes.append(f"短時間再流入を統合: {original_id}")
        else:
            merged.append(sub)
    return merged, notes


def safe_filename(text: str, max_len: int = 96) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text)).strip(" ._")
    return (safe or "subtrip")[:max_len]


def write_subtrip_csv(out_dir: Path, seq_no: int, subtrip_id: str, points: Sequence[TripPoint]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"area15_{seq_no:06d}_{safe_filename(subtrip_id)}.csv"
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        for idx, point in enumerate(points):
            base = list(point.values[:16]) + [""] * max(0, 16 - len(point.values))
            if len(base) <= FLAG_INDEX:
                base.extend([""] * (FLAG_INDEX + 1 - len(base)))
            base[FLAG_INDEX] = "0" if idx == 0 else "1" if idx == len(points) - 1 else ""
            writer.writerow(base[:16])
    return out_path


def iter_csv_files(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.csv") if recursive else path.glob("*.csv"))


def run_screening(config: ScreeningConfig, progress_cb: ProgressCB = None, cancel_flag=None) -> dict:
    started = time.time()
    area = load_area_definition(config.area_geojson)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    files = iter_csv_files(config.input_path, config.recursive)
    stats = ScreeningStats(files=len(files))
    settings = {
        "input_path": str(config.input_path),
        "area_geojson": str(config.area_geojson),
        "boundary_tolerance_m": config.boundary_tolerance_m,
        "min_subtrip_distance_m": config.min_subtrip_distance_m,
        "min_subtrip_duration_sec": config.min_subtrip_duration_sec,
        "merge_gap_sec": config.merge_gap_sec,
        "max_check_geojson_features": config.max_check_geojson_features,
        "note": "第1.5スクリーニングは分析区域内サブトリップCSVの切り出しのみを行います。",
    }
    (config.output_dir / "15_area_screening_settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_header = ["source_file", "original_trip_id", "subtrip_id", "subtrip_csv", "start_time", "end_time", "duration_sec", "distance_m", "point_count", "note"]
    subtrip_csv_dir = config.output_dir / "15_area_subtrip_csv"
    summary_path = config.output_dir / "15_subtrip_summary.csv"
    excluded_path = config.output_dir / "15_excluded_trips.csv"
    multi_path = config.output_dir / "15_multi_entry_trips.csv"
    geojson_path = config.output_dir / "15_subtrip_check.geojson"
    check_features: list[dict] = []
    skipped_check_features = 0
    subtrip_file_seq = 0

    with summary_path.open("w", encoding="utf-8-sig", newline="") as sfh, excluded_path.open("w", encoding="utf-8-sig", newline="") as efh, multi_path.open("w", encoding="utf-8-sig", newline="") as mfh:
        summary_writer = csv.writer(sfh)
        excluded_writer = csv.writer(efh)
        multi_writer = csv.writer(mfh)
        summary_writer.writerow(summary_header)
        excluded_writer.writerow(["source_file", "original_trip_id", "reason"])
        multi_writer.writerow(["source_file", "original_trip_id", "subtrip_count"])

        for file_index, csv_path in enumerate(files, start=1):
            if cancel_flag is not None and cancel_flag.is_set():
                break
            if progress_cb:
                progress_cb("READ", file_index - 1, len(files), {"file": csv_path.name, "subtrips": stats.subtrips})
            try:
                rows = read_trip_rows(csv_path, config.encoding)
            except Exception as exc:
                stats.excluded += 1
                excluded_writer.writerow([str(csv_path), csv_path.stem, f"CSV読込失敗: {exc}"])
                continue
            slices = list(iter_trip_slices(rows))
            if not slices and rows:
                stats.excluded += 1
                excluded_writer.writerow([str(csv_path), original_trip_id(rows, csv_path.stem), "点数不足"])
                continue
            for start, end in slices:
                trip_rows = rows[start:end]
                oid = original_trip_id(trip_rows, csv_path.stem)
                stats.original_trips += 1
                pts = [row_to_point(row, i) for i, row in enumerate(trip_rows)]
                if any(p is None for p in pts):
                    stats.excluded += 1
                    excluded_writer.writerow([str(csv_path), oid, "座標欠損または異常座標"])
                    continue
                trip_points = sorted([p for p in pts if p is not None], key=lambda p: p.timestamp or datetime.min)
                if len(trip_points) < 2:
                    stats.excluded += 1
                    excluded_writer.writerow([str(csv_path), oid, "点数不足"])
                    continue
                if any(p.timestamp is None for p in trip_points):
                    stats.excluded += 1
                    excluded_writer.writerow([str(csv_path), oid, "日時欠損または不正日時"])
                    continue
                subtrips, notes = extract_subtrips(trip_points, area, config, oid)
                if not subtrips:
                    stats.excluded += 1
                    excluded_writer.writerow([str(csv_path), oid, ";".join(notes) or "分析区域内サブトリップなし"])
                    continue
                if len(subtrips) >= 2:
                    stats.multi_entry_trips += 1
                    multi_writer.writerow([str(csv_path), oid, len(subtrips)])
                for sub_idx, sub in enumerate(subtrips, start=1):
                    sid = f"{oid}-{sub_idx:02d}"
                    dist = polyline_distance_m(sub)
                    duration = (sub[-1].timestamp - sub[0].timestamp).total_seconds() if sub[0].timestamp and sub[-1].timestamp else 0.0
                    stats.subtrips += 1
                    subtrip_file_seq += 1
                    subtrip_path = write_subtrip_csv(subtrip_csv_dir, subtrip_file_seq, sid, sub)
                    row = {
                        "source_file": str(csv_path),
                        "original_trip_id": oid,
                        "subtrip_id": sid,
                        "subtrip_csv": str(subtrip_path),
                        "start_time": format_timestamp(sub[0].timestamp, sub[0].timestamp_text),
                        "end_time": format_timestamp(sub[-1].timestamp, sub[-1].timestamp_text),
                        "duration_sec": f"{duration:.1f}",
                        "distance_m": f"{dist:.1f}",
                        "point_count": str(len(sub)),
                        "note": ";".join(notes),
                    }
                    summary_writer.writerow([row[h] for h in summary_header])
                    if len(check_features) < config.max_check_geojson_features:
                        check_features.append(
                            {
                                "type": "Feature",
                                "properties": row,
                                "geometry": {"type": "LineString", "coordinates": [[p.lon, p.lat] for p in sub]},
                            }
                        )
                    else:
                        skipped_check_features += 1
            if progress_cb:
                progress_cb("PROCESS", file_index, len(files), {"file": csv_path.name, "subtrips": stats.subtrips, "excluded": stats.excluded})

    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "metadata": {
                    "note": "品質確認用GeoJSONです。大量データでは先頭側の一部のみを出力します。",
                    "max_features": config.max_check_geojson_features,
                    "skipped_features": skipped_check_features,
                },
                "features": check_features,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    elapsed = time.time() - started
    log_lines = [
        "=== 15 エリア第1.5スクリーニング ===",
        f"input: {config.input_path}",
        f"area: {config.area_geojson}",
        f"output: {config.output_dir}",
        "mode: clip_only",
        f"files: {stats.files}",
        f"original_trips: {stats.original_trips}",
        f"subtrips: {stats.subtrips}",
        f"subtrip_csv_dir: {subtrip_csv_dir}",
        f"excluded: {stats.excluded}",
        f"multi_entry_trips: {stats.multi_entry_trips}",
        f"elapsed_sec: {elapsed:.1f}",
    ]
    log_path = config.output_dir / f"15_area_screening_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    if progress_cb:
        progress_cb("DONE", len(files), len(files), {"subtrips": stats.subtrips, "excluded": stats.excluded})
    return {
        "output_dir": str(config.output_dir),
        "subtrip_csv_dir": str(subtrip_csv_dir),
        "summary_csv": str(summary_path),
        "excluded_csv": str(excluded_path),
        "multi_entry_csv": str(multi_path),
        "geojson": str(geojson_path),
        "log": str(log_path),
        "stats": stats.__dict__,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="エリア第1.5スクリーニング")
    parser.add_argument("--input", required=True, help="第1スクリーニングCSVまたはフォルダ")
    parser.add_argument("--area", required=True, help="エリア設定GeoJSON")
    parser.add_argument("--output", required=True, help="出力フォルダ")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--boundary-tolerance-m", type=float, default=0.0)
    parser.add_argument("--min-distance-m", type=float, default=10.0)
    parser.add_argument("--min-duration-sec", type=float, default=5.0)
    parser.add_argument("--merge-gap-sec", type=float, default=10.0)
    parser.add_argument("--max-check-geojson-features", type=int, default=5000, help="品質確認GeoJSONへ出力するサブトリップ数の上限")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = ScreeningConfig(
        input_path=Path(args.input),
        area_geojson=Path(args.area),
        output_dir=Path(args.output),
        encoding=args.encoding,
        recursive=args.recursive,
        boundary_tolerance_m=args.boundary_tolerance_m,
        min_subtrip_distance_m=args.min_distance_m,
        min_subtrip_duration_sec=args.min_duration_sec,
        merge_gap_sec=args.merge_gap_sec,
        max_check_geojson_features=args.max_check_geojson_features,
    )
    result = run_screening(config, progress_cb=lambda stage, done, total, extra: print(f"{stage}: {done}/{total} {extra}", flush=True))
    print(json.dumps(result["stats"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
