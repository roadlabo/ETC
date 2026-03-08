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


def iter_csv_files(folder: Path, recursive: bool) -> list[Path]:
    if recursive:
        return sorted(p for p in folder.rglob("*.csv") if p.is_file())
    return sorted(p for p in folder.glob("*.csv") if p.is_file())


def _guess_datetime_idx(header: list[str]) -> int | None:
    hmap = {c.strip().lower(): i for i, c in enumerate(header)}

    def pick(cands: Iterable[str]) -> int | None:
        for c in cands:
            if c in hmap:
                return hmap[c]
        return None

    return pick(["gps時刻", "gps", "gps_time", "gpsdatetime", "datetime", "time", "timestamp", "date"])


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


def slot_label(slot_index: int) -> str:
    start_m = slot_index * 30
    end_m = start_m + 29
    sh, sm = divmod(start_m, 60)
    eh, em = divmod(end_m, 60)
    return f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"


def process_file(path: Path, target_dates: set[date]) -> dict[int, int]:
    slot_counts: defaultdict[int, int] = defaultdict(int)
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        first = next(reader, None)
        if first is None:
            return dict(slot_counts)

        has_header = any(not re.fullmatch(r"[-+]?\d+(\.\d+)?", c.strip()) for c in first)
        if has_header:
            dt_idx = _guess_datetime_idx(first)
        else:
            dt_idx = 6
            row = first
            dt = _parse_row_datetime(row, dt_idx)
            if dt and dt.date() in target_dates:
                slot = (dt.hour * 60 + dt.minute) // 30
                slot_counts[slot] += 1

        for row in reader:
            dt = _parse_row_datetime(row, dt_idx if has_header else 6)
            if dt is None or dt.date() not in target_dates:
                continue
            slot = (dt.hour * 60 + dt.minute) // 30
            slot_counts[slot] += 1
    return dict(slot_counts)


def write_output_csv(
    output: Path,
    slot_counts_total: list[int],
    target_day_count: int,
    dates_expr: str,
) -> None:
    safe_target_day_count = max(1, target_day_count)
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_slot", "対象日該当レコード総数", "対象日該当レコード数（日平均）", "対象日"])
        for i in range(48):
            total = slot_counts_total[i]
            avg = int(total / safe_target_day_count)
            w.writerow([slot_label(i), total, avg, dates_expr])


def run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    if not input_dir.exists() or not input_dir.is_dir():
        log_error(f"input folder not found: {input_dir}")
        return 2

    target_dates = parse_dates(args.dates)
    if not target_dates:
        log_error("dates empty")
        return 2
    files = iter_csv_files(input_dir, args.recursive)
    total = len(files)
    target_day_count = max(1, len(target_dates))
    log_info(f"対象CSV数: {total}")
    log_info(f"対象日数: {target_day_count}")
    log_info("集計条件: 対象日一致のみ（メッシュ条件なし）")
    log_info("集計方法: 全CSVの全行を走査し、対象日の日時を30分スロットへ累積")
    log_info("表示値: 対象日数で割った日平均レコード数（整数止め）")
    log_info("SLOTCOUNT は日平均レコード数を表す（内部では総数を保持）")
    if total <= 0:
        log_error("対象CSVが0件")
        return 2

    slot_counts_total = [0] * 48
    done = 0
    err = 0
    last_emit_t = 0.0

    for fp in files:
        try:
            file_slot_counts = process_file(fp, target_dates)
            for s, c in file_slot_counts.items():
                if 0 <= s < 48 and c > 0:
                    slot_counts_total[s] += c
            for s in range(48):
                avg_count = int(slot_counts_total[s] / target_day_count)
                print(f"SLOTCOUNT:{s}:{avg_count}", flush=True)
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
    write_output_csv(out, slot_counts_total, target_day_count, args.dates_compact or args.dates)

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
