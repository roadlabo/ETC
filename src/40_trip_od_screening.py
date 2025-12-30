"""様式1-3参照ODリスト生成スクリプト。

本スクリプトは、入力フォルダに格納された「第1（運行IDごと）」または
「第2（トリップごと）」のCSVファイルを自動判定し、(運行日, 運行ID,
トリップ番号) のキー集合を作成します。さらに、様式1-3 ZIP をストリーミング
参照して OD 座標を取得し、1行=1トリップの「様式1-3参照ODリスト」を出力します。

出力した OD リストは、後段の ``OD_extractor.py`` でゾーン集計を行う前段として
利用します。
"""

from __future__ import annotations

import csv
import io
import itertools
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

# ============================================================================
# CONFIG — ここだけ触ればOK
# ============================================================================
# 出力基準フォルダ（各データセットの od_list_* をここに作成）
OUTPUT_DIR = Path(r"C:\path\to\od_output")

# 曜日フィルタ（デフォルト: 火・水・木）
TARGET_WEEKDAYS = {"火", "水", "木"}

# データセット定義
# - input_dir: 第1/第2 どちらでもOK。中身を自動判定。
# - style13_dir: 様式1-3 ZIP が並ぶフォルダ（ZIP 内に data.csv 想定）
# - output_od_list_csv: 出力する「様式1-3参照ODリスト」のファイル名
DATASETS: list[dict[str, Path]] = [
    {
        "name": "dataset01",
        "input_dir": Path(r"C:\path\to\inputs"),
        "style13_dir": Path(r"C:\path\to\style13"),
        "output_od_list_csv": Path("od_list_style1-3.csv"),
    },
]

# 入力ファイルの先頭数行だけ覗いて判定する際の行数
SNIFF_ROWS = 20

# ============================================================================
# 正規表現とヘルパ
# ============================================================================

SPLIT1_PATTERN = re.compile(r"^R\d+_\d{2}_(\d{12})\.csv$")
TRIP2_PATTERN = re.compile(
    r"^2nd_(?P<route>.+?)_(?P<wd>[A-Z]{3})_ID(?P<opid>\d{12})_"
    r"(?P<date>\d{8})_t(?P<trip>\d{3})_E\d+_F\d+\.csv$"
)
ZIP_DATE_PATTERN = re.compile(r"OUT1-3_(\d{8})\.zip$", re.IGNORECASE)

WEEKDAY_MAP = {
    "MON": "月",
    "TUE": "火",
    "WED": "水",
    "THU": "木",
    "FRI": "金",
    "SAT": "土",
    "SUN": "日",
}

FILE_ENCODINGS = ("utf-8-sig", "utf-8", "cp932")
ZIP_ENCODINGS = ("cp932", "utf-8-sig", "utf-8")

OUTPUT_HEADER = [
    "dataset",
    "source_kind",
    "operation_date",
    "weekday",
    "opid",
    "trip_no",
    "route_name",
    "o_lon",
    "o_lat",
    "d_lon",
    "d_lat",
    "status",
    "src_file",
]


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

    def update(self, *, done: int, total: int, hit: int, missing: int) -> None:
        now = datetime.now()
        if (now - self.last_print).total_seconds() < 0.5 and done != total:
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
        print(line, end="", flush=True)

    def finalize(self) -> None:
        print()


# ============================================================================
# 入力判定とキー収集
# ============================================================================


def detect_input_kind(filename: str) -> str:
    """ファイル名から入力種別を判定する。"""

    if TRIP2_PATTERN.match(filename):
        return "trip2"
    if SPLIT1_PATTERN.match(filename):
        return "split1"
    return "unknown"


@dataclass
class TripMeta:
    dataset: str
    source_kind: str
    operation_date: str
    weekday: str
    opid: str
    trip_no: str
    route_name: str
    src_file: str
    status: str = field(default="PENDING")
    o_lon: str = field(default="")
    o_lat: str = field(default="")
    d_lon: str = field(default="")
    d_lat: str = field(default="")


@dataclass
class WantedResult:
    wanted_keys: set[tuple[str, str, int]]
    meta_map: dict[tuple[str, str, int], TripMeta]
    skipped_meta: list[TripMeta]


# ------------- CSV 読み込みヘルパ -------------


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


def sniff_input_kind(path: Path) -> str:
    """ファイル内容を先頭数行だけ読んで種別を推定する。"""

    rows: list[list[str]] = []
    for i, row in enumerate(iter_csv_rows(path, FILE_ENCODINGS), start=1):
        rows.append(row)
        if i >= SNIFF_ROWS:
            break

    if not rows:
        return "unknown"

    # 第1: 運行日=C列(2), 運行ID=D列(3), トリップ番号=I列(8) がありそうか
    for row in rows:
        if len(row) >= 9:
            date_token = (row[2] or "").strip()
            opid_token = (row[3] or "").strip()
            trip_token = (row[8] or "").strip()
            if (
                len(date_token) == 8
                and date_token.isdigit()
                and opid_token.isdigit()
                and trip_token
            ):
                return "split1"

    # 第2: 1行=1トリップで列数が小さい（6〜8列程度）
    for row in rows:
        if 5 <= len(row) <= 10:
            if any("ID" in cell for cell in row):
                return "trip2"

    return "unknown"


# ------------- 曜日変換 -------------


def weekday_from_date(date_text: str) -> str:
    try:
        dt = datetime.strptime(date_text.strip(), "%Y%m%d")
    except Exception:
        return ""
    return "月火水木金土日"[dt.weekday()]


# ------------- wanted_keys 収集 -------------


def collect_wanted_keys_from_input_dir(
    *,
    dataset_name: str,
    input_dir: Path,
    target_weekdays: set[str],
) -> WantedResult:
    wanted_keys: set[tuple[str, str, int]] = set()
    meta_map: dict[tuple[str, str, int], TripMeta] = {}
    skipped_meta: list[TripMeta] = []

    files = sorted(p for p in input_dir.glob("*.csv") if p.is_file())
    progress = ProgressPrinter(label=f"Phase1 {dataset_name}")
    total = len(files)
    done = 0

    for csv_path in files:
        done += 1
        filename = csv_path.name
        kind = detect_input_kind(filename)
        if kind == "unknown":
            kind = sniff_input_kind(csv_path)
        if kind == "unknown":
            log(f"[WARN] unknown file pattern, skipping: {filename}")
            skipped_meta.append(
                TripMeta(
                    dataset=dataset_name,
                    source_kind="unknown",
                    operation_date="",
                    weekday="",
                    opid="",
                    trip_no="",
                    route_name="",
                    src_file=filename,
                    status="UNKNOWN_INPUT",
                )
            )
            progress.update(done=done, total=total, hit=len(wanted_keys), missing=len(skipped_meta))
            continue

        if kind == "trip2":
            m = TRIP2_PATTERN.match(filename)
            if not m:
                log(f"[WARN] trip2 file did not match pattern after detect: {filename}")
                progress.update(done=done, total=total, hit=len(wanted_keys), missing=len(skipped_meta))
                continue
            op_date = m.group("date")
            opid = m.group("opid")
            trip_no = m.group("trip")
            weekday_en = m.group("wd")
            weekday_jp = WEEKDAY_MAP.get(weekday_en.upper(), "")
            route_name = m.group("route")

            if weekday_jp and weekday_jp not in target_weekdays:
                skipped_meta.append(
                    TripMeta(
                        dataset=dataset_name,
                        source_kind="trip2",
                        operation_date=op_date,
                        weekday=weekday_jp,
                        opid=opid,
                        trip_no=str(int(trip_no)),
                        route_name=route_name,
                        src_file=filename,
                        status="SKIP_WEEKDAY",
                    )
                )
                progress.update(done=done, total=total, hit=len(wanted_keys), missing=len(skipped_meta))
                continue

            key = (op_date, opid, int(trip_no))
            if key not in wanted_keys:
                wanted_keys.add(key)
                meta_map[key] = TripMeta(
                    dataset=dataset_name,
                    source_kind="trip2",
                    operation_date=op_date,
                    weekday=weekday_jp or weekday_from_date(op_date),
                    opid=opid,
                    trip_no=str(int(trip_no)),
                    route_name=route_name,
                    src_file=filename,
                )
            progress.update(done=done, total=total, hit=len(wanted_keys), missing=len(skipped_meta))
            continue

        # split1
        seen_in_file: set[tuple[str, str, int]] = set()
        for row in iter_csv_rows(csv_path, FILE_ENCODINGS):
            if len(row) < 9:
                continue
            op_date = (row[2] or "").strip()
            opid = (row[3] or "").strip()
            trip_token = (row[8] or "").strip()
            if not (op_date and opid and trip_token.isdigit() and len(opid) == 12):
                continue
            weekday_jp = weekday_from_date(op_date)
            if weekday_jp and weekday_jp not in target_weekdays:
                key = (op_date, opid, int(trip_token))
                if key not in seen_in_file:
                    seen_in_file.add(key)
                    skipped_meta.append(
                        TripMeta(
                            dataset=dataset_name,
                            source_kind="split1",
                            operation_date=op_date,
                            weekday=weekday_jp,
                            opid=opid,
                            trip_no=str(int(trip_token)),
                            route_name="",
                            src_file=filename,
                            status="SKIP_WEEKDAY",
                        )
                    )
                continue

            trip_no = int(trip_token)
            key = (op_date, opid, trip_no)
            if key in seen_in_file:
                continue
            seen_in_file.add(key)
            if key not in wanted_keys:
                wanted_keys.add(key)
                meta_map[key] = TripMeta(
                    dataset=dataset_name,
                    source_kind="split1",
                    operation_date=op_date,
                    weekday=weekday_jp,
                    opid=opid,
                    trip_no=str(trip_no),
                    route_name="",
                    src_file=filename,
                )
        progress.update(done=done, total=total, hit=len(wanted_keys), missing=len(skipped_meta))

    progress.finalize()
    return WantedResult(wanted_keys=wanted_keys, meta_map=meta_map, skipped_meta=skipped_meta)


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
) -> dict[tuple[str, str, int], tuple[str, str, str, str]]:
    lookup: dict[tuple[str, str, int], tuple[str, str, str, str]] = {}
    remaining = set(wanted_keys)
    needed_dates = {key[0] for key in wanted_keys}

    zip_files = sorted(p for p in zip_dir.glob("*.zip") if p.is_file())
    total = len(zip_files)
    progress = ProgressPrinter(label="Phase2 ZIP")

    if not remaining:
        progress.finalize()
        return lookup

    for idx, zip_path in enumerate(zip_files, start=1):
        if not remaining:
            break
        date_match = ZIP_DATE_PATTERN.search(zip_path.name)
        if date_match:
            zip_date = date_match.group(1)
            needed_dates = {k[0] for k in remaining}
            if zip_date not in needed_dates:
                progress.update(done=idx, total=total, hit=len(lookup), missing=len(remaining))
                continue

        if not zip_path.exists():
            log(f"[WARN] ZIP not found: {zip_path}")
            progress.update(done=idx, total=total, hit=len(lookup), missing=len(remaining))
            continue

        with zipfile.ZipFile(zip_path) as zf:
            member = choose_zip_member(zf)
            if member is None:
                log(f"[WARN] ZIP内にCSVがありません: {zip_path.name}")
                progress.update(done=idx, total=total, hit=len(lookup), missing=len(remaining))
                continue

            rows_iter = iter_csv_rows_from_zip_member(zf, member)
            try:
                first_row = next(rows_iter)
            except StopIteration:
                progress.update(done=idx, total=total, hit=len(lookup), missing=len(remaining))
                continue

            header = first_row if len(first_row) >= 18 and ("運行日" in first_row[0]) else None
            if header is None:
                data_iter: Iterable[list[str]] = itertools.chain((first_row,), rows_iter)
            else:
                data_iter = rows_iter

            for row in data_iter:
                if len(row) < 15:
                    continue
                op_date = (row[0] or "").strip()
                opid = (row[1] or "").strip()
                trip_token = (row[7] or "").strip()
                if not (op_date and opid and trip_token.isdigit()):
                    continue
                trip_no = int(trip_token)
                key = (op_date, opid, trip_no)
                if key not in remaining:
                    continue
                o_lon, o_lat, d_lon, d_lat = row[11], row[12], row[13], row[14]
                lookup[key] = (o_lon, o_lat, d_lon, d_lat)
                remaining.discard(key)
                if not remaining:
                    break
        progress.update(done=idx, total=total, hit=len(lookup), missing=len(remaining))

    progress.finalize()
    return lookup


# ============================================================================
# 出力組み立て
# ============================================================================


def build_output_rows(
    *,
    dataset_name: str,
    wanted_keys: set[tuple[str, str, int]],
    meta_map: Mapping[tuple[str, str, int], TripMeta],
    skipped_meta: Sequence[TripMeta],
    od_lookup: Mapping[tuple[str, str, int], tuple[str, str, str, str]],
) -> list[list[str]]:
    rows: list[list[str]] = []
    # Wanted keys: OK / MISSING_OD
    for key in sorted(wanted_keys):
        meta = meta_map.get(key)
        if meta is None:
            op_date, opid, trip_no = key
            meta = TripMeta(
                dataset=dataset_name,
                source_kind="unknown",
                operation_date=op_date,
                weekday=weekday_from_date(op_date),
                opid=opid,
                trip_no=str(trip_no),
                route_name="",
                src_file="",
            )
        od = od_lookup.get(key)
        if od:
            meta.status = "OK"
            meta.o_lon, meta.o_lat, meta.d_lon, meta.d_lat = od
        else:
            meta.status = "MISSING_OD"
        rows.append(
            [
                meta.dataset,
                meta.source_kind,
                meta.operation_date,
                meta.weekday,
                meta.opid,
                meta.trip_no,
                meta.route_name,
                meta.o_lon,
                meta.o_lat,
                meta.d_lon,
                meta.d_lat,
                meta.status,
                meta.src_file,
            ]
        )

    # skipped/unknown entriesも追加
    for meta in skipped_meta:
        rows.append(
            [
                meta.dataset,
                meta.source_kind,
                meta.operation_date,
                meta.weekday,
                meta.opid,
                meta.trip_no,
                meta.route_name,
                meta.o_lon,
                meta.o_lat,
                meta.d_lon,
                meta.d_lat,
                meta.status,
                meta.src_file,
            ]
        )
    return rows


# ============================================================================
# メインフロー
# ============================================================================


def process_dataset(dataset: Mapping[str, Path]) -> None:
    name = str(dataset.get("name", "(no-name)"))
    input_dir = Path(dataset["input_dir"])
    style13_dir = Path(dataset["style13_dir"])
    output_csv = dataset.get("output_od_list_csv", Path(f"od_list_{name}.csv"))

    log(f"=== Dataset: {name} ===")
    if not input_dir.exists():
        log(f"[WARN] input_dir not found: {input_dir}")
        return
    if not style13_dir.exists():
        log(f"[WARN] style13_dir not found: {style13_dir}")
        return

    log(f"入力フォルダ: {input_dir}")
    log(f"様式1-3フォルダ: {style13_dir}")

    result = collect_wanted_keys_from_input_dir(
        dataset_name=name,
        input_dir=input_dir,
        target_weekdays=TARGET_WEEKDAYS,
    )
    log(
        f"wanted_keys: {len(result.wanted_keys)} 件 (skip: {len(result.skipped_meta)} 件)"
    )

    if not result.wanted_keys:
        log("[WARN] 対象トリップがありません。出力のみ実行します。")

    od_lookup = build_youshiki_lookup(
        zip_dir=style13_dir,
        wanted_keys=result.wanted_keys,
    )

    output_rows = build_output_rows(
        dataset_name=name,
        wanted_keys=result.wanted_keys,
        meta_map=result.meta_map,
        skipped_meta=result.skipped_meta,
        od_lookup=od_lookup,
    )

    output_path = (OUTPUT_DIR / output_csv).resolve()
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
