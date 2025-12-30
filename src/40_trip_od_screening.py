"""様式1-3参照ODリスト生成スクリプト。

入力フォルダにある「第1/第2 どちらのCSVでも良い」トリップデータから
(運行日, 運行ID, トリップ番号) のキーをストリーミング抽出し、様式1-3 ZIP
(ZIP 内 `data.csv` 想定) を運行日で絞り込んで OD 座標を引き当てます。

1行=1トリップの **「様式1-3参照ODリスト」** を出力し、後段の
``OD_extractor.py`` でゾーン集計するための下処理を行います。
"""

from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Mapping, Sequence

# ============================================================================
# CONFIG — ここだけ触ればOK
# ============================================================================
# 出力基準フォルダ（各データセットの od_list_* をここに作成）
# - 出力先は必ず OUTPUT_DIR 配下
# - dataset側では output_od_list_name（ファイル名のみ）。省略すると自動命名
OUTPUT_DIR = Path(r"C:\path\to\od_output")

# 曜日フィルタ（通常は使わない：OD_extractor側で絞る）
# None の場合は全曜日を出力する
TARGET_WEEKDAYS: set[str] | None = None

# データセット定義
# - input_dir: 第1/第2どちらでもOK（ファイル名にも依存しない）
# - style13_dir: 様式1-3 ZIP が並ぶフォルダ（ZIP 内に data.csv 想定）
# - output_od_list_name: 出力する「様式1-3参照ODリスト」のファイル名（省略可）
DATASETS: list[dict[str, str | Path]] = [
    {
        "name": "dataset01",
        "input_dir": Path(r"C:\path\to\inputs"),
        "style13_dir": Path(r"C:\path\to\style13"),
        "output_od_list_name": "od_list_style1-3_dataset01.csv",
    },
]

# ============================================================================
# 正規表現とヘルパ
# ============================================================================

ZIP_DATE_PATTERN = re.compile(r"(\d{8})")

FILE_ENCODINGS = ("utf-8-sig", "utf-8", "cp932")
ZIP_ENCODINGS = ("cp932", "utf-8-sig", "utf-8")

OUTPUT_HEADER = [
    "dataset",
    "operation_date",
    "weekday",
    "opid",
    "trip_no",
    "o_lon",
    "o_lat",
    "d_lon",
    "d_lat",
    "status",
    "src_files_count",
]

ZIP_HEARTBEAT_SEC = 0.7


# ============================================================================
# ログ・進捗表示
# ============================================================================


def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


class ProgressPrinter:
    """改行なしで進捗を上書き表示する。"""

    def __init__(self, label: str) -> None:
        self.label = label
        self.start_time = datetime.now()
        self.last_print = datetime.now()

    def update(self, *, done: int, total: int, hit: int, missing: int, note: str = "") -> None:
        now = datetime.now()
        if (now - self.last_print).total_seconds() < 0.5 and done != total and not note:
            return
        self.last_print = now
        elapsed = now - self.start_time
        percent = (done / total * 100) if total else 0
        eta_seconds = (elapsed.total_seconds() / done * (total - done)) if done else float("nan")
        eta = timedelta(seconds=eta_seconds)
        eta_str = str(eta).split(".")[0] if eta == eta else "--:--:--"
        line = (
            f"\r[{self.label}] {done}/{total} ({percent:5.1f}%) "
            f"HIT:{hit} MISSING:{missing} ETA:{eta_str}"
        )
        if note:
            line += f" | {note}"
        print(line, end="", flush=True)

    def finalize(self) -> None:
        print()


# ============================================================================
# 入力走査（キー収集）
# ============================================================================


@dataclass
class KeyMeta:
    operation_date: str
    weekday: str
    opid: str
    trip_no: int
    src_files_count: int = 0


@dataclass
class CollectStats:
    csv_total: int = 0
    csv_done: int = 0
    rows_total: int = 0
    skipped_weekday: int = 0
    invalid_rows: int = 0
    meta_map: dict[tuple[str, str, int], KeyMeta] = field(default_factory=dict)


def iter_csv_rows(path: Path, encodings: Sequence[str]) -> Iterator[list[str]]:
    for enc in encodings:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    yield row
            return
        except UnicodeDecodeError:
            continue


def weekday_from_date(date_text: str) -> str:
    try:
        dt = datetime.strptime(date_text.strip(), "%Y%m%d")
    except Exception:
        return ""
    return "月火水木金土日"[dt.weekday()]


def collect_wanted_keys(
    *,
    input_dir: Path,
    target_weekdays: set[str] | None,
) -> tuple[set[tuple[str, str, int]], set[str], CollectStats]:
    """入力CSV群から (運行日, 運行ID, トリップ番号) のキー集合を収集する。"""

    wanted_keys: set[tuple[str, str, int]] = set()
    needed_dates: set[str] = set()
    stats = CollectStats()

    log("[Phase0] scanning CSV file list...")
    files = []
    for p in input_dir.iterdir():
        if p.suffix.lower() == ".csv":
            files.append(p)
    csv_total = len(files)
    log(f"[Phase0] found {csv_total:,} CSV files")
    stats.csv_total = csv_total

    for csv_idx, csv_path in enumerate(files, start=1):
        percent = csv_idx * 100.0 / stats.csv_total if stats.csv_total else 0.0
        msg = f"[Phase1 CSV] {csv_idx}/{stats.csv_total} ({percent:6.2f}%) file={csv_path.name}"
        print("\r" + msg.ljust(120), end="", flush=True)
        stats.csv_done += 1
        seen_in_file: set[tuple[str, str, int]] = set()
        rows_read = 0
        for row in iter_csv_rows(csv_path, FILE_ENCODINGS):
            rows_read += 1
            stats.rows_total += 1
            op_date = ""
            opid = ""
            trip_token = ""
            weekday = ""
            valid = True
            if len(row) < 9:
                stats.invalid_rows += 1
                valid = False
            else:
                op_date = (row[2] or "").strip()
                opid = (row[3] or "").strip()
                trip_token = (row[8] or "").strip()
                if not (op_date and opid and trip_token):
                    stats.invalid_rows += 1
                    valid = False
                else:
                    weekday = weekday_from_date(op_date)
                    if not weekday:
                        stats.invalid_rows += 1
                        valid = False
                    elif target_weekdays and weekday not in target_weekdays:
                        stats.skipped_weekday += 1
                        valid = False
                    elif not trip_token.isdigit():
                        stats.invalid_rows += 1
                        valid = False
            if valid:
                trip_no = int(trip_token)
                key = (op_date, opid, trip_no)
                needed_dates.add(op_date)
                if key not in wanted_keys:
                    wanted_keys.add(key)
                    stats.meta_map[key] = KeyMeta(
                        operation_date=op_date,
                        weekday=weekday,
                        opid=opid,
                        trip_no=trip_no,
                        src_files_count=1,
                    )
                    seen_in_file.add(key)
                elif key not in seen_in_file:
                    seen_in_file.add(key)
                    stats.meta_map[key].src_files_count += 1

    print()

    return wanted_keys, needed_dates, stats


# ============================================================================
# ZIP 走査（ストリーミング）
# ============================================================================


def iter_csv_rows_from_zip_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[list[str]]:
    for enc in ZIP_ENCODINGS:
        try:
            with zf.open(info) as fp:
                text = io.TextIOWrapper(fp, encoding=enc, errors="replace", newline="")
                reader = csv.reader(text)
                for row in reader:
                    yield row
            return
        except UnicodeDecodeError:
            continue


def choose_zip_member(zf: zipfile.ZipFile) -> zipfile.ZipInfo | None:
    fallback: zipfile.ZipInfo | None = None
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".csv"):
            continue
        filename = Path(info.filename).name.lower()
        if filename == "data.csv":
            return info
        if fallback is None:
            fallback = info
    return fallback


def build_youshiki_lookup(
    *,
    zip_dir: Path,
    wanted_keys: set[tuple[str, str, int]],
    needed_dates: set[str],
) -> dict[tuple[str, str, int], tuple[str, str, str, str]]:
    lookup: dict[tuple[str, str, int], tuple[str, str, str, str]] = {}
    remaining = set(wanted_keys)
    if not remaining:
        return lookup

    zip_files = sorted(p for p in zip_dir.glob("*.zip") if p.is_file())

    dated_zips: list[tuple[Path, str]] = []
    unknown_date_zips: list[Path] = []
    for zp in zip_files:
        m = ZIP_DATE_PATTERN.search(zp.name)
        if m:
            dated_zips.append((zp, m.group(1)))
        else:
            unknown_date_zips.append(zp)

    target_zips: list[tuple[Path, str]] = []
    skipped_zips: list[tuple[Path, str]] = []
    for zip_path, zip_date in dated_zips:
        if needed_dates and zip_date not in needed_dates:
            skipped_zips.append((zip_path, zip_date))
        else:
            target_zips.append((zip_path, zip_date))

    target_total = len(target_zips)

    def process_zip(zip_path: Path, *, label: str) -> None:
        nonlocal remaining
        if not zip_path.exists():
            log(f"[WARN] ZIP not found: {zip_path}")
            return
        zip_label = f"[Phase2 ZIP] {label} zip={zip_path.name}"
        print("\r" + f"{zip_label} ...".ljust(120), end="", flush=True)
        zip_t0 = time.perf_counter()
        last_beat = zip_t0
        rows_read = 0
        hit_before = len(lookup)
        with zipfile.ZipFile(zip_path) as zf:
            member = choose_zip_member(zf)
            if member is None:
                log(f"[WARN] ZIP内にCSVがありません: {zip_path.name}")
                print("\r" + f"{zip_label} [WARN] no CSV".ljust(120), end="", flush=True)
                print()
                return
            rows_iter = iter_csv_rows_from_zip_member(zf, member)
            header_skipped = False
            for row in rows_iter:
                rows_read += 1
                cell0 = (row[0] or "").strip() if row else ""
                if not header_skipped and cell0.startswith("運行日"):
                    header_skipped = True
                else:
                    header_skipped = True
                    if len(row) >= 15:
                        op_date = (row[0] or "").strip()
                        opid = (row[1] or "").strip()
                        trip_token = (row[7] or "").strip()
                        if op_date and opid and trip_token.isdigit():
                            key = (op_date, opid, int(trip_token))
                            if key in remaining:
                                o_lon, o_lat, d_lon, d_lat = row[11], row[12], row[13], row[14]
                                lookup[key] = (o_lon, o_lat, d_lon, d_lat)
                                remaining.discard(key)
                now = time.perf_counter()
                if now - last_beat >= ZIP_HEARTBEAT_SEC:
                    elapsed = now - zip_t0
                    rate = rows_read / elapsed if elapsed > 0 else 0.0
                    msg = (
                        f"{zip_label} rows={rows_read:,} "
                        f"rate={rate:,.0f}/s hit={len(lookup)} missing={len(remaining)}"
                    )
                    print("\r" + msg.ljust(120), end="", flush=True)
                    last_beat = now
                if not remaining:
                    break
        elapsed = time.perf_counter() - zip_t0
        rate = rows_read / elapsed if elapsed > 0 else 0.0
        final_msg = (
            f"{zip_label} rows={rows_read:,} "
            f"rate={rate:,.0f}/s hit={len(lookup)} missing={len(remaining)}"
        )
        if rows_read and elapsed >= 0:
            elapsed_td = timedelta(seconds=int(elapsed))
            final_msg += f" elapsed={elapsed_td}"
        hit_added = len(lookup) - hit_before
        if hit_added:
            final_msg += f" hit+= {hit_added}"
        print("\r" + final_msg.ljust(120), end="", flush=True)
        print()

    for idx, (zip_path, zip_date) in enumerate(target_zips, start=1):
        label = f"{idx}/{target_total}"
        process_zip(zip_path, label=label)
        if not remaining:
            break

    if skipped_zips:
        log(f"[Phase2 ZIP] skipped by date: {len(skipped_zips)}")

    if remaining and unknown_date_zips:
        log(f"[Phase2 ZIP] unknown date zips: {len(unknown_date_zips)}")
        for zip_path in unknown_date_zips:
            process_zip(zip_path, label="unknown")
            if not remaining:
                break

    return lookup


# ============================================================================
# 出力組み立て
# ============================================================================


def build_output_rows(
    *,
    dataset_name: str,
    wanted_keys: set[tuple[str, str, int]],
    meta_map: Mapping[tuple[str, str, int], KeyMeta],
    od_lookup: Mapping[tuple[str, str, int], tuple[str, str, str, str]],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for op_date, opid, trip_no in sorted(wanted_keys):
        meta = meta_map.get((op_date, opid, trip_no)) or KeyMeta(
            operation_date=op_date,
            weekday=weekday_from_date(op_date),
            opid=opid,
            trip_no=trip_no,
            src_files_count=0,
        )
        od = od_lookup.get((op_date, opid, trip_no))
        status = "OK" if od else "MISSING_OD"
        o_lon, o_lat, d_lon, d_lat = od if od else ("", "", "", "")
        rows.append(
            [
                dataset_name,
                meta.operation_date,
                meta.weekday,
                meta.opid,
                meta.trip_no,
                o_lon,
                o_lat,
                d_lon,
                d_lat,
                status,
                meta.src_files_count,
            ]
        )
    return rows


# ============================================================================
# メインフロー
# ============================================================================

_DEPRECATED_OUTPUT_LOGGED = False


def process_dataset(dataset: Mapping[str, str | Path]) -> None:
    name = str(dataset.get("name", "(no-name)"))
    input_dir = Path(dataset["input_dir"])
    style13_dir = Path(dataset["style13_dir"])
    output_name = dataset.get("output_od_list_name")
    if "output_od_list_csv" in dataset:
        global _DEPRECATED_OUTPUT_LOGGED
        if not _DEPRECATED_OUTPUT_LOGGED:
            log("NOTE: output_od_list_csv は廃止。OUTPUT_DIR と output_od_list_name を使用します")
            _DEPRECATED_OUTPUT_LOGGED = True
    output_name = output_name or f"od_list_style1-3_{name}.csv"
    output_name = str(output_name)
    output_name_path = Path(output_name)
    if output_name_path.name != output_name:
        log(f"[INFO] output_od_list_name はファイル名のみを使用します: {output_name_path.name}")

    log(f"=== Dataset: {name} ===")
    if not input_dir.exists():
        log(f"[WARN] input_dir not found: {input_dir}")
        return
    if not style13_dir.exists():
        log(f"[WARN] style13_dir not found: {style13_dir}")
        return

    log(f"入力フォルダ: {input_dir}")
    log(f"様式1-3フォルダ: {style13_dir}")

    wanted_keys, needed_dates, stats = collect_wanted_keys(
        input_dir=input_dir,
        target_weekdays=TARGET_WEEKDAYS,
    )
    log(
        f"wanted_keys: {len(wanted_keys)} 件 "
        f"(rows={stats.rows_total}, skip_weekday={stats.skipped_weekday}, invalid={stats.invalid_rows})"
    )

    if not wanted_keys:
        log("[WARN] 対象トリップがありません。出力のみ実行します。")

    od_lookup = build_youshiki_lookup(
        zip_dir=style13_dir,
        wanted_keys=wanted_keys,
        needed_dates=needed_dates,
    )

    output_rows = build_output_rows(
        dataset_name=name,
        wanted_keys=wanted_keys,
        meta_map=stats.meta_map,
        od_lookup=od_lookup,
    )

    output_path = (OUTPUT_DIR / output_name_path.name).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_HEADER)
        writer.writerows(output_rows)
    log(f"出力: {output_path} ({len(output_rows)} 行)")


def main() -> None:
    log("40_trip_od_screening を開始します")
    for ds in DATASETS:
        process_dataset(ds)
    log("完了")


if __name__ == "__main__":
    main()
