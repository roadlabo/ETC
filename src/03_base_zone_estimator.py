from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Sequence

ENCODINGS = ("utf-8-sig", "utf-8", "cp932")
NIGHT_START_HOUR = 20
MORNING_START_HOUR = 5
MORNING_END_HOUR = 10
NIGHT_CROSS_MAX_DIST_M = 300.0


@dataclass
class PolygonZone:
    name: str
    points: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]


@dataclass
class Record:
    ts: datetime
    lat: float
    lon: float
    op_id: str | None


@dataclass
class NightCrossCandidate:
    zone: str
    rep_lat: float
    rep_lon: float
    last_ts: datetime
    next_ts: datetime


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).strip())
    except Exception:
        return None


def parse_datetime_any(text: str | None) -> datetime | None:
    if not text:
        return None
    token = str(text).strip()
    if not token:
        return None
    fmts = [
        "%Y%m%d%H%M%S",
        "%Y%m%d%H%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(token[: len(datetime.now().strftime(fmt))], fmt)
        except Exception:
            continue
    digits = re.sub(r"\D", "", token)
    if len(digits) >= 14:
        try:
            return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


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


def _pick_zone_columns(headers: Sequence[str]) -> tuple[int | None, int | None]:
    name_idx = _find_col_index(headers, ["zone_name", "zone", "name", "ゾーン名", "名称"])
    poly_idx = _find_col_index(headers, ["polygon", "points", "coords", "座標", "polygon_wkt", "wkt"])
    return name_idx, poly_idx


def _parse_polygon_text(text: str) -> list[tuple[float, float]]:
    nums = [parse_float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text or "")]
    vals = [x for x in nums if x is not None]
    points: list[tuple[float, float]] = []
    for i in range(0, len(vals), 2):
        if i + 1 < len(vals):
            points.append((float(vals[i]), float(vals[i + 1])))
    return points


def load_zone_definition(zoning_csv: Path) -> list[PolygonZone]:
    rows: list[list[str]] = []
    for enc in ENCODINGS:
        try:
            with zoning_csv.open("r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    if not rows:
        return []

    header = rows[0]
    has_header = any(not re.fullmatch(r"[-+]?\d+(\.\d+)?", c.strip()) for c in header[1:])
    start_idx = 1 if has_header else 0
    name_idx = poly_idx = None
    if has_header:
        name_idx, poly_idx = _pick_zone_columns(header)

    polygons: list[PolygonZone] = []
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
            vals = [parse_float(x) for x in row if x is not None]
            seq = [x for x in vals if x is not None]
            for i in range(0, len(seq), 2):
                if i + 1 < len(seq):
                    points.append((float(seq[i]), float(seq[i + 1])))
        else:
            zone_name = (row[0] or "").strip()
            vals = [parse_float(x) for x in row[1:]]
            seq = [x for x in vals if x is not None]
            for i in range(0, len(seq), 2):
                if i + 1 < len(seq):
                    points.append((float(seq[i]), float(seq[i + 1])))
        if not zone_name or len(points) < 3:
            continue
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        polygons.append(PolygonZone(zone_name, points, (min(lons), min(lats), max(lons), max(lats))))
    log(f"[INFO] 有効ゾーン数: {len(polygons)}")
    return polygons


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


def assign_point_to_zone(lat: float | None, lon: float | None, zone_def: Sequence[PolygonZone]) -> str | None:
    if lat is None or lon is None:
        return None
    for poly in zone_def:
        min_lon, min_lat, max_lon, max_lat = poly.bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if point_in_polygon(lon, lat, poly.points):
            return poly.name
    return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def extract_day_boundaries(records: list[Record]) -> list[tuple[Record, Record]]:
    by_day: dict[datetime.date, list[Record]] = defaultdict(list)
    for rec in records:
        by_day[rec.ts.date()].append(rec)
    days = sorted(by_day.keys())
    pairs: list[tuple[Record, Record]] = []
    for d in days:
        next_d = d.fromordinal(d.toordinal() + 1)
        if next_d not in by_day:
            continue
        pairs.append((by_day[d][-1], by_day[next_d][0]))
    return pairs


def find_night_cross_candidates(
    records: list[Record],
    zones: Sequence[PolygonZone],
    night_start: int = NIGHT_START_HOUR,
    morning_start: int = MORNING_START_HOUR,
    morning_end: int = MORNING_END_HOUR,
    max_dist_m: float = NIGHT_CROSS_MAX_DIST_M,
) -> list[NightCrossCandidate]:
    candidates: list[NightCrossCandidate] = []
    for last_of_day, first_of_next_day in extract_day_boundaries(records):
        cond_a = (
            last_of_day.ts.hour >= night_start
            and morning_start <= first_of_next_day.ts.hour < morning_end
        )
        if not cond_a:
            continue
        dist = _haversine_m(last_of_day.lat, last_of_day.lon, first_of_next_day.lat, first_of_next_day.lon)
        if dist > max_dist_m:
            continue
        rep_lat = (last_of_day.lat + first_of_next_day.lat) / 2.0
        rep_lon = (last_of_day.lon + first_of_next_day.lon) / 2.0
        zone = assign_point_to_zone(rep_lat, rep_lon, zones)
        if not zone:
            continue
        candidates.append(
            NightCrossCandidate(
                zone=zone,
                rep_lat=rep_lat,
                rep_lon=rep_lon,
                last_ts=last_of_day.ts,
                next_ts=first_of_next_day.ts,
            )
        )
    return candidates


def nearest_to_3am_record(records: list[Record]) -> Record | None:
    if not records:
        return None

    def score(r: Record) -> float:
        base = datetime.combine(r.ts.date(), time(hour=3, minute=0, second=0))
        return abs((r.ts - base).total_seconds())

    return min(records, key=score)


def estimate_base_zone_with_fallback(records: list[Record], zones: Sequence[PolygonZone]) -> tuple[str, str]:
    night_cross = find_night_cross_candidates(records, zones)
    if night_cross:
        votes = Counter(c.zone for c in night_cross)
        top_zone, top_votes = votes.most_common(1)[0]
        if len(night_cross) >= 2 and top_votes >= 2:
            return top_zone, f"夜越し地点{top_votes}回一致"
        return top_zone, "夜越し地点1回"

    near_3 = nearest_to_3am_record(records)
    if near_3 is None:
        return "判定不可", "有効レコード0件"
    fallback_zone = assign_point_to_zone(near_3.lat, near_3.lon, zones)
    if fallback_zone:
        return fallback_zone, "深夜3時近傍点で判定"
    return "判定不可", "夜越し地点なし・深夜3時近傍もゾーン外"


def _detect_input_columns(headers: Sequence[str]) -> tuple[int | None, int | None, int | None, int | None]:
    lat_i = _find_col_index(headers, ["lat", "latitude", "緯度", "y"])
    lon_i = _find_col_index(headers, ["lon", "lng", "longitude", "経度", "x"])
    time_i = _find_col_index(headers, ["gps時刻", "gps", "gps_time", "time", "timestamp", "datetime", "日時", "時刻"])
    op_i = _find_col_index(headers, ["op_id", "opid", "運行id", "運行ID"])
    return lat_i, lon_i, time_i, op_i


def _looks_like_header_row(first_row: Sequence[str]) -> bool:
    normalized = [_normalize(c) for c in first_row]
    groups = [
        ["lat", "latitude", "緯度"],
        ["lon", "lng", "longitude", "経度"],
        ["gps時刻", "gps_time", "time", "timestamp", "時刻"],
        ["op_id", "opid", "運行id", "運行ID"],
    ]
    hit = 0
    for candidates in groups:
        cand_norm = [_normalize(c) for c in candidates]
        found = any(any(cn in cell for cn in cand_norm) for cell in normalized)
        if found:
            hit += 1
    return hit >= 2


def _try_read_style12_rows(rows: Sequence[Sequence[str]]) -> tuple[list[Record], str | None]:
    op_i, time_i, lon_i, lat_i = 3, 6, 14, 15
    records: list[Record] = []
    op_from_col = None
    for row in rows:
        if max(op_i, time_i, lon_i, lat_i) >= len(row):
            continue
        dt = parse_datetime_any(row[time_i])
        lat = parse_float(row[lat_i])
        lon = parse_float(row[lon_i])
        if dt is None or lat is None or lon is None:
            continue
        op_v = (row[op_i] or "").strip() or None
        if op_from_col is None and op_v:
            op_from_col = op_v
        records.append(Record(dt, lat, lon, op_v))
    records.sort(key=lambda r: r.ts)
    return records, op_from_col


def _try_read_header_rows(first_row: Sequence[str], rows: Sequence[Sequence[str]]) -> tuple[list[Record], str | None]:
    lat_i, lon_i, time_i, op_i = _detect_input_columns(first_row)
    if lat_i is None or lon_i is None or time_i is None:
        raise RuntimeError("緯度/経度/時刻列を特定できません")

    records: list[Record] = []
    op_from_col = None
    for row in rows:
        if max(lat_i, lon_i, time_i) >= len(row):
            continue
        dt = parse_datetime_any(row[time_i])
        lat = parse_float(row[lat_i])
        lon = parse_float(row[lon_i])
        if dt is None or lat is None or lon is None:
            continue
        op_v = None
        if op_i is not None and op_i < len(row):
            op_v = (row[op_i] or "").strip() or None
        if op_from_col is None and op_v:
            op_from_col = op_v
        records.append(Record(dt, lat, lon, op_v))
    records.sort(key=lambda r: r.ts)
    return records, op_from_col


def _read_records(csv_path: Path) -> tuple[list[Record], str | None]:
    for enc in ENCODINGS:
        try:
            with csv_path.open("r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
                if not rows:
                    return [], None
                records, op_from_col = _try_read_style12_rows(rows)
                if records:
                    log("[INFO] 読込方式: 様式1-2固定列")
                    return records, op_from_col

                first = rows[0]
                if not _looks_like_header_row(first):
                    raise RuntimeError("様式1-2固定列/ヘッダー付き方式のいずれでも有効レコードを読めません")

                log("[INFO] 読込方式: ヘッダー付き汎用CSV")
                return _try_read_header_rows(first, rows[1:])
        except UnicodeDecodeError:
            continue
    raise RuntimeError("CSVの読み込みに失敗しました")


def _resolve_op_id(records: list[Record], op_col: str | None, file_path: Path) -> str:
    if op_col:
        return op_col
    for r in records:
        if r.op_id:
            return r.op_id
    return file_path.stem


def estimate_for_file(csv_path: Path, zone_def: Sequence[PolygonZone]) -> tuple[str, str, str]:
    records, op_col = _read_records(csv_path)
    op_id = _resolve_op_id(records, op_col, csv_path)
    if not records:
        return op_id, "判定不可", "有効レコード0件"
    base_zone, memo = estimate_base_zone_with_fallback(records, zone_def)
    return op_id, base_zone, memo


def iter_csv_files(folder: Path, recursive: bool) -> list[Path]:
    gen = folder.rglob("*.csv") if recursive else folder.glob("*.csv")
    return sorted(p for p in gen if p.is_file())


def run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    zone_path = Path(args.zoning)
    if not input_dir.exists() or not input_dir.is_dir():
        log(f"[ERROR] 入力フォルダ不正: {input_dir}")
        return 2
    if not zone_path.exists():
        log(f"[ERROR] ゾーニングCSVがありません: {zone_path}")
        return 2

    zone_def = load_zone_definition(zone_path)
    if not zone_def:
        log("[ERROR] ゾーニングCSVから有効なゾーンを読み込めません")
        return 2

    files = iter_csv_files(input_dir, args.recursive)
    total = len(files)
    log(f"[INFO] 開始 / 対象CSV数={total} / ゾーン数={len(zone_def)}")
    log(f"[TOTAL] total={total}")
    if total == 0:
        log("[ERROR] 対象CSVが0件です")
        return 2

    out_csv = Path(args.output) if args.output else input_dir.parent / f"{input_dir.name}_拠点ゾーン.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    out_rows: list[tuple[str, str, str]] = []
    hit_count = 0
    for i, fp in enumerate(files, 1):
        try:
            op_id, base_zone, reason = estimate_for_file(fp, zone_def)
        except Exception:
            op_id, base_zone, reason = fp.stem, "判定不可", "読込失敗"
            log(f"[ERROR] 読込失敗 / file={fp.name}")
        out_rows.append((op_id, base_zone, reason))
        if base_zone != "判定不可":
            hit_count += 1
            log(f"[HIT] op_id={op_id} zone={base_zone} hit_count={hit_count}")
        log(f"[PROGRESS] done={i} total={total} file={fp.name}")

    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["op_id", "base_zone", "判定メモ"])
        w.writerows(out_rows)

    log(f"[INFO] 完了 / 出力CSV={out_csv}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="運行ID別 推定拠点ゾーン対応表 作成")
    p.add_argument("--input", required=True, help="第1スクリーニングフォルダ")
    p.add_argument("--zoning", required=True, help="任意ゾーニングCSV")
    p.add_argument("--output", default="", help="出力CSV。未指定時は入力フォルダと同階層")
    p.add_argument("--recursive", action="store_true", help="サブフォルダを含める")
    return p


if __name__ == "__main__":
    parser = build_parser()
    ns = parser.parse_args()
    sys.exit(run(ns))
