"""
交差点通過性能算出スクリプト（31/16 ロジック準拠）
"""
from __future__ import annotations

import csv
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
# 交差点通過判定ロジック（31 / 16 と完全一致）
# ============================================================
CROSSROAD_HIT_DIST_M = 20.0
CROSSROAD_SEG_HIT_DIST_M = 20.0
CROSSROAD_MIN_HITS = 1
EARTH_RADIUS_M = 6_371_000.0


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
    "道なり距離(m)",
    "所要時間(s)",
    "交差点通過速度(km/h)",
    # ここから追加 → 前後5点の経度/緯度/GPS時刻
    "5P前_経度","5P前_緯度","5P前_GPS時刻",
    "4P前_経度","4P前_緯度","4P前_GPS時刻",
    "3P前_経度","3P前_緯度","3P前_GPS時刻",
    "2P前_経度","2P前_緯度","2P前_GPS時刻",
    "1P前_経度","1P前_緯度","1P前_GPS時刻",
    "中心点_経度","中心点_緯度","中心点_GPS時刻",
    "1P後_経度","1P後_緯度","1P後_GPS時刻",
    "2P後_経度","2P後_緯度","2P後_GPS時刻",
    "3P後_経度","3P後_緯度","3P後_GPS時刻",
    "4P後_経度","4P後_緯度","4P後_GPS時刻",
    "5P後_経度","5P後_緯度","5P後_GPS時刻",
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
    WINDOW = 5  # 5点前後

    for cfg in CONFIG:
        trip_folder = Path(cfg["trip_folder"])
        crossroad_path = Path(cfg["crossroad_file"])

        out_dir = Path(OUTPUT_BASE_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / f"{crossroad_path.stem}_performance.csv"

        with out_csv.open("w", encoding="cp932", errors="ignore", newline="") as fw:
            writer = csv.writer(fw)
            writer.writerow(HEADER)

            # 交差点CSVの読み込み（dir_deg を持つ枝一覧など）
            cross = load_crossroad_file(crossroad_path)

            # 様式1-2 CSV を全部処理
            for trip_csv in sorted(trip_folder.glob("*.csv")):
                # 第2スクリーニング様式1-2（ヘッダ無し）を読み込む
                df = pd.read_csv(trip_csv, dtype=str, encoding="cp932", header=None)

                if df.empty:
                    continue

                # I列（COL_TRIP_NO）にトリップ番号が入っている前提で groupby
                if COL_TRIP_NO < df.shape[1]:
                    trip_groups = df.groupby(df[COL_TRIP_NO])
                else:
                    # 念のため：トリップ列が無ければファイル全体を1トリップとして扱う
                    trip_groups = [("ALL", df)]

                for trip_key, g in trip_groups:
                    # --- 運行日・曜日の算出 ---
                    trip_date = ""
                    if COL_DATE < g.shape[1]:
                        trip_date = str(g.iloc[0, COL_DATE])  # C列: YYYYMMDD
                    weekday = weekday_abbr(trip_date[:8]) if trip_date else ""

                    # 曜日フィルタ：TARGET_WEEKDAYS が空でなければ、その中に含まれるものだけ処理
                    if TARGET_WEEKDAYS and weekday not in TARGET_WEEKDAYS:
                        continue

                    # 運行ID・トリップID・車種・用途
                    run_id = str(g.iloc[0, COL_RUN_ID]) if COL_RUN_ID < g.shape[1] else ""
                    trip_id = str(trip_key)
                    vehicle_type = str(g.iloc[0, COL_VEHICLE_TYPE]) if COL_VEHICLE_TYPE < g.shape[1] else ""
                    vehicle_use = str(g.iloc[0, COL_VEHICLE_USE]) if COL_VEHICLE_USE < g.shape[1] else ""

                    # --- 座標列とGPS時刻列を構成 ---
                    points: list[tuple[float, float]] = []
                    gps_times: list[str] = []
                    for _, row in g.iterrows():
                        try:
                            lon_str = row[COL_LON]
                            lat_str = row[COL_LAT]
                        except Exception:
                            continue

                        if lon_str == "" or lat_str == "":
                            continue

                        try:
                            lon = float(lon_str)
                            lat = float(lat_str)
                        except Exception:
                            continue

                        points.append((lat, lon))
                        gps_times.append(str(row[COL_GPS_TIME]) if COL_GPS_TIME < len(row) else "")

                    if not points:
                        continue

                    # ① 通過判定（16/31 と同じ）
                    if not trip_passes_crossroad(points, cross.center_lat, cross.center_lon):
                        continue

                    # ② 中心点 index
                    idx_center = closest_center_index(points, cross.center_lat, cross.center_lon)
                    if idx_center is None:
                        continue

                    # ③ 前後5点の index 範囲
                    idx_s = max(0, idx_center - WINDOW)
                    idx_e = min(len(points) - 1, idx_center + WINDOW)

                    # ④ 流入・流出方向の判定（中心の1つ前と1つ後から方位角を算出）
                    idx_b = max(0, idx_center - 1)
                    idx_a = min(len(points) - 1, idx_center + 1)

                    in_angle = bearing_deg(
                        points[idx_b][0], points[idx_b][1],
                        points[idx_center][0], points[idx_center][1],
                    )
                    out_angle = bearing_deg(
                        points[idx_center][0], points[idx_center][1],
                        points[idx_a][0], points[idx_a][1],
                    )

                    in_branch = find_nearest_branch(in_angle, cross.branches)
                    out_branch = find_nearest_branch(out_angle, cross.branches)

                    # ⑤ 道なり距離・所要時間・速度
                    dist_m = accum_distance(points, idx_s, idx_e)

                    t_start = parse_dt14(gps_times[idx_s]) if gps_times[idx_s] else None
                    t_end = parse_dt14(gps_times[idx_e]) if gps_times[idx_e] else None
                    elapsed = (t_end - t_start).total_seconds() if (t_start and t_end) else None
                    speed_kmh = dist_m / elapsed * 3.6 if (elapsed and elapsed > 0) else None

                    # ⑥ 性能部分の基本カラム
                    row_out = [
                        crossroad_path.name,         # 交差点ファイル名
                        cross.cross_id,              # 交差点ID
                        trip_csv.name,               # 抽出CSVファイル名
                        trip_date,                   # 運行日(C列)
                        weekday,                     # 曜日(MON〜SUN)
                        run_id,                      # 運行ID(D列)
                        trip_id,                     # トリップID(I列)
                        vehicle_type,                # 自動車の種別(E列)
                        vehicle_use,                 # 用途(F列)
                        str(in_branch),              # 流入枝番
                        str(out_branch),             # 流出枝番
                        f"{dist_m:.3f}",
                        f"{elapsed:.3f}" if elapsed else "",
                        f"{speed_kmh:.3f}" if speed_kmh else "",
                    ]

                    # ⑦ 前後5点の経度・緯度・GPS時刻を右側に追加（範囲外は空欄）
                    def safe(idx: int) -> tuple[str, str, str]:
                        if 0 <= idx < len(points):
                            lat, lon = points[idx]
                            t = gps_times[idx] if idx < len(gps_times) else ""
                            # 出力順は「経度, 緯度, GPS時刻」
                            return f"{lon}", f"{lat}", t
                        return "", "", ""

                    for offset in range(-5, 6):  # -5 ～ +5
                        lon_s, lat_s, t_s = safe(idx_center + offset)
                        row_out.extend([lon_s, lat_s, t_s])

                    writer.writerow(row_out)


if __name__ == "__main__":
    main()
