"""
交差点通過性能算出スクリプト（31/16 ロジック準拠）
"""
from __future__ import annotations

import argparse
import bisect
import csv
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import pandas as pd

# ============================================================
# 出力設定（ユーザーが冒頭で編集する項目）
# ============================================================
# 出力フォルダ（ここだけ変更すればすべての出力がこの中に生成される）
OUTPUT_BASE_DIR = r"C:\\path\\to\\output_folder"

# 交差点ごとに性能CSVを生成する設定
# trip_folder: 第2スクリーニング済み様式1-2 CSV が大量に入ったフォルダ
# crossroad_file: 交差点CSVファイルのフルパス（1セット=1交差点）
CONFIG = [
    {
        "trip_folder": r"C:\\path\\to\\screening2_folder1",
        "crossroad_file": r"C:\\path\\to\\crossroad1.csv",
    },
    {
        "trip_folder": r"C:\\path\\to\\screening2_folder2",
        "crossroad_file": r"C:\\path\\to\\crossroad2.csv",
    },
    # 必要なだけ追加可能
]

# 抜粋したい曜日（TRIP_DATE から算出した MON〜SUN の略称）
# 例：火・水・木のみ抽出したい場合
TARGET_WEEKDAYS = ["TUE", "WED", "THU"]
# 全曜日を対象にしたい場合は空リストにする：
# TARGET_WEEKDAYS: list[str] = []


# ============================================================
# 交差点影響区間（独自定義）の計測区間設定
# 交差点中心（算出中心：線分上最近接点）を基準として、走行方向に
#   前 MEASURE_PRE_M 〜 後 MEASURE_POST_M の所要時間を線形補間で算出する。
# 例：前100m〜後20m → 距離（MEASURE_PRE_M+MEASURE_POST_M）m固定。
#
# 本スクリプトは所要時間(s)を基本量として出力し、
# 方向別（流入枝番×流出枝番）に
#   閑散時所要時間 T0（所要時間 下位5%平均）
# を求め、遅れ時間(s)=所要時間(s)-T0(s) を算出して出力する。
# ============================================================
MEASURE_PRE_M = 100.0
MEASURE_POST_M = 20.0

# ============================================================
# 交差点通過判定ロジック（31 / 16 と完全一致）
# ============================================================
CROSSROAD_HIT_DIST_M = 20.0
CROSSROAD_SEG_HIT_DIST_M = 20.0
CROSSROAD_MIN_HITS = 1
EARTH_RADIUS_M = 6_371_000.0

# ============================================================
# 枝判定（流入/流出）角度の安定化設定
#  - 中心点の前後「2点」から走行方向を作り、枝(dir_deg)へマッチングする
#  - in:  (center-30m -> center-5m) の走行方向を推定し、180度反転して "center->branch" に合わせる
#  - out: (center+10m -> center+60m) の走行方向を推定し、そのまま "center->branch" として使う
# ============================================================
DIR_IN_FAR_M = 30.0
DIR_IN_NEAR_M = 5.0
DIR_OUT_NEAR_M = 10.0
DIR_OUT_FAR_M = 60.0
BRANCH_MAX_ANGLE_DIFF_DEG = 35.0  # これを超えたら枝番は未確定（空欄）


def haversine_m(lat1, lon1, lat2, lon2):
    import math
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return EARTH_RADIUS_M * c


def point_to_segment_distance_m(cx, cy, ax, ay, bx, by):
    import math
    km_lat = 111.32
    km_lon = km_lat * math.cos(math.radians(cy))

    axm = (ax - cx) * km_lon * 1000
    aym = (ay - cy) * km_lat * 1000
    bxm = (bx - cx) * km_lon * 1000
    bym = (by - cy) * km_lat * 1000

    vx = bxm - axm
    vy = bym - aym
    wx = -axm
    wy = -aym
    vv = vx*vx + vy*vy
    if vv == 0:
        return (axm*axm + aym*aym)**0.5

    t = max(0, min(1, (wx*vx + wy*vy)/vv))
    px = axm + t * vx
    py = aym + t * vy
    return (px*px + py*py)**0.5


def segment_closest_t_and_dist_m(cx, cy, ax, ay, bx, by):
    """中心点(cx,cy)から線分A(ax,ay)-B(bx,by)への最接近パラメータt(0-1)と距離[m]を返す。"""
    import math
    km_lat = 111.32
    km_lon = km_lat * math.cos(math.radians(cy))

    axm = (ax - cx) * km_lon * 1000
    aym = (ay - cy) * km_lat * 1000
    bxm = (bx - cx) * km_lon * 1000
    bym = (by - cy) * km_lat * 1000

    vx = bxm - axm
    vy = bym - aym
    vv = vx*vx + vy*vy
    if vv == 0:
        return 0.0, (axm*axm + aym*aym) ** 0.5

    # 中心点(0,0)からの射影
    t = ((-axm)*vx + (-aym)*vy) / vv
    t = max(0.0, min(1.0, t))
    px = axm + t * vx
    py = aym + t * vy
    d = (px*px + py*py) ** 0.5
    return t, d


def closest_segment_to_center(points, center_lat, center_lon):
    """交差点中心への最近接線分(i,i+1)と、その線分上の最近接t(0-1),距離[m]を返す。"""
    best_i = None
    best_t = 0.0
    best_d = float("inf")
    for i in range(len(points) - 1):
        lat1, lon1 = points[i]
        lat2, lon2 = points[i + 1]
        t, d = segment_closest_t_and_dist_m(center_lon, center_lat, lon1, lat1, lon2, lat2)
        if d < best_d:
            best_d = d
            best_t = t
            best_i = i
    return best_i, best_t, best_d


def build_cumdist(points):
    """points[0]からの道なり累積距離[m]（点数と同じ長さ）"""
    cum = [0.0]
    for i in range(len(points) - 1):
        lat1, lon1 = points[i]
        lat2, lon2 = points[i + 1]
        cum.append(cum[-1] + haversine_m(lat1, lon1, lat2, lon2))
    return cum


def interpolate_at_distance(points, dt_list, cumdist, target_m):
    """道なり距離target_m地点の (lat, lon, datetime) を線形補間で返す。"""
    from datetime import timedelta
    if target_m <= 0:
        return points[0][0], points[0][1], dt_list[0]
    if target_m >= cumdist[-1]:
        return points[-1][0], points[-1][1], dt_list[-1]

    # 二分探索
    j = bisect.bisect_right(cumdist, target_m) - 1
    j = max(0, min(j, len(points) - 2))

    d0 = cumdist[j]
    d1 = cumdist[j + 1]
    if d1 <= d0:
        return points[j][0], points[j][1], dt_list[j]

    r = (target_m - d0) / (d1 - d0)
    lat0, lon0 = points[j]
    lat1, lon1 = points[j + 1]
    lat = lat0 + r * (lat1 - lat0)
    lon = lon0 + r * (lon1 - lon0)

    t0 = dt_list[j]
    t1 = dt_list[j + 1]
    if not t0 or not t1:
        return lat, lon, None
    dt = t0 + timedelta(seconds=r * (t1 - t0).total_seconds())
    return lat, lon, dt


def interpolate_point_at_distance(points, cumdist, target_m):
    """道なり距離target_m地点の (lat, lon) を線形補間で返す。範囲外はNone。"""
    if target_m < 0 or target_m > cumdist[-1]:
        return None
    if target_m == 0:
        return points[0]
    if target_m == cumdist[-1]:
        return points[-1]
    j = bisect.bisect_right(cumdist, target_m) - 1
    j = max(0, min(j, len(points) - 2))
    d0 = cumdist[j]
    d1 = cumdist[j + 1]
    if d1 <= d0:
        return points[j]
    r = (target_m - d0) / (d1 - d0)
    lat0, lon0 = points[j]
    lat1, lon1 = points[j + 1]
    return (lat0 + r * (lat1 - lat0), lon0 + r * (lon1 - lon0))


def trip_passes_crossroad(points, center_lat, center_lon):
    """このトリップが交差点を通過したかどうかを判定する。"""
    import math
    valid = [i for i, (lat, lon) in enumerate(points)
             if not (math.isnan(lat) or math.isnan(lon))]
    if not valid:
        return False

    hits = 0
    for k, idx in enumerate(valid):
        lat, lon = points[idx]
        # 点距離ヒット
        if haversine_m(lat, lon, center_lat, center_lon) <= CROSSROAD_HIT_DIST_M:
            hits += 1

        # 線分距離ヒット（連続する2点）
        if k < len(valid) - 1:
            idx2 = valid[k + 1]
            lat2, lon2 = points[idx2]
            if point_to_segment_distance_m(center_lon, center_lat,
                                           lon, lat, lon2, lat2) <= CROSSROAD_SEG_HIT_DIST_M:
                hits += 1

        if hits >= CROSSROAD_MIN_HITS:
            return True

    return False


def closest_center_index(points, center_lat, center_lon):
    """中心点に最も近い座標の index を返す。"""
    import math
    best_i, best_d = None, float("inf")
    for i, (lat, lon) in enumerate(points):
        if math.isnan(lat) or math.isnan(lon):
            continue
        d = haversine_m(lat, lon, center_lat, center_lon)
        if d < best_d:
            best_d, best_i = d, i
    return best_i


def accum_distance(points, s, e):
    """points[s] から points[e] までの道なり距離[m]"""
    dist = 0.0
    for i in range(s, e):
        lat1, lon1 = points[i]
        lat2, lon2 = points[i + 1]
        dist += haversine_m(lat1, lon1, lat2, lon2)
    return dist


def bearing_deg(lat1, lon1, lat2, lon2):
    """二点間の方位角[deg]（北=0, 東=90）"""
    import math
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    br = math.degrees(math.atan2(x, y))
    return (br + 360.0) % 360.0


def parse_dt14(s):
    """YYYYMMDDhhmmss → datetime（失敗時 None）"""
    from datetime import datetime
    if not s or len(str(s)) != 14:
        return None
    try:
        return datetime.strptime(str(s), "%Y%m%d%H%M%S")
    except Exception:
        return None


def weekday_abbr(date8):
    """YYYYMMDD → MON〜SUN"""
    from datetime import datetime
    if not date8 or len(date8) != 8:
        return ""
    dt = datetime.strptime(date8, "%Y%m%d")
    return ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][dt.weekday()]


@dataclass
class Branch:
    branch_no: str
    dir_deg: float


@dataclass
class Crossroad:
    cross_id: str
    center_lat: float
    center_lon: float
    branches: List[Branch]


def angular_diff(a: float, b: float) -> float:
    diff = abs(a - b) % 360
    return diff if diff <= 180 else 360 - diff


def find_nearest_branch(angle: float, branches: Iterable[Branch]) -> str:
    min_branch = None
    min_diff = float("inf")
    for br in branches:
        diff = angular_diff(angle, br.dir_deg)
        if diff < min_diff:
            min_diff = diff
            min_branch = br.branch_no
    return min_branch or ""


def find_nearest_branch_with_diff(angle: float, branches: Iterable[Branch]) -> tuple[str, float]:
    """最も近い枝番と角度差を返す（枝が無ければ('',inf)）"""
    min_branch = ""
    min_diff = float("inf")
    for br in branches:
        diff = angular_diff(angle, br.dir_deg)
        if diff < min_diff:
            min_diff = diff
            min_branch = br.branch_no
    return min_branch, min_diff


def read_csv_flexible(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            continue
    raise RuntimeError(f"CSVを読み込めませんでした: {path}")


def load_crossroad_file(path: Path) -> Crossroad:
    df = read_csv_flexible(path)
    required_cols = [0, 1, 2, 3, 5]
    if any(df.shape[1] <= c for c in required_cols):
        raise ValueError("交差点CSVの列数が不足しています")

    cross: Crossroad | None = None
    for _, row in df.iterrows():
        try:
            cross_id = str(row.iloc[0])
            center_lon = float(row.iloc[1])
            center_lat = float(row.iloc[2])
            branch_no = str(row.iloc[3])
            dir_deg_val = float(row.iloc[5])
        except Exception:
            continue

        if cross is None:
            cross = Crossroad(cross_id, center_lat, center_lon, [])
        cross.branches.append(Branch(branch_no, dir_deg_val))

    if cross is None:
        raise ValueError("交差点CSVからデータを取得できませんでした")
    return cross


HEADER = [
    "交差点ファイル名",
    "交差点ID",
    "抽出CSVファイル名",
    "運行日",
    "曜日",
    "運行ID",
    "トリップID",
    "自動車の種別",
    "用途",
    "流入枝番",
    "流出枝番",
    "流入角度差(deg)",
    "流出角度差(deg)",
    "角度算出方式",
    "計測距離(m)",
    "所要時間(s)",
    "閑散時所要時間(s)",
    "遅れ時間(s)",
    "所要時間算出可否",
    "所要時間算出不可理由",
    # ---- ここから診断用（補間区間・最近接情報）----
    "計測区間_前(m)",
    "計測区間_後(m)",
    "中心最近接距離(m)",
    "中心最近接位置(m)",
    "計測開始位置(m)",
    "計測終了位置(m)",
    "計測開始_経度(補間)", "計測開始_緯度(補間)", "計測開始_GPS時刻(補間)",
    "計測終了_経度(補間)", "計測終了_緯度(補間)", "計測終了_GPS時刻(補間)",
    # ---- 可視化・検証用（中心点ずれの確認）----
    "交差点中心_経度", "交差点中心_緯度",
    "算出中心_経度", "算出中心_緯度", "算出中心_GPS時刻",
    "point-4経度", "point-4緯度", "point-4GPS時刻",
    "point-3経度", "point-3緯度", "point-3GPS時刻",
    "point-2経度", "point-2緯度", "point-2GPS時刻",
    "point-1経度", "point-1緯度", "point-1GPS時刻",
    "【中央】経度", "【中央】緯度", "【中央】GPS時刻",
    "point+1経度", "point+1緯度", "point+1GPS時刻",
    "point+2経度", "point+2緯度", "point+2GPS時刻",
    "point+3経度", "point+3緯度", "point+3GPS時刻",
    "point+4経度", "point+4緯度", "point+4GPS時刻",
]


# 様式1-2（ヘッダ無し）の列インデックス（0 始まり）
COL_DATE = 2          # C列: 運行日 YYYYMMDD
COL_RUN_ID = 3        # D列: 運行ID
COL_VEHICLE_TYPE = 4  # E列: 自動車の種別
COL_VEHICLE_USE = 5   # F列: 自動車の用途
COL_GPS_TIME = 6      # G列: GPS時刻 YYYYMMDDhhmmss
COL_TRIP_NO = 8       # I列: トリップ番号
COL_LON = 14          # O列: 経度
COL_LAT = 15          # P列: 緯度


def main() -> None:
    parser = argparse.ArgumentParser(description="交差点通過性能算出スクリプト")
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="デバッグ用: 中間temp CSVを削除せず残す",
    )
    args = parser.parse_args()
    # -------------------- 全体開始 --------------------
    start_all = time.time()
    start_all_str = time.strftime("%Y-%m-%d %H:%M:%S")

    print("=== 交差点性能解析: 31_crossroad_trip_performance ===")
    print(f"開始時間: {start_all_str}")
    print(f"出力フォルダ: {OUTPUT_BASE_DIR}")
    print(f"設定セット数: {len(CONFIG)}")
    if TARGET_WEEKDAYS:
        print(f"対象曜日: {', '.join(TARGET_WEEKDAYS)}")
    else:
        print("対象曜日: 全曜日")
    print(
        f"計測区間(所要時間算出): 前{MEASURE_PRE_M:.0f}m〜後{MEASURE_POST_M:.0f}m"
        f"（距離{MEASURE_PRE_M+MEASURE_POST_M:.0f}m固定）"
    )
    print("遅れ定義: 方向別(流入×流出)の所要時間 下位5%平均をT0とし、遅れ=所要時間-T0")
    print("--------------------------------------------------")

    # ==============================================================
    #   各 CONFIG セット（交差点ごと）の処理
    # ==============================================================
    for cfg_idx, cfg in enumerate(CONFIG, start=1):
        tmp_path = None
        tmp_fh = None
        tmp_writer = None
        try:
            trip_folder = Path(cfg["trip_folder"])
            crossroad_path = Path(cfg["crossroad_file"])

            out_dir = Path(OUTPUT_BASE_DIR)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_csv = out_dir / f"{crossroad_path.stem}_performance.csv"

            trip_files = sorted(trip_folder.glob("*.csv"))
            total_files = len(trip_files)

            if total_files == 0:
                print(f"[{cfg_idx}/{len(CONFIG)}] 交差点: {crossroad_path.name}  入力CSVなし（スキップ）")
                continue

            # -------------------- セット開始 --------------------
            cfg_start = time.time()
            cfg_start_str = time.strftime("%Y-%m-%d %H:%M:%S")

            # カウンタ類
            total_trips = 0
            hit_trips = 0
            nopass_trips = 0
            bad_time_trips = 0
            out_of_range_trips = 0
            time_ok_trips = 0
            time_ng_trips = 0
            no_segment_trips = 0

            print(f"\n[{cfg_idx}/{len(CONFIG)}] 交差点: {crossroad_path.name}")
            print(f"  入力フォルダ: {trip_folder}")
            print(f"  対象CSVファイル数: {total_files}")
            print(f"  セット開始時間: {cfg_start_str}")
            if TARGET_WEEKDAYS:
                print(f"  曜日フィルタ: {', '.join(TARGET_WEEKDAYS)}")
            else:
                print("  曜日フィルタ: なし（全曜日）")

            tmp_fh = tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8-sig",
                delete=False,
                prefix="tmp_31_crossroad_",
                suffix=".csv",
            )
            tmp_path = tmp_fh.name
            tmp_writer = csv.writer(tmp_fh)
            tmp_writer.writerow(HEADER)

            idx_t0 = HEADER.index("閑散時所要時間(s)")
            idx_delay = HEADER.index("遅れ時間(s)")
            elapsed_map = {}

            with out_csv.open("w", encoding="cp932", errors="ignore", newline="") as fw:
                final_writer = csv.writer(fw)
                final_writer.writerow(HEADER)

                cross = load_crossroad_file(crossroad_path)

                # ====================== CSVループ ======================
                for file_idx, trip_csv in enumerate(trip_files, start=1):
                    df = pd.read_csv(trip_csv, dtype=str, encoding="cp932", header=None)

                    if df.empty:
                        continue

                    if COL_TRIP_NO < df.shape[1]:
                        trip_groups = df.groupby(df[COL_TRIP_NO])
                    else:
                        trip_groups = [("ALL", df)]

                    # ------------------- トリップごとの処理 -------------------
                    for trip_key, g in trip_groups:
                        trip_date = str(g.iloc[0, COL_DATE])
                        weekday = weekday_abbr(trip_date[:8]) if trip_date else ""

                        if TARGET_WEEKDAYS and weekday not in TARGET_WEEKDAYS:
                            continue

                        total_trips += 1

                        run_id = str(g.iloc[0, COL_RUN_ID])
                        trip_id = str(trip_key)
                        vehicle_type = str(g.iloc[0, COL_VEHICLE_TYPE])
                        vehicle_use = str(g.iloc[0, COL_VEHICLE_USE])

                        # 座標と時刻
                        points = []
                        gps_times = []
                        for _, row in g.iterrows():
                            try:
                                lon = float(row[COL_LON])
                                lat = float(row[COL_LAT])
                            except:
                                continue
                            points.append((lat, lon))
                            gps_times.append(str(row[COL_GPS_TIME]))

                        if not points:
                            nopass_trips += 1
                            continue

                        if not trip_passes_crossroad(points, cross.center_lat, cross.center_lon):
                            nopass_trips += 1
                            continue

                        hit_trips += 1

                        # --------- ここから：通過したら必ず1行出す（所要時間は欠損でもOK） ---------
                        cumdist = build_cumdist(points)
                        dist_m = MEASURE_PRE_M + MEASURE_POST_M  # 定義上の距離（MEASURE_PRE_M+MEASURE_POST_M固定）
                        elapsed = None
                        time_valid = 0
                        time_reason = "OK"

                        # 診断用のデフォルト（埋まるところだけ埋める）
                        seg_i = None
                        seg_d = ""
                        center_pos_m = ""
                        start_pos_m = ""
                        end_pos_m = ""
                        lon_s = ""
                        lat_s = ""
                        t_s = ""
                        lon_e = ""
                        lat_e = ""
                        t_e = ""
                        in_diff = float("inf")
                        out_diff = float("inf")
                        angle_method_str = ""
                        # 交差点中心（指定）と、算出中心（トリップ最近接点）
                        cross_center_lon_s = ""
                        cross_center_lat_s = ""
                        center_lon_calc_s = ""
                        center_lat_calc_s = ""
                        center_time_calc_s = ""

                        # 最近接線分（中心への最短距離となる線分）を求める
                        seg_i_i, seg_t_f, seg_d_f = closest_segment_to_center(points, cross.center_lat, cross.center_lon)
                        seg_i = seg_i_i
                        idx_center = closest_center_index(points, cross.center_lat, cross.center_lon)

                        # 交差点指定中心（比較表示用）
                        cross_center_lon_s = f"{cross.center_lon:.8f}"
                        cross_center_lat_s = f"{cross.center_lat:.8f}"

                        # 算出中心（線分上最近接点の座標）
                        if seg_i is not None:
                            lat1, lon1 = points[seg_i]
                            lat2, lon2 = points[seg_i + 1]
                            lat_c = lat1 + seg_t_f * (lat2 - lat1)
                            lon_c = lon1 + seg_t_f * (lon2 - lon1)
                            center_lon_calc_s = f"{lon_c:.8f}"
                            center_lat_calc_s = f"{lat_c:.8f}"

                        # --- 枝判定用の中心位置（道なり距離）を「最近接線分上の最近接点」にする ---
                        center_pos_val_dir = None
                        if seg_i is not None:
                            seg_len_dir = cumdist[seg_i + 1] - cumdist[seg_i]
                            center_pos_val_dir = cumdist[seg_i] + seg_t_f * seg_len_dir
                        elif idx_center is not None and idx_center < len(cumdist):
                            center_pos_val_dir = cumdist[idx_center]

                        # 流入/流出枝番：基本は最近接線分の前後点。取れない場合は中心最近接点±1で代替。
                        if seg_i is not None:
                            idx_b = seg_i
                            idx_a = seg_i + 1
                        elif idx_center is None:
                            # ここまで来て points があるのに中心最寄りが取れないのは例外的
                            time_reason = "NO_SEGMENT"
                            time_valid = 0
                            no_segment_trips += 1
                            idx_b = 0
                            idx_a = min(1, len(points) - 1)
                        else:
                            idx_b = max(0, idx_center - 1)
                            idx_a = min(len(points) - 1, idx_center + 1)

                        # ============================================================
                        # 枝判定角度：中心前後の「2点」から走行方向を推定して安定化
                        # ============================================================
                        angle_method = []
                        # ※ center_pos_val_dir は「指定中心点に対するトリップ最近接点」（線分上）を優先する
                        center_pos_val = center_pos_val_dir

                        # ---- IN（流入）: (center-60m -> center-10m) の走行方向を推定し、180度反転 ----
                        if center_pos_val is not None:
                            p_in_far = interpolate_point_at_distance(points, cumdist, center_pos_val - DIR_IN_FAR_M)
                            p_in_near = interpolate_point_at_distance(points, cumdist, center_pos_val - DIR_IN_NEAR_M)
                        else:
                            p_in_far = None
                            p_in_near = None

                        if p_in_far and p_in_near:
                            approach_bearing = bearing_deg(p_in_far[0], p_in_far[1], p_in_near[0], p_in_near[1])
                            in_angle = (approach_bearing + 180.0) % 360.0  # center->branch と同じ向きへ
                            angle_method.append("IN:interp2pt")
                        else:
                            idx_center_fallback = idx_center if idx_center is not None else idx_b
                            idx_in = max(0, idx_center_fallback - 2)
                            in_angle = bearing_deg(points[idx_center_fallback][0], points[idx_center_fallback][1],
                                                   points[idx_in][0], points[idx_in][1])
                            angle_method.append("IN:fallback_idx-2")

                        # ---- OUT（流出）: (center+10m -> center+60m) の走行方向を推定 ----
                        if center_pos_val is not None:
                            p_out_near = interpolate_point_at_distance(points, cumdist, center_pos_val + DIR_OUT_NEAR_M)
                            p_out_far = interpolate_point_at_distance(points, cumdist, center_pos_val + DIR_OUT_FAR_M)
                        else:
                            p_out_near = None
                            p_out_far = None

                        if p_out_near and p_out_far:
                            out_angle = bearing_deg(p_out_near[0], p_out_near[1], p_out_far[0], p_out_far[1])
                            angle_method.append("OUT:interp2pt")
                        else:
                            idx_center_fallback = idx_center if idx_center is not None else idx_a
                            idx_out = min(len(points) - 1, idx_center_fallback + 2)
                            out_angle = bearing_deg(points[idx_center_fallback][0], points[idx_center_fallback][1],
                                                    points[idx_out][0], points[idx_out][1])
                            angle_method.append("OUT:fallback_idx+2")

                        in_branch_raw, in_diff = find_nearest_branch_with_diff(in_angle, cross.branches)
                        out_branch_raw, out_diff = find_nearest_branch_with_diff(out_angle, cross.branches)
                        in_branch = in_branch_raw if in_diff <= BRANCH_MAX_ANGLE_DIFF_DEG else ""
                        out_branch = out_branch_raw if out_diff <= BRANCH_MAX_ANGLE_DIFF_DEG else ""
                        angle_method_str = "/".join(angle_method)

                        # 最近接線分の診断情報（可能な範囲で記録）
                        if seg_i is not None:
                            seg_d = f"{seg_d_f:.3f}"

                        # GPS時刻（datetime）を用意（補間で必要）
                        dt_list = [parse_dt14(t) for t in gps_times]
                        if any(d is None for d in dt_list):
                            # 通過としてはカウントするが、所要時間は算出不可
                            time_valid = 0
                            time_reason = "TIME_MISSING"
                            bad_time_trips += 1
                        else:
                            # 算出中心の時刻（線分上最近接点：seg_i と seg_t_f で補間）
                            if seg_i is not None:
                                from datetime import timedelta
                                dt0 = dt_list[seg_i]
                                dt1 = dt_list[seg_i + 1]
                                if dt0 is not None and dt1 is not None:
                                    dtc = dt0 + timedelta(seconds=seg_t_f * (dt1 - dt0).total_seconds())
                                    center_time_calc_s = dtc.strftime("%Y%m%d%H%M%S")

                            # 道なり距離と中心基準位置（線分上最近接）を計算
                            if seg_i is None:
                                time_valid = 0
                                time_reason = "NO_SEGMENT"
                                no_segment_trips += 1
                            else:
                                seg_len = cumdist[seg_i + 1] - cumdist[seg_i]
                                center_pos_val = cumdist[seg_i] + seg_t_f * seg_len
                                center_pos_m = f"{center_pos_val:.3f}"

                                start_pos_val = center_pos_val - MEASURE_PRE_M
                                end_pos_val = center_pos_val + MEASURE_POST_M
                                start_pos_m = f"{start_pos_val:.3f}"
                                end_pos_m = f"{end_pos_val:.3f}"

                                # 計測区間がトリップ範囲外 → 所要時間算出不可（ただし行は出す）
                                if start_pos_val < 0 or end_pos_val > cumdist[-1]:
                                    time_valid = 0
                                    time_reason = "OUT_OF_RANGE"
                                    out_of_range_trips += 1
                                else:
                                    lat_s_v, lon_s_v, dt_s = interpolate_at_distance(
                                        points,
                                        dt_list,
                                        cumdist,
                                        start_pos_val,
                                    )
                                    lat_e_v, lon_e_v, dt_e = interpolate_at_distance(
                                        points,
                                        dt_list,
                                        cumdist,
                                        end_pos_val,
                                    )
                                    if dt_s is None or dt_e is None:
                                        time_valid = 0
                                        time_reason = "TIME_MISSING"
                                        bad_time_trips += 1
                                    else:
                                        elapsed = (dt_e - dt_s).total_seconds()
                                        if elapsed and elapsed > 0:
                                            time_valid = 1
                                            time_reason = "OK"
                                        else:
                                            elapsed = None
                                            time_valid = 0
                                            time_reason = "TIME_MISSING"
                                        lon_s, lat_s = f"{lon_s_v:.8f}", f"{lat_s_v:.8f}"
                                        lon_e, lat_e = f"{lon_e_v:.8f}", f"{lat_e_v:.8f}"
                                        t_s = dt_s.strftime("%Y%m%d%H%M%S")
                                        t_e = dt_e.strftime("%Y%m%d%H%M%S")

                        if time_valid == 1:
                            time_ok_trips += 1
                        else:
                            time_ng_trips += 1

                        # 生プロット（中心付近の前後4点＋中央）
                        if idx_center is not None:
                            raw_center_idx = idx_center
                        elif seg_i is not None:
                            raw_center_idx = min(seg_i + 1, len(points) - 1)
                        else:
                            raw_center_idx = 0

                        def _fmt_pt(i):
                            try:
                                lat_v, lon_v = points[i]
                                lon_s_v = f"{lon_v:.8f}"
                                lat_s_v = f"{lat_v:.8f}"
                                gps_s_v = gps_times[i] if i < len(gps_times) else ""
                                return lon_s_v, lat_s_v, gps_s_v
                            except Exception:
                                return "", "", ""

                        raw_cols = []
                        for k in [-4, -3, -2, -1, 0, 1, 2, 3, 4]:
                            idx_raw = max(0, min(raw_center_idx + k, len(points) - 1))
                            lon_raw, lat_raw, gps_raw = _fmt_pt(idx_raw)
                            raw_cols.extend([lon_raw, lat_raw, gps_raw])

                        row_out = [
                            crossroad_path.name,
                            cross.cross_id,
                            trip_csv.name,
                            trip_date,
                            weekday,
                            run_id,
                            trip_id,
                            vehicle_type,
                            vehicle_use,
                            str(in_branch),
                            str(out_branch),
                            f"{in_diff:.1f}" if in_diff != float("inf") else "",
                            f"{out_diff:.1f}" if out_diff != float("inf") else "",
                            angle_method_str,
                            f"{dist_m:.3f}",
                            f"{elapsed:.3f}" if elapsed is not None else "",
                            "",
                            "",
                            str(time_valid),
                            time_reason,
                        ]
                        # ---- 診断用列（補間区間・最近接情報） ----
                        row_out.extend([
                            f"{MEASURE_PRE_M:.0f}",
                            f"{MEASURE_POST_M:.0f}",
                            seg_d,
                            center_pos_m,
                            start_pos_m,
                            end_pos_m,
                            lon_s, lat_s, t_s,
                            lon_e, lat_e, t_e,
                            cross_center_lon_s, cross_center_lat_s,
                            center_lon_calc_s, center_lat_calc_s, center_time_calc_s,
                        ])
                        row_out.extend(raw_cols)

                        assert len(row_out) == len(HEADER)
                        row_out[idx_t0] = ""
                        row_out[idx_delay] = ""
                        tmp_writer.writerow(row_out)

                        if elapsed is not None:
                            key = (str(in_branch), str(out_branch))
                            elapsed_map.setdefault(key, []).append(float(elapsed))

                    # ----------- 進捗表示（1行上書き） -----------
                    progress = file_idx / total_files * 100.0
                    elapsed_cfg = time.time() - cfg_start

                    print(
                        f"\r  進捗: {file_idx:4d}/{total_files:4d} "
                        f"({progress:5.1f}%)  "
                        f"対象トリップ: {total_trips:6d}  HIT: {hit_trips:6d}  "
                        f"該当なし: {nopass_trips:6d}  "
                        f"経過時間: {elapsed_cfg/60:5.1f}分",
                        end="",
                        flush=True,
                    )

                tmp_fh.close()
                tmp_fh = None

                t0_map = {}
                for key, vals in elapsed_map.items():
                    if not vals:
                        continue
                    vals.sort()
                    k = max(1, int(len(vals) * 0.05))
                    t0 = sum(vals[:k]) / k
                    t0_map[key] = t0

                idx_in_b = HEADER.index("流入枝番")
                idx_out_b = HEADER.index("流出枝番")
                idx_elapsed = HEADER.index("所要時間(s)")

                with open(tmp_path, "r", newline="", encoding="utf-8-sig") as rf:
                    reader = csv.reader(rf)
                    header_in = next(reader, None)
                    if header_in != HEADER:
                        raise RuntimeError(
                            "temp CSV header mismatch: HEADERが一致しません。31/32の列整合を確認してください。"
                        )
                    for row in reader:
                        in_b = row[idx_in_b]
                        out_b = row[idx_out_b]
                        key = (in_b, out_b)
                        if row[idx_elapsed] != "" and key in t0_map:
                            try:
                                elapsed_val = float(row[idx_elapsed])
                                t0 = float(t0_map[key])
                                row[idx_t0] = f"{t0:.3f}"
                                row[idx_delay] = f"{(elapsed_val - t0):.3f}"
                            except Exception:
                                pass
                        final_writer.writerow(row)
        finally:
            try:
                if tmp_fh is not None:
                    tmp_fh.close()
            except Exception:
                pass
            if tmp_path is not None and not args.keep_temp:
                try:
                    os.remove(tmp_path)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    print(f"[WARN] failed to delete temp: {tmp_path} ({e})")
            if tmp_path is not None and args.keep_temp:
                print(f"[KEEP] temp kept: {tmp_path}")

        # --------------- セット終了情報 ---------------
        cfg_end = time.time()
        cfg_end_str = time.strftime("%Y-%m-%d %H:%M:%S")
        cfg_minutes = (cfg_end - cfg_start) / 60

        print()  # 強制改行
        print(f"  セット終了時間: {cfg_end_str}")
        print(
            f"  完了: ファイル={total_files}, 対象トリップ={total_trips}, "
            f"HIT={hit_trips}, 該当なし={nopass_trips}, "
            f"所要時間OK={time_ok_trips}, 所要時間NG={time_ng_trips}, "
            f"所要時間NG(時刻欠損)={bad_time_trips}, 所要時間NG(区間範囲外)={out_of_range_trips}, "
            f"所要時間NG(線分取得不可)={no_segment_trips}, "
            f"所要時間={cfg_minutes:5.1f}分"
        )

    # -------------------- 全体終了 --------------------
    end_all = time.time()
    end_all_str = time.strftime("%Y-%m-%d %H:%M:%S")
    total_minutes = (end_all - start_all) / 60

    print("--------------------------------------------------")
    print(f"終了時間: {end_all_str}")
    print(f"総所要時間: {total_minutes:5.1f}分")
    print("=== 全セット完了 ===")


if __name__ == "__main__":
    main()
