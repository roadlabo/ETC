"""Crossroad passage extractor for "様式1-2" screened CSV files.

Reads trip CSV files, detects passages through predefined crossroads
(crossroadXXX.csv), and outputs per-passage metrics such as approach/exit
branches, timestamps, distances, and speeds.
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

# ============================================================
# 16_crossroad_extractor.py 設定セクション（ユーザーが自由に変更）
# ============================================================
# 入力フォルダのデフォルト（最大15個まで指定可能）
DEFAULT_INPUT_DIRS: List[Path] = [
    # Path(r"/path/to/input1"),
    # Path(r"/path/to/input2"),
]

# 交差点定義フォルダのデフォルト
DEFAULT_CROSSROAD_DIR: Path | None = None  # Path(r"/path/to/crossroads")

# 出力CSVパスのデフォルト
DEFAULT_OUTPUT_PATH: Path | None = None  # Path(r"/path/to/output.csv")

# Column indices (0-based) for 様式1-2
RSU_ID_IDX = 0
RECV_TIME_IDX = 1
TRIP_DATE_IDX = 2
TRIP_ID_IDX = 3
VEHICLE_TYPE_IDX = 4
VEHICLE_USE_IDX = 5
GPS_TIME_IDX = 6
SEQ_NO_IDX = 7
TRIP_NO_IDX = 8
LON_IDX = 14
LAT_IDX = 15
MM_LON_IDX = 22
MM_LAT_IDX = 23
DIST_FROM_IN_NODE_IDX = 27
BASIC_SECTION_ID_IDX = 34
UP_DOWN_IDX = 35

EARTH_RADIUS_M = 6_371_000.0

# 交差点通過判定用のしきい値
# 16_trip_extractor_point.py の THRESH_M / MIN_HITS と同じ値に揃えること
CROSSROAD_HIT_DIST_M = 20.0        # 点が交差点中心からこの距離以内ならヒット
CROSSROAD_SEG_HIT_DIST_M = 20.0    # 線分距離がこの距離以内ならヒット
CROSSROAD_MIN_HITS = 1             # 通過とみなす最小ヒット数（16側と同じ値にする）


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class CSVRow:
    values: List[str]

    def get(self, idx: int) -> Optional[str]:
        if idx >= len(self.values):
            return None
        return self.values[idx]


@dataclass
class CrossroadBranch:
    no: int
    dir_deg: float
    name: str


@dataclass
class CrossroadDef:
    crossroad_id: str
    center_lat: float
    center_lon: float
    branches: List[CrossroadBranch]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _haversine_m(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    lat1, lon1 = p1
    lat2, lon2 = p2
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_RADIUS_M * c


def _bearing_deg(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _point_to_segment_distance_m(
    p: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]
) -> float:
    """Approximate point-line distance using local equirectangular projection."""

    lat_ref = math.radians(p[0])

    def to_xy(pt: Tuple[float, float]) -> Tuple[float, float]:
        lat, lon = pt
        x = math.radians(lon) * math.cos(lat_ref) * EARTH_RADIUS_M
        y = math.radians(lat) * EARTH_RADIUS_M
        return x, y

    px, py = to_xy(p)
    ax, ay = to_xy(a)
    bx, by = to_xy(b)

    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay

    seg_len2 = vx * vx + vy * vy
    if seg_len2 == 0:
        return math.hypot(wx, wy)

    t = max(0.0, min(1.0, (wx * vx + wy * vy) / seg_len2))
    proj_x = ax + t * vx
    proj_y = ay + t * vy
    return math.hypot(px - proj_x, py - proj_y)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_float(token: Optional[str]) -> Optional[float]:
    if token is None:
        return None
    token = token.strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _parse_int(token: Optional[str]) -> Optional[int]:
    if token is None:
        return None
    token = token.strip()
    if not token:
        return None
    try:
        return int(float(token))
    except ValueError:
        return None


def _parse_dt14(token: Optional[str]) -> Optional[datetime]:
    if token is None:
        return None
    token = token.strip()
    if len(token) < 14:
        return None
    try:
        return datetime.strptime(token[:14], "%Y%m%d%H%M%S")
    except Exception:
        return None


def _weekday_abbr(ymd: str) -> Optional[str]:
    if len(ymd) != 8 or not ymd.isdigit():
        return None
    dt = datetime.strptime(ymd, "%Y%m%d")
    wk = dt.weekday()  # Mon=0 .. Sun=6
    if wk == 6:
        return "SUN"
    return ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"][wk + 1]


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _read_rows(path: Path) -> List[CSVRow]:
    rows: List[CSVRow] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(CSVRow(list(row)))
    return rows


def _iter_trip_groups(rows: List[CSVRow]) -> Iterator[Tuple[Tuple[str, str, str], List[CSVRow]]]:
    by_key: Dict[Tuple[str, str, str], List[CSVRow]] = defaultdict(list)
    for row in rows:
        trip_date = row.get(TRIP_DATE_IDX) or ""
        trip_id = row.get(TRIP_ID_IDX) or ""
        trip_no = row.get(TRIP_NO_IDX) or ""
        by_key[(trip_date, trip_id, trip_no)].append(row)

    for key, group in by_key.items():
        group.sort(key=lambda r: (
            _parse_int(r.get(SEQ_NO_IDX)) if _parse_int(r.get(SEQ_NO_IDX)) is not None else 0,
            r.get(GPS_TIME_IDX) or "",
        ))
        yield key, group


def _load_crossroad_defs(crossroad_dir: Path, verbose: bool = False) -> Dict[str, CrossroadDef]:
    required_cols = {"crossroad_id", "center_lon", "center_lat", "branch_no", "dir_deg"}
    crossroads: Dict[str, CrossroadDef] = {}

    for csv_path in sorted(crossroad_dir.glob("*.csv")):
        try:
            with csv_path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = set(reader.fieldnames or [])
                if not required_cols.issubset(fieldnames):
                    if verbose:
                        missing = required_cols - fieldnames
                        print(f"[crossroad] skip {csv_path.name}: missing columns {sorted(missing)}")
                    continue

                for row in reader:
                    cid_raw = row.get("crossroad_id")
                    cid = str(cid_raw).strip() if cid_raw is not None else ""
                    center_lon = _parse_float(row.get("center_lon"))
                    center_lat = _parse_float(row.get("center_lat"))
                    branch_no = _parse_int(row.get("branch_no"))
                    dir_deg = _parse_float(row.get("dir_deg"))
                    branch_name = row.get("branch_name") or ""

                    if not cid:
                        if verbose:
                            print(f"[crossroad] skip row without crossroad_id in {csv_path.name}")
                        continue
                    if center_lon is None or center_lat is None:
                        if verbose:
                            print(f"[crossroad] skip row without center coordinates in {csv_path.name} (id={cid})")
                        continue

                    cross = crossroads.setdefault(
                        cid,
                        CrossroadDef(
                            crossroad_id=cid, center_lat=center_lat, center_lon=center_lon, branches=[]
                        ),
                    )

                    if (abs(cross.center_lat - center_lat) > 1e-9 or abs(cross.center_lon - center_lon) > 1e-9) and verbose:
                        print(
                            f"[crossroad] warning: center mismatch for id={cid}:"
                            f" existing=({cross.center_lon},{cross.center_lat}), new=({center_lon},{center_lat})"
                        )

                    if branch_no is None or dir_deg is None:
                        if verbose:
                            print(f"[crossroad] skip row without branch_no/dir_deg in {csv_path.name} (id={cid})")
                        continue

                    cross.branches.append(
                        CrossroadBranch(no=branch_no, dir_deg=dir_deg % 360.0, name=str(branch_name))
                    )
        except Exception as exc:  # pragma: no cover - defensive
            if verbose:
                print(f"[crossroad] failed to read {csv_path}: {exc}")

    invalid_ids = [cid for cid, cr in crossroads.items() if len(cr.branches) < 3]
    for cid in invalid_ids:
        if verbose:
            print(f"[crossroad] discard id={cid}: need >=3 branches (got {len(crossroads[cid].branches)})")
        del crossroads[cid]

    return crossroads


# ---------------------------------------------------------------------------
# Core detection logic
# ---------------------------------------------------------------------------


def _valid_coord(row: CSVRow) -> Optional[Tuple[float, float]]:
    mm_lon = _parse_float(row.get(MM_LON_IDX))
    mm_lat = _parse_float(row.get(MM_LAT_IDX))
    if mm_lon is not None and mm_lat is not None:
        return mm_lat, mm_lon
    lon = _parse_float(row.get(LON_IDX))
    lat = _parse_float(row.get(LAT_IDX))
    if lon is None or lat is None:
        return None
    return lat, lon


def _closest_branch(dir_deg: float, branches: Sequence[CrossroadBranch]) -> int:
    def angle_diff(a: float, b: float) -> float:
        d = abs((a - b + 180.0) % 360.0 - 180.0)
        return d

    best_no = branches[0].no
    best_diff = 999.0
    for br in branches:
        d = angle_diff(dir_deg, br.dir_deg)
        if d < best_diff:
            best_diff = d
            best_no = br.no
    return best_no


def _accum_distance(points: List[Tuple[float, float]], start_idx: int, end_idx: int) -> float:
    dist = 0.0
    for i in range(start_idx, end_idx):
        dist += _haversine_m(points[i], points[i + 1])
    return dist


def _trip_passes_crossroad(
    coords: List[Tuple[float, float]],
    center_lat: float,
    center_lon: float,
    thresh_point_m: float = CROSSROAD_HIT_DIST_M,
    thresh_seg_m: float = CROSSROAD_SEG_HIT_DIST_M,
    min_hits: int = CROSSROAD_MIN_HITS,
) -> bool:
    """
    16_trip_extractor_point.py と同じ思想で、
    「このトリップが交差点を通過したかどうか」だけを判定する関数。

    判定基準:
    - どこか1点でも中心から thresh_point_m 以内ならヒット
    - 連続する2点の線分が中心から thresh_seg_m 以内ならヒット
    - ヒット数(point + segment) が min_hits 以上であれば通過とみなす
    """
    center = (center_lat, center_lon)
    valid_indices = [i for i, c in enumerate(coords) if not (math.isnan(c[0]) or math.isnan(c[1]))]
    if not valid_indices:
        return False

    hits = 0

    for k, idx in enumerate(valid_indices):
        p = coords[idx]
        dist_p = _haversine_m(p, center)
        if dist_p <= thresh_point_m:
            hits += 1

        if k < len(valid_indices) - 1:
            idx_next = valid_indices[k + 1]
            p_next = coords[idx_next]
            seg_dist = _point_to_segment_distance_m(center, p, p_next)
            if seg_dist <= thresh_seg_m:
                hits += 1

        if hits >= min_hits:
            return True

    return False


def _closest_center_index(
    coords: List[Tuple[float, float]],
    center_lat: float,
    center_lon: float,
) -> Optional[int]:
    """
    通過していることが分かったトリップについて、
    交差点中心に最も近い有効なポイントの index を返す。

    ・座標が NaN のポイントは無視
    ・1点も有効なポイントがなければ None
    """
    center = (center_lat, center_lon)
    best_idx: Optional[int] = None
    best_dist = float("inf")

    for idx, p in enumerate(coords):
        if math.isnan(p[0]) or math.isnan(p[1]):
            continue
        dist = _haversine_m(p, center)
        if dist < best_dist:
            best_dist = dist
            best_idx = idx

    return best_idx


# ---------------------------------------------------------------------------
# Processing per trip
# ---------------------------------------------------------------------------


def _process_trip(
    trip_key: Tuple[str, str, str],
    rows: List[CSVRow],
    crossroad: CrossroadDef,
    screening_label: str,
    route_name: str,
    debounce_sec: int,
) -> List[List[str]]:
    """
    1トリップについて交差点通過を判定し、通過している場合のみ
    ・中心に最も近いポイントを中心点とみなして
    ・branch_in / branch_out / 時刻 / 距離 / 速度 を1レコードとして返す。

    通過判定のロジックは 16_trip_extractor_point.py と同じ思想
    （点距離 + 線分距離 + MIN_HITS）に揃える。
    """

    coords: List[Tuple[float, float]] = []
    for row in rows:
        c = _valid_coord(row)
        coords.append(c if c else (math.nan, math.nan))

    # 有効な座標が1点もなければ通過判定不能
    valid_indices = [i for i, c in enumerate(coords) if not (math.isnan(c[0]) or math.isnan(c[1]))]
    if not valid_indices:
        return []

    center_lat = crossroad.center_lat
    center_lon = crossroad.center_lon

    # ① 通過判定：16_trip_extractor_point.py と同じ思想で YES/NO を決める
    passed = _trip_passes_crossroad(
        coords,
        center_lat=center_lat,
        center_lon=center_lon,
        thresh_point_m=CROSSROAD_HIT_DIST_M,
        thresh_seg_m=CROSSROAD_SEG_HIT_DIST_M,
        min_hits=CROSSROAD_MIN_HITS,
    )

    # 通過していないトリップはここで「該当なし」として終了
    if not passed:
        return []

    # ② 通過しているトリップについては、中心点に最も近いポイントを center とみなす
    idx_center = _closest_center_index(coords, center_lat=center_lat, center_lon=center_lon)
    if idx_center is None:
        # 理論上ほぼ起こらないが、安全のため
        return []

    # 前後のポイント index を決定（最大 ±2点を確保するが、端では縮む）
    idx_before = max(0, idx_center - 2)
    idx_after = min(len(rows) - 1, idx_center + 2)
    if idx_center - idx_before < 2 and idx_center > 0:
        idx_before = idx_center - 1
    if idx_after - idx_center < 2 and idx_center + 1 < len(rows):
        idx_after = idx_center + 1

    p_before = coords[idx_before]
    p_center = coords[idx_center]
    p_after = coords[idx_after]

    # どこか1つでも座標欠損があれば、このトリップは通過判定はYESだが中心点が取れないので無視
    if any(math.isnan(v) for v in p_before + p_center + p_after):
        return []

    # 時刻を取得
    dt_before = _parse_dt14(rows[idx_before].get(GPS_TIME_IDX))
    dt_center = _parse_dt14(rows[idx_center].get(GPS_TIME_IDX))
    dt_after = _parse_dt14(rows[idx_after].get(GPS_TIME_IDX))

    # 方位角から branch_in / branch_out を決定（角度の許容誤差ロジックは既存のまま）
    # crossroad 定義 (dir_deg) は center→branch の方位角なので、流入も同じ向きに合わせる
    dir_in = _bearing_deg(p_center, p_before)
    dir_out = _bearing_deg(p_center, p_after)
    branch_in = _closest_branch(dir_in, crossroad.branches)
    branch_out = _closest_branch(dir_out, crossroad.branches)

    # before〜after 間の距離と速度を計算
    dist_m = _accum_distance(coords, idx_before, idx_after)

    delta_t_sec = None
    if dt_before and dt_after:
        delta_t_sec = (dt_after - dt_before).total_seconds()

    speed_kmh = None
    if delta_t_sec and delta_t_sec > 0:
        speed_kmh = dist_m / delta_t_sec * 3.6

    # その他の情報をセット
    trip_date, trip_id, trip_no = trip_key
    weekday = _weekday_abbr(trip_date[:8] if len(trip_date) >= 8 else "") or ""
    vehicle_type = rows[idx_center].get(VEHICLE_TYPE_IDX) or ""
    vehicle_use = rows[idx_center].get(VEHICLE_USE_IDX) or ""

    hit_row = [
        screening_label,
        route_name,
        weekday,
        trip_id,
        trip_date,
        trip_no,
        vehicle_type,
        vehicle_use,
        str(branch_in),
        str(branch_out),
        rows[idx_before].get(GPS_TIME_IDX) or "",
        rows[idx_center].get(GPS_TIME_IDX) or "",
        rows[idx_after].get(GPS_TIME_IDX) or "",
        f"{dist_m:.3f}",
        f"{speed_kmh:.3f}" if speed_kmh is not None else "",
    ]

    return [hit_row]


# ---------------------------------------------------------------------------
# File-level processing
# ---------------------------------------------------------------------------


def _process_file(
    path: Path,
    crossroad: CrossroadDef,
    screening_label: str,
    route_name: str,
    debounce_sec: int,
    verbose: bool,
) -> Tuple[int, int, List[List[str]]]:
    try:
        rows = _read_rows(path)
    except Exception as exc:
        if verbose:
            print(f"failed to read {path}: {exc}")
        return 0, 0, []

    hit_count = 0
    trip_count = 0
    all_hits: List[List[str]] = []

    for trip_key, trip_rows in _iter_trip_groups(rows):
        trip_count += 1
        hits = _process_trip(trip_key, trip_rows, crossroad, screening_label, route_name, debounce_sec)
        hit_count += len(hits)
        all_hits.extend(hits)

    return trip_count, hit_count, all_hits


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="交差点通過抽出 (様式1-2)")
    parser.add_argument(
        "--input-dirs",
        nargs="+",
        type=Path,
        help="入力CSVが入ったフォルダ（最大15個まで複数指定可）",
    )
    parser.add_argument(
        "--crossroad-dir",
        default=DEFAULT_CROSSROAD_DIR,
        type=Path,
        help="交差点定義CSVが入ったフォルダ",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH or Path("crossroad_hits.csv"),
        type=Path,
        help="出力CSVパス",
    )
    parser.add_argument("--screening-label", default="", help="スクリーニング区分ラベル")
    parser.add_argument("--route-name", default="", help="ルート名")
    parser.add_argument("--debounce-sec", type=int, default=30, help="同一トリップ内の連続ヒットを抑制する秒数")
    parser.add_argument("--verbose", action="store_true", help="詳細ログ")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_dirs: List[Path] = []
    if args.input_dirs:
        input_dirs = list(args.input_dirs)
    elif DEFAULT_INPUT_DIRS:
        input_dirs = list(DEFAULT_INPUT_DIRS)

    if not input_dirs:
        print("input directories not specified (use --input-dirs or DEFAULT_INPUT_DIRS)")
        return 1
    if len(input_dirs) > 15:
        print(f"input directories exceed limit (15): {len(input_dirs)}")
        return 1

    if args.crossroad_dir is None:
        print("crossroad directory not specified (use --crossroad-dir or DEFAULT_CROSSROAD_DIR)")
        return 1
    if not args.crossroad_dir.is_dir():
        print(f"crossroad directory not found: {args.crossroad_dir}")
        return 1

    crossroads = _load_crossroad_defs(args.crossroad_dir, args.verbose)
    if not crossroads:
        print(f"no valid crossroad definitions found in {args.crossroad_dir}")
        return 1

    output_rows: List[List[str]] = []
    header = [
        "screening_label",
        "route_name",
        "weekday",
        "trip_id",
        "trip_date",
        "trip_no",
        "vehicle_type",
        "vehicle_use",
        "branch_in",
        "branch_out",
        "time_before",
        "time_center",
        "time_after",
        "distance_m",
        "speed_kmh",
        "crossroad_id",
    ]

    print(
        f"[16_crossroad_extractor] 開始します。input_dirs = {len(input_dirs)}, crossroads = {len(crossroads)}"
    )
    start = time.time()
    total_hits = 0
    total_files = 0

    
    input_dir_files: List[Tuple[Path, List[Path]]] = []
    for dir_path in input_dirs:
        files = sorted(dir_path.glob("*.csv"))
        input_dir_files.append((dir_path, files))

    for cid, crossroad in crossroads.items():
        if args.verbose:
            print(f"[16_crossroad_extractor] 処理中 crossroad_id={cid}")
        for dir_idx, (dir_path, files) in enumerate(input_dir_files, start=1):
            print(
                f"[16_crossroad_extractor] Processing input_folder={dir_path} ({len(files)} files)"
            )
            for file_idx, path in enumerate(files, start=1):
                trip_count, file_hits, hits = _process_file(
                    path,
                    crossroad,
                    args.screening_label,
                    args.route_name,
                    args.debounce_sec,
                    args.verbose,
                )

                for row in hits:
                    row.append(crossroad.crossroad_id)

                output_rows.extend(hits)
                total_hits += file_hits
                total_files += 1

                if args.verbose or file_idx % 10 == 0:
                    print(
                        f"[16_crossroad_extractor] ({file_idx}/{len(files)}) file={path.name},",
                        f" hits={file_hits}, crossroad_id={cid}"
                    )


    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="cp932", errors="ignore", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(output_rows)

    elapsed = time.time() - start
    print(
        f"[16_crossroad_extractor] 完了しました。total_files={total_files}, total_hits={total_hits}, elapsed={elapsed:.1f} sec"
    )
    print(f"出力: {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
