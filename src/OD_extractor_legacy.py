"""HOW TO USE
---------------
このスクリプトは以下を自動生成します（すべて ``OUTPUT_DIR`` 配下）：
- ``od_long.csv``: (operation_date, opid, trip_no) ごとのOD座標とゾーン
- ``od_matrix.csv``: ゾーン間マトリクス（行列ラベルは ``id:name`` 形式）
- ``zone_master.csv``: ゾーン名に連番IDを振ったマスタ（MISSING は最後尾）
- ``zone_production_attraction.csv``: ゾーン別の発着集計

必要な入力は次の3つです：
- ``split_dir``: 「運行IDごとのCSVが直下に大量に並ぶ」階層（例: ``split_1st/000000000001.csv`` ...）
- ``style13_dir``: 「様式1-3 ZIP（data.csv を内包）が直下に並ぶ」階層（例: ``OUT1-3_20250201.zip`` ...）※CSV 展開済みでも可
- ``zones_csv``: ``12_polygon_builder.html`` から出力したゾーンポリゴンCSV

実行手順（設定 → 実行 → 出力確認）
1) ファイル先頭の CONFIG セクションで各パスを設定する
2) ターミナルで ``python src/OD_extractor.py`` を実行する
3) ``OUTPUT_DIR`` に生成された ``od_long / od_matrix / zone_master / production_attraction`` を確認する

よくある間違いと対処
- split_dir を1つ上の階層にしてしまい ``*.csv`` が0件 → 正しく「CSVが直下に並ぶ階層」を指定する
- style13_dir が空 or 別階層 → 「様式1-3 ZIP/CSV が直下にある階層」を指定する
- CSVの列ズレや日付欠損 → エラーメッセージを確認し、列インデックス設定を見直す
- 入力CSV/ZIPが0件 → 実行前チェックが停止するのでパスを修正する
"""

from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Mapping, Sequence


# ===========================================================================
# CONFIG — ここだけ触ればOK
# ===========================================================================

# 出力先フォルダ
OUTPUT_DIR = Path(r"D:\path\to\od_output")

# ゾーンポリゴンCSV（12_polygon_builder.html 出力）
ZONES_CSV_PATH = Path(r"D:\path\to\zones.csv")

# データセットの指定（split_dir と style13_dir をペアで設定）
# split_dir は「運行IDごとのCSVが直接並ぶフォルダ」を指す
# 例: split_1st\000000000001.csv, 000000000002.csv, ...
# style13_dir は「様式1-3 ZIP が直下に並ぶフォルダ」を指す（ZIP 内に data.csv がある想定）
# 例: OUT1-3_20250201.zip, OUT1-3_20250202.zip, ...
# ※CSV 展開済みで data.csv が直下にある場合も対応（CSV があれば CSV、なければ ZIP を自動判定）
# ※1つ上の階層を指定しないこと（*.csv/zip が0件になる）
DATASETS = [
    {
        "name": "R7_02",
        "split_dir": Path(r"D:\path\to\01_split_output"),
        "style13_dir": Path(r"D:\path\to\style1-3_csvs"),
    },
]

# 曜日フィルタ（運行日がこの曜日に一致するデータのみ対象）
TARGET_WEEKDAYS = {"火", "水", "木"}

# 津山市中心（東西南北ゾーン判定の基準点）
TSUYAMA_CENTER_LON = 133.93
TSUYAMA_CENTER_LAT = 35.07

# 入力で試行するエンコーディング候補（上から順に試す）
ENCODINGS = ("utf-8-sig", "utf-8", "cp932")

# 列インデックス（上級者向け）: 0 始まり
SPLIT_COL_OPERATION_DATE = 2  # YYYYMMDD
SPLIT_COL_OPID = 3
SPLIT_COL_TRIP_NO = 8

STYLE13_COL_OPID = 1
STYLE13_COL_TRIP_NO = 7
STYLE13_COL_OPERATION_DATE = 0
STYLE13_COL_O_LON = 11
STYLE13_COL_O_LAT = 12
STYLE13_COL_D_LON = 13
STYLE13_COL_D_LAT = 14

# (opid, trip_no) を一意にカウントする場合は True
COUNT_UNIQUE_TRIP = True

ZIP_ENCODINGS = ("cp932", "utf-8-sig", "utf-8")
ZIP_DATE_PATTERN = re.compile(r"(\d{8})")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def validate_config() -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    """設定ファイルを実行前に検証し、ディレクトリ内CSV/ZIP一覧を返す。"""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - guard clause
        errors.append(f"OUTPUT_DIR を作成できません: {exc}")

    if not DATASETS:
        errors.append("DATASETS が空です。split_dir と style13_dir のペアを設定してください。")

    split_files: dict[str, list[Path]] = {}
    style_files: dict[str, list[Path]] = {}

    for ds in DATASETS:
        name = ds.get("name", "(no-name)")
        split_dir = ds.get("split_dir")
        style_dir = ds.get("style13_dir")

        if split_dir is None or style_dir is None:
            errors.append(f"{name}: split_dir/style13_dir が設定されていません。")
            continue

        split_dir = Path(split_dir)
        style_dir = Path(style_dir)

        if not split_dir.exists():
            errors.append(f"{name}: split_dir が存在しません。パスを確認してください: {split_dir}")
        if not style_dir.exists():
            errors.append(f"{name}: style13_dir が存在しません。パスを確認してください: {style_dir}")

        split_list = list_csv_files(split_dir)
        style_list = list_style13_sources(style_dir)
        split_files[name] = split_list
        style_files[name] = style_list

        if len(split_list) == 0:
            errors.append(f"{name}: split_dirにCSVが見つかりません。1つ上の階層を指定していませんか？")
        if len(style_list) == 0:
            errors.append(
                f"{name}: style13_dirにZIP/CSVが見つかりません。"
                "ZIP（OUT1-3_YYYYMMDD.zip）が直接入っている階層を指定してください。"
            )

    if not ZONES_CSV_PATH.exists():
        warnings.append(f"ゾーンポリゴンCSVが見つかりません: {ZONES_CSV_PATH}")

    for w in warnings:
        log(f"[WARN] {w}")
    if errors:
        log("設定エラーのため終了します:")
        for e in errors:
            log(f"  - {e}")
        sys.exit(1)

    return split_files, style_files

# ---------------------------------------------------------------------------
# Directional zones
# ---------------------------------------------------------------------------

EAST_ZONE = "東方面ゾーン"
WEST_ZONE = "西方面ゾーン"
NORTH_ZONE = "北方面ゾーン"
SOUTH_ZONE = "南方面ゾーン"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PolygonZone:
    name: str
    points: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def log(message: str) -> None:
    """時刻付きでシンプルにログを出す。"""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


class ProgressPrinter:
    """改行なしで進捗を上書き表示する。"""

    def __init__(self) -> None:
        self.start_time = datetime.now()
        self.last_print = datetime.now()
        self.current_line = ""

    def _format_time(self, delta: timedelta) -> str:
        total = int(delta.total_seconds())
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def update(self, *, phase: str, done: int, total: int, hit: int, missing: int) -> None:
        now = datetime.now()
        if (now - self.last_print).total_seconds() < 0.5 and done != total:
            return
        self.last_print = now

        elapsed = now - self.start_time
        percent = (done / total * 100) if total else 0
        eta_seconds = (elapsed.total_seconds() / done * (total - done)) if done else float("nan")
        eta_str = self._format_time(timedelta(seconds=eta_seconds)) if eta_seconds == eta_seconds else "--:--:--"

        line = (
            f"\r[{self.start_time.strftime('%H:%M:%S')}] "
            f"elapsed:{self._format_time(elapsed)} "
            f"ETA:{eta_str} "
            f"{percent:5.1f}% "
            f"HIT:{hit} MISSING:{missing} "
            f"phase:{phase} "
            f"count:{done}/{total}"
        )
        if line != self.current_line:
            print(line, end="", flush=True)
            self.current_line = line

    def finalize(self) -> None:
        """最後に改行して見やすく締める。"""
        print()


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def iter_csv_rows(path: Path, encodings: Sequence[str]) -> Iterator[list[str]]:
    """指定エンコーディング順にCSVを試行して1行ずつ返す。"""
    for enc in encodings:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    yield row
            return
        except UnicodeDecodeError:
            continue


def iter_csv_rows_from_zip_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[list[str]]:
    """ZIP メンバーから CSV をストリーミングで読み込み、エンコーディングを順に試す。"""
    for encoding in ZIP_ENCODINGS:
        try:
            with zf.open(info) as fp:
                text = io.TextIOWrapper(fp, encoding=encoding, errors="replace", newline="")
                reader = csv.reader(text)
                for row in reader:
                    yield row
            return
        except UnicodeDecodeError:
            continue
    log(f"[WARN] ZIPメンバーのデコードに失敗しました: {info.filename}")


def choose_zip_csv_member(zf: zipfile.ZipFile) -> zipfile.ZipInfo | None:
    """ZIP 内の data.csv を優先し、無ければ最初の CSV を返す。"""
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


def normalize_trip_no(value: str) -> int | None:
    """空文字を除外し、整数に変換できる場合のみ返す。"""
    s = (value or "").strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return int(s)
        except Exception:
            return None
    return None


def parse_float(value: str) -> float | None:
    """空文字や変換失敗時は None を返す。"""
    try:
        return float((value or "").strip())
    except Exception:
        return None


def get_weekday_jp(date_text: str) -> str | None:
    """YYYYMMDD を曜日（日本語1文字）に変換する。"""
    s = (date_text or "").strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y%m%d")
    except Exception:
        return None
    return "月火水木金土日"[dt.weekday()]


# ---------------------------------------------------------------------------
# Polygon handling
# ---------------------------------------------------------------------------


def point_in_polygon(lon: float, lat: float, points: Sequence[tuple[float, float]]) -> bool:
    """単純なレイキャスティングで点が多角形に含まれるか判定する。"""
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]
        intersect = (yi > lat) != (yj > lat) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-20) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def load_polygons(csv_path: Path) -> list[PolygonZone]:
    """ゾーンポリゴンCSVを読み込み、bbox付きのリストにする。"""
    polygons: list[PolygonZone] = []
    if not csv_path.exists():
        log(f"[WARN] Polygon CSV not found: {csv_path}")
        return polygons

    for row in iter_csv_rows(csv_path, ENCODINGS):
        if not row:
            continue
        zone_name = (row[0] or "").strip()
        if not zone_name:
            continue
        coords = row[1:]
        if len(coords) < 6:
            continue
        points: list[tuple[float, float]] = []
        for i in range(0, len(coords), 2):
            if i + 1 >= len(coords):
                break
            lon = parse_float(coords[i])
            lat = parse_float(coords[i + 1])
            if lon is None or lat is None:
                continue
            points.append((lon, lat))
        if len(points) < 3:
            continue
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        bbox = (min(lons), min(lats), max(lons), max(lats))
        polygons.append(PolygonZone(zone_name, points, bbox))

    log(f"Loaded polygons: {len(polygons)} zones")
    return polygons


def directional_zone(lon: float, lat: float) -> str:
    """津山市中心点から東西南北のどれに位置するかを返す。"""
    dx = lon - TSUYAMA_CENTER_LON
    dy = lat - TSUYAMA_CENTER_LAT
    if abs(dx) >= abs(dy):
        return EAST_ZONE if dx > 0 else WEST_ZONE
    return NORTH_ZONE if dy > 0 else SOUTH_ZONE


def assign_zone(lon: float | None, lat: float | None, polygons: Sequence[PolygonZone]) -> str:
    """座標がポリゴンに入ればゾーン名、入らなければ東西南北、欠損は MISSING。"""
    if lon is None or lat is None:
        return "MISSING"
    for poly in polygons:
        min_lon, min_lat, max_lon, max_lat = poly.bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if point_in_polygon(lon, lat, poly.points):
            return poly.name
    return directional_zone(lon, lat)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def list_csv_files(directory: Path) -> list[Path]:
    """指定ディレクトリ直下のCSVファイル一覧を返す。"""
    if not directory.exists():
        log(f"[WARN] Directory not found: {directory}")
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".csv")


def list_style13_sources(directory: Path) -> list[Path]:
    """様式1-3 の ZIP/CSV を列挙する。ZIP を優先し、無ければ CSV。"""
    if not directory.exists():
        log(f"[WARN] Directory not found: {directory}")
        return []
    zips = sorted(p for p in directory.glob("*.zip") if p.is_file())
    csvs = sorted(p for p in directory.glob("*.csv") if p.is_file())
    return zips + csvs


def extract_zip_date(path: Path) -> str | None:
    """ZIP 名から YYYYMMDD を抽出する。"""
    m = ZIP_DATE_PATTERN.search(path.name)
    if not m:
        return None
    return m.group(1)


def collect_trip_counts(
    datasets: Sequence[Mapping[str, Path]],
    split_files: Mapping[str, list[Path]],
    progress: ProgressPrinter,
    total_units: int,
) -> tuple[dict[tuple[str, str, int], int], set[str], int, int]:
    """分割CSVを走査し (op_date, opid, trip_no) の頻度を集計する。"""
    counts: dict[tuple[str, str, int], int] = {}
    needed_dates: set[str] = set()
    invalid_date_count = 0
    processed = 0
    for ds in datasets:
        name = ds["name"]
        files = split_files.get(name, [])
        for csv_path in files:
            for row in iter_csv_rows(csv_path, ENCODINGS):
                if len(row) <= SPLIT_COL_TRIP_NO:
                    continue
                op_date = (row[SPLIT_COL_OPERATION_DATE] or "").strip()
                weekday = get_weekday_jp(op_date)
                if weekday is None:
                    invalid_date_count += 1
                    continue
                if weekday not in TARGET_WEEKDAYS:
                    continue

                needed_dates.add(op_date)
                opid = (row[SPLIT_COL_OPID] or "").strip()
                trip_no = normalize_trip_no(row[SPLIT_COL_TRIP_NO])
                if not opid or trip_no is None:
                    continue
                key = (op_date, opid, trip_no)
                counts[key] = counts.get(key, 0) + 1
            processed += 1
            progress.update(
                phase="Phase1 split走査",
                done=processed,
                total=total_units,
                hit=len(counts),
                missing=invalid_date_count,
            )
    if processed == 0:
        progress.update(
            phase="Phase1 split走査",
            done=processed,
            total=total_units,
            hit=len(counts),
            missing=invalid_date_count,
        )
    log(f"Collected trip keys: {len(counts)} unique trip rows")
    return counts, needed_dates, invalid_date_count, processed


def load_od_lookup(
    datasets: Sequence[Mapping[str, Path]],
    style_files: Mapping[str, list[Path]],
    progress: ProgressPrinter,
    total_units: int,
    base_progress: int,
    needed_dates: set[str],
    wanted_keys: set[tuple[str, str, int]],
) -> tuple[dict[tuple[str, str, int], tuple[float, float, float, float]], int]:
    """様式1-3 を ZIP 優先で走査し (op_date, opid, trip_no) → OD座標 を辞書化する。"""
    lookup: dict[tuple[str, str, int], tuple[float, float, float, float]] = {}
    remaining: set[tuple[str, str, int]] = set(wanted_keys)
    processed = 0
    for ds in datasets:
        if not remaining:
            break
        name = ds["name"]
        files = style_files.get(name, [])
        zip_files = [p for p in files if p.suffix.lower() == ".zip"]
        csv_files = [p for p in files if p.suffix.lower() == ".csv"]

        selected_zips: list[Path] = []
        fallback_zips: list[Path] = []
        for zp in zip_files:
            date_token = extract_zip_date(zp)
            if date_token and needed_dates and date_token not in needed_dates:
                processed += 1
                progress.update(
                    phase="Phase2 様式1-3(ZIP/CSV)",
                    done=base_progress + processed,
                    total=total_units,
                    hit=len(lookup),
                    missing=len(remaining),
                )
                continue
            if date_token is None:
                fallback_zips.append(zp)
            else:
                selected_zips.append(zp)

        zip_log_printed = False

        def _register_row(row: list[str]) -> None:
            if len(row) <= STYLE13_COL_D_LAT:
                return
            op_date = (row[STYLE13_COL_OPERATION_DATE] or "").strip()
            opid = (row[STYLE13_COL_OPID] or "").strip()
            trip_no = normalize_trip_no(row[STYLE13_COL_TRIP_NO])
            if not op_date or not opid or trip_no is None:
                return
            key = (op_date, opid, trip_no)
            if key not in remaining:
                return
            o_lon = parse_float(row[STYLE13_COL_O_LON])
            o_lat = parse_float(row[STYLE13_COL_O_LAT])
            d_lon = parse_float(row[STYLE13_COL_D_LON])
            d_lat = parse_float(row[STYLE13_COL_D_LAT])
            if None in (o_lon, o_lat, d_lon, d_lat):
                return
            lookup[key] = (o_lon, o_lat, d_lon, d_lat)
            remaining.discard(key)

        all_zip_paths = selected_zips + fallback_zips
        total_zips = len(all_zip_paths)
        for zip_idx, zip_path in enumerate(all_zip_paths, start=1):
            if not remaining:
                break
            if not zip_path.exists():
                log(f"[WARN] ZIP not found, skipping: {zip_path}")
                processed += 1
                continue
            if not zip_log_printed:
                log(f"Reading ZIP: {zip_path.name}")
                zip_log_printed = True
            zip_date = extract_zip_date(zip_path)
            if zip_date is None:
                log(f"[WARN] ZIP名から日付を抽出できません: {zip_path.name} (needed_dates 優先でないため後順位で処理)")
            progress.update(
                phase=f"Phase2 様式1-3 読込中 (zip {zip_idx}/{total_zips})",
                done=base_progress + processed,
                total=total_units,
                hit=len(lookup),
                missing=len(remaining),
            )
            with zipfile.ZipFile(zip_path) as zf:
                member = choose_zip_csv_member(zf)
                if member is None:
                    log(f"[WARN] ZIP内にCSVが見つかりません: {zip_path}")
                else:
                    last_update = datetime.now()
                    rows_processed = 0
                    for row in iter_csv_rows_from_zip_member(zf, member):
                        rows_processed += 1
                        _register_row(row)
                        now = datetime.now()
                        if (
                            rows_processed % 50000 == 0
                            or (now - last_update).total_seconds() >= 0.5
                        ):
                            progress.update(
                                phase=f"Phase2 様式1-3 読込中 (zip {zip_idx}/{total_zips})",
                                done=base_progress + processed,
                                total=total_units,
                                hit=len(lookup),
                                missing=len(remaining),
                            )
                            last_update = now
                        if not remaining:
                            break
            processed += 1
            progress.update(
                phase="Phase2 様式1-3(ZIP/CSV)",
                done=base_progress + processed,
                total=total_units,
                hit=len(lookup),
                missing=len(remaining),
            )

        for csv_path in csv_files:
            if not remaining:
                break
            for row in iter_csv_rows(csv_path, ENCODINGS):
                _register_row(row)
                if not remaining:
                    break
            processed += 1
            progress.update(
                phase="Phase2 様式1-3(ZIP/CSV)",
                done=base_progress + processed,
                total=total_units,
                hit=len(lookup),
                missing=len(remaining),
            )

        if not selected_zips and not fallback_zips and not csv_files:
            log(f"[WARN] {name}: style13_dir に処理対象のZIP/CSVがありません。")
        if not remaining:
            break

    if processed == 0:
        progress.update(
            phase="Phase2 様式1-3(ZIP/CSV)",
            done=base_progress,
            total=total_units,
            hit=len(lookup),
            missing=len(remaining),
        )
    log(f"Loaded STYLE1-3 records: {len(lookup)} keys")
    return lookup, processed


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------


def ensure_output_dir(path: Path) -> None:
    """出力ディレクトリを作成する。"""
    path.mkdir(parents=True, exist_ok=True)


def build_od_outputs(
    trip_counts: Mapping[tuple[str, str, int], int],
    od_lookup: Mapping[tuple[str, str, int], tuple[float, float, float, float]],
    polygons: Sequence[PolygonZone],
    output_dir: Path,
    progress: ProgressPrinter,
    total_units: int,
    base_progress: int,
) -> None:
    """ゾーン割当と集計を行い、4種類のCSVを出力する。"""
    ensure_output_dir(output_dir)
    od_long_path = output_dir / "od_long.csv"
    od_matrix_path = output_dir / "od_matrix.csv"
    prod_attr_path = output_dir / "zone_production_attraction.csv"
    zone_master_path = output_dir / "zone_master.csv"

    matrix: dict[str, dict[str, float]] = {}
    col_sums: dict[str, float] = {}
    zones_set: set[str] = set()

    unique_total = len(trip_counts)
    found_keys = 0
    missing_keys = 0
    processed = 0

    with od_long_path.open("w", encoding="utf-8-sig", newline="") as f_long:
        writer = csv.writer(f_long)
        writer.writerow([
            "operation_date",
            "opid",
            "trip_no",
            "zone_o",
            "zone_d",
            "o_lon",
            "o_lat",
            "d_lon",
            "d_lat",
            "weight",
        ])

        for op_date, opid, trip_no in sorted(trip_counts.keys()):
            occurrences = trip_counts[(op_date, opid, trip_no)]
            weight = 1 if COUNT_UNIQUE_TRIP else occurrences

            coords = od_lookup.get((op_date, opid, trip_no))
            if coords is None:
                missing_keys += 1
                zone_o = zone_d = "MISSING"
                o_lon = o_lat = d_lon = d_lat = ""
            else:
                found_keys += 1
                o_lon, o_lat, d_lon, d_lat = coords
                zone_o = assign_zone(o_lon, o_lat, polygons)
                zone_d = assign_zone(d_lon, d_lat, polygons)

            zones_set.update([zone_o, zone_d])
            matrix.setdefault(zone_o, {})[zone_d] = matrix.get(zone_o, {}).get(zone_d, 0) + weight
            col_sums[zone_d] = col_sums.get(zone_d, 0) + weight
            processed += 1
            progress.update(
                phase="Phase3 ゾーン割当/集計",
                done=base_progress + processed,
                total=total_units,
                hit=found_keys,
                missing=missing_keys,
            )

            writer.writerow([
                op_date,
                opid,
                trip_no,
                zone_o,
                zone_d,
                o_lon,
                o_lat,
                d_lon,
                d_lat,
                weight,
            ])
    progress.update(
        phase="Phase3 ゾーン割当/集計",
        done=base_progress + max(processed, len(trip_counts)),
        total=total_units,
        hit=found_keys,
        missing=missing_keys,
    )

    zones = sorted(zones_set)
    if "MISSING" in zones:
        zones.remove("MISSING")
        zones.append("MISSING")

    zone_master: list[tuple[int, str]] = []
    for idx, name in enumerate(zones, start=1):
        zone_master.append((idx, name))
    zone_to_id = {name: idx for idx, name in zone_master}

    with zone_master_path.open("w", encoding="utf-8-sig", newline="") as f_master:
        writer = csv.writer(f_master)
        writer.writerow(["zone_id", "zone_name"])
        for idx, name in zone_master:
            writer.writerow([f"{idx:03d}", name])

    with od_matrix_path.open("w", encoding="utf-8-sig", newline="") as f_mat:
        writer = csv.writer(f_mat)
        header = ["O\\D"]
        header.extend(f"{zone_to_id[z]:03d}:{z}" for z in zones)
        writer.writerow(header)
        for zo in zones:
            row_counts = [matrix.get(zo, {}).get(zd, 0) for zd in zones]
            writer.writerow([f"{zone_to_id[zo]:03d}:{zo}", *row_counts])

    with prod_attr_path.open("w", encoding="utf-8-sig", newline="") as f_pa:
        writer = csv.writer(f_pa)
        writer.writerow(["zone", "production", "attraction"])
        for z in zones:
            production = sum(matrix.get(z, {}).values())
            attraction = col_sums.get(z, 0)
            writer.writerow([z, production, attraction])

    log(f"Unique (op_date, opid, trip_no): {unique_total}")
    log(f"STYLE1-3 found: {found_keys} / missing: {missing_keys}")
    log("Outputs written:")
    log(f"  - {od_long_path}")
    log(f"  - {od_matrix_path}")
    log(f"  - {prod_attr_path}")
    log(f"  - {zone_master_path}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    log("=== CONFIG VALIDATION ===")
    split_files, style_files = validate_config()

    total_split_files = sum(len(v) for v in split_files.values())
    total_style_files = sum(len(v) for v in style_files.values())
    style_zip_total = sum(1 for v in style_files.values() for p in v if p.suffix.lower() == ".zip")
    style_csv_total = sum(1 for v in style_files.values() for p in v if p.suffix.lower() == ".csv")
    total_units = total_split_files + total_style_files  # Phase1 + Phase2 分

    log(f"Datasets: {len(DATASETS)}")
    log(f"Split CSV files: {total_split_files}")
    log(f"STYLE1-3 ZIP/CSV files: {total_style_files}")
    log(f"  └ ZIP: {style_zip_total} / CSV: {style_csv_total}")
    log("=== Phase1: split 走査 → (opid, trip_no) 収集（曜日フィルタ） ===")
    log(f"対象 split CSV: {total_split_files} ファイル")

    progress = ProgressPrinter()
    polygons = load_polygons(ZONES_CSV_PATH)

    trip_counts, needed_dates, invalid_date_count, processed_split = collect_trip_counts(
        DATASETS,
        split_files,
        progress,
        total_units=total_units if total_units else 1,
    )
    total_units += max(len(trip_counts), 1)  # Phase3 分を後ろに足す
    progress.update(
        phase="Phase1 split走査",
        done=processed_split,
        total=total_units,
        hit=len(trip_counts),
        missing=invalid_date_count,
    )
    if invalid_date_count:
        log(f"[WARN] Invalid or unparsable dates in split files: {invalid_date_count} rows")

    log("=== Phase2: 様式1-3 走査 → OD座標ヒット ===")
    log(f"対象 style1-3 ZIP/CSV: {total_style_files} ファイル")
    wanted_keys = set(trip_counts.keys())
    od_lookup, processed_style = load_od_lookup(
        DATASETS,
        style_files,
        progress,
        total_units=total_units,
        base_progress=processed_split,
        needed_dates=needed_dates,
        wanted_keys=wanted_keys,
    )

    log("=== Phase3: ゾーン割当 → 集計 → 出力 ===")
    log(f"ユニークキー数 (見込み作業量): {len(trip_counts)}")
    build_od_outputs(
        trip_counts,
        od_lookup,
        polygons,
        OUTPUT_DIR,
        progress,
        total_units=total_units,
        base_progress=processed_split + processed_style,
    )
    progress.finalize()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"[ERROR] {exc}")
        sys.exit(1)
