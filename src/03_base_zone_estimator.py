from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

ENCODINGS = ("utf-8-sig", "utf-8", "cp932")
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 5
DIST_THRESHOLD_M = 100.0
MIN_STOP_MINUTES = 30.0


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
class StopSegment:
    start_idx: int
    end_idx: int
    start_time: datetime
    end_time: datetime
    stop_minutes: float
    rep_lat: float
    rep_lon: float


@dataclass
class ZoneCandidate:
    zone: str | None
    stop_minutes: float
    rep_lat: float
    rep_lon: float
    basis_count: int
    dawn_score: float


def log(msg: str) -> None:
    print(msg, flush=True)


def _warn_limited(counter: dict[str, int], key: str, msg: str, limit: int = 20) -> None:
    current = counter.get(key, 0)
    if current < limit:
        log(msg)
    elif current == limit:
        log(f"[WARN] {key}: 以降省略")
    counter[key] = current + 1


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
    for row_no, row in enumerate(rows[start_idx:], start=start_idx + 1):
        if not row:
            continue
        zone_name = ""
        points: list[tuple[float, float]] = []
        if has_header and name_idx is not None and poly_idx is not None and max(name_idx, poly_idx) < len(row):
            zone_name = (row[name_idx] or "").strip()
            points = _parse_polygon_text(row[poly_idx] or "")
        elif has_header and name_idx is not None and name_idx < len(row):
            zone_name = (row[name_idx] or "").strip()
            seq = [parse_float(x) for x in row if x is not None]
            vals = [x for x in seq if x is not None]
            for i in range(0, len(vals), 2):
                if i + 1 < len(vals):
                    points.append((float(vals[i]), float(vals[i + 1])))
        else:
            zone_name = (row[0] or "").strip()
            seq = [parse_float(x) for x in row[1:]]
            vals = [x for x in seq if x is not None]
            for i in range(0, len(vals), 2):
                if i + 1 < len(vals):
                    points.append((float(vals[i]), float(vals[i + 1])))
        if not zone_name or len(points) < 3:
            log(f"[WARN] ゾーン定義スキップ: 行番号{row_no}（頂点不足または名称なし）")
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


def assign_zone(lat: float | None, lon: float | None, zone_def: Sequence[PolygonZone]) -> str | None:
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


def _night_minute(ts: datetime) -> int:
    minute = ts.hour * 60 + ts.minute
    if minute < NIGHT_END_HOUR * 60:
        return minute + 24 * 60
    return minute


def extract_night_records(records: list[Record], start_hour: int = NIGHT_START_HOUR, end_hour: int = NIGHT_END_HOUR) -> list[Record]:
    out: list[Record] = []
    for r in records:
        h = r.ts.hour
        if h >= start_hour or h < end_hour:
            out.append(r)
    return out


def detect_stop_segments(records: list[Record], dist_threshold_m: float = DIST_THRESHOLD_M, min_stop_minutes: float = MIN_STOP_MINUTES) -> list[StopSegment]:
    segments: list[StopSegment] = []
    if len(records) < 2:
        return segments

    start_idx: int | None = None
    for i in range(1, len(records)):
        prev = records[i - 1]
        cur = records[i]
        dist = _haversine_m(prev.lat, prev.lon, cur.lat, cur.lon)
        if dist <= dist_threshold_m:
            if start_idx is None:
                start_idx = i - 1
        else:
            if start_idx is not None:
                seg = _build_segment(records, start_idx, i - 1)
                if seg.stop_minutes >= min_stop_minutes:
                    segments.append(seg)
                start_idx = None
    if start_idx is not None:
        seg = _build_segment(records, start_idx, len(records) - 1)
        if seg.stop_minutes >= min_stop_minutes:
            segments.append(seg)
    return segments


def _build_segment(records: list[Record], start_idx: int, end_idx: int) -> StopSegment:
    seg_rows = records[start_idx : end_idx + 1]
    st = seg_rows[0].ts
    ed = seg_rows[-1].ts
    stop_minutes = max(0.0, (ed - st).total_seconds() / 60.0)
    rep_lat = sum(r.lat for r in seg_rows) / len(seg_rows)
    rep_lon = sum(r.lon for r in seg_rows) / len(seg_rows)
    return StopSegment(start_idx, end_idx, st, ed, stop_minutes, rep_lat, rep_lon)


def representative_point(segment: StopSegment) -> tuple[float, float]:
    return segment.rep_lat, segment.rep_lon


def judge_zone_from_stop(segment: StopSegment, records: list[Record], zone_def: Sequence[PolygonZone]) -> str | None:
    rep_lat, rep_lon = representative_point(segment)
    rep_zone = assign_zone(rep_lat, rep_lon, zone_def)
    if rep_zone:
        return rep_zone

    prev_zone = None
    next_zone = None
    if segment.start_idx - 1 >= 0:
        prev = records[segment.start_idx - 1]
        prev_zone = assign_zone(prev.lat, prev.lon, zone_def)
    if segment.end_idx + 1 < len(records):
        nxt = records[segment.end_idx + 1]
        next_zone = assign_zone(nxt.lat, nxt.lon, zone_def)

    if prev_zone and next_zone and prev_zone == next_zone:
        return prev_zone
    if prev_zone:
        return prev_zone
    if next_zone:
        return next_zone
    return None


def select_best_base_zone(candidates: list[ZoneCandidate]) -> tuple[str, ZoneCandidate | None]:
    if not candidates:
        return "判定不可", None

    valid = [c for c in candidates if c.zone]
    if not valid:
        best = max(candidates, key=lambda c: (c.stop_minutes, c.dawn_score))
        return "ゾーン外", best

    zone_freq = Counter(str(c.zone) for c in valid)
    best = max(valid, key=lambda c: (c.stop_minutes, zone_freq[str(c.zone)], c.dawn_score))
    return str(best.zone), best


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
    warn_counter: dict[str, int] = {}
    records: list[Record] = []
    op_from_col = None
    for row_no, row in enumerate(rows, start=1):
        if max(op_i, time_i, lon_i, lat_i) >= len(row):
            _warn_limited(warn_counter, "行長不足", f"[WARN] 行長不足のためスキップ: 行番号{row_no}")
            continue
        dt = parse_datetime_any(row[time_i])
        lat = parse_float(row[lat_i])
        lon = parse_float(row[lon_i])
        if dt is None or lat is None or lon is None:
            _warn_limited(warn_counter, "解析失敗", f"[WARN] 日時/緯度/経度の解析失敗でスキップ: 行番号{row_no}")
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
        log("[ERROR] 緯度/経度/時刻列を特定できません")
        raise RuntimeError("緯度/経度/時刻列を特定できません")

    warn_counter: dict[str, int] = {}
    records: list[Record] = []
    op_from_col = None
    for row_no, row in enumerate(rows, start=2):
        if max(lat_i, lon_i, time_i) >= len(row):
            _warn_limited(warn_counter, "行長不足", f"[WARN] 行長不足のためスキップ: 行番号{row_no}")
            continue
        dt = parse_datetime_any(row[time_i])
        lat = parse_float(row[lat_i])
        lon = parse_float(row[lon_i])
        if dt is None or lat is None or lon is None:
            _warn_limited(warn_counter, "解析失敗", f"[WARN] 日時/緯度/経度の解析失敗でスキップ: 行番号{row_no}")
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

                log("[INFO] 読込方式: 様式1-2固定列")
                records, op_from_col = _try_read_style12_rows(rows)
                if records:
                    return records, op_from_col

                log("[WARN] 固定列方式で有効レコード0件、ヘッダー方式にフォールバック")
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
        log("[WARN] 有効レコード0件")
        return op_id, "判定不可", "有効レコード0件"

    night = extract_night_records(records)
    if not night:
        log("[WARN] 夜間レコード0件")
        return op_id, "判定不可", "夜間レコード0件"

    segments = detect_stop_segments(night)
    if not segments:
        log("[WARN] 停留候補0件")
        return op_id, "判定不可", "停留候補0件"

    candidates: list[ZoneCandidate] = []
    for seg in segments:
        zone = judge_zone_from_stop(seg, night, zone_def)
        candidates.append(
            ZoneCandidate(
                zone=zone,
                stop_minutes=seg.stop_minutes,
                rep_lat=seg.rep_lat,
                rep_lon=seg.rep_lon,
                basis_count=1,
                dawn_score=_night_minute(seg.end_time),
            )
        )

    base_zone, _best = select_best_base_zone(candidates)
    if base_zone == "ゾーン外":
        log("[WARN] 停留候補はあるが全てゾーン外")
        return op_id, base_zone, "ゾーン外"
    return op_id, base_zone, "正常判定"


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
    log(f"[TOTAL] total={total}")
    log(f"[INFO] 対象CSV数: {total}")
    if total == 0:
        log("[ERROR] 対象CSVが0件です")
        return 2

    if args.output:
        out_csv = Path(args.output)
    else:
        out_csv = input_dir.parent / f"{input_dir.name}_拠点ゾーン.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    out_rows: list[tuple[str, str, str]] = []
    hit_count = 0
    for i, fp in enumerate(files, 1):
        log(f"[INFO] 現在処理中: {fp.name}")
        try:
            op_id, base_zone, reason = estimate_for_file(fp, zone_def)
        except Exception as e:
            log(f"[ERROR] {fp.name}: {e}")
            op_id, base_zone, reason = fp.stem, "判定不可", "読込失敗"
        out_rows.append((op_id, base_zone, reason))
        if reason == "正常判定" and base_zone != "判定不可":
            hit_count += 1
            log(f"[HIT] op_id={op_id} zone={base_zone} hit_count={hit_count}")
        log(f"[PROGRESS] done={i} total={total} file={fp.name}")

    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["op_id", "base_zone", "判定メモ"])
        w.writerows(out_rows)

    log(f"[INFO] 出力CSV: {out_csv}")
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
