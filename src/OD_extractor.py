"""様式1-3参照ODリスト専用の高速ゾーン集計スクリプト。

40_trip_od_screening.py が出力する「様式1-3参照ODリスト」を入力に、
ゾーン割当（ポリゴン or 東西南北）、OD マトリクス、発生集中量を一気に生成する。
様式1-3 ZIP を直接読む機能は持たない（必要なら OD_extractor_legacy.py を使用）。
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Mapping, Sequence

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

# ============================================================================
# ログと進捗
# ============================================================================


def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


class ProgressPrinter:
    def __init__(self, label: str) -> None:
        self.label = label
        self.start = datetime.now()
        self.last_print = self.start

    def update(self, *, done: int, total: int, ok: int) -> None:
        now = datetime.now()
        if (now - self.last_print).total_seconds() < 0.5 and done != total:
            return
        self.last_print = now
        elapsed = now - self.start
        percent = (done / total * 100) if total else 0
        eta_seconds = (elapsed.total_seconds() / done * (total - done)) if done else float("nan")
        eta = timedelta(seconds=eta_seconds)
        eta_str = str(eta).split(".")[0] if eta == eta else "--:--:--"
        line = (
            f"\r[{self.label}] {done}/{total} ({percent:5.1f}%) "
            f"OK:{ok} ETA:{eta_str}"
        )
        print(line, end="", flush=True)

    def finalize(self) -> None:
        print()


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

    total_rows = 0
    ok_rows = 0
    missing_status: dict[str, int] = defaultdict(int)
    filtered_weekday = 0

    # まず総行数を概算
    for path in od_list_files:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            total_rows += max(sum(1 for _ in f) - 1, 0)  # header 分控除

    progress = ProgressPrinter(label="zone-assign")
    processed = 0

    for row in iter_od_records(od_list_files):
        processed += 1
        status = (row.get("status") or "").strip()
        weekday = (row.get("weekday") or "").strip()
        op_date = (row.get("operation_date") or "").strip()
        weekday = weekday or weekday_from_date(op_date)

        if weekday and TARGET_WEEKDAYS and weekday not in TARGET_WEEKDAYS:
            filtered_weekday += 1
            progress.update(done=processed, total=total_rows, ok=ok_rows)
            continue

        if status != "OK":
            missing_status[status or "(empty)"] += 1
            progress.update(done=processed, total=total_rows, ok=ok_rows)
            continue

        o_lon = parse_float(row.get("o_lon", ""))
        o_lat = parse_float(row.get("o_lat", ""))
        d_lon = parse_float(row.get("d_lon", ""))
        d_lat = parse_float(row.get("d_lat", ""))

        zone_o = assign_zone(o_lon, o_lat, polygons)
        zone_d = assign_zone(d_lon, d_lat, polygons)
        zones_set.update([zone_o, zone_d])

        matrix[zone_o][zone_d] += 1
        col_sums[zone_d] += 1
        ok_rows += 1
        progress.update(done=processed, total=total_rows, ok=ok_rows)

    progress.finalize()

    zones = sorted(zones_set)
    if "MISSING" in zones:
        zones.remove("MISSING")
        zones.append("MISSING")

    output_dir.mkdir(parents=True, exist_ok=True)
    zone_master_path = output_dir / "zone_master.csv"
    od_matrix_path = output_dir / "od_matrix.csv"
    prod_attr_path = output_dir / "zone_production_attraction.csv"

    zone_master: list[tuple[int, str]] = [(idx, name) for idx, name in enumerate(zones, start=1)]
    zone_to_id = {name: idx for idx, name in zone_master}

    with zone_master_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["zone_id", "zone_name"])
        for idx, name in zone_master:
            writer.writerow([f"{idx:03d}", name])

    with od_matrix_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        header = ["O\\D", *[f"{zone_to_id[z]:03d}:{z}" for z in zones]]
        writer.writerow(header)
        for zo in zones:
            row_counts = [matrix.get(zo, {}).get(zd, 0) for zd in zones]
            writer.writerow([f"{zone_to_id[zo]:03d}:{zo}", *row_counts])

    with prod_attr_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["zone", "production", "attraction"])
        for z in zones:
            production = sum(matrix.get(z, {}).values())
            attraction = col_sums.get(z, 0)
            writer.writerow([z, production, attraction])

    log(f"OD list rows (total): {total_rows}")
    log(f"OD list rows (OK): {ok_rows}")
    if filtered_weekday:
        log(f"Weekday filtered rows: {filtered_weekday}")
    if missing_status:
        for status, count in sorted(missing_status.items()):
            log(f"status={status}: {count} rows")
    log("Outputs written:")
    log(f"  - {od_matrix_path}")
    log(f"  - {prod_attr_path}")
    log(f"  - {zone_master_path}")


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
