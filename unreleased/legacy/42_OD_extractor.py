"""様式1-3参照ODリスト専用の高速ゾーン集計スクリプト。

40_trip_od_screening.py が出力する「様式1-3参照ODリスト」を入力に、
ゾーン割当（ポリゴン or 東西南北）、OD マトリクス、発生集中量を一気に生成する。
様式1-3 ZIP を直接読む機能は持たない（必要なら OD_extractor_legacy.py を使用）。
"""

from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Sequence

LOG_LINES: list[str] = []

# ============================================================================
# CONFIG — ここだけ触ればOK
# ============================================================================

# 出力フォルダ
OUTPUT_DIR = Path(r"C:\path\to\od_output")

# 40_trip_od_screening.py が出力した「様式1-3参照ODリスト」を1個以上指定
OD_LIST_FILES = [
    Path(r"C:\path\to\od_list_style1-3.csv"),
]

# ゾーンポリゴンCSV（12_polygon_builder.html 出力）
ZONES_CSV_PATH = Path(r"C:\path\to\zones.csv")

# 曜日フィルタ（二重チェック用。ODリスト側が既に絞っていても安全のため再判定）
TARGET_WEEKDAYS = {"火", "水", "木"}

# 津山市中心点（東西南北ゾーン判定）
TSUYAMA_CENTER_LON = 133.93
TSUYAMA_CENTER_LAT = 35.07

# 入力エンコーディング候補
ENCODINGS = ("utf-8-sig", "utf-8", "cp932")

# 進捗用の事前行数カウント（OFF 推奨）
ENABLE_PRECOUNT = True

# ============================================================================
# ログと進捗
# ============================================================================


def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    line = f"[{now}] {message}"
    print(line)
    LOG_LINES.append(line)


class ProgressPrinter:
    def __init__(self, label: str) -> None:
        self.label = label
        self.start = datetime.now()
        self.last_print = self.start
        self.last_line: str | None = None

    def update(self, *, done: int, total: int, ok: int) -> None:
        now = datetime.now()
        if (now - self.last_print).total_seconds() < 0.5 and (total == 0 or done != total):
            return
        self.last_print = now
        elapsed = now - self.start
        percent = (done / total * 100) if total else 0
        eta_seconds = (elapsed.total_seconds() / done * (total - done)) if (done and total) else float("nan")
        if math.isfinite(eta_seconds) and eta_seconds >= 0:
            eta = timedelta(seconds=int(eta_seconds))
            eta_str = str(eta).split(".")[0]
        else:
            eta_str = "--:--:--"
        total_str = f"{total}" if total else "?"
        percent_str = f" ({percent:5.1f}%)" if total else ""
        line = (
            f"\r[{self.label}] {done}/{total_str}{percent_str} "
            f"集計対象OK:{ok} ETA:{eta_str}"
        )
        print(line, end="", flush=True)
        # TXTログ用（\r を除いた同等内容）
        self.last_line = line.replace("\r", "")

    def finalize(self) -> None:
        print()
        if self.last_line:
            LOG_LINES.append(self.last_line)


# ============================================================================
# ポリゴン/ゾーン判定
# ============================================================================


@dataclass
class PolygonZone:
    name: str
    points: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]


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


def parse_float(value: str) -> float | None:
    try:
        return float((value or "").strip())
    except Exception:
        return None


def weekday_from_date(text: str) -> str:
    try:
        dt = datetime.strptime(text.strip(), "%Y%m%d")
    except Exception:
        return ""
    return "月火水木金土日"[dt.weekday()]


def point_in_polygon(lon: float, lat: float, points: Sequence[tuple[float, float]]) -> bool:
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
    dx = lon - TSUYAMA_CENTER_LON
    dy = lat - TSUYAMA_CENTER_LAT
    if abs(dx) >= abs(dy):
        return "東方面ゾーン" if dx > 0 else "西方面ゾーン"
    return "北方面ゾーン" if dy > 0 else "南方面ゾーン"


def assign_zone(lon: float | None, lat: float | None, polygons: Sequence[PolygonZone]) -> str:
    if lon is None or lat is None:
        return "MISSING"
    for poly in polygons:
        min_lon, min_lat, max_lon, max_lat = poly.bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if point_in_polygon(lon, lat, poly.points):
            return poly.name
    return directional_zone(lon, lat)


def zone_label(name: str) -> str:
    return re.sub(r"^\s*\d+\s*:\s*", "", name)


# ============================================================================
# ODリスト読込 & 集計
# ============================================================================


def iter_od_records(paths: Sequence[Path]) -> Iterator[dict[str, str]]:
    for path in paths:
        if not path.exists():
            log(f"[WARN] OD list not found: {path}")
            continue
        for enc in ENCODINGS:
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        yield row
                break
            except UnicodeDecodeError:
                continue


def build_outputs(
    *,
    polygons: Sequence[PolygonZone],
    od_list_files: Sequence[Path],
    output_dir: Path,
) -> None:
    matrix: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    col_sums: dict[str, float] = defaultdict(float)
    zones_set: set[str] = set()

    total_rows = 0  # 事前カウント結果（ENABLE_PRECOUNT 時）
    rows_total_seen = 0
    rows_ok_all = 0
    rows_weekday_pass = 0
    rows_ok_used = 0
    rows_zone_assigned = 0
    rows_added_to_matrix = 0

    status_counts_after_weekday: dict[str, int] = defaultdict(int)
    filtered_weekday = 0
    op_dates_set: set[str] = set()

    if ENABLE_PRECOUNT:
        for path in od_list_files:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
                total_rows += max(sum(1 for _ in f) - 1, 0)  # header 分控除

    progress = ProgressPrinter(label="ゾーン割当")
    processed = 0

    for row in iter_od_records(od_list_files):
        processed += 1
        rows_total_seen += 1
        status = (row.get("status") or "").strip()
        weekday = (row.get("weekday") or "").strip()
        op_date = (row.get("operation_date") or "").strip()
        weekday = weekday or weekday_from_date(op_date)

        if status == "OK":
            rows_ok_all += 1

        if weekday and TARGET_WEEKDAYS and weekday not in TARGET_WEEKDAYS:
            filtered_weekday += 1
            progress.update(done=processed, total=total_rows, ok=rows_ok_used)
            continue

        rows_weekday_pass += 1

        if status != "OK":
            status_counts_after_weekday[status or "(empty)"] += 1
            progress.update(done=processed, total=total_rows, ok=rows_ok_used)
            continue

        rows_ok_used += 1

        o_lon = parse_float(row.get("o_lon", ""))
        o_lat = parse_float(row.get("o_lat", ""))
        d_lon = parse_float(row.get("d_lon", ""))
        d_lat = parse_float(row.get("d_lat", ""))

        zone_o = assign_zone(o_lon, o_lat, polygons)
        zone_d = assign_zone(d_lon, d_lat, polygons)
        rows_zone_assigned += 1
        zones_set.update([zone_o, zone_d])

        matrix[zone_o][zone_d] += 1
        rows_added_to_matrix += 1
        col_sums[zone_d] += 1
        if op_date:
            op_dates_set.add(op_date)
        progress.update(done=processed, total=total_rows, ok=rows_ok_used)

    progress.finalize()
    if not ENABLE_PRECOUNT:
        total_rows = processed

    zones = sorted(zones_set)
    if "MISSING" in zones:
        zones.remove("MISSING")
        zones.append("MISSING")

    output_dir.mkdir(parents=True, exist_ok=True)
    zone_master_path = output_dir / "zone_master.csv"
    od_matrix_path = output_dir / "od_matrix.csv"
    od_matrix_all_path = output_dir / "od_matrix(all).csv"
    od_matrix_perday_path = output_dir / "od_matrix(perday).csv"
    prod_attr_path = output_dir / "zone_production_attraction.csv"

    zone_master: list[tuple[int, str]] = [(idx, name) for idx, name in enumerate(zones, start=1)]
    zone_labels = [zone_label(name) for name in zones]

    with zone_master_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["zone_id", "zone_name"])
        for idx, name in zone_master:
            writer.writerow([f"{idx:03d}", name])

    with od_matrix_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        header = ["O\\D", *zone_labels]
        writer.writerow(header)
        for zo, zo_label in zip(zones, zone_labels):
            row_counts = [matrix.get(zo, {}).get(zd, 0) for zd in zones]
            writer.writerow([zo_label, *row_counts])

    # od_matrix(all).csv（od_matrix.csv と同一内容）
    with od_matrix_all_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        header = ["O\\D", *zone_labels]
        writer.writerow(header)
        for zo, zo_label in zip(zones, zone_labels):
            row_counts = [matrix.get(zo, {}).get(zd, 0) for zd in zones]
            writer.writerow([zo_label, *row_counts])

    # od_matrix(perday).csv（総日数で割る）
    days = len(op_dates_set)
    denom = days if days > 0 else 1
    with od_matrix_perday_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        header = ["O\\D", *zone_labels]
        writer.writerow(header)
        for zo, zo_label in zip(zones, zone_labels):
            row_counts = [matrix.get(zo, {}).get(zd, 0) / denom for zd in zones]
            writer.writerow([zo_label, *row_counts])

    with prod_attr_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["zone", "production", "attraction"])
        for z in zones:
            production = sum(matrix.get(z, {}).values())
            attraction = col_sums.get(z, 0)
            writer.writerow([z, production, attraction])

    # ===== 集計サマリ（誤解防止の日本語ログ）=====
    log("----- 集計サマリ（件数の定義を明確化）-----")
    log(f"入力ODリスト：読み込んだ総行数（ヘッダー除く）= {rows_total_seen}")
    log(f"status=\"OK\" の行数（曜日フィルタ前）= {rows_ok_all}  ※ExcelのCOUNTIF等と比較する場合はこの数")
    log(f"曜日フィルタで除外された行数（TARGET_WEEKDAYS対象外）= {filtered_weekday}")
    log(f"曜日フィルタ通過行数（statusは不問）= {rows_weekday_pass}")
    log(f"曜日フィルタ通過後の status=\"OK\" 行数（この後の集計に使用）= {rows_ok_used}")
    log(f"ゾーン割当を実施した行数（曜日フィルタ通過かつ status=\"OK\"）= {rows_zone_assigned}")
    log(f"ODマトリクスに加算した行数（最終反映）= {rows_added_to_matrix}")
    log(f"対象トリップ日数（perdayの割り算に使用）= {len(op_dates_set)} 日")
    if status_counts_after_weekday:
        log("【除外（曜日通過後）】status別の件数：")
        for s, c in sorted(status_counts_after_weekday.items()):
            log(f"  - {s}: {c} 行")

    log_txt_path = output_dir / "42_OD_extractor_LOG.txt"
    log("Outputs written:")
    log(f"  - {zone_master_path}")
    log(f"  - {od_matrix_path}")
    log(f"  - {od_matrix_all_path}")
    log(f"  - {od_matrix_perday_path}")
    log(f"  - {prod_attr_path}")
    log(f"  - {log_txt_path}")

    # ===== LOG TXT 出力 =====
    op_dates = sorted(op_dates_set)
    with log_txt_path.open("w", encoding="utf-8") as f:
        f.write("－－－このログについて－－－\n")
        f.write("・本スクリプトは、入力ODリストを読み込み、曜日フィルタ後に status=\"OK\" の行だけをOD集計に使用します。\n")
        f.write("・Excelの COUNTIF で status=\"OK\" を数える場合は「曜日フィルタ前」のOK件数と比較してください。\n")
        f.write("\n")
        f.write("－－－対象トリップ日（perdayの割り算に使用）－－－\n")
        for d in op_dates:
            f.write(f"{d}\n")
        f.write(f"総日数: {len(op_dates)} 日\n")
        f.write("\n")
        f.write("－－－LOG（実行ログ）－－－\n")
        for line in LOG_LINES:
            f.write(line + "\n")


# ============================================================================
# Entrypoint
# ============================================================================


def main() -> None:
    log("OD_extractor (ODリスト版) を開始します")
    polygons = load_polygons(ZONES_CSV_PATH)
    build_outputs(polygons=polygons, od_list_files=OD_LIST_FILES, output_dir=OUTPUT_DIR)
    log("完了")


if __name__ == "__main__":
    main()
