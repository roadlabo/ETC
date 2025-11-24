"""Debug trip extractor for crossroad point matching.

This script inspects the first 100 trip CSV files and emits detailed logs
about how each segment is judged against crossroad center points.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Sequence, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# 入力トリップCSVディレクトリ / 出力ディレクトリ
DEFAULT_INPUT_DIR: Path | None = Path(r"C:\Users\owner\Documents\temp(ETC)\02_1st_screening")
DEFAULT_OUTPUT_DIR: Path | None = Path(r"C:\Users\owner\Documents\temp(ETC)\06_2nd_point\市内10か所")

# 交差点CSVファイルが入っているフォルダ（この中の *.csv を全て対象とする）
CROSSROAD_CSV_DIR: Path | None = Path(r"C:\Users\owner\Documents\temp(ETC)\04_crossroads\市内10か所")

# ---------------------------------------------------------------------------
# Constants (aligned with production 16_trip_extractor_point.py)
# ---------------------------------------------------------------------------
LAT_INDEX = 14
LON_INDEX = 15
FLAG_INDEX = 12
DATE_INDEX = 6  # G列。例: 20250224161105（YYYYMMDDHHMMSS）
OP_DATE_INDEX = 2  # C列: 運行日 (YYYYMMDD)
TRIP_NO_INDEX = 8

EARTH_RADIUS_M = 6_371_000.0
MAX_DEBUG_FILES = 100


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
    name: str
    lon: float
    lat: float


@dataclass
class DebugMatchResult:
    hit: bool
    point_distances: list[float]
    min_point_distance: float | None
    segment_min_distance: float | None
    point_hits: int
    segment_hits: int
    reason: str


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def read_csv_rows(path: Path) -> List[CSVRow]:
    rows: List[CSVRow] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(CSVRow(list(row)))
    return rows


def load_crossroad_points(paths: list[Path]) -> list[CrossroadPoint]:
    points: list[CrossroadPoint] = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                reader = csv.reader(f)
                try:
                    next(reader)  # skip header
                except StopIteration:
                    continue
                try:
                    row = next(reader)
                except StopIteration:
                    continue
                if len(row) <= 2:
                    continue
                try:
                    lon = float(row[1])
                    lat = float(row[2])
                except (TypeError, ValueError):
                    continue
                point = CrossroadPoint(name=path.stem, lon=lon, lat=lat)
                print(f"[Crossroad Loaded] name={point.name}, lon={point.lon:.5f}, lat={point.lat:.5f}")
                points.append(point)
        except Exception:
            continue
    return points


# ---------------------------------------------------------------------------
# Weekday helpers
# ---------------------------------------------------------------------------

def _weekday_from_row(row: "CSVRow") -> int | None:
    try:
        if len(row.values) <= DATE_INDEX:
            return None
        token = row.values[DATE_INDEX]
        if not token:
            return None
        ymd = token[:8]
        dt = datetime.strptime(ymd, "%Y%m%d")
        py = dt.weekday()  # Mon=0, Tue=1, ..., Sun=6
        return 1 if py == 6 else py + 2
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def haversine_distance_m(lat0: float, lon0: float, lat1: float, lon1: float) -> float:
    phi1 = math.radians(lat0)
    phi2 = math.radians(lat1)
    dphi = phi2 - phi1
    dlambda = math.radians(lon1 - lon0)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def _to_local_xy(lon: float, lat: float, lon0: float, lat0: float) -> Tuple[float, float]:
    dx = math.radians(lon - lon0) * math.cos(math.radians(lat0))
    dy = math.radians(lat - lat0)
    return EARTH_RADIUS_M * dx, EARTH_RADIUS_M * dy


def _segment_distance_to_origin(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    x0, y0 = p1
    x1, y1 = p2
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
# Trip helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Debug logic
# ---------------------------------------------------------------------------

def debug_print_trip_rows(rows: Sequence[CSVRow]) -> None:
    print("Example rows (first 5):")
    for idx, row in enumerate(rows[:5]):
        lon = row.values[LON_INDEX] if len(row.values) > LON_INDEX else ""
        lat = row.values[LAT_INDEX] if len(row.values) > LAT_INDEX else ""
        time_val = row.values[DATE_INDEX] if len(row.values) > DATE_INDEX else ""
        print(f"  idx={idx} lon={lon} lat={lat} time={time_val}")


def debug_trip_matches_point(
    rows: Sequence[CSVRow],
    start: int,
    end: int,
    cross_lat_deg: float,
    cross_lon_deg: float,
    thresh_m: float,
    min_hits: int,
    target_weekdays: set[int],
) -> DebugMatchResult:
    point_hits = 0
    segment_hits = 0
    point_distances: list[float] = []
    coords: list[Tuple[float, float]] = []
    weekday_skips = 0
    malformed_rows = 0

    for row in rows[start:end]:
        if target_weekdays:
            wd = _weekday_from_row(row)
            if wd is None or wd not in target_weekdays:
                weekday_skips += 1
                continue

        if len(row.values) <= max(LAT_INDEX, LON_INDEX):
            malformed_rows += 1
            continue
        try:
            lat = float(row.values[LAT_INDEX])
            lon = float(row.values[LON_INDEX])
        except (TypeError, ValueError):
            malformed_rows += 1
            continue

        coords.append((lon, lat))
        distance = haversine_distance_m(lat, lon, cross_lat_deg, cross_lon_deg)
        point_distances.append(distance)
        if distance <= thresh_m:
            point_hits += 1

    min_point_distance = min(point_distances) if point_distances else None
    segment_min_distance: float | None = None

    if point_hits > 0 or len(coords) < 2:
        hit = point_hits + segment_hits >= min_hits
        reason = _decide_reason(
            hit=hit,
            min_point_distance=min_point_distance,
            segment_min_distance=segment_min_distance,
            point_hits=point_hits,
            segment_hits=segment_hits,
            min_hits=min_hits,
            weekday_skips=weekday_skips,
            malformed_rows=malformed_rows,
            thresh_m=thresh_m,
        )
        return DebugMatchResult(
            hit=hit,
            point_distances=point_distances,
            min_point_distance=min_point_distance,
            segment_min_distance=segment_min_distance,
            point_hits=point_hits,
            segment_hits=segment_hits,
            reason=reason,
        )

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
        segment_min_distance = dist if segment_min_distance is None else min(segment_min_distance, dist)
        if dist <= thresh_m:
            segment_hits += 1
        last_x, last_y = x, y

    hit = point_hits + segment_hits >= min_hits
    reason = _decide_reason(
        hit=hit,
        min_point_distance=min_point_distance,
        segment_min_distance=segment_min_distance,
        point_hits=point_hits,
        segment_hits=segment_hits,
        min_hits=min_hits,
        weekday_skips=weekday_skips,
        malformed_rows=malformed_rows,
        thresh_m=thresh_m,
    )
    return DebugMatchResult(
        hit=hit,
        point_distances=point_distances,
        min_point_distance=min_point_distance,
        segment_min_distance=segment_min_distance,
        point_hits=point_hits,
        segment_hits=segment_hits,
        reason=reason,
    )


def _decide_reason(
    hit: bool,
    min_point_distance: float | None,
    segment_min_distance: float | None,
    point_hits: int,
    segment_hits: int,
    min_hits: int,
    weekday_skips: int,
    malformed_rows: int,
    thresh_m: float,
) -> str:
    if hit:
        return "hit"
    if point_hits + segment_hits < min_hits:
        return "min_hits"
    distances = [d for d in [min_point_distance, segment_min_distance] if d is not None]
    effective_min = min(distances) if distances else None
    if effective_min is not None and effective_min > thresh_m:
        return "distance"
    if weekday_skips > 0 and point_hits == 0 and segment_hits == 0:
        return "weekday"
    if malformed_rows > 0:
        return "malformed"
    return "unknown"


def debug_segment_analysis(
    rows: Sequence[CSVRow],
    start: int,
    end: int,
    cross: CrossroadPoint,
    thresh_m: float,
    min_hits: int,
    target_weekdays: set[int],
) -> DebugMatchResult:
    result = debug_trip_matches_point(
        rows=rows,
        start=start,
        end=end,
        cross_lat_deg=cross.lat,
        cross_lon_deg=cross.lon,
        thresh_m=thresh_m,
        min_hits=min_hits,
        target_weekdays=target_weekdays,
    )

    print(f"Cross={cross.name}")
    if result.point_distances:
        formatted = ", ".join(f"{d:.1f}" for d in result.point_distances)
        print(f"  point_distances: [{formatted}]")
    else:
        print("  point_distances: []")
    print(f"  min_point_distance = {result.min_point_distance:.1f}" if result.min_point_distance is not None else "  min_point_distance = N/A")
    print(
        f"  segment_min_distance = {result.segment_min_distance:.1f}"
        if result.segment_min_distance is not None
        else "  segment_min_distance = N/A"
    )
    print(f"  hits_point = {result.point_hits}")
    print(f"  hits_segment = {result.segment_hits}")
    print(f"  MIN_HITS required = {min_hits} \u2192 {'OK' if result.point_hits + result.segment_hits >= min_hits else 'NG'}")

    if result.hit:
        print(
            f"  => RESULT: HIT (最短距離：{_format_distance(result)}m, point_hits={result.point_hits}, segment_hits={result.segment_hits})"
        )
    else:
        reason_detail = _format_reason(result, thresh_m, min_hits)
        print(f"  => RESULT: NON-HIT ({reason_detail})")

    return result


def _format_distance(result: DebugMatchResult) -> str:
    candidates = [d for d in [result.min_point_distance, result.segment_min_distance] if d is not None]
    if not candidates:
        return "N/A"
    return f"{min(candidates):.1f}"


def _format_reason(result: DebugMatchResult, thresh_m: float, min_hits: int) -> str:
    if result.reason == "min_hits":
        return (
            "理由: ヒット数不足 "
            f"(point={result.point_hits}, segment={result.segment_hits}, MIN_HITS要求={min_hits})"
        )
    if result.reason == "weekday":
        return "理由: 曜日不一致で全スキップ"
    if result.reason == "malformed":
        return "理由: 列不足/値エラーで判定不能"
    if result.reason == "distance":
        shortest = _format_distance(result)
        return f"理由: 最短距離{shortest}m > 閾値{thresh_m}m"
    return "理由: 判定条件未達"


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_debug_file(
    path: Path,
    file_no: int,
    crossroads: list[CrossroadPoint],
    thresh_m: float,
    min_hits: int,
    target_weekdays: set[int],
) -> tuple[bool, dict[str, int], dict[str, int]]:
    rows = read_csv_rows(path)
    print(f"==== File #{file_no}: {path.name} ====")
    print(f"Total rows = {len(rows)}")
    debug_print_trip_rows(rows)

    boundaries = build_boundaries(rows)
    hits_per_cross: dict[str, int] = {c.name: 0 for c in crossroads}
    reasons: dict[str, int] = {"distance": 0, "min_hits": 0, "weekday": 0, "malformed": 0, "unknown": 0}

    any_hit = False
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        if start == end:
            continue
        print(f"-- Segment {i + 1}: rows {start}-{end - 1} --")
        for cross in crossroads:
            result = debug_segment_analysis(rows, start, end, cross, thresh_m, min_hits, target_weekdays)
            if result.hit:
                if hits_per_cross[cross.name] == 0:
                    hits_per_cross[cross.name] = 1
                any_hit = True
            else:
                reasons[result.reason] += 1
    return any_hit, hits_per_cross, reasons


def summarize_results(
    total_files: int,
    total_hits: int,
    hit_counter: dict[str, int],
    reason_counter: dict[str, int],
) -> None:
    print("===== DEBUG SUMMARY =====")
    print(f"Total files checked: {total_files}")
    print(f"Total HIT (any cross): {total_hits}")
    print("Hits per crossroad:")
    for name, count in hit_counter.items():
        print(f"  {name}: {count} / {total_files}")
    print("推定原因:")
    if reason_counter["distance"]:
        print("  - 多くで最短距離が閾値を超過")
    if reason_counter["min_hits"]:
        print("  - MIN_HITS 未達による非ヒットが多数")
    if reason_counter["weekday"]:
        print("  - 曜日不一致で対象外となったセグメントが存在")
    if reason_counter["malformed"]:
        print("  - TRIP座標の列不足や変換エラーが発生")
    if reason_counter["unknown"]:
        print("  - その他の要因による未判定ケースあり")
    if not any(reason_counter.values()):
        print("  - 目立った原因なし")
    print("=========================")


def parse_target_weekdays(token: str) -> set[int]:
    if not token:
        return set()
    weekdays = set()
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            num = int(part)
        except ValueError:
            continue
        if 1 <= num <= 7:
            weekdays.add(num)
    return weekdays


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug trip extractor for crossroad points")
    parser.add_argument(
        "trip_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Trip CSV directory (default: DEFAULT_INPUT_DIR)",
    )
    parser.add_argument(
        "--crossroad-csv",
        nargs="+",
        type=Path,
        help="Crossroad CSV files (default: all CSVs under CROSSROAD_CSV_DIR)",
    )
    parser.add_argument("--thresh-m", type=float, default=20.0, help="Threshold distance in meters")
    parser.add_argument("--min-hits", type=int, default=1, help="Minimum hits (point + segment)")
    parser.add_argument("--target-weekdays", type=str, default="1,2,3,4,5,6,7", help="Weekday filter (1=SUN ... 7=SAT)")
    parser.add_argument("--recursive", action="store_true", help="Search trip_dir recursively")

    args = parser.parse_args()

    crossroad_csv_paths: list[Path] = []

    if args.crossroad_csv:
        crossroad_csv_paths.extend(args.crossroad_csv)
    elif CROSSROAD_CSV_DIR is not None:
        crossroad_csv_paths.extend(sorted(CROSSROAD_CSV_DIR.glob("*.csv")))

    if not crossroad_csv_paths:
        print("No crossroad CSV files specified and CROSSROAD_CSV_DIR is not set. Aborting.")
        return

    crossroads = load_crossroad_points(crossroad_csv_paths)
    if not crossroads:
        print("No valid crossroad points loaded. Aborting.")
        return

    target_weekdays = parse_target_weekdays(args.target_weekdays)

    trip_dir: Path = args.trip_dir
    if args.recursive:
        files = sorted(trip_dir.rglob("*.csv"))
    else:
        files = sorted(trip_dir.glob("*.csv"))

    if not files:
        print("No trip CSV files found.")
        return

    files = files[:MAX_DEBUG_FILES]

    total_hit_files = 0
    aggregate_hits: dict[str, int] = {c.name: 0 for c in crossroads}
    reason_counter: dict[str, int] = {"distance": 0, "min_hits": 0, "weekday": 0, "malformed": 0, "unknown": 0}

    for idx, file_path in enumerate(files, start=1):
        any_hit, hits_per_cross, reasons = process_debug_file(
            path=file_path,
            file_no=idx,
            crossroads=crossroads,
            thresh_m=args.thresh_m,
            min_hits=args.min_hits,
            target_weekdays=target_weekdays,
        )
        if any_hit:
            total_hit_files += 1
        for name, count in hits_per_cross.items():
            aggregate_hits[name] += count
        for key, count in reasons.items():
            reason_counter[key] += count

    summarize_results(
        total_files=len(files),
        total_hits=total_hit_files,
        hit_counter=aggregate_hits,
        reason_counter=reason_counter,
    )


if __name__ == "__main__":
    main()
