"""Route performance aggregation for ETC2.0 trip CSVs.

The tool reads route bucket points created by step 10 and second-screened trip
CSVs created by step 20.  It projects each trip segment to every route, fills
all crossed buckets without gaps, and guarantees that one trip contributes at
most one value to the same route bucket.
"""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd

SRC_DIR = Path(__file__).resolve().parent
EARTH_R = 6_371_000.0

ROUTE_DIR_CANDIDATES = [
    "10_ルート(Route)データ",
    "10_ルートデータ",
    "10_繝ｫ繝ｼ繝・Route)繝・・繧ｿ",
    "10_繝ｫ繝ｼ繝医ョ繝ｼ繧ｿ",
]
SCREENING_DIR_CANDIDATES = [
    "20_第2スクリーニング(ルート)",
    "20_第2スクリーニング",
    "20_第２スクリーニング(ルート)",
    "20_第２スクリーニング",
    "20_隨ｬ・偵せ繧ｯ繝ｪ繝ｼ繝九Φ繧ｰ(繝ｫ繝ｼ繝・",
    "20_隨ｬ・偵せ繧ｯ繝ｪ繝ｼ繝九Φ繧ｰ",
]
OUTPUT_DIR_NAME = "30_route_performance"
OUTPUT_DIR_CANDIDATES = [
    "30_route_performance",
    "30_ルートパフォーマンス",
]

ROUTE_LON_COL = 14
ROUTE_LAT_COL = 15
COL_OPERATION_DATE = 2
COL_TIME = 6
COL_TRIP_NO = 8
COL_LON = 14
COL_LAT = 15

MAX_OFF_ROUTE_M = 30.0
MAX_SEGMENT_DISTANCE_M = 350.0
MAX_SEGMENT_TIME_S = 600.0
MIN_SEGMENT_DISTANCE_M = 1.0
MAX_SPEED_KMH = 180.0
ROUTE_BBOX_MARGIN_DEG = 0.001
KP_DECIMALS = 3

DIRECTIONS = ("forward", "reverse")
DIRECTION_LABEL = {"forward": "順方向", "reverse": "逆方向"}
METRIC_LABEL = {"speed": "速度", "volume": "交通量"}
PERIODS = ["平日", "休日", "月", "火", "水", "木", "金", "土", "日"]
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

ProgressCallback = Optional[Callable[[int, str, dict], None]]


def deg2rad(value: float) -> float:
    return value * math.pi / 180.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1r, lon1r, lat2r, lon2r = map(deg2rad, (lat1, lon1, lat2, lon2))
    dlat = lat2r - lat1r
    dlon = lon2r - lon1r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def read_csv_rows(path: Path) -> list[list[str]]:
    last: Exception | None = None
    for enc in ("utf-8-sig", "cp932", "shift_jis", "utf-8"):
        try:
            with path.open("r", encoding=enc, errors="strict", newline="") as fh:
                return list(csv.reader(fh))
        except Exception as exc:
            last = exc
    raise RuntimeError(f"CSVを読めませんでした: {path} ({last})")


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" ._") or "route"


def normalize_date_token(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2)}{m.group(3)}"


def parse_datetime_from_row(row: list[str]) -> Optional[datetime]:
    date_text = str(row[COL_OPERATION_DATE]).strip() if len(row) > COL_OPERATION_DATE else ""
    time_text = str(row[COL_TIME]).strip() if len(row) > COL_TIME else ""
    candidates = []
    if date_text and time_text:
        candidates.append(f"{date_text} {time_text}")
    if time_text:
        candidates.append(time_text)

    for text in candidates:
        cleaned = text.replace("T", " ").replace("/", "-")
        cleaned = re.sub(r"([+-]\d{2}:?\d{2}|Z)$", "", cleaned).strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y%m%d %H:%M:%S.%f",
            "%Y%m%d %H:%M:%S",
            "%Y%m%d %H:%M",
            "%Y%m%d %H%M%S",
            "%Y%m%d%H%M%S",
            "%H:%M:%S.%f",
            "%H:%M:%S",
            "%H:%M",
            "%H%M%S",
            "%H%M",
        ):
            try:
                dt = datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
            if dt.year == 1900:
                token = normalize_date_token(date_text)
                if token:
                    base = datetime.strptime(token, "%Y%m%d")
                    return base.replace(hour=dt.hour, minute=dt.minute, second=dt.second, microsecond=dt.microsecond)
            return dt
    return None


def seconds_to_hhmmss(value: Optional[float]) -> str:
    if value is None:
        return ""
    sec = int(round(value)) % 86400
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def speed_metric_value(speeds: list[float], metric: str) -> Optional[float]:
    if not speeds:
        return None
    if metric == "speed_freeflow":
        n = max(1, math.ceil(len(speeds) * 0.05))
        vals = sorted(speeds, reverse=True)[:n]
    elif metric == "speed_congested":
        n = max(1, math.ceil(len(speeds) * 0.05))
        vals = sorted(speeds)[:n]
    else:
        vals = speeds
    return sum(vals) / len(vals)


def period_keys(dt: datetime) -> list[str]:
    return (["平日"] if dt.weekday() < 5 else ["休日"]) + [WEEKDAY_JA[dt.weekday()]]


def trip_key(path: Path, row: list[str], fallback: int) -> str:
    trip_no = row[COL_TRIP_NO].strip() if len(row) > COL_TRIP_NO else ""
    return f"{path.resolve()}::{trip_no or fallback}"


@dataclass
class Projection:
    s_m: float
    off_m: float


@dataclass
class RouteModel:
    name: str
    path: Path
    lons: list[float]
    lats: list[float]
    xs: list[float]
    ys: list[float]
    kp_m: list[float]
    origin_lon: float
    origin_lat: float

    @property
    def length_m(self) -> float:
        return self.kp_m[-1] if self.kp_m else 0.0

    def to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        lat0r = deg2rad(self.origin_lat)
        return (
            deg2rad(lon - self.origin_lon) * EARTH_R * math.cos(lat0r),
            deg2rad(lat - self.origin_lat) * EARTH_R,
        )

    def project(self, lon: float, lat: float) -> Projection:
        px, py = self.to_xy(lon, lat)
        best_s = 0.0
        best_d = float("inf")
        for i in range(len(self.xs) - 1):
            ax, ay = self.xs[i], self.ys[i]
            bx, by = self.xs[i + 1], self.ys[i + 1]
            vx, vy = bx - ax, by - ay
            seg2 = vx * vx + vy * vy
            if seg2 <= 0:
                continue
            t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / seg2))
            qx, qy = ax + t * vx, ay + t * vy
            d = math.hypot(px - qx, py - qy)
            if d < best_d:
                best_d = d
                best_s = self.kp_m[i] + t * (self.kp_m[i + 1] - self.kp_m[i])
        return Projection(best_s, best_d)


def load_route(path: Path) -> RouteModel:
    lons: list[float] = []
    lats: list[float] = []
    for row in read_csv_rows(path):
        try:
            lon = float(row[ROUTE_LON_COL])
            lat = float(row[ROUTE_LAT_COL])
        except Exception:
            continue
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            lons.append(lon)
            lats.append(lat)
    if len(lons) < 2:
        raise RuntimeError(f"ルートCSVから2点以上の座標を読めません: {path}")

    origin_lon, origin_lat = lons[0], lats[0]
    lat0r = deg2rad(origin_lat)
    xs = [deg2rad(lon - origin_lon) * EARTH_R * math.cos(lat0r) for lon in lons]
    ys = [deg2rad(lat - origin_lat) * EARTH_R for lat in lats]
    kp_m = [0.0]
    for i in range(1, len(lons)):
        kp_m.append(kp_m[-1] + haversine_m(lats[i - 1], lons[i - 1], lats[i], lons[i]))
    return RouteModel(path.stem, path, lons, lats, xs, ys, kp_m, origin_lon, origin_lat)


def crossed_bucket_indices(kp_m: list[float], s1: float, s2: float) -> Iterable[int]:
    eps = 1e-6
    if s2 > s1:
        for i, kp in enumerate(kp_m):
            if s1 + eps < kp <= s2 + eps:
                yield i
    elif s2 < s1:
        for i, kp in enumerate(kp_m):
            if s2 - eps <= kp < s1 - eps:
                yield i


@dataclass
class Event:
    route: str
    trip: str
    bucket_idx: int
    direction: str
    pass_dt: datetime
    speed_kmh: float
    segment_distance_m: float
    segment_time_s: float


class RouteAggregator:
    def __init__(self, route: RouteModel, expansion_factor: float) -> None:
        self.route = route
        self.expansion_factor = expansion_factor
        self.speed_values: dict[tuple[str, int, str, int], list[float]] = defaultdict(list)
        self.time_values: dict[tuple[str, int, str, int], list[float]] = defaultdict(list)
        self.counts: dict[tuple[str, int, str, int], int] = defaultdict(int)
        self.seen_trip_bucket: set[tuple[str, int]] = set()
        self.date_tokens: set[str] = set()
        self.event_count = 0

    def add_event(self, event: Event) -> bool:
        unique_key = (event.trip, event.bucket_idx)
        if unique_key in self.seen_trip_bucket:
            return False
        self.seen_trip_bucket.add(unique_key)
        self.event_count += 1
        sec = event.pass_dt.hour * 3600 + event.pass_dt.minute * 60 + event.pass_dt.second + event.pass_dt.microsecond / 1_000_000
        date_token = event.pass_dt.strftime("%Y%m%d")
        self.date_tokens.add(date_token)
        for period in period_keys(event.pass_dt) + [date_token]:
            key = (event.direction, event.bucket_idx, period, event.pass_dt.hour)
            self.speed_values[key].append(event.speed_kmh)
            self.time_values[key].append(sec)
            self.counts[key] += 1
        return True

    def summary_periods(self) -> list[str]:
        return PERIODS + sorted(self.date_tokens)

    def summary_rows(self, include_empty: bool = True, date_only: bool = False) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for i, kp in enumerate(self.route.kp_m):
            for direction in DIRECTIONS:
                for period in self.summary_periods():
                    if date_only and not re.fullmatch(r"\d{8}", period):
                        continue
                    for hour in range(24):
                        key = (direction, i, period, hour)
                        speeds = self.speed_values.get(key, [])
                        times = self.time_values.get(key, [])
                        count = self.counts.get(key, 0)
                        if not include_empty and not count:
                            continue
                        rows.append(
                            {
                                "route": self.route.name,
                                "bucket_index": i,
                                "kp_km": round(kp / 1000, KP_DECIMALS),
                                "lon": self.route.lons[i],
                                "lat": self.route.lats[i],
                                "direction": direction,
                                "direction_label": DIRECTION_LABEL[direction],
                                "period": period,
                                "date": period if re.fullmatch(r"\d{8}", period) else "",
                                "hour": hour,
                                "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else "",
                                "freeflow_speed_kmh": round(speed_metric_value(speeds, "speed_freeflow"), 1) if speeds else "",
                                "congested_speed_kmh": round(speed_metric_value(speeds, "speed_congested"), 1) if speeds else "",
                                "median_speed_kmh": round(statistics.median(speeds), 1) if speeds else "",
                                "trip_count": count if count else "",
                                "expanded_volume": round(count * self.expansion_factor, 1) if count else "",
                                "avg_pass_time": seconds_to_hhmmss(sum(times) / len(times)) if times else "",
                            }
                        )
        return rows

    def pivot(self, direction: str, metric: str) -> pd.DataFrame:
        columns = [f"{period}_{hour:02d}" for period in self.summary_periods() for hour in range(24)]
        data: list[dict[str, object]] = []
        for i, kp in enumerate(self.route.kp_m):
            row: dict[str, object] = {
                "bucket_index": i,
                "KP[km]": round(kp / 1000, KP_DECIMALS),
                "lon": self.route.lons[i],
                "lat": self.route.lats[i],
            }
            for period in self.summary_periods():
                for hour in range(24):
                    col = f"{period}_{hour:02d}"
                    key = (direction, i, period, hour)
                    if metric == "speed":
                        vals = self.speed_values.get(key, [])
                        row[col] = round(sum(vals) / len(vals), 1) if vals else ""
                    elif metric == "volume":
                        count = self.counts.get(key, 0)
                        row[col] = round(count * self.expansion_factor, 1) if count else ""
                    elif metric == "count":
                        row[col] = self.counts.get(key, "") or ""
                    else:
                        vals = self.time_values.get(key, [])
                        row[col] = seconds_to_hhmmss(sum(vals) / len(vals)) if vals else ""
            data.append(row)
        return pd.DataFrame(data, columns=["bucket_index", "KP[km]", "lon", "lat"] + columns)

    def daily_wide_rows(self, date_token: str, direction: str, metric: str, hours: Iterable[int]) -> list[dict[str, object]]:
        hour_list = list(hours)
        rows: list[dict[str, object]] = []
        for i, kp in enumerate(self.route.kp_m):
            row: dict[str, object] = {
                "bucket_index": i,
                "KP[km]": round(kp / 1000, KP_DECIMALS),
                "lon": self.route.lons[i],
                "lat": self.route.lats[i],
            }
            weighted_speed_sum = 0.0
            volume_sum = 0.0
            daily_speeds: list[float] = []
            for hour in hour_list:
                key = (direction, i, date_token, hour)
                speeds = self.speed_values.get(key, [])
                count = self.counts.get(key, 0)
                volume = count * self.expansion_factor
                if metric.startswith("speed"):
                    speed_value = speed_metric_value(speeds, metric)
                    row[f"{hour:02d}"] = round(speed_value, 1) if speed_value is not None else ""
                    if metric == "speed" and speed_value is not None and volume > 0:
                        weighted_speed_sum += speed_value * volume
                        volume_sum += volume
                    elif metric != "speed":
                        daily_speeds.extend(speeds)
                elif metric == "volume":
                    row[f"{hour:02d}"] = round(volume, 1) if volume else ""
                    volume_sum += volume
                else:
                    row[f"{hour:02d}"] = count if count else ""
                    volume_sum += count
            if metric == "speed":
                row["daily"] = round(weighted_speed_sum / volume_sum, 1) if volume_sum else ""
            elif metric.startswith("speed"):
                daily_value = speed_metric_value(daily_speeds, metric)
                row["daily"] = round(daily_value, 1) if daily_value is not None else ""
            elif metric == "volume":
                row["daily"] = round(volume_sum, 1) if volume_sum else ""
            else:
                row["daily"] = int(volume_sum) if volume_sum else ""
            rows.append(row)
        return rows

def find_first_existing(project_dir: Path, candidates: list[str]) -> Path:
    for name in candidates:
        path = project_dir / name
        if path.exists():
            return path
    raise FileNotFoundError(f"必要なフォルダが見つかりません: {', '.join(candidates)}")


def resolve_project_paths(project_dir: str | Path) -> tuple[Path, Path, Path]:
    project = Path(project_dir)
    route_dir = find_first_existing(project, ROUTE_DIR_CANDIDATES)
    screening_dir = find_first_existing(project, SCREENING_DIR_CANDIDATES)
    out_dir = next((project / name for name in OUTPUT_DIR_CANDIDATES if (project / name).exists()), project / OUTPUT_DIR_NAME)
    out_dir.mkdir(parents=True, exist_ok=True)
    return screening_dir, route_dir, out_dir


def list_route_csvs(route_dir: str | Path) -> list[Path]:
    return sorted(p for p in Path(route_dir).glob("*.csv") if p.is_file())


def list_input_csvs(input_dir: str | Path, recursive: bool = True) -> list[Path]:
    root = Path(input_dir)
    pattern = "**/*.csv" if recursive else "*.csv"
    return sorted(p for p in root.glob(pattern) if p.is_file())


def extract_available_dates(input_dir: str | Path, recursive: bool = True) -> list[str]:
    dates: set[str] = set()
    for path in list_input_csvs(input_dir, recursive):
        try:
            rows = read_csv_rows(path)
        except Exception:
            continue
        for row in rows:
            dt = parse_datetime_from_row(row)
            if dt:
                dates.add(dt.strftime("%Y%m%d"))
    return sorted(dates)


def interpolate_event(
    route: RouteModel,
    trip: str,
    bucket_idx: int,
    direction: str,
    t1: datetime,
    s1: float,
    t2: datetime,
    s2: float,
) -> Event:
    ds = s2 - s1
    dt_s = (t2 - t1).total_seconds()
    ratio = (route.kp_m[bucket_idx] - s1) / ds
    pass_dt = t1 + timedelta(seconds=dt_s * ratio)
    dist_m = abs(ds)
    return Event(route.name, trip, bucket_idx, direction, pass_dt, dist_m / dt_s * 3.6, dist_m, dt_s)


def write_route_outputs(
    aggregator: RouteAggregator,
    output_dir: str | Path,
    expansion_factor: float,
) -> dict[str, object]:
    route = aggregator.route
    route_dir = Path(output_dir) / safe_name(route.name)
    route_dir.mkdir(parents=True, exist_ok=True)
    json_path = route_dir / f"{safe_name(route.name)}_viewer.json"

    daily_summary_rows = aggregator.summary_rows(include_empty=False, date_only=True)

    viewer_payload = {
        "route": route.name,
        "expansion_factor": expansion_factor,
        "points": [
            {"bucket_index": i, "kp_km": round(kp / 1000, KP_DECIMALS), "lat": route.lats[i], "lon": route.lons[i]}
            for i, kp in enumerate(route.kp_m)
        ],
        "summary": daily_summary_rows,
    }
    json_path.write_text(json.dumps(viewer_payload, ensure_ascii=False), encoding="utf-8")

    return {
        "xlsx": "",
        "summary_csv": "",
        "daily_hourly_csv": "",
        "events_csv": "",
        "viewer_json": str(json_path),
        "daily_xlsx_files": [],
    }


def analyze_route(
    input_dir: str | Path,
    route_path: str | Path,
    output_dir: str | Path,
    recursive: bool = True,
    allowed_dates: Optional[set[str]] = None,
    allowed_hours: Optional[set[int]] = None,
    expansion_factor: float = 1.0,
    max_off_route_m: float = MAX_OFF_ROUTE_M,
    progress_callback: ProgressCallback = None,
) -> dict[str, object]:
    route = load_route(Path(route_path))
    aggregator = RouteAggregator(route, expansion_factor)
    files = list_input_csvs(input_dir, recursive)
    projected: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
    total_rows = valid_points = 0

    def progress(percent: int, message: str, **stats: object) -> None:
        payload = {
            "route": route.name,
            "total_files": len(files),
            "events": aggregator.event_count,
            "valid_points": valid_points,
            **stats,
        }
        print(f"[PROGRESS] {percent:3d}% {route.name}: {message}", flush=True)
        if progress_callback:
            progress_callback(percent, message, payload)

    progress(0, "ルートを読み込みました", buckets=len(route.kp_m))
    for file_index, path in enumerate(files, start=1):
        progress(5 + int(45 * (file_index - 1) / max(len(files), 1)), f"投影中 {file_index}/{len(files)} {path.name}", current_file=file_index, current_file_name=path.name)
        for row_index, row in enumerate(read_csv_rows(path)):
            total_rows += 1
            try:
                lon = float(row[COL_LON])
                lat = float(row[COL_LAT])
            except Exception:
                continue
            dt = parse_datetime_from_row(row)
            if dt is None:
                continue
            if allowed_dates is not None and dt.strftime("%Y%m%d") not in allowed_dates:
                continue
            projection = route.project(lon, lat)
            if projection.off_m > max_off_route_m:
                continue
            projected[trip_key(path, row, row_index)].append((dt, projection.s_m, projection.off_m))
            valid_points += 1

    skipped = 0
    trips = list(projected.items())
    for trip_index, (trip, points) in enumerate(trips, start=1):
        if trip_index == 1 or trip_index % 100 == 0 or trip_index == len(trips):
            progress(50 + int(40 * trip_index / max(len(trips), 1)), f"バケツ投入中 {trip_index}/{len(trips)}", trips=len(trips))
        points.sort(key=lambda x: x[0])
        for (t1, s1, _off1), (t2, s2, _off2) in zip(points, points[1:]):
            dt_s = (t2 - t1).total_seconds()
            ds = s2 - s1
            abs_ds = abs(ds)
            if dt_s <= 0 or abs_ds < MIN_SEGMENT_DISTANCE_M:
                skipped += 1
                continue
            if abs_ds > MAX_SEGMENT_DISTANCE_M or dt_s > MAX_SEGMENT_TIME_S:
                skipped += 1
                continue
            speed = abs_ds / dt_s * 3.6
            if speed > MAX_SPEED_KMH:
                skipped += 1
                continue
            direction = "forward" if ds > 0 else "reverse"
            for bucket_idx in crossed_bucket_indices(route.kp_m, s1, s2):
                event = interpolate_event(route, trip, bucket_idx, direction, t1, s1, t2, s2)
                if allowed_hours is not None and event.pass_dt.hour not in allowed_hours:
                    continue
                aggregator.add_event(event)

    outputs = write_route_outputs(aggregator, output_dir, expansion_factor)

    progress(100, "出力完了", trips=len(trips), skipped_segments=skipped)
    return {
        "route": route.name,
        "route_path": str(route.path),
        **outputs,
        "events": aggregator.event_count,
        "trips": len(trips),
        "valid_points": valid_points,
        "skipped_segments": skipped,
        "expansion_factor": expansion_factor,
    }


def finalize_projected_route(
    route: RouteModel,
    aggregator: RouteAggregator,
    projected: dict[str, list[tuple[datetime, float, float]]],
    output_dir: str | Path,
    allowed_hours: Optional[set[int]],
    valid_points: int,
    expansion_factor: float,
    progress_callback: ProgressCallback = None,
) -> dict[str, object]:
    skipped = 0
    trips = list(projected.items())
    for trip_index, (trip, points) in enumerate(trips, start=1):
        if progress_callback and (trip_index == 1 or trip_index % 100 == 0 or trip_index == len(trips)):
            progress_callback(0, f"バケツ投入中 {trip_index}/{len(trips)}", {"trips": len(trips), "valid_points": valid_points, "events": aggregator.event_count})
        points.sort(key=lambda x: x[0])
        for (t1, s1, _off1), (t2, s2, _off2) in zip(points, points[1:]):
            dt_s = (t2 - t1).total_seconds()
            ds = s2 - s1
            abs_ds = abs(ds)
            if dt_s <= 0 or abs_ds < MIN_SEGMENT_DISTANCE_M:
                skipped += 1
                continue
            if abs_ds > MAX_SEGMENT_DISTANCE_M or dt_s > MAX_SEGMENT_TIME_S:
                skipped += 1
                continue
            speed = abs_ds / dt_s * 3.6
            if speed > MAX_SPEED_KMH:
                skipped += 1
                continue
            direction = "forward" if ds > 0 else "reverse"
            for bucket_idx in crossed_bucket_indices(route.kp_m, s1, s2):
                event = interpolate_event(route, trip, bucket_idx, direction, t1, s1, t2, s2)
                if allowed_hours is not None and event.pass_dt.hour not in allowed_hours:
                    continue
                aggregator.add_event(event)

    outputs = write_route_outputs(aggregator, output_dir, expansion_factor)

    return {
        "route": route.name,
        "route_path": str(route.path),
        **outputs,
        "events": aggregator.event_count,
        "trips": len(trips),
        "valid_points": valid_points,
        "skipped_segments": skipped,
        "expansion_factor": expansion_factor,
    }


def color_for_speed(speed: object) -> str:
    try:
        v = float(speed)
    except Exception:
        return "#9ca3af"
    if v >= 45:
        return "#16a34a"
    if v >= 30:
        return "#eab308"
    if v >= 15:
        return "#f97316"
    return "#dc2626"


def color_for_volume(volume: object, max_volume: float) -> str:
    try:
        v = float(volume)
    except Exception:
        return "#9ca3af"
    if max_volume <= 0:
        return "#9ca3af"
    r = v / max_volume
    if r >= 0.75:
        return "#7c2d12"
    if r >= 0.5:
        return "#ea580c"
    if r >= 0.25:
        return "#facc15"
    return "#22c55e"


def build_viewer(output_dir: str | Path, results: list[dict[str, object]]) -> Path:
    out_dir = Path(output_dir)
    manifest_routes = []
    date_tokens: set[str] = set()
    all_points = []
    for result in results:
        json_path = Path(str(result["viewer_json"]))
        if json_path.exists():
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            points = payload.get("points", [])
            all_points.extend(points)
            for row in payload.get("summary", []):
                period = str(row.get("period", ""))
                if re.fullmatch(r"\d{8}", period):
                    date_tokens.add(period)
            manifest_routes.append(
                {
                    "route": payload.get("route", json_path.stem.removesuffix("_viewer")),
                    "json": json_path.relative_to(out_dir).as_posix(),
                    "point_count": len(points),
                }
            )
    center_lat = sum(p["lat"] for p in all_points) / len(all_points) if all_points else 35.6812
    center_lon = sum(p["lon"] for p in all_points) / len(all_points) if all_points else 139.7671
    lons = [float(p["lon"]) for p in all_points if "lon" in p]
    lats = [float(p["lat"]) for p in all_points if "lat" in p]
    html_path = out_dir / "30_route_performance_viewer.html"
    manifest_path = out_dir / "30_route_performance_viewer_manifest.json"
    manifest = {
        "routes": manifest_routes,
        "dates": sorted(date_tokens),
        "center": {"lat": center_lat, "lon": center_lon},
        "bounds": {
            "min_lon": min(lons) if lons else 139.7,
            "max_lon": max(lons) if lons else 139.8,
            "min_lat": min(lats) if lats else 35.6,
            "max_lat": max(lats) if lats else 35.8,
            "has_points": bool(lons and lats),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    leaflet_css_path = SRC_DIR / "leaflet" / "leaflet.css"
    leaflet_js_path = SRC_DIR / "leaflet" / "leaflet.js"
    leaflet_css = leaflet_css_path.read_text(encoding="utf-8") if leaflet_css_path.exists() else ""
    leaflet_css_link = "" if leaflet_css else '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
    leaflet_script = (
        f"<script>{leaflet_js_path.read_text(encoding='utf-8')}</script>"
        if leaflet_js_path.exists()
        else '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
    )
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>30 Route Performance Viewer</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {leaflet_css_link}
  <style>
    {leaflet_css}
    html, body, #map {{ height:100%; margin:0; background:#fff; font-family: "Segoe UI", "Meiryo UI", sans-serif; }}
    .leaflet-container {{ background:#fff; }}
    #fallbackSvg {{ position:absolute; inset:0; width:100%; height:100%; background:#fff; cursor:grab; }}
    #fallbackSvg:active {{ cursor:grabbing; }}
    .panel {{ position:absolute; z-index:1000; left:12px; top:12px; width:min(560px, calc(100vw - 24px)); background:#ffffffee; border-radius:8px; box-shadow:0 8px 24px #0003; padding:10px; }}
    .row {{ display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-top:6px; }}
    button {{ border:1px solid #b8c2cc; background:#fff; border-radius:6px; padding:5px 8px; cursor:pointer; }}
    button.active {{ background:#0f766e; color:#fff; border-color:#0f766e; }}
    button:disabled {{ color:#cbd5e1; cursor:default; background:#f8fafc; }}
    .calendars {{ display:grid; grid-template-columns:repeat(4, minmax(118px, 1fr)); gap:8px; margin-top:8px; }}
    .calendar {{ border:1px solid #d7dee8; border-radius:6px; padding:6px; background:#fff; }}
    .monthTitle {{ text-align:center; font-weight:700; margin-bottom:4px; }}
    .week, .days {{ display:grid; grid-template-columns:repeat(7, 1fr); gap:2px; text-align:center; }}
    .week span {{ font-size:10px; color:#64748b; }}
    .day {{ min-width:0; padding:3px 0; font-size:11px; }}
    .hit {{ background:#ccfbf1; border-color:#0f766e; color:#0f172a; font-weight:700; }}
    .hit.active {{ background:#0f766e; color:#fff; }}
    .legend span {{ display:inline-block; width:14px; height:10px; margin-right:4px; }}
    select {{ min-width:130px; background:#fff; border:1px solid #b8c2cc; border-radius:6px; padding:4px; }}
  </style>
  {leaflet_script}
</head>
<body>
<div id="map"></div>
<div class="panel">
  <b>30 Route Performance Viewer</b>
  <div class="row"><small>出力フォルダ: {html_lib.escape(str(out_dir))}</small></div>
  <div class="row" id="metric"></div>
  <div class="row" id="speedKind"></div>
  <div class="row" id="monthbar"></div>
  <div class="calendars" id="calendars"></div>
  <div class="row"><b id="selectedDate"></b><span id="selectedHours"></span></div>
  <div class="row"><select id="hoursList" multiple size="8"></select><button id="hourUp">▲</button><button id="hourDown">▼</button></div>
  <div class="row" id="hourtools"></div>
  <div class="row"><button id="redrawButton">再描画</button><button id="exportButton">データ抽出</button></div>
  <div class="row legend" id="legend"></div>
</div>
<script>
const MANIFEST = {json.dumps(manifest, ensure_ascii=False)};
const DATA = [];
const DATE_PERIODS = MANIFEST.dates || [];
const HIT_DATES = new Set(DATE_PERIODS);
const HIT_MONTHS = Array.from(new Set(DATE_PERIODS.map(d => d.slice(0, 6)))).sort();
const HAS_LEAFLET = typeof L !== 'undefined';
let map = null;
let fallback = null;
if (HAS_LEAFLET) {{
  map = L.map('map').setView([{center_lat:.7f}, {center_lon:.7f}], 13);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19, attribution: '&copy; OpenStreetMap' }}).addTo(map);
}} else {{
  initFallbackMap();
}}
const state = {{metric:'speed', speedKind:'avg', period: DATE_PERIODS[0] || '', hours:new Set([8])}};
let monthIndex = 0;
let layers = [];
let dataLoaded = false;
let loadingPromise = null;
let loadedSelectionKey = '';
const loadingEl = document.createElement('div');
loadingEl.style.cssText = 'position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:2000;background:#ffffffee;border-radius:8px;box-shadow:0 8px 24px #0003;padding:12px 16px;font-weight:700;';
loadingEl.textContent = 'データ読み込み中...';
function setLoading(visible, text='データ読み込み中...') {{
  loadingEl.textContent = text;
  if (visible && !loadingEl.parentNode) document.body.appendChild(loadingEl);
  if (!visible && loadingEl.parentNode) loadingEl.parentNode.removeChild(loadingEl);
}}
async function fetchJson(path) {{
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${{path}}: ${{response.status}}`);
  return await response.json();
}}
async function ensureDataLoaded() {{
  if (dataLoaded) return;
  if (loadingPromise) return loadingPromise;
  loadingPromise = (async () => {{
    setLoading(true, 'ルートパフォーマンス出力を読み込み中...');
    for (let i = 0; i < MANIFEST.routes.length; i++) {{
      const route = MANIFEST.routes[i];
      setLoading(true, `ルートデータ読み込み中 ${{i + 1}}/${{MANIFEST.routes.length}}`);
      const payload = await fetchJson(route.json);
      const selected = new Map();
      payload.summary.forEach(r => {{
        const period = String(r.period || '');
        const hour = Number(r.hour);
        if (period === state.period && state.hours.has(hour)) {{
          selected.set(`${{r.bucket_index}}|${{r.direction}}|${{hour}}`, r);
        }}
      }});
      payload.summaryIndex = selected;
      delete payload.summary;
      DATA.push(payload);
    }}
    dataLoaded = true;
    loadedSelectionKey = selectionKey();
    setLoading(false);
  }})();
  return loadingPromise;
}}
async function reloadSelectedData() {{
  if (!dataLoaded) return ensureDataLoaded();
  setLoading(true, '選択条件のデータを再集計中...');
  for (let i = 0; i < MANIFEST.routes.length; i++) {{
    const route = MANIFEST.routes[i];
    const payload = DATA[i];
    const fresh = await fetchJson(route.json);
    const selected = new Map();
    fresh.summary.forEach(r => {{
      const period = String(r.period || '');
      const hour = Number(r.hour);
      if (period === state.period && state.hours.has(hour)) {{
        selected.set(`${{r.bucket_index}}|${{r.direction}}|${{hour}}`, r);
      }}
    }});
    payload.summaryIndex = selected;
  }}
  loadedSelectionKey = selectionKey();
  setLoading(false);
}}
function selectionKey() {{
  return `${{state.period}}|${{Array.from(state.hours).sort((a,b) => a-b).join(',')}}`;
}}
function periodLabel(period) {{
  period = String(period);
  if (/^\\d{{8}}$/.test(period)) return `${{period.slice(0,4)}}-${{period.slice(4,6)}}-${{period.slice(6,8)}}`;
  return period;
}}
function monthLabel(month) {{
  return `${{month.slice(0,4)}}-${{month.slice(4,6)}}`;
}}
function renderMonthbar() {{
  const el = document.getElementById('monthbar');
  el.innerHTML = '';
  if (!HIT_MONTHS.length) {{
    el.textContent = '日付データなし';
    return;
  }}
  HIT_MONTHS.forEach((month, idx) => {{
    const b = document.createElement('button');
    b.textContent = monthLabel(month);
    b.onclick = () => {{ monthIndex = idx; renderMonthbar(); renderCalendars(); }};
    if (idx >= monthIndex && idx < monthIndex + 4) b.className = 'active';
    el.appendChild(b);
  }});
}}
function renderCalendars() {{
  const el = document.getElementById('calendars');
  el.innerHTML = '';
  HIT_MONTHS.slice(monthIndex, monthIndex + 4).forEach(month => {{
    const year = Number(month.slice(0, 4));
    const mon = Number(month.slice(4, 6));
    const first = new Date(year, mon - 1, 1);
    const daysInMonth = new Date(year, mon, 0).getDate();
    const box = document.createElement('div');
    box.className = 'calendar';
    box.innerHTML = `<div class="monthTitle">${{year}}年${{mon}}月</div><div class="week"><span>日</span><span>月</span><span>火</span><span>水</span><span>木</span><span>金</span><span>土</span></div>`;
    const days = document.createElement('div');
    days.className = 'days';
    for (let i = 0; i < first.getDay(); i++) days.appendChild(document.createElement('span'));
    for (let day = 1; day <= daysInMonth; day++) {{
      const token = `${{month}}${{String(day).padStart(2, '0')}}`;
      const b = document.createElement('button');
      b.className = 'day';
      b.textContent = String(day);
      if (HIT_DATES.has(token)) {{
        b.className += ' hit';
        b.onclick = () => {{ state.period = token; renderCalendars(); renderSelectionStatus(); redraw(); }};
        if (state.period === token) b.className += ' active';
      }} else {{
        b.disabled = true;
      }}
      days.appendChild(b);
    }}
    box.appendChild(days);
    el.appendChild(box);
  }});
}}
function offsetPoint(a, b, side, meters) {{
  const lat = (a.lat + b.lat) / 2;
  const mLat = 111320;
  const mLon = 111320 * Math.cos(lat * Math.PI / 180);
  const dx = (b.lon - a.lon) * mLon;
  const dy = (b.lat - a.lat) * mLat;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len * meters * side;
  const ny = dx / len * meters * side;
  return [
    [a.lat + ny / mLat, a.lon + nx / mLon],
    [b.lat + ny / mLat, b.lon + nx / mLon],
  ];
}}
function initFallbackMap() {{
  const mapEl = document.getElementById('map');
  mapEl.innerHTML = '<svg id="fallbackSvg" xmlns="http://www.w3.org/2000/svg"><g id="fallbackLayer"></g></svg><div style="position:absolute;right:12px;bottom:12px;background:#ffffffdd;padding:4px 8px;border-radius:4px;font-size:12px;">背景地図なし / ルート形状のみ</div>';
  const bounds = MANIFEST.bounds || {{}};
  const minLon = Number(bounds.min_lon ?? 139.7);
  const maxLon = Number(bounds.max_lon ?? 139.8);
  const minLat = Number(bounds.min_lat ?? 35.6);
  const maxLat = Number(bounds.max_lat ?? 35.8);
  const minY = -maxLat;
  const maxY = -minLat;
  const padX = Math.max((maxLon - minLon) * 0.08, 0.001);
  const padY = Math.max((maxY - minY) * 0.08, 0.001);
  fallback = {{
    svg: document.getElementById('fallbackSvg'),
    layer: document.getElementById('fallbackLayer'),
    view: {{x:minLon - padX, y:minY - padY, w:(maxLon - minLon) + padX * 2, h:(maxY - minY) + padY * 2}},
    drag: null,
  }};
  updateFallbackView();
  fallback.svg.addEventListener('wheel', e => {{
    e.preventDefault();
    const p = svgPoint(e);
    const factor = e.deltaY < 0 ? 0.85 : 1.15;
    fallback.view.x = p.x - (p.x - fallback.view.x) * factor;
    fallback.view.y = p.y - (p.y - fallback.view.y) * factor;
    fallback.view.w *= factor;
    fallback.view.h *= factor;
    updateFallbackView();
  }}, {{passive:false}});
  fallback.svg.addEventListener('pointerdown', e => {{
    fallback.svg.setPointerCapture(e.pointerId);
    fallback.drag = {{x:e.clientX, y:e.clientY, vx:fallback.view.x, vy:fallback.view.y}};
  }});
  fallback.svg.addEventListener('pointermove', e => {{
    if (!fallback.drag) return;
    fallback.view.x = fallback.drag.vx - (e.clientX - fallback.drag.x) * fallback.view.w / fallback.svg.clientWidth;
    fallback.view.y = fallback.drag.vy - (e.clientY - fallback.drag.y) * fallback.view.h / fallback.svg.clientHeight;
    updateFallbackView();
  }});
  fallback.svg.addEventListener('pointerup', () => {{ fallback.drag = null; }});
}}
function svgPoint(e) {{
  const p = fallback.svg.createSVGPoint();
  p.x = e.clientX; p.y = e.clientY;
  return p.matrixTransform(fallback.svg.getScreenCTM().inverse());
}}
function updateFallbackView() {{
  const v = fallback.view;
  fallback.svg.setAttribute('viewBox', `${{v.x}} ${{v.y}} ${{v.w}} ${{v.h}}`);
}}
function clearTrafficLayers() {{
  if (HAS_LEAFLET) {{
    layers.forEach(l => map.removeLayer(l));
  }} else if (fallback) {{
    fallback.layer.innerHTML = '';
  }}
  layers = [];
}}
function addTrafficLine(coords, color, width, tooltip) {{
  if (HAS_LEAFLET) {{
    const line = L.polyline(coords, {{color, weight:width, opacity:.92}}).bindTooltip(tooltip);
    line.addTo(map); layers.push(line);
    return;
  }}
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  line.setAttribute('x1', coords[0][1]);
  line.setAttribute('y1', -coords[0][0]);
  line.setAttribute('x2', coords[1][1]);
  line.setAttribute('y2', -coords[1][0]);
  line.setAttribute('stroke', color);
  line.setAttribute('stroke-width', width);
  line.setAttribute('stroke-linecap', 'round');
  line.setAttribute('opacity', '.92');
  line.setAttribute('vector-effect', 'non-scaling-stroke');
  const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  title.textContent = tooltip.replace(/<br>/g, '\\n');
  line.appendChild(title);
  fallback.layer.appendChild(line);
}}
function aggregateSummary(payload, bucket, direction) {{
  let speedNumerator = 0;
  let freeNumerator = 0;
  let jamNumerator = 0;
  let volume = 0;
  let trips = 0;
  state.hours.forEach(hour => {{
    const row = payload.summaryIndex.get(`${{bucket}}|${{direction}}|${{hour}}`);
    if (!row) return;
    const rowVolume = Number(row.expanded_volume) || 0;
    const rowTrips = Number(row.trip_count) || 0;
    const rowSpeed = Number(row.avg_speed_kmh);
    const rowFree = Number(row.freeflow_speed_kmh);
    const rowJam = Number(row.congested_speed_kmh);
    volume += rowVolume;
    trips += rowTrips;
    if (Number.isFinite(rowSpeed) && rowVolume > 0) speedNumerator += rowSpeed * rowVolume;
    if (Number.isFinite(rowFree) && rowVolume > 0) freeNumerator += rowFree * rowVolume;
    if (Number.isFinite(rowJam) && rowVolume > 0) jamNumerator += rowJam * rowVolume;
  }});
  return {{
    avg_speed_kmh: volume > 0 ? speedNumerator / volume : '',
    freeflow_speed_kmh: volume > 0 ? freeNumerator / volume : '',
    congested_speed_kmh: volume > 0 ? jamNumerator / volume : '',
    expanded_volume: volume || '',
    trip_count: trips || '',
  }};
}}
function speedValue(s) {{
  if (state.speedKind === 'free') return s.freeflow_speed_kmh;
  if (state.speedKind === 'jam') return s.congested_speed_kmh;
  return s.avg_speed_kmh;
}}
const SPEED_BREAKS = [
  [10, '#e60000', '10以下'],
  [20, '#ff7a00', '10-20'],
  [30, '#ffd400', '20-30'],
  [40, '#9acd32', '30-40'],
  [50, '#008000', '40-50'],
  [60, '#00bcd4', '50-60'],
  [70, '#1e90ff', '60-70'],
  [80, '#0057ff', '70-80'],
  [90, '#0000cc', '80-90'],
  [100, '#000080', '90-100'],
  [Infinity, '#4b0082', '100超'],
];
function speedColor(v) {{
  v = Number(v); if (!Number.isFinite(v)) return '#9ca3af';
  return SPEED_BREAKS.find(([limit]) => v <= limit)[1];
}}
const TRIP_COLORS = ['#f3e8ff', '#d8b4fe', '#c084fc', '#9333ea', '#4c1d95'];
const VOLUME_COLORS = ['#dcfce7', '#86efac', '#22c55e', '#15803d', '#064e3b'];
function autoBreaks(maxValue, colors) {{
  maxValue = Math.max(0, Number(maxValue) || 0);
  if (maxValue <= 0) return [];
  const step = Math.max(1, Math.ceil(maxValue / colors.length));
  return colors.map((color, idx) => {{
    const min = idx * step + 1;
    const max = idx === colors.length - 1 ? maxValue : Math.min(maxValue, (idx + 1) * step);
    return {{min, max, color}};
  }}).filter(b => b.min <= maxValue);
}}
function rangedColor(v, breaks) {{
  v = Number(v); if (!Number.isFinite(v) || v <= 0) return '#9ca3af';
  const bucket = breaks.find(b => v <= b.max) || breaks[breaks.length - 1];
  return bucket ? bucket.color : '#9ca3af';
}}
async function redraw() {{
  try {{
    await ensureDataLoaded();
    if (loadedSelectionKey !== selectionKey()) await reloadSelectedData();
  }} catch (err) {{
    setLoading(false);
    alert(`ビューアーデータの読み込みに失敗しました。\\n${{err.message || err}}`);
    return;
  }}
  clearTrafficLayers();
  renderSelectionStatus();
  let maxTrip = 0;
  let maxVolume = 0;
  DATA.forEach(payload => {{
    const pts = payload.points;
    for (let i = 1; i < pts.length; i++) {{
      [['forward', i], ['reverse', i-1]].forEach(([dir, bucket]) => {{
        const s = aggregateSummary(payload, bucket, dir);
        maxTrip = Math.max(maxTrip, Number(s.trip_count) || 0);
        maxVolume = Math.max(maxVolume, Number(s.expanded_volume) || 0);
      }});
    }}
  }});
  const tripRanges = autoBreaks(maxTrip, TRIP_COLORS);
  const volumeRanges = autoBreaks(maxVolume, VOLUME_COLORS);
  DATA.forEach(payload => {{
    const pts = payload.points;
    for (let i = 1; i < pts.length; i++) {{
        const a = pts[i-1], b = pts[i];
        [['forward', 1, i], ['reverse', -1, i-1]].forEach(([dir, side, bucket]) => {{
        const s = aggregateSummary(payload, bucket, dir);
        const value = state.metric === 'speed' ? speedValue(s) : (state.metric === 'trip' ? s.trip_count : s.expanded_volume);
        const color = state.metric === 'speed' ? speedColor(value) : (state.metric === 'trip' ? rangedColor(value, tripRanges) : rangedColor(value, volumeRanges));
        const maxValue = state.metric === 'trip' ? maxTrip : maxVolume;
        const width = state.metric === 'speed' ? 7 : Math.max(5, Math.min(15, 5 + (Number(value) || 0) / Math.max(maxValue, 1) * 10));
        const speedText = Number.isFinite(Number(s.avg_speed_kmh)) ? Number(s.avg_speed_kmh).toFixed(1) : 'なし';
        const freeText = Number.isFinite(Number(s.freeflow_speed_kmh)) ? Number(s.freeflow_speed_kmh).toFixed(1) : 'なし';
        const jamText = Number.isFinite(Number(s.congested_speed_kmh)) ? Number(s.congested_speed_kmh).toFixed(1) : 'なし';
        const volumeText = Number.isFinite(Number(s.expanded_volume)) ? Number(s.expanded_volume).toFixed(1) : 'なし';
        addTrafficLine(offsetPoint(a, b, side, 7), color, width, `${{payload.route}}<br>${{dir === 'forward' ? '順方向（路線左側）' : '逆方向（反対側）'}} bucket=${{bucket}}<br>閑散時速度: ${{freeText}} km/h<br>平均速度: ${{speedText}} km/h<br>渋滞時速度: ${{jamText}} km/h<br>交通量: ${{volumeText}}<br>実トリップ数: ${{s.trip_count || 'なし'}}`);
      }});
    }}
  }});
  document.getElementById('legend').innerHTML = state.metric === 'speed'
    ? SPEED_BREAKS.map(([limit, color, label]) => `<span style="background:${{color}}"></span>${{label}}`).join(' ')
    : (state.metric === 'trip'
      ? (tripRanges.length ? tripRanges.map(b => `<span style="background:${{b.color}}"></span>${{b.min}}-${{b.max}}トリップ/日`).join(' ') : '<span style="background:#9ca3af"></span>トリップなし')
      : (volumeRanges.length ? volumeRanges.map(b => `<span style="background:${{b.color}}"></span>${{b.min}}-${{b.max}}台/日`).join(' ') : '<span style="background:#9ca3af"></span>交通量なし'));
}}
function renderSelectionStatus() {{
  document.getElementById('selectedDate').textContent = state.period ? `対象日: ${{periodLabel(state.period)}}` : '対象日なし';
  const hourText = Array.from(state.hours).sort((a,b) => a-b).map(h => `${{String(h).padStart(2,'0')}}:00`).join(', ');
  document.getElementById('selectedHours').textContent = `対象時間: ${{hourText || '未選択'}}`;
}}
function buttons(id, values, key) {{
  const el = document.getElementById(id);
  el.innerHTML = '';
  values.forEach(v => {{
    const b = document.createElement('button');
    b.textContent = v.label ?? v;
    b.onclick = () => {{ state[key] = v.value ?? v; buttons(id, values, key); renderCalendars(); renderSelectionStatus(); redraw(); }};
    if (String(state[key]) === String(v.value ?? v)) b.className = 'active';
    el.appendChild(b);
  }});
}}
function renderHours() {{
  const el = document.getElementById('hoursList');
  el.innerHTML = '';
  for (let i = 0; i < 24; i++) {{
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = `${{String(i).padStart(2,'0')}}:00 - ${{String(i).padStart(2,'0')}}:59`;
    opt.selected = state.hours.has(i);
    el.appendChild(opt);
  }}
  el.onchange = () => {{
    state.hours = new Set(Array.from(el.selectedOptions).map(o => Number(o.value)));
    renderSelectionStatus();
    redraw();
  }};
  const tools = document.getElementById('hourtools');
  tools.innerHTML = '';
  [
    ['全時間ON', Array.from({{length:24}}, (_, i) => i)],
    ['朝夕', [7,8,9,17,18,19]],
    ['3時間: 7-9', [7,8,9]],
    ['全時間OFF', []],
  ].forEach(([label, hours]) => {{
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = () => {{ state.hours = new Set(hours); renderHours(); renderSelectionStatus(); redraw(); }};
    tools.appendChild(b);
  }});
}}
function moveHourSelection(delta, extend) {{
  const selected = Array.from(state.hours).sort((a,b) => a-b);
  if (!selected.length) selected.push(8);
  if (extend) {{
    const target = delta < 0 ? selected[0] - 1 : selected[selected.length - 1] + 1;
    if (target >= 0 && target <= 23) state.hours.add(target);
  }} else {{
    const target = Math.max(0, Math.min(23, selected[0] + delta));
    state.hours = new Set([target]);
  }}
  renderHours();
  renderSelectionStatus();
  document.getElementById('hoursList').focus();
  redraw();
}}
function selectedHoursLabel() {{
  return Array.from(state.hours).sort((a,b) => a-b).map(h => String(h).padStart(2,'0')).join(',');
}}
function haversineKm(a, b) {{
  const r = 6371.0088;
  const toRad = d => Number(d) * Math.PI / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * r * Math.asin(Math.min(1, Math.sqrt(h)));
}}
function segmentDistanceKm(points, index) {{
  const a = points[index - 1];
  const b = points[index];
  const kpDistance = Math.abs(Number(b.kp_km) - Number(a.kp_km));
  return Number.isFinite(kpDistance) && kpDistance > 0 ? kpDistance : haversineKm(a, b);
}}
function metricSpeedValue(summary, metric) {{
  if (metric === 'free') return summary.freeflow_speed_kmh;
  if (metric === 'jam') return summary.congested_speed_kmh;
  return summary.avg_speed_kmh;
}}
function routeTravelTime(payload, direction, metric) {{
  let minutes = 0;
  let distanceKm = 0;
  const points = payload.points;
  for (let i = 1; i < points.length; i++) {{
    const bucket = direction === 'forward' ? i : i - 1;
    const speed = Number(metricSpeedValue(aggregateSummary(payload, bucket, direction), metric));
    if (!Number.isFinite(speed) || speed <= 0) continue;
    const distance = segmentDistanceKm(points, i);
    if (!Number.isFinite(distance) || distance <= 0) continue;
    distanceKm += distance;
    minutes += distance / speed * 60;
  }}
  return {{distanceKm, minutes}};
}}
function exportRows(direction, metric) {{
  const rows = [['route','bucket_index','KP[km]','lon','lat','value']];
  DATA.forEach(payload => {{
    payload.points.forEach(point => {{
      const s = aggregateSummary(payload, point.bucket_index, direction);
      let value = '';
      if (metric === 'trip') value = s.trip_count || '';
      else if (metric === 'volume') value = s.expanded_volume || '';
      else if (metric === 'free') value = s.freeflow_speed_kmh || '';
      else if (metric === 'jam') value = s.congested_speed_kmh || '';
      else value = s.avg_speed_kmh || '';
      rows.push([payload.route, point.bucket_index, point.kp_km, point.lon, point.lat, value]);
    }});
  }});
  if (['free', 'avg', 'jam'].includes(metric)) {{
    rows.push([]);
    rows.push(['平均所要時間(分)', 'route', 'direction', '対象距離[km]', '', 'minutes']);
    let totalMinutes = 0;
    let totalDistance = 0;
    DATA.forEach(payload => {{
      const t = routeTravelTime(payload, direction, metric);
      totalMinutes += t.minutes;
      totalDistance += t.distanceKm;
      rows.push(['平均所要時間(分)', payload.route, direction, Number(t.distanceKm.toFixed(3)), '', t.minutes ? Number(t.minutes.toFixed(2)) : '']);
    }});
    rows.push(['平均所要時間(分)', '全路線合計', direction, Number(totalDistance.toFixed(3)), '', totalMinutes ? Number(totalMinutes.toFixed(2)) : '']);
  }}
  return rows;
}}
function exportWorkbook() {{
  const conditions = [
    ['項目', '値'],
    ['抽出日', periodLabel(state.period)],
    ['抽出日token', state.period],
    ['抽出時間', selectedHoursLabel()],
    ['速度集計', '速度系は選択時間の時間交通量で加重平均'],
    ['出力時刻', new Date().toLocaleString()],
  ];
  const sheets = [
    ['抽出条件', conditions],
    ['トリップ(F)', exportRows('forward', 'trip')],
    ['交通量(F)', exportRows('forward', 'volume')],
    ['閑散時速度(F)', exportRows('forward', 'free')],
    ['平均速度(F)', exportRows('forward', 'avg')],
    ['渋滞時速度(F)', exportRows('forward', 'jam')],
    ['トリップ(R)', exportRows('reverse', 'trip')],
    ['交通量(R)', exportRows('reverse', 'volume')],
    ['閑散時速度(R)', exportRows('reverse', 'free')],
    ['平均速度(R)', exportRows('reverse', 'avg')],
    ['渋滞時速度(R)', exportRows('reverse', 'jam')],
  ];
  downloadXlsx('route_performance.xlsx', sheets);
}}
function xmlEscape(value) {{
  return String(value ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}
function colName(n) {{
  let s = '';
  while (n > 0) {{
    const m = (n - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    n = Math.floor((n - 1) / 26);
  }}
  return s;
}}
function sheetXml(rows) {{
  const body = rows.map((row, rIdx) => {{
    const cells = row.map((value, cIdx) => {{
      const ref = `${{colName(cIdx + 1)}}${{rIdx + 1}}`;
      if (typeof value === 'number' && Number.isFinite(value)) return `<c r="${{ref}}"><v>${{value}}</v></c>`;
      return `<c r="${{ref}}" t="inlineStr"><is><t>${{xmlEscape(value)}}</t></is></c>`;
    }}).join('');
    return `<row r="${{rIdx + 1}}">${{cells}}</row>`;
  }}).join('');
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${{body}}</sheetData></worksheet>`;
}}
function workbookXml(sheets) {{
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${{sheets.map((s, i) => `<sheet name="${{xmlEscape(s[0])}}" sheetId="${{i + 1}}" r:id="rId${{i + 1}}"/>`).join('')}}</sheets></workbook>`;
}}
function workbookRels(sheets) {{
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${{sheets.map((s, i) => `<Relationship Id="rId${{i + 1}}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${{i + 1}}.xml"/>`).join('')}}</Relationships>`;
}}
function contentTypes(sheets) {{
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${{sheets.map((s, i) => `<Override PartName="/xl/worksheets/sheet${{i + 1}}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}}</Types>`;
}}
function rootRels() {{
  return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>';
}}
const CRC_TABLE = (() => {{
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {{
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    table[n] = c >>> 0;
  }}
  return table;
}})();
function crc32(bytes) {{
  let c = 0xffffffff;
  bytes.forEach(b => c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8));
  return (c ^ 0xffffffff) >>> 0;
}}
function u16(v) {{ return [v & 255, (v >>> 8) & 255]; }}
function u32(v) {{ return [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255]; }}
function concatBytes(parts) {{
  const length = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(length);
  let offset = 0;
  parts.forEach(part => {{ out.set(part, offset); offset += part.length; }});
  return out;
}}
function zipStore(files) {{
  const enc = new TextEncoder();
  const chunks = [];
  const central = [];
  let offset = 0;
  files.forEach(file => {{
    const name = enc.encode(file.name);
    const data = enc.encode(file.content);
    const crc = crc32(data);
    const localHeader = new Uint8Array([0x50,0x4b,0x03,0x04, ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(name.length), ...u16(0)]);
    const local = concatBytes([localHeader, name, data]);
    chunks.push(local);
    const centralHeader = new Uint8Array([0x50,0x4b,0x01,0x02, ...u16(20), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(name.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(offset)]);
    central.push(concatBytes([centralHeader, name]));
    offset += local.length;
  }});
  const centralOffset = offset;
  central.forEach(c => {{ chunks.push(c); offset += c.length; }});
  chunks.push(new Uint8Array([0x50,0x4b,0x05,0x06, ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length), ...u32(offset - centralOffset), ...u32(centralOffset), ...u16(0)]));
  return new Blob(chunks, {{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}});
}}
function downloadXlsx(filename, sheets) {{
  const files = [
    {{name:'[Content_Types].xml', content:contentTypes(sheets)}},
    {{name:'_rels/.rels', content:rootRels()}},
    {{name:'xl/workbook.xml', content:workbookXml(sheets)}},
    {{name:'xl/_rels/workbook.xml.rels', content:workbookRels(sheets)}},
    ...sheets.map((s, i) => ({{name:`xl/worksheets/sheet${{i + 1}}.xml`, content:sheetXml(s[1])}})),
  ];
  const a = document.createElement('a');
  a.href = URL.createObjectURL(zipStore(files));
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}}
buttons('metric', [{{label:'速度', value:'speed'}}, {{label:'トリップ(トリップ/日)', value:'trip'}}, {{label:'交通量(台/日)', value:'volume'}}], 'metric');
buttons('speedKind', [{{label:'閑散時', value:'free'}}, {{label:'平均', value:'avg'}}, {{label:'渋滞時', value:'jam'}}], 'speedKind');
renderMonthbar();
renderCalendars();
renderHours();
document.getElementById('redrawButton').onclick = redraw;
document.getElementById('exportButton').onclick = exportWorkbook;
document.getElementById('hourUp').onclick = e => moveHourSelection(-1, e.shiftKey);
document.getElementById('hourDown').onclick = e => moveHourSelection(1, e.shiftKey);
document.getElementById('hoursList').onkeydown = e => {{
  if (e.key === 'ArrowUp') {{
    e.preventDefault();
    moveHourSelection(-1, e.shiftKey);
  }} else if (e.key === 'ArrowDown') {{
    e.preventDefault();
    moveHourSelection(1, e.shiftKey);
  }}
}};
redraw();
</script>
</body>
</html>"""
    html_path.write_text(html, encoding="utf-8")
    return html_path


def discover_viewer_results(output_dir: str | Path) -> list[dict[str, object]]:
    out_dir = Path(output_dir)
    return [{"viewer_json": str(path)} for path in sorted(out_dir.glob("*/*_viewer.json"))]


def build_viewer_from_output(output_dir: str | Path) -> Path:
    results = discover_viewer_results(output_dir)
    if not results:
        raise FileNotFoundError(f"viewer JSONが見つかりません: {Path(output_dir)}")
    return build_viewer(output_dir, results)


def analyze_project(
    project_dir: str | Path,
    recursive: bool = True,
    allowed_dates: Optional[set[str]] = None,
    allowed_hours: Optional[set[int]] = None,
    expansion_factors: Optional[dict[str, float]] = None,
    max_off_route_m: float = MAX_OFF_ROUTE_M,
    progress_callback: ProgressCallback = None,
) -> dict[str, object]:
    input_dir, route_dir, output_dir = resolve_project_paths(project_dir)
    route_paths = list_route_csvs(route_dir)
    if not route_paths:
        raise FileNotFoundError(f"ルートCSVが見つかりません: {route_dir}")
    files = list_input_csvs(input_dir, recursive)
    routes = [load_route(path) for path in route_paths]
    bbox_margin_deg = max(ROUTE_BBOX_MARGIN_DEG, float(max_off_route_m) / 111_320 + 0.0002)
    route_bounds = [
        (
            min(route.lats) - bbox_margin_deg,
            max(route.lats) + bbox_margin_deg,
            min(route.lons) - bbox_margin_deg,
            max(route.lons) + bbox_margin_deg,
        )
        for route in routes
    ]
    factors: list[float] = []
    for route, route_path in zip(routes, route_paths):
        factor = 1.0
        if expansion_factors:
            factor = float(
                expansion_factors.get(
                    route.name,
                    expansion_factors.get(route_path.stem, expansion_factors.get(route_path.name, 1.0)),
                )
            )
        factors.append(factor)

    aggregators = [RouteAggregator(route, factor) for route, factor in zip(routes, factors)]
    projected_by_route: list[dict[str, list[tuple[datetime, float, float]]]] = [defaultdict(list) for _ in routes]
    valid_points_by_route = [0 for _ in routes]
    total_rows = 0

    def emit(percent: int, message: str, stats: dict[str, object]) -> None:
        if progress_callback:
            progress_callback(percent, message, stats)

    def csv_scan_percent(file_index: int) -> int:
        if not files:
            return 60
        return max(1, min(60, math.ceil(60 * file_index / len(files))))

    emit(
        0,
        f"CSV走査を開始します: {len(files)}ファイル / {len(routes)}路線",
        {
            "phase": "CSV走査",
            "total_files": len(files),
            "total_routes": len(routes),
            "current_file": 0,
            "current_route": 0,
            "route_names": [route.name for route in routes],
            "route_valid_points": list(valid_points_by_route),
        },
    )
    for file_index, path in enumerate(files, start=1):
        emit(
            csv_scan_percent(file_index),
            f"CSV走査中 {file_index}/{len(files)}",
            {
                "phase": "CSV走査",
                "current_file": file_index,
                "total_files": len(files),
                "current_route": 0,
                "total_routes": len(routes),
                "rows": total_rows,
                "valid_points": sum(valid_points_by_route),
                "route_names": [route.name for route in routes],
                "route_valid_points": list(valid_points_by_route),
                "events": sum(agg.event_count for agg in aggregators),
            },
        )
        for row_index, row in enumerate(read_csv_rows(path)):
            total_rows += 1
            try:
                lon = float(row[COL_LON])
                lat = float(row[COL_LAT])
            except Exception:
                continue
            dt = parse_datetime_from_row(row)
            if dt is None:
                continue
            if allowed_dates is not None and dt.strftime("%Y%m%d") not in allowed_dates:
                continue
            trip = trip_key(path, row, row_index)
            for route_index, route in enumerate(routes):
                min_lat, max_lat, min_lon, max_lon = route_bounds[route_index]
                if lat < min_lat or lat > max_lat or lon < min_lon or lon > max_lon:
                    continue
                projection = route.project(lon, lat)
                if projection.off_m > max_off_route_m:
                    continue
                projected_by_route[route_index][trip].append((dt, projection.s_m, projection.off_m))
                valid_points_by_route[route_index] += 1
            if total_rows % 20000 == 0:
                emit(
                    csv_scan_percent(file_index),
                    f"CSV走査中 {file_index}/{len(files)}",
                    {
                        "phase": "CSV走査",
                        "current_file": file_index,
                        "total_files": len(files),
                        "current_route": 0,
                        "total_routes": len(routes),
                        "rows": total_rows,
                        "valid_points": sum(valid_points_by_route),
                        "route_names": [route.name for route in routes],
                        "route_valid_points": list(valid_points_by_route),
                        "events": sum(agg.event_count for agg in aggregators),
                    },
                )

    results: list[dict[str, object]] = []
    for route_index, (route, aggregator, projected, factor) in enumerate(zip(routes, aggregators, projected_by_route, factors), start=1):
        route_start = 60 + int(35 * (route_index - 1) / max(len(routes), 1))
        route_end = 60 + int(35 * route_index / max(len(routes), 1))

        def route_progress(_percent: int, message: str, stats: dict) -> None:
            stats = dict(stats or {})
            stats.update(
                {
                    "phase": "バケツ投入",
                    "current_route": route_index,
                    "total_routes": len(routes),
                    "current_route_name": route.name,
                    "current_file": len(files),
                    "total_files": len(files),
                    "rows": total_rows,
                    "valid_points": valid_points_by_route[route_index - 1],
                    "route_names": [route.name for route in routes],
                    "route_valid_points": list(valid_points_by_route),
                    "events": aggregator.event_count,
                }
            )
            emit(route_start, f"[{route_index}/{len(routes)}] {route.name}: {message}", stats)

        emit(
            route_start,
            f"バケツ投入中 {route_index}/{len(routes)}: {route.name}",
            {
                "phase": "バケツ投入",
                "current_route": route_index,
                "total_routes": len(routes),
                "current_route_name": route.name,
                "current_file": len(files),
                "total_files": len(files),
                "rows": total_rows,
                "valid_points": valid_points_by_route[route_index - 1],
                "route_names": [route.name for route in routes],
                "route_valid_points": list(valid_points_by_route),
                "events": aggregator.event_count,
            },
        )
        results.append(
            finalize_projected_route(
                route,
                aggregator,
                projected,
                output_dir,
                allowed_hours=allowed_hours,
                valid_points=valid_points_by_route[route_index - 1],
                expansion_factor=factor,
                progress_callback=route_progress,
            )
        )
        emit(
            route_end,
            f"出力完了 {route_index}/{len(routes)}: {route.name}",
            {
                "phase": "出力",
                "current_route": route_index,
                "total_routes": len(routes),
                "current_route_name": route.name,
                "current_file": len(files),
                "total_files": len(files),
                "rows": total_rows,
                "valid_points": valid_points_by_route[route_index - 1],
                "route_names": [route.name for route in routes],
                "route_valid_points": list(valid_points_by_route),
                "events": results[-1]["events"],
                "trips": results[-1]["trips"],
            },
        )
    emit(
        98,
        "ビューアを作成中",
        {
            "phase": "ビューア作成",
            "current_file": len(files),
            "total_files": len(files),
            "current_route": len(routes),
            "total_routes": len(routes),
            "route_names": [route.name for route in routes],
            "route_valid_points": list(valid_points_by_route),
        },
    )
    viewer = build_viewer(output_dir, results)
    emit(
        100,
        "解析完了",
        {
            "phase": "解析完了",
            "current_file": len(files),
            "total_files": len(files),
            "current_route": len(routes),
            "total_routes": len(routes),
            "rows": total_rows,
            "route_names": [route.name for route in routes],
            "route_valid_points": list(valid_points_by_route),
        },
    )
    return {"project_dir": str(project_dir), "input_dir": str(input_dir), "route_dir": str(route_dir), "output_dir": str(output_dir), "viewer": str(viewer), "results": results}


def parse_dates(value: str | None) -> Optional[set[str]]:
    if not value:
        return None
    return {token for token in (normalize_date_token(part) for part in value.split(",")) if token}


def parse_hours(value: str | None) -> Optional[set[int]]:
    if not value:
        return None
    hours: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = [int(x) for x in part.split("-", 1)]
            hours.update(range(max(0, a), min(23, b) + 1))
        else:
            hours.add(int(part))
    return {h for h in hours if 0 <= h <= 23}


def main() -> None:
    parser = argparse.ArgumentParser(description="ETC2.0 route performance aggregation")
    parser.add_argument("--project", help="Project folder containing step 10 route and step 20 trip outputs")
    parser.add_argument("--build-viewer", help="Existing 30_route_performance output folder; rebuild viewer without reanalysis")
    parser.add_argument("--dates", help="Target dates, comma-separated YYYYMMDD")
    parser.add_argument("--hours", help="Target hours, comma-separated or ranges, e.g. 7,8,17-19")
    parser.add_argument("--max-off-route-m", type=float, default=MAX_OFF_ROUTE_M, help="Maximum distance from route in meters (default: 30)")
    parser.add_argument("--recursive", action="store_true", default=True)
    args = parser.parse_args()
    if args.build_viewer:
        viewer = build_viewer_from_output(args.build_viewer)
        print(json.dumps({"viewer": str(viewer)}, ensure_ascii=False, indent=2))
        return
    if not args.project:
        parser.error("--project or --build-viewer is required")
    result = analyze_project(args.project, args.recursive, parse_dates(args.dates), parse_hours(args.hours), max_off_route_m=args.max_off_route_m)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
