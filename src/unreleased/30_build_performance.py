import argparse
import csv
import glob
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Callable, Iterable, Optional

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

INPUT_DIR = r"D:\\path\\to\\trip_extractor_outputs"
ROUTE_PATH = r"D:\\path\\to\\自動運転ルート.csv"
OUTPUT_PATH = r"D:\\path\\to\\route_performance_directional.xlsx"
FOLDER_ROUTE_CANDIDATES = ["10_ルート(Route)データ", "10_ルートデータ"]
FOLDER_SCREENING2 = "20_第２スクリーニング"
FOLDER_OUTPUT = "30_ルートパフォーマンス"

ROUTE_LON_COL = 14
ROUTE_LAT_COL = 15
COL_DATE = 2
COL_TIME = 6
COL_TRIP_NO = 8
COL_LON = 14
COL_LAT = 15

RECURSIVE = True
MAX_OFF_ROUTE_M = 30.0
MAX_SEGMENT_DISTANCE_M = 250.0
MAX_SEGMENT_TIME_S = 300.0
MIN_SEGMENT_DISTANCE_M = 1.0
KP_DECIMALS = 2
ROUND_DIGITS = 1
EARTH_R = 6_371_000.0

PERIODS = ["平日", "休日", "月", "火", "水", "木", "金", "土", "日"]
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]
SHEET_NAMES = {
    "forward_speed": "速度（順方向）",
    "forward_count": "トリップ数（順方向）",
    "forward_time": "GPS時刻（順方向）",
    "reverse_speed": "速度（逆方向）",
    "reverse_count": "トリップ数（逆方向）",
    "reverse_time": "GPS時刻（逆方向）",
}


def deg2rad(d: float) -> float:
    return d * math.pi / 180.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1r, lon1r, lat2r, lon2r = map(deg2rad, (lat1, lon1, lat2, lon2))
    dlat = lat2r - lat1r
    dlon = lon2r - lon1r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def parse_datetime_from_row(row: list[str]) -> Optional[datetime]:
    values = []
    if len(row) > COL_TIME:
        values.append(str(row[COL_TIME]).strip())
    if len(row) > COL_DATE and len(row) > COL_TIME:
        values.append(f"{str(row[COL_DATE]).strip()} {str(row[COL_TIME]).strip()}")
    for text in values:
        if not text:
            continue
        t = text.replace("T", " ").replace("/", "-")
        t = re.sub(r"([+-]\d{2}:?\d{2}|Z)$", "", t).strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%Y%m%d %H:%M:%S", "%Y%m%d %H:%M", "%Y%m%d %H%M%S", "%Y%m%d%H%M%S",
            "%H:%M:%S.%f", "%H:%M:%S", "%H:%M", "%H%M%S", "%H%M",
        ):
            try:
                dt = datetime.strptime(t, fmt)
                if dt.year == 1900 and len(row) > COL_DATE:
                    d = str(row[COL_DATE]).strip()
                    if re.fullmatch(r"\d{8}", d):
                        base = datetime.strptime(d, "%Y%m%d")
                        return base.replace(hour=dt.hour, minute=dt.minute, second=dt.second, microsecond=dt.microsecond)
                return dt
            except ValueError:
                pass
    return None


def seconds_to_hhmmss(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    sec = int(round(sec)) % 86400
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


@dataclass
class RouteModel:
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

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        px, py = self.to_xy(lon, lat)
        best_s, best_d = 0.0, float("inf")
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
        return best_s, best_d


def read_csv_rows(path: Path) -> list[list[str]]:
    last = None
    for enc in ("cp932", "utf-8-sig", "utf-8", "shift_jis"):
        try:
            with path.open("r", newline="", encoding=enc) as f:
                return list(csv.reader(f))
        except Exception as exc:
            last = exc
    raise last or RuntimeError(f"Failed to read {path}")


def build_route_model(route_path: str | Path) -> RouteModel:
    rows = read_csv_rows(Path(route_path))
    lons, lats = [], []
    for row in rows:
        try:
            lon = float(row[ROUTE_LON_COL])
            lat = float(row[ROUTE_LAT_COL])
        except Exception:
            continue
        lons.append(lon); lats.append(lat)
    if len(lons) < 2:
        raise RuntimeError("ルートCSVから2点以上の座標を読み取れませんでした。")
    origin_lon, origin_lat = lons[0], lats[0]
    lat0r = deg2rad(origin_lat)
    xs = [deg2rad(lon - origin_lon) * EARTH_R * math.cos(lat0r) for lon in lons]
    ys = [deg2rad(lat - origin_lat) * EARTH_R for lat in lats]
    kp_m = [0.0]
    for i in range(1, len(lons)):
        kp_m.append(kp_m[-1] + haversine_m(lats[i - 1], lons[i - 1], lats[i], lons[i]))
    print(f"[ROUTE] points={len(kp_m)}, length_km={kp_m[-1] / 1000:.3f}")
    return RouteModel(lons, lats, xs, ys, kp_m, origin_lon, origin_lat)


def list_input_csvs(input_dir: str | Path, recursive: bool) -> list[Path]:
    pattern = "**/*.csv" if recursive else "*.csv"
    return [Path(p) for p in glob.glob(str(Path(input_dir) / pattern), recursive=recursive)]


def find_route_dir(project_dir: str | Path) -> Path:
    project = Path(project_dir)
    for name in FOLDER_ROUTE_CANDIDATES:
        candidate = project / name
        if candidate.exists():
            return candidate
    names = " / ".join(FOLDER_ROUTE_CANDIDATES)
    raise FileNotFoundError(f"ルートフォルダが見つかりません: {names}")


def resolve_project_paths(project_dir: str | Path) -> tuple[Path, Path, Path]:
    project = Path(project_dir)
    if not project.exists():
        raise FileNotFoundError(f"プロジェクトフォルダが見つかりません: {project}")
    input_dir = project / FOLDER_SCREENING2
    if not input_dir.exists():
        raise FileNotFoundError(f"第2スクリーニングフォルダが見つかりません: {input_dir}")
    route_dir = find_route_dir(project)
    output_dir = project / FOLDER_OUTPUT
    output_dir.mkdir(parents=True, exist_ok=True)
    return input_dir, route_dir, output_dir


def list_route_csvs(route_dir: str | Path) -> list[Path]:
    return sorted(Path(route_dir).glob("*.csv"))


def route_output_path(output_dir: str | Path, route_path: str | Path) -> Path:
    stem = Path(route_path).stem
    route_out_dir = Path(output_dir) / stem
    route_out_dir.mkdir(parents=True, exist_ok=True)
    return route_out_dir / f"{stem}_directional.xlsx"


def normalize_date_token(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2)}{m.group(3)}"


def format_date_token(date_token: str) -> str:
    token = normalize_date_token(date_token) or date_token
    if re.fullmatch(r"\d{8}", token):
        return f"{token[:4]}-{token[4:6]}-{token[6:8]}"
    return str(date_token)


def extract_available_dates(input_dir: str | Path, recursive: bool = RECURSIVE) -> list[str]:
    dates: set[str] = set()
    for path in list_input_csvs(input_dir, recursive):
        try:
            rows = read_csv_rows(path)
        except Exception:
            continue
        for row in rows:
            token = normalize_date_token(row[COL_DATE] if len(row) > COL_DATE else "")
            if token is None:
                dt = parse_datetime_from_row(row)
                token = dt.strftime("%Y%m%d") if dt is not None else None
            if token is not None:
                dates.add(token)
    return sorted(dates)


def row_trip_key(path: Path, row: list[str], fallback: int) -> tuple[str, str]:
    trip = row[COL_TRIP_NO].strip() if len(row) > COL_TRIP_NO else ""
    return (path.name, trip or f"ALL-{fallback}")


def period_keys(dt: datetime) -> list[str]:
    wd = dt.weekday()
    return (["平日"] if wd < 5 else ["休日"]) + [WEEKDAY_JA[wd]]


def col_key(period: str, hour: int) -> str:
    return f"{period}_{hour:02d}時台"


class Aggregator:
    def __init__(self, kp_m: list[float]):
        self.kp_m = kp_m
        self.speed_sum = defaultdict(float)
        self.count = defaultdict(int)
        self.time_sum = defaultdict(float)
        self.time_count = defaultdict(int)

    def add(self, direction: str, kp_idx: int, dt: datetime, speed_kmh: float) -> None:
        sec = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1_000_000
        for period in period_keys(dt):
            key = (direction, kp_idx, period, dt.hour)
            self.speed_sum[key] += speed_kmh
            self.count[key] += 1
            self.time_sum[key] += sec
            self.time_count[key] += 1

    def table(self, direction: str, metric: str) -> list[list[object]]:
        columns = [col_key(p, h) for p in PERIODS for h in range(24)]
        rows: list[list[object]] = [["KP[km]"] + columns]
        for i, kp in enumerate(self.kp_m):
            row: list[object] = [round(kp / 1000, KP_DECIMALS)]
            for period in PERIODS:
                for h in range(24):
                    key = (direction, i, period, h)
                    c = self.count.get(key, 0)
                    if metric == "speed":
                        row.append(round(self.speed_sum[key] / c, ROUND_DIGITS) if c else "")
                    elif metric == "count":
                        row.append(c if c else "")
                    else:
                        tc = self.time_count.get(key, 0)
                        row.append(seconds_to_hhmmss(self.time_sum[key] / tc) if tc else "")
            rows.append(row)
        return rows

    def frame(self, direction: str, metric: str):
        if pd is None:
            raise RuntimeError("pandas が必要です。")
        table = self.table(direction, metric)
        return pd.DataFrame(table[1:], columns=table[0])



def xml_escape(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def excel_col(n: int) -> str:
    name = ""
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def write_minimal_xlsx(output_path: Path, sheets: dict[str, list[list[object]]]) -> None:
    import zipfile
    sheet_items = list(sheets.items())
    content_types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">', '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>', '<Default Extension="xml" ContentType="application/xml"/>', '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    for i in range(1, len(sheet_items) + 1):
        content_types.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append('</Types>')
    workbook_sheets = []
    workbook_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i, (name, _) in enumerate(sheet_items, start=1):
        workbook_sheets.append(f'<sheet name="{xml_escape(name[:31])}" sheetId="{i}" r:id="rId{i}"/>')
        workbook_rels.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
    workbook_rels.append('</Relationships>')
    workbook_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(workbook_sheets) + '</sheets></workbook>'
    root_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'

    def sheet_xml(rows: list[list[object]]) -> str:
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
        for r_idx, row in enumerate(rows, start=1):
            out.append(f'<row r="{r_idx}">')
            for c_idx, value in enumerate(row, start=1):
                if value == "" or value is None:
                    continue
                ref = f'{excel_col(c_idx)}{r_idx}'
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    out.append(f'<c r="{ref}" t="inlineStr"><is><t>{xml_escape(value)}</t></is></c>')
            out.append('</row>')
        out.append('</sheetData></worksheet>')
        return ''.join(out)

    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', ''.join(content_types))
        zf.writestr('_rels/.rels', root_rels)
        zf.writestr('xl/workbook.xml', workbook_xml)
        zf.writestr('xl/_rels/workbook.xml.rels', ''.join(workbook_rels))
        for i, (_, rows) in enumerate(sheet_items, start=1):
            zf.writestr(f'xl/worksheets/sheet{i}.xml', sheet_xml(rows))


def crossing_kp_indices(kp_m: list[float], s1: float, s2: float) -> Iterable[int]:
    lo, hi = sorted((s1, s2))
    eps = 1e-6
    for i, kp in enumerate(kp_m):
        # 連続区間の終点側を含めることで、GPS点がKPちょうどにある場合も通過として扱う。
        # 始点側は除外し、隣接セグメントとの二重計上を抑える。
        if lo + eps < kp <= hi + eps:
            yield i


def analyze(
    input_dir: str | Path,
    route_path: str | Path,
    output_path: str | Path,
    recursive: bool = RECURSIVE,
    progress_callback: Optional[Callable[..., None]] = None,
    allowed_dates: Optional[set[str]] = None,
) -> dict[str, int]:
    progress_state = {
        "total_files": 0,
        "current_file": 0,
        "current_file_name": "",
        "raw_trips": 0,
        "split_count": 0,
        "split_total_trips": 0,
        "events": 0,
    }

    def progress(percent: int, message: str, **stats) -> None:
        progress_state.update(stats)
        percent = max(0, min(100, int(percent)))
        stat_text = (
            f"files={progress_state['current_file']}/{progress_state['total_files']} "
            f"raw_trips={progress_state['raw_trips']} "
            f"splits={progress_state['split_count']} "
            f"split_trips={progress_state['split_total_trips']} "
            f"events={progress_state['events']}"
        )
        print(f"[PROGRESS] {percent:3d}% {message} | {stat_text}")
        if progress_callback is not None:
            progress_callback(percent, message, dict(progress_state))

    progress(0, "ルート読込中")
    route = build_route_model(route_path)
    ag = Aggregator(route.kp_m)
    files = list_input_csvs(input_dir, recursive)
    print(f"[INFO] input_csvs={len(files)}")
    progress(5, f"入力CSV {len(files)} 件を検出", total_files=len(files))
    projected_by_trip: dict[tuple[str, str], list[tuple[datetime, float, float]]] = defaultdict(list)
    total_rows = valid_points = 0
    for file_index, path in enumerate(files, start=1):
        progress(
            5 + int(50 * (file_index - 1) / max(len(files), 1)),
            f"投影中 {file_index}/{len(files)}: {path.name}",
            current_file=file_index,
            current_file_name=path.name,
        )
        for n, row in enumerate(read_csv_rows(path)):
            total_rows += 1
            try:
                lon = float(row[COL_LON]); lat = float(row[COL_LAT])
            except Exception:
                continue
            dt = parse_datetime_from_row(row)
            if dt is None:
                continue
            if allowed_dates is not None and dt.strftime("%Y%m%d") not in allowed_dates:
                continue
            s_m, off_m = route.project(lon, lat)
            if off_m > MAX_OFF_ROUTE_M:
                continue
            projected_by_trip[row_trip_key(path, row, n)].append((dt, s_m, off_m))
            valid_points += 1
    events = skipped_segments = 0
    trip_items = list(projected_by_trip.items())
    progress(55, f"投影完了: 有効点 {valid_points} / 行 {total_rows}", raw_trips=len(trip_items))
    split_count = 0
    split_total_trips = 0
    for trip_index, (key, pts) in enumerate(trip_items, start=1):
        if trip_index == 1 or trip_index % 50 == 0 or trip_index == len(trip_items):
            progress(
                55 + int(35 * trip_index / max(len(trip_items), 1)),
                f"KP通過イベント生成 {trip_index}/{len(trip_items)}",
                raw_trips=len(trip_items),
                split_count=split_count,
                split_total_trips=split_total_trips,
                events=events,
            )
        pts.sort(key=lambda x: x[0])
        prev_direction = None
        trip_piece_count = 0
        for a, b in zip(pts, pts[1:]):
            t1, s1, _ = a; t2, s2, _ = b
            dt_s = (t2 - t1).total_seconds()
            ds = s2 - s1
            abs_ds = abs(ds)
            if dt_s <= 0 or abs_ds < MIN_SEGMENT_DISTANCE_M:
                skipped_segments += 1; continue
            if abs_ds > MAX_SEGMENT_DISTANCE_M or dt_s > MAX_SEGMENT_TIME_S:
                skipped_segments += 1; continue
            direction = "forward" if ds > 0 else "reverse"
            if direction != prev_direction:
                if prev_direction is not None:
                    split_count += 1
                trip_piece_count += 1
                prev_direction = direction
            speed_kmh = abs_ds / dt_s * 3.6
            if speed_kmh > 300:
                skipped_segments += 1; continue
            for kp_idx in crossing_kp_indices(route.kp_m, s1, s2):
                kp = route.kp_m[kp_idx]
                ratio = (kp - s1) / ds
                pass_dt = t1 + timedelta(seconds=dt_s * ratio)
                ag.add(direction, kp_idx, pass_dt, speed_kmh)
                events += 1
        split_total_trips += trip_piece_count
    progress(
        90,
        "KP通過イベント生成完了",
        raw_trips=len(trip_items),
        split_count=split_count,
        split_total_trips=split_total_trips,
        events=events,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    progress(
        90,
        "Excelシート作成中",
        raw_trips=len(trip_items),
        split_count=split_count,
        split_total_trips=split_total_trips,
        events=events,
    )
    sheet_tables = {}
    sheet_items = list(SHEET_NAMES.items())
    for sheet_index, (sheet_key, sheet_name) in enumerate(sheet_items, start=1):
        direction, metric = sheet_key.split("_", 1)
        sheet_tables[sheet_name] = ag.table(direction, metric)
        progress(
            90 + int(8 * sheet_index / max(len(sheet_items), 1)),
            f"Excelシート作成 {sheet_index}/{len(sheet_items)}",
            raw_trips=len(trip_items),
            split_count=split_count,
            split_total_trips=split_total_trips,
            events=events,
        )
    write_minimal_xlsx(output_path, sheet_tables)
    progress(100, "Excel出力完了", raw_trips=len(trip_items), split_count=split_count, split_total_trips=split_total_trips, events=events)
    print(f"[DONE] output={output_path}")
    print(f"[STATS] rows={total_rows}, valid_points={valid_points}, trips={len(projected_by_trip)}, split_count={split_count}, split_total_trips={split_total_trips}, events={events}, skipped_segments={skipped_segments}")
    return {
        "rows": total_rows,
        "valid_points": valid_points,
        "trips": len(projected_by_trip),
        "split_count": split_count,
        "split_total_trips": split_total_trips,
        "events": events,
        "skipped_segments": skipped_segments,
    }


def analyze_project(
    project_dir: str | Path,
    recursive: bool = RECURSIVE,
    progress_callback: Optional[Callable[..., None]] = None,
    allowed_dates: Optional[set[str]] = None,
) -> dict[str, object]:
    input_dir, route_dir, output_dir = resolve_project_paths(project_dir)
    route_files = list_route_csvs(route_dir)
    if not route_files:
        raise FileNotFoundError(f"ルートCSVが見つかりません: {route_dir}")

    results: list[dict[str, object]] = []
    total_routes = len(route_files)
    for route_index, route_path in enumerate(route_files, start=1):
        output_path = route_output_path(output_dir, route_path)

        def route_progress(percent: int, message: str, stats: dict | None = None) -> None:
            stats = dict(stats or {})
            stats.update(
                {
                    "total_routes": total_routes,
                    "current_route": route_index,
                    "current_route_name": route_path.name,
                    "output_path": str(output_path),
                }
            )
            overall = int(((route_index - 1) * 100 + percent) / max(total_routes, 1))
            print(f"[PROJECT] {overall:3d}% [{route_index}/{total_routes}] {route_path.name}: {message}")
            if progress_callback is not None:
                progress_callback(overall, f"[{route_index}/{total_routes}] {route_path.name}: {message}", stats)

        route_progress(0, "解析開始", {})
        stats = analyze(input_dir, route_path, output_path, recursive, route_progress, allowed_dates)
        results.append({"route": str(route_path), "output": str(output_path), "stats": stats})
        route_progress(100, "解析完了", stats)

    return {
        "project_dir": str(Path(project_dir)),
        "input_dir": str(input_dir),
        "route_dir": str(route_dir),
        "output_dir": str(output_dir),
        "route_count": total_routes,
        "results": results,
    }

def main():
    parser = argparse.ArgumentParser(description="方向別ルートパフォーマンスをExcel出力します。")
    parser.add_argument("--project", help="プロジェクトフォルダ（指定時は全ルートCSVを一括解析）")
    parser.add_argument("--dates", help="対象日（YYYYMMDD/カンマ区切り）。未指定なら全日")
    parser.add_argument("--input-dir", default=INPUT_DIR, help="第2スクリーニング後CSVフォルダ")
    parser.add_argument("--route", default=ROUTE_PATH, help="ルートCSV")
    parser.add_argument("--output", default=OUTPUT_PATH, help="出力Excelファイル")
    parser.add_argument("--recursive", action="store_true", default=RECURSIVE, help="サブフォルダも探索")
    args = parser.parse_args()
    allowed_dates = None
    if args.dates:
        allowed_dates = {token for token in (normalize_date_token(x) for x in args.dates.split(",")) if token}
    if args.project:
        analyze_project(args.project, args.recursive, allowed_dates=allowed_dates)
    else:
        analyze(args.input_dir, args.route, args.output, args.recursive, allowed_dates=allowed_dates)


if __name__ == "__main__":
    main()
