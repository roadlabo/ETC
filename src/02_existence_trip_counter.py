from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

TIME_FMT = "%H:%M-%H:%M"
PROGRESS_EMIT_SEC = 0.8

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def log_info(msg: str) -> None:
    print(f"[INFO] {msg}", flush=True)


def log_error(msg: str) -> None:
    print(f"[ERROR] {msg}", flush=True)


def parse_meshes(text: str) -> list[str]:
    meshes = [m.strip() for m in text.split("+") if m.strip()]
    if not meshes:
        raise ValueError("meshes is empty")
    uniq: list[str] = []
    seen: set[str] = set()
    for m in meshes:
        if not re.fullmatch(r"\d+", m):
            raise ValueError(f"invalid mesh: {m}")
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    return uniq


def parse_dates(text: str) -> set[date]:
    token = (text or "").strip()
    if not token:
        return set()
    items: list[str]
    try:
        loaded = json.loads(token)
        if isinstance(loaded, list):
            items = [str(x) for x in loaded]
        else:
            items = [str(loaded)]
    except json.JSONDecodeError:
        items = [x.strip() for x in token.split(",") if x.strip()]
    out: set[date] = set()
    for it in items:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                out.add(datetime.strptime(it, fmt).date())
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"invalid date token: {it}")
    return out


def second_mesh_code(lat: float, lon: float) -> str:
    """Calculate Japanese 2nd-level mesh code from lat/lon.

    1st mesh: lat*1.5 and lon-100 (1 degree)
    2nd mesh subdivides 1st mesh into 8 (lat) x 8 (lon):
      lat cell = 5 minutes, lon cell = 7.5 minutes.
    """

    p = int(lat * 60.0 / 40.0)
    a = int(lon) - 100
    lat_rem_min = lat * 60.0 - (p * 40.0)
    lon_rem_min = (lon - int(lon)) * 60.0
    q = int(lat_rem_min / 5.0)
    b = int(lon_rem_min / 7.5)
    return f"{p:02d}{a:02d}{q}{b}"


def iter_csv_files(folder: Path, recursive: bool) -> list[Path]:
    if recursive:
        return sorted(p for p in folder.rglob("*.csv") if p.is_file())
    return sorted(p for p in folder.glob("*.csv") if p.is_file())


def _guess_column_map(header: list[str]) -> tuple[int | None, int | None, int | None, int | None]:
    hmap = {c.strip().lower(): i for i, c in enumerate(header)}

    def pick(cands: Iterable[str]) -> int | None:
        for c in cands:
            if c in hmap:
                return hmap[c]
        return None

    dt_idx = pick(["gps時刻", "gps", "gps_time", "gpsdatetime", "datetime", "time", "timestamp", "date"]) 
    lat_idx = pick(["緯度", "lat", "latitude"])
    lon_idx = pick(["経度", "lon", "lng", "longitude"])
    mesh_idx = pick(["2次メッシュコード", "mesh2", "mesh_code", "second_mesh", "2nd_mesh"])
    return dt_idx, lat_idx, lon_idx, mesh_idx


def _parse_row_datetime(row: list[str], idx: int | None) -> datetime | None:
    if idx is None or idx >= len(row):
        return None
    token = row[idx].strip()
    if not token:
        return None
    fmts = [
        "%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S", "%Y%m%d",
    ]
    for f in fmts:
        try:
            return datetime.strptime(token[: len(datetime.now().strftime(f))], f)
        except Exception:
            continue
    return None


def _parse_float(row: list[str], idx: int | None, fallback: int | None = None) -> float | None:
    for i in (idx, fallback):
        if i is None or i >= len(row):
            continue
        t = row[i].strip()
        if not t:
            continue
        try:
            return float(t)
        except ValueError:
            continue
    return None


def _parse_mesh_code(
    row: list[str],
    mesh_idx: int | None,
    lat_idx: int | None,
    lon_idx: int | None,
    fallback_lat_idx: int | None = None,
    fallback_lon_idx: int | None = None,
) -> str | None:
    if mesh_idx is not None and mesh_idx < len(row):
        mesh_token = row[mesh_idx].strip()
        if mesh_token and re.fullmatch(r"\d+", mesh_token):
            return mesh_token

    lat = _parse_float(row, lat_idx, fallback_lat_idx)
    lon = _parse_float(row, lon_idx, fallback_lon_idx)
    if lat is None or lon is None:
        return None
    return second_mesh_code(lat, lon)


def slot_label(slot_index: int) -> str:
    start_m = slot_index * 30
    end_m = start_m + 29
    sh, sm = divmod(start_m, 60)
    eh, em = divmod(end_m, 60)
    return f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"


def process_file(path: Path, target_dates: set[date], mesh_set: set[str]) -> tuple[set[int], dict[int, int]]:
    hit_slots: set[int] = set()
    record_counts: defaultdict[int, int] = defaultdict(int)
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        first = next(reader, None)
        if first is None:
            return hit_slots, dict(record_counts)

        has_header = any(not re.fullmatch(r"[-+]?\d+(\.\d+)?", c.strip()) for c in first)
        if has_header:
            dt_idx, lat_idx, lon_idx, mesh_idx = _guess_column_map(first)
        else:
            dt_idx, lat_idx, lon_idx, mesh_idx = 6, 15, 14, 24
            row = first
            dt = _parse_row_datetime(row, dt_idx)
            if dt and dt.date() in target_dates:
                mesh_code = _parse_mesh_code(row, mesh_idx, lat_idx, lon_idx, 15, 14)
                if mesh_code in mesh_set:
                    slot = (dt.hour * 60 + dt.minute) // 30
                    hit_slots.add(slot)
                    record_counts[slot] += 1

        for row in reader:
            dt = _parse_row_datetime(row, dt_idx if has_header else 6)
            if dt is None or dt.date() not in target_dates:
                continue
            mesh_code = _parse_mesh_code(
                row,
                mesh_idx if has_header else 24,
                lat_idx if has_header else 15,
                lon_idx if has_header else 14,
                15,
                14,
            )
            if mesh_code not in mesh_set:
                continue
            slot = (dt.hour * 60 + dt.minute) // 30
            hit_slots.add(slot)
            record_counts[slot] += 1
    return hit_slots, dict(record_counts)


def write_output_csv(
    output: Path,
    trip_counts: list[int],
    record_counts: list[int],
    meshes_expr: str,
    dates_expr: str,
) -> None:
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_slot", "存在トリップ数", "該当レコード数", "メッシュ番号", "対象日"])
        for i in range(48):
            w.writerow([slot_label(i), trip_counts[i], record_counts[i], meshes_expr, dates_expr])


def run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    if not input_dir.exists() or not input_dir.is_dir():
        log_error(f"input folder not found: {input_dir}")
        return 2

    target_dates = parse_dates(args.dates)
    if not target_dates:
        log_error("dates empty")
        return 2
    meshes = parse_meshes(args.meshes)
    mesh_set = set(meshes)

    files = iter_csv_files(input_dir, args.recursive)
    total = len(files)
    log_info(f"対象CSV数: {total}")
    log_info(f"対象日数: {len(target_dates)}")
    log_info(f"対象メッシュ: {'+'.join(meshes)}")
    if total <= 0:
        log_error("対象CSVが0件")
        return 2

    log_info("集計定義: 存在トリップ数=1CSVが同一30分帯に存在すれば1件 / 該当レコード数=対象条件に合う行数を加算")

    trip_counts = [0] * 48
    record_counts = [0] * 48
    done = 0
    err = 0
    last_emit_t = 0.0

    for fp in files:
        try:
            hit_slots, record_counts_by_file = process_file(fp, target_dates, mesh_set)
            for s in hit_slots:
                if 0 <= s < 48:
                    trip_counts[s] += 1
                    print(f"SLOTCOUNT:{s}:{trip_counts[s]}", flush=True)
            for s, c in record_counts_by_file.items():
                if 0 <= s < 48 and c > 0:
                    record_counts[s] += c
                    print(f"SLOTRECORD:{s}:{record_counts[s]}", flush=True)
        except Exception as e:
            err += 1
            log_error(f"{fp.name}: {e}")

        done += 1
        now = time.time()
        if now - last_emit_t >= PROGRESS_EMIT_SEC or done == total:
            print(f"進捗ファイル: {done}/{total}", flush=True)
            last_emit_t = now

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_output_csv(out, trip_counts, record_counts, "+".join(meshes), args.dates_compact or args.dates)

    log_info(f"エラー数: {err}")
    log_info(f"出力CSV: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="30分帯存在トリップ数集計")
    p.add_argument("--input", required=True, help="第1スクリーニング済みフォルダ")
    p.add_argument("--meshes", required=True, help="2次メッシュ(+)区切り")
    p.add_argument("--dates", required=True, help="対象日(JSON array または CSV)")
    p.add_argument("--dates-compact", default="", help="出力CSV用圧縮日付表現")
    p.add_argument("--recursive", action="store_true", help="サブフォルダを含める")
    p.add_argument("--output", required=True, help="出力CSVパス")
    return p


if __name__ == "__main__":
    parser = build_parser()
    ns = parser.parse_args()
    sys.exit(run(ns))
