from __future__ import annotations

"""43_ピーク30分帯内OD抽出。

第1スクリーニング済みフォルダ内の各CSV(1ファイル=1運行ID)をストリーミング処理し、
指定30分帯内に実在する最初の点をO、最後の点をDとしてODを集計する。

任意ゾーニングポリゴン外の点については、42_OD_extractor.py と同様に、
中心点(既定: 津山市中心点)との位置関係で東西南北の方向別ゾーンへ割り当てる。
これによりポリゴン外流入/流出も方向付き需要として扱える。
"""

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence

DEFAULT_CENTER_LON = 133.93
DEFAULT_CENTER_LAT = 35.07
DEFAULT_CENTER_NAME = "津山市中心点（既定値）"

ENCODINGS = ("utf-8-sig", "utf-8", "cp932")

DIR_ZONE_LABELS = {
    "EAST": "東方面ゾーン",
    "WEST": "西方面ゾーン",
    "NORTH": "北方面ゾーン",
    "SOUTH": "南方面ゾーン",
}


def now_text() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def log(line: str) -> None:
    print(line, flush=True)


def parse_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).strip())
    except Exception:
        return None


@dataclass
class PolygonZone:
    name: str
    points: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]


def point_in_polygon(lon: float, lat: float, points: Sequence[tuple[float, float]]) -> bool:
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]
        intersect = (yi > lat) != (yj > lat) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) + 1e-20) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def directional_zone(lon: float, lat: float, center_lon: float, center_lat: float) -> str:
    dx = lon - center_lon
    dy = lat - center_lat
    if abs(dx) >= abs(dy):
        return DIR_ZONE_LABELS["EAST"] if dx > 0 else DIR_ZONE_LABELS["WEST"]
    return DIR_ZONE_LABELS["NORTH"] if dy > 0 else DIR_ZONE_LABELS["SOUTH"]


def assign_zone_with_direction(
    lon: float | None,
    lat: float | None,
    polygons: Sequence[PolygonZone],
    center_lon: float,
    center_lat: float,
) -> str:
    if lon is None or lat is None:
        return "MISSING"
    matches: list[str] = []
    for poly in polygons:
        min_lon, min_lat, max_lon, max_lat = poly.bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if point_in_polygon(lon, lat, poly.points):
            matches.append(poly.name)
    if not matches:
        return directional_zone(lon, lat, center_lon, center_lat)
    if len(matches) > 1:
        log(f"[WARN] 重なりポリゴン検知: {matches} -> {matches[0]} を採用")
    return matches[0]


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]", "", s.strip().lower())


def _find_col_index(headers: Sequence[str], candidates: Sequence[str]) -> int | None:
    norms = [_normalize(h) for h in headers]
    for cand in candidates:
        nc = _normalize(cand)
        if nc in norms:
            return norms.index(nc)
    for i, n in enumerate(norms):
        if any(_normalize(c) in n for c in candidates):
            return i
    return None


def parse_time_to_minutes(text: str | None) -> int | None:
    if not text:
        return None
    t = str(text).strip()
    if not t:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
    if m:
        hh = int(m.group(1)) % 24
        mm = int(m.group(2))
        if 0 <= mm <= 59:
            return hh * 60 + mm
    digits = re.sub(r"\D", "", t)
    if len(digits) >= 14:  # YYYYMMDDHHMMSS
        hh = int(digits[8:10]) % 24
        mm = int(digits[10:12])
        return hh * 60 + mm if 0 <= mm <= 59 else None
    if len(digits) >= 12:  # YYYYMMDDHHMM
        hh = int(digits[8:10]) % 24
        mm = int(digits[10:12])
        return hh * 60 + mm if 0 <= mm <= 59 else None
    if len(digits) >= 4:
        hh = int(digits[-4:-2]) % 24
        mm = int(digits[-2:])
        return hh * 60 + mm if 0 <= mm <= 59 else None
    return None


def parse_time_to_date(text: str | None) -> date | None:
    if not text:
        return None
    t = str(text).strip()
    if not t:
        return None
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    digits = re.sub(r"\D", "", t)
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def compact_dates(dates: list[date]) -> str:
    if not dates:
        return ""
    by_ym: dict[tuple[int, int], list[int]] = defaultdict(list)
    for d in sorted(dates):
        by_ym[(d.year, d.month)].append(d.day)
    parts = []
    prev_year = None
    for (y, m), ds in sorted(by_ym.items()):
        dpart = "+".join(str(x) for x in sorted(ds))
        if prev_year != y:
            parts.append(f"{y}_{m}_{dpart}")
        else:
            parts.append(f"{m}_{dpart}")
        prev_year = y
    return "/".join(parts)


def _open_csv_dict_reader(path: Path) -> tuple[csv.DictReader, object] | tuple[None, None]:
    for enc in ENCODINGS:
        try:
            f = path.open("r", encoding=enc, newline="")
            return csv.DictReader(f), f
        except UnicodeDecodeError:
            continue
        except Exception:
            break
    return None, None


def _iter_csv_files(input_dir: Path, recursive: bool) -> list[Path]:
    gen = input_dir.rglob("*.csv") if recursive else input_dir.glob("*.csv")
    return sorted(p for p in gen if p.is_file())


def _pick_zone_columns(headers: Sequence[str]) -> tuple[int | None, int | None]:
    name_idx = _find_col_index(headers, ["zone_name", "zone", "name", "ゾーン名", "名称"]) 
    poly_idx = _find_col_index(headers, ["polygon", "points", "coords", "座標", "polygon_wkt", "wkt"])
    return name_idx, poly_idx


def _parse_polygon_text(text: str) -> list[tuple[float, float]]:
    nums = [parse_float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text or "")]
    nums = [x for x in nums if x is not None]
    pts: list[tuple[float, float]] = []
    for i in range(0, len(nums), 2):
        if i + 1 >= len(nums):
            break
        pts.append((float(nums[i]), float(nums[i + 1])))
    return pts


def load_polygons(zoning_csv: Path) -> list[PolygonZone]:
    polygons: list[PolygonZone] = []
    for enc in ENCODINGS:
        try:
            with zoning_csv.open("r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError("ゾーニングCSVの読み込みに失敗しました(encoding)")

    if not rows:
        return polygons

    header = rows[0]
    has_header = any(not re.fullmatch(r"[-+]?\d+(\.\d+)?", c.strip()) for c in header[1:])
    start_idx = 1 if has_header else 0
    name_idx = poly_idx = None
    if has_header:
        name_idx, poly_idx = _pick_zone_columns(header)

    for row in rows[start_idx:]:
        if not row:
            continue
        zone_name = ""
        points: list[tuple[float, float]] = []
        if has_header and name_idx is not None and poly_idx is not None and max(name_idx, poly_idx) < len(row):
            zone_name = (row[name_idx] or "").strip()
            points = _parse_polygon_text(row[poly_idx] or "")
        elif has_header and name_idx is not None and name_idx < len(row):
            zone_name = (row[name_idx] or "").strip()
            raw = [parse_float(x) for x in row if x is not None]
            seq = [x for x in raw if x is not None]
            for i in range(0, len(seq), 2):
                if i + 1 < len(seq):
                    points.append((float(seq[i]), float(seq[i + 1])))
        else:
            zone_name = (row[0] or "").strip()
            seq = [parse_float(x) for x in row[1:]]
            vals = [x for x in seq if x is not None]
            for i in range(0, len(vals), 2):
                if i + 1 < len(vals):
                    points.append((float(vals[i]), float(vals[i + 1])))

        if not zone_name or len(points) < 3:
            continue
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        polygons.append(PolygonZone(zone_name, points, (min(lons), min(lats), max(lons), max(lats))))
    return polygons


def detect_input_columns(headers: Sequence[str]) -> tuple[str, str, str | None]:
    lon_key = headers[_find_col_index(headers, ["lon", "longitude", "経度", "x"]) or -1] if headers else ""
    lat_key = headers[_find_col_index(headers, ["lat", "latitude", "緯度", "y"]) or -1] if headers else ""
    t_idx = _find_col_index(headers, ["time", "時刻", "日時", "datetime", "timestamp"])
    t_key = headers[t_idx] if t_idx is not None else None
    if not lon_key or not lat_key or t_key is None:
        raise RuntimeError("入力CSVの列推定に失敗(lon/lat/time)")
    return lon_key, lat_key, t_key


def process_file(
    file_path: Path,
    slot_index: int,
    polygons: Sequence[PolygonZone],
    center_lon: float,
    center_lat: float,
    selected_dates: set[date],
) -> dict | None:
    reader, handle = _open_csv_dict_reader(file_path)
    if not reader or not handle:
        return None
    try:
        headers = reader.fieldnames or []
        lon_key, lat_key, t_key = detect_input_columns(headers)
        start_min = slot_index * 30
        end_min = start_min + 30

        first = last = None
        c = 0
        for row in reader:
            ts = (row.get(t_key) or "").strip()
            d = parse_time_to_date(ts)
            if d is None or d not in selected_dates:
                continue
            mm = parse_time_to_minutes(ts)
            if mm is None or not (start_min <= mm < end_min):
                continue
            lon = parse_float(row.get(lon_key))
            lat = parse_float(row.get(lat_key))
            rec = {"lon": lon, "lat": lat, "time": ts}
            if first is None:
                first = rec
            last = rec
            c += 1
        if c == 0 or first is None or last is None:
            return None

        o_zone = assign_zone_with_direction(first["lon"], first["lat"], polygons, center_lon, center_lat)
        d_zone = assign_zone_with_direction(last["lon"], last["lat"], polygons, center_lon, center_lat)
        return {
            "o_time": first["time"], "d_time": last["time"],
            "o_lat": first["lat"], "o_lon": first["lon"],
            "d_lat": last["lat"], "d_lon": last["lon"],
            "o_zone": o_zone, "d_zone": d_zone,
            "point_count_in_slot": c,
        }
    finally:
        handle.close()


def slot_label(slot_index: int) -> str:
    start = slot_index * 30
    end = start + 29
    sh, sm = divmod(start, 60)
    eh, em = divmod(end, 60)
    return f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"


def run_peak30min_od(
    input_dir: Path,
    zoning_csv: Path,
    slot_index: int,
    output_matrix: Path,
    output_detail: Path,
    output_summary: Path,
    center_lon: float,
    center_lat: float,
    center_name: str,
    selected_dates: set[date],
    recursive: bool = False,
    progress_callback=None,
    log_callback=None,
    dates_compact: str = "",
) -> int:
    logger = log_callback or log

    def emit_progress(done: int, total: int) -> None:
        if progress_callback:
            try:
                progress_callback(done, total)
            except Exception:
                pass

    if not input_dir.exists() or not input_dir.is_dir():
        logger(f"[ERROR] 入力フォルダ不正: {input_dir}")
        return 2
    if not zoning_csv.exists():
        logger(f"[ERROR] ゾーニングCSVがありません: {zoning_csv}")
        return 2
    if not (0 <= slot_index <= 47):
        logger("[ERROR] --slot-index は 0..47")
        return 2
    if not (-180 <= center_lon <= 180 and -90 <= center_lat <= 90):
        logger("[ERROR] center座標が不正")
        return 2
    if not selected_dates:
        logger("[ERROR] 対象日が0件です")
        return 2

    csv_files = _iter_csv_files(input_dir, recursive)
    if not csv_files:
        logger("[ERROR] 入力CSV 0件")
        return 2

    polygons = load_polygons(zoning_csv)
    if not polygons:
        logger("[ERROR] ゾーニングCSVから有効ポリゴンを読み込めません")
        return 2

    total = len(csv_files)
    slot = slot_label(slot_index)
    logger(f"[INFO] 開始: {now_text()}")
    logger(f"[INFO] 対象CSV数: {total}")
    logger(f"[INFO] 指定30分帯: {slot}")
    logger(f"[INFO] 対象日数: {len(selected_dates)}")
    logger(f"[INFO] ゾーン数: {len(polygons)}")
    logger(f"[INFO] 方向判定中心点: {center_name} lon={center_lon} lat={center_lat}")

    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    detail_rows: list[dict[str, object]] = []
    od_counts: Counter[tuple[str, str]] = Counter()
    used_zones: set[str] = set(p.name for p in polygons)

    direction_count = Counter({"EAST": 0, "WEST": 0, "NORTH": 0, "SOUTH": 0})
    missing_count = 0
    same_zone = 0

    for i, fp in enumerate(csv_files, 1):
        try:
            rec = process_file(fp, slot_index, polygons, center_lon, center_lat, selected_dates)
        except Exception as e:
            logger(f"[ERROR] {fp.name}: {e}")
            logger(f"進捗ファイル: {i}/{total}")
            emit_progress(i, total)
            continue
        if rec is None:
            logger(f"進捗ファイル: {i}/{total}")
            emit_progress(i, total)
            continue

        o_zone = rec["o_zone"]
        d_zone = rec["d_zone"]
        if o_zone == d_zone:
            same_zone += 1

        for z in (o_zone, d_zone):
            if z == "MISSING":
                missing_count += 1
            elif z == DIR_ZONE_LABELS["EAST"]:
                direction_count["EAST"] += 1
            elif z == DIR_ZONE_LABELS["WEST"]:
                direction_count["WEST"] += 1
            elif z == DIR_ZONE_LABELS["NORTH"]:
                direction_count["NORTH"] += 1
            elif z == DIR_ZONE_LABELS["SOUTH"]:
                direction_count["SOUTH"] += 1

        used_zones.add(o_zone)
        used_zones.add(d_zone)
        matrix[o_zone][d_zone] += 1
        od_counts[(o_zone, d_zone)] += 1

        detail_rows.append({"opid": fp.stem, "slot": slot, **rec})

        logger(f"ODCOUNT:{o_zone}:{d_zone}:{od_counts[(o_zone, d_zone)]}")
        for key in ("EAST", "WEST", "NORTH", "SOUTH"):
            logger(f"DIRCOUNT:{key}:{direction_count[key]}")
        logger(f"進捗ファイル: {i}/{total}")
        emit_progress(i, total)

    output_matrix.parent.mkdir(parents=True, exist_ok=True)

    zones_sorted = sorted(used_zones)
    with output_matrix.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["o_zone\\d_zone", *zones_sorted])
        for oz in zones_sorted:
            w.writerow([oz] + [matrix[oz].get(dz, 0) for dz in zones_sorted])

    with output_detail.open("w", encoding="utf-8-sig", newline="") as f:
        fields = [
            "opid", "slot", "o_time", "d_time", "o_lat", "o_lon", "d_lat", "d_lon",
            "o_zone", "d_zone", "point_count_in_slot",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in detail_rows:
            w.writerow(r)

    total_trips = len(detail_rows)
    same_ratio = (same_zone / total_trips * 100.0) if total_trips else 0.0

    with output_summary.open("w", encoding="utf-8-sig", newline="") as f:
        fields = [
            "slot", "total_trips_in_slot", "same_zone_od_count", "same_zone_od_ratio",
            "east_zone_count", "west_zone_count", "north_zone_count", "south_zone_count", "missing_count",
            "center_lon", "center_lat", "center_name",
            "selected_day_count", "selected_dates_compact",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow(
            {
                "slot": slot,
                "total_trips_in_slot": total_trips,
                "same_zone_od_count": same_zone,
                "same_zone_od_ratio": f"{same_ratio:.2f}",
                "east_zone_count": direction_count["EAST"],
                "west_zone_count": direction_count["WEST"],
                "north_zone_count": direction_count["NORTH"],
                "south_zone_count": direction_count["SOUTH"],
                "missing_count": missing_count,
                "center_lon": center_lon,
                "center_lat": center_lat,
                "center_name": center_name,
                "selected_day_count": len(selected_dates),
                "selected_dates_compact": dates_compact or compact_dates(sorted(selected_dates)),
            }
        )

    logger(f"[INFO] SAME_ZONE_RATIO: {same_ratio:.1f}")
    logger("[INFO] 方向別ゾーン件数:")
    logger(f"[INFO]   東方面ゾーン: {direction_count['EAST']}")
    logger(f"[INFO]   西方面ゾーン: {direction_count['WEST']}")
    logger(f"[INFO]   北方面ゾーン: {direction_count['NORTH']}")
    logger(f"[INFO]   南方面ゾーン: {direction_count['SOUTH']}")
    logger(f"[INFO] 出力CSV: {output_matrix}")
    logger(f"[INFO] 出力CSV: {output_detail}")
    logger(f"[INFO] 出力CSV: {output_summary}")
    logger(f"[INFO] 完了: {now_text()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--zoning", required=True)
    ap.add_argument("--slot-index", type=int, required=True)
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--output-matrix", required=True)
    ap.add_argument("--output-detail", required=True)
    ap.add_argument("--output-summary", default="")
    ap.add_argument("--center-lon", type=float, default=DEFAULT_CENTER_LON)
    ap.add_argument("--center-lat", type=float, default=DEFAULT_CENTER_LAT)
    ap.add_argument("--center-name", default=DEFAULT_CENTER_NAME)
    ap.add_argument("--dates", required=True, help="JSON array of YYYY-MM-DD")
    ap.add_argument("--dates-compact", default="")
    ap.add_argument("--keep-out-of-zone", action="store_true", help="互換オプション(現在は方向別ゾーン優先)")
    args = ap.parse_args()

    input_dir = Path(args.input)
    zoning_csv = Path(args.zoning)
    out_matrix = Path(args.output_matrix)
    out_detail = Path(args.output_detail)
    out_summary = Path(args.output_summary) if args.output_summary else out_matrix.with_name(out_matrix.stem.replace("_matrix", "_summary") + out_matrix.suffix)

    try:
        date_tokens = json.loads(args.dates)
        if not isinstance(date_tokens, list):
            raise ValueError
        selected_dates = {datetime.strptime(str(x), "%Y-%m-%d").date() for x in date_tokens}
    except Exception:
        log("[ERROR] --dates の形式が不正です。JSON配列(YYYY-MM-DD)を指定してください")
        return 2
    return run_peak30min_od(
        input_dir=input_dir,
        zoning_csv=zoning_csv,
        slot_index=args.slot_index,
        output_matrix=out_matrix,
        output_detail=out_detail,
        output_summary=out_summary,
        center_lon=args.center_lon,
        center_lat=args.center_lat,
        center_name=args.center_name,
        selected_dates=selected_dates,
        recursive=args.recursive,
        dates_compact=args.dates_compact,
    )


if __name__ == "__main__":
    sys.exit(main())
