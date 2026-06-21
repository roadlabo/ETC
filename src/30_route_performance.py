"""Route performance aggregation for ETC2.0 trip CSVs.

The tool reads route bucket points created by step 10 and second-screened trip
CSVs created by step 20.  It projects each trip segment to every route, fills
all crossed buckets without gaps, and guarantees that one trip contributes at
most one value to the same route bucket.
"""

from __future__ import annotations

import argparse
import csv
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
    "20_隨ｬ・偵せ繧ｯ繝ｪ繝ｼ繝九Φ繧ｰ(繝ｫ繝ｼ繝・",
    "20_隨ｬ・偵せ繧ｯ繝ｪ繝ｼ繝九Φ繧ｰ",
]
OUTPUT_DIR_NAME = "30_route_performance"

ROUTE_LON_COL = 14
ROUTE_LAT_COL = 15
COL_OPERATION_DATE = 2
COL_TIME = 6
COL_TRIP_NO = 8
COL_LON = 14
COL_LAT = 15

MAX_OFF_ROUTE_M = 35.0
MAX_SEGMENT_DISTANCE_M = 350.0
MAX_SEGMENT_TIME_S = 600.0
MIN_SEGMENT_DISTANCE_M = 1.0
MAX_SPEED_KMH = 180.0
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
        self.events: list[Event] = []

    def add_event(self, event: Event) -> bool:
        unique_key = (event.trip, event.bucket_idx)
        if unique_key in self.seen_trip_bucket:
            return False
        self.seen_trip_bucket.add(unique_key)
        self.events.append(event)
        sec = event.pass_dt.hour * 3600 + event.pass_dt.minute * 60 + event.pass_dt.second + event.pass_dt.microsecond / 1_000_000
        for period in period_keys(event.pass_dt):
            key = (event.direction, event.bucket_idx, period, event.pass_dt.hour)
            self.speed_values[key].append(event.speed_kmh)
            self.time_values[key].append(sec)
            self.counts[key] += 1
        return True

    def summary_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for i, kp in enumerate(self.route.kp_m):
            for direction in DIRECTIONS:
                for period in PERIODS:
                    for hour in range(24):
                        key = (direction, i, period, hour)
                        speeds = self.speed_values.get(key, [])
                        times = self.time_values.get(key, [])
                        count = self.counts.get(key, 0)
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
                                "hour": hour,
                                "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else "",
                                "median_speed_kmh": round(statistics.median(speeds), 1) if speeds else "",
                                "trip_count": count if count else "",
                                "expanded_volume": round(count * self.expansion_factor, 1) if count else "",
                                "avg_pass_time": seconds_to_hhmmss(sum(times) / len(times)) if times else "",
                            }
                        )
        return rows

    def pivot(self, direction: str, metric: str) -> pd.DataFrame:
        columns = [f"{period}_{hour:02d}" for period in PERIODS for hour in range(24)]
        data: list[dict[str, object]] = []
        for i, kp in enumerate(self.route.kp_m):
            row: dict[str, object] = {
                "bucket_index": i,
                "KP[km]": round(kp / 1000, KP_DECIMALS),
                "lon": self.route.lons[i],
                "lat": self.route.lats[i],
            }
            for period in PERIODS:
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
    out_dir = project / OUTPUT_DIR_NAME
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


def analyze_route(
    input_dir: str | Path,
    route_path: str | Path,
    output_dir: str | Path,
    recursive: bool = True,
    allowed_dates: Optional[set[str]] = None,
    allowed_hours: Optional[set[int]] = None,
    expansion_factor: float = 1.0,
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
            "events": len(aggregator.events),
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
            if projection.off_m > MAX_OFF_ROUTE_M:
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

    route_dir = Path(output_dir) / safe_name(route.name)
    route_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = route_dir / f"{safe_name(route.name)}_performance.csv"
    event_csv = route_dir / f"{safe_name(route.name)}_events.csv"
    xlsx_path = route_dir / f"{safe_name(route.name)}_performance.xlsx"
    json_path = route_dir / f"{safe_name(route.name)}_viewer.json"

    summary_df = pd.DataFrame(aggregator.summary_rows())
    events_df = pd.DataFrame([event.__dict__ | {"pass_dt": event.pass_dt.isoformat()} for event in aggregator.events])
    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    events_df.to_csv(event_csv, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="summary_long", index=False)
        events_df.to_excel(writer, sheet_name="events", index=False)
        for direction in DIRECTIONS:
            aggregator.pivot(direction, "speed").to_excel(writer, sheet_name=f"{DIRECTION_LABEL[direction]}_速度", index=False)
            aggregator.pivot(direction, "volume").to_excel(writer, sheet_name=f"{DIRECTION_LABEL[direction]}_交通量", index=False)
            aggregator.pivot(direction, "count").to_excel(writer, sheet_name=f"{DIRECTION_LABEL[direction]}_実トリップ数", index=False)
            aggregator.pivot(direction, "time").to_excel(writer, sheet_name=f"{DIRECTION_LABEL[direction]}_通過時刻", index=False)

    viewer_payload = {
        "route": route.name,
        "expansion_factor": expansion_factor,
        "points": [
            {"bucket_index": i, "kp_km": round(kp / 1000, KP_DECIMALS), "lat": route.lats[i], "lon": route.lons[i]}
            for i, kp in enumerate(route.kp_m)
        ],
        "summary": summary_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(viewer_payload, ensure_ascii=False), encoding="utf-8")

    progress(100, "出力完了", trips=len(trips), skipped_segments=skipped)
    return {
        "route": route.name,
        "route_path": str(route.path),
        "xlsx": str(xlsx_path),
        "summary_csv": str(summary_csv),
        "events_csv": str(event_csv),
        "viewer_json": str(json_path),
        "events": len(aggregator.events),
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
    payloads = []
    for result in results:
        json_path = Path(str(result["viewer_json"]))
        if json_path.exists():
            payloads.append(json.loads(json_path.read_text(encoding="utf-8")))
    all_points = [p for payload in payloads for p in payload["points"]]
    center_lat = sum(p["lat"] for p in all_points) / len(all_points) if all_points else 35.6812
    center_lon = sum(p["lon"] for p in all_points) / len(all_points) if all_points else 139.7671
    html_path = out_dir / "30_route_performance_viewer.html"
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>30 Route Performance Viewer</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map {{ height:100%; margin:0; font-family: "Segoe UI", "Meiryo UI", sans-serif; }}
    .panel {{ position:absolute; z-index:1000; left:12px; top:12px; width:min(560px, calc(100vw - 24px)); background:#ffffffee; border-radius:8px; box-shadow:0 8px 24px #0003; padding:10px; }}
    .row {{ display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-top:6px; }}
    button {{ border:1px solid #b8c2cc; background:#fff; border-radius:6px; padding:5px 8px; cursor:pointer; }}
    button.active {{ background:#0f766e; color:#fff; border-color:#0f766e; }}
    .legend span {{ display:inline-block; width:14px; height:10px; margin-right:4px; }}
  </style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <b>30 Route Performance Viewer</b>
  <div class="row" id="metric"></div>
  <div class="row" id="period"></div>
  <div class="row" id="hours"></div>
  <div class="row legend" id="legend"></div>
</div>
<script>
const DATA = {json.dumps(payloads, ensure_ascii=False)};
const map = L.map('map').setView([{center_lat:.7f}, {center_lon:.7f}], 13);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19, attribution: '&copy; OpenStreetMap' }}).addTo(map);
const state = {{metric:'speed', period:'平日', hour:8}};
let layers = [];
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
function findSummary(payload, bucket, direction) {{
  return payload.summary.find(r => r.bucket_index === bucket && r.direction === direction && r.period === state.period && Number(r.hour) === state.hour) || {{}};
}}
function speedColor(v) {{
  v = Number(v); if (!Number.isFinite(v)) return '#9ca3af';
  if (v >= 45) return '#16a34a'; if (v >= 30) return '#eab308'; if (v >= 15) return '#f97316'; return '#dc2626';
}}
function volumeColor(v, maxV) {{
  v = Number(v); if (!Number.isFinite(v) || maxV <= 0) return '#9ca3af';
  const r = v / maxV; if (r >= .75) return '#7c2d12'; if (r >= .5) return '#ea580c'; if (r >= .25) return '#facc15'; return '#22c55e';
}}
function redraw() {{
  layers.forEach(l => map.removeLayer(l)); layers = [];
  let maxVol = 0;
  DATA.forEach(payload => payload.summary.forEach(r => {{
    if (r.period === state.period && Number(r.hour) === state.hour) maxVol = Math.max(maxVol, Number(r.expanded_volume) || 0);
  }}));
  DATA.forEach(payload => {{
    const pts = payload.points;
    for (let i = 1; i < pts.length; i++) {{
      const a = pts[i-1], b = pts[i];
      [['forward', 1, i], ['reverse', -1, i-1]].forEach(([dir, side, bucket]) => {{
        const s = findSummary(payload, bucket, dir);
        const value = state.metric === 'speed' ? s.avg_speed_kmh : s.expanded_volume;
        const color = state.metric === 'speed' ? speedColor(value) : volumeColor(value, maxVol);
        const width = state.metric === 'speed' ? 7 : Math.max(4, Math.min(14, 4 + (Number(value) || 0) / Math.max(maxVol, 1) * 10));
        const line = L.polyline(offsetPoint(a, b, side, 5), {{color, weight:width, opacity:.9}})
          .bindTooltip(`${{payload.route}}<br>${{dir === 'forward' ? '順方向' : '逆方向'}} bucket=${{bucket}}<br>${{state.metric === 'speed' ? '速度' : '交通量'}}: ${{value || 'なし'}}`);
        line.addTo(map); layers.push(line);
      }});
    }}
  }});
  document.getElementById('legend').innerHTML = state.metric === 'speed'
    ? '<span style="background:#16a34a"></span>45km/h以上 <span style="background:#eab308"></span>30-45 <span style="background:#f97316"></span>15-30 <span style="background:#dc2626"></span>15未満'
    : '<span style="background:#22c55e"></span>少 <span style="background:#facc15"></span>中 <span style="background:#ea580c"></span>多 <span style="background:#7c2d12"></span>最多';
}}
function buttons(id, values, key) {{
  const el = document.getElementById(id);
  el.innerHTML = '';
  values.forEach(v => {{
    const b = document.createElement('button');
    b.textContent = v.label ?? v;
    b.onclick = () => {{ state[key] = v.value ?? v; buttons(id, values, key); redraw(); }};
    if (String(state[key]) === String(v.value ?? v)) b.className = 'active';
    el.appendChild(b);
  }});
}}
buttons('metric', [{{label:'速度', value:'speed'}}, {{label:'交通量', value:'volume'}}], 'metric');
buttons('period', ['平日','休日','月','火','水','木','金','土','日'], 'period');
buttons('hours', Array.from({{length:24}}, (_, i) => ({{label:String(i).padStart(2,'0'), value:i}})), 'hour');
redraw();
</script>
</body>
</html>"""
    html_path.write_text(html, encoding="utf-8")
    return html_path


def analyze_project(
    project_dir: str | Path,
    recursive: bool = True,
    allowed_dates: Optional[set[str]] = None,
    allowed_hours: Optional[set[int]] = None,
    expansion_factors: Optional[dict[str, float]] = None,
    progress_callback: ProgressCallback = None,
) -> dict[str, object]:
    input_dir, route_dir, output_dir = resolve_project_paths(project_dir)
    routes = list_route_csvs(route_dir)
    if not routes:
        raise FileNotFoundError(f"ルートCSVが見つかりません: {route_dir}")
    results: list[dict[str, object]] = []
    for route_index, route_path in enumerate(routes, start=1):
        factor = 1.0
        if expansion_factors:
            factor = float(expansion_factors.get(route_path.stem, expansion_factors.get(route_path.name, 1.0)))

        def route_progress(percent: int, message: str, stats: dict) -> None:
            overall = int(((route_index - 1) * 100 + percent) / max(len(routes), 1))
            stats = dict(stats or {})
            stats.update({"current_route": route_index, "total_routes": len(routes), "current_route_name": route_path.name})
            if progress_callback:
                progress_callback(overall, f"[{route_index}/{len(routes)}] {route_path.name}: {message}", stats)

        results.append(
            analyze_route(
                input_dir,
                route_path,
                output_dir,
                recursive=recursive,
                allowed_dates=allowed_dates,
                allowed_hours=allowed_hours,
                expansion_factor=factor,
                progress_callback=route_progress,
            )
        )
    viewer = build_viewer(output_dir, results)
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
    parser.add_argument("--project", required=True, help="Project folder containing step 10 route and step 20 trip outputs")
    parser.add_argument("--dates", help="Target dates, comma-separated YYYYMMDD")
    parser.add_argument("--hours", help="Target hours, comma-separated or ranges, e.g. 7,8,17-19")
    parser.add_argument("--recursive", action="store_true", default=True)
    args = parser.parse_args()
    result = analyze_project(args.project, args.recursive, parse_dates(args.dates), parse_hours(args.hours))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
