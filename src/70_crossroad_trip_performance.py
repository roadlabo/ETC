"""
交差点通過性能算出スクリプト

CONFIG に複数セットを記載し、各セットについて以下を行う。
- 様式1-2 (第2スクリーニング後) CSV が入ったフォルダの全CSVを解析
- 交差点CSVを読み込み、中心点・枝方向をもとに流入/流出枝番号を判定
- 3ポイント前〜1ポイント後の距離・時間・速度などを集計し、交差点ごとに1行にまとめて出力

依存: pandas, numpy
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# =========================================
# 入出力設定（ユーザーが冒頭で編集可能にする）
# 出力フォルダ（ここだけ変更すればすべての出力がこの中に生成される）
OUTPUT_BASE_DIR = r"C:\\path\\to\\output_folder"

CONFIG = [
    {
        "trip_folder": r"C:\\path\\to\\screening2_folder1",  # 様式1-2 が大量に入ったフォルダ
        "crossroad_file": r"C:\\path\\to\\crossroad1.csv",   # 交差点CSV（フルパス）
    },
    {
        "trip_folder": r"C:\\path\\to\\screening2_folder2",
        "crossroad_file": r"C:\\path\\to\\crossroad2.csv",
    },
    # 必要なだけ追加できる
]
# =========================================

# 設定値
MAX_DISTANCE_M = 20.0  # 交差点中心からの許容距離
EARTH_R = 6_371_000.0  # 地球半径[m]

# 様式1-2列定義（0始まりindex／ヘッダー名候補）
COL_DATE = 2  # 運行日(C)
COL_RUN_ID = 3  # 運行ID(D)
COL_TRIP_ID = 8  # トリップ番号(I)
COL_VEHICLE_TYPE = 4  # 自動車の種別(E)
COL_VEHICLE_USAGE = 5  # 自動車の用途(F)
COL_TIME = 6  # 時刻(G)
COL_LON = 14  # 経度(O)
COL_LAT = 15  # 緯度(P)

DATE_NAMES = ["運行日", "date", "運行DATE"]
RUN_ID_NAMES = ["運行ID", "run_id", "運行Id"]
TRIP_ID_NAMES = ["トリップ番号", "trip_id", "trip", "トリップID"]
VEHICLE_TYPE_NAMES = ["自動車の種別", "vehicle_type"]
VEHICLE_USAGE_NAMES = ["自動車の用途", "vehicle_usage"]
TIME_NAMES = ["時刻", "time"]
LON_NAMES = ["経度", "lon", "longitude"]
LAT_NAMES = ["緯度", "lat", "latitude"]

OUTPUT_COLUMNS = [
    "交差点ファイル名",
    "抽出CSVファイル名",
    "運行日",
    "曜日",
    "運行ID",
    "トリップID",
    "自動車の種別",
    "自動車の用途",
    "流入枝番",
    "流出枝番",
    "3Point前～1Point後の道なり距離(m)",
    "3Point前～1Point後の所要時間(秒)",
    "交差点通過速度(m/s)",
    "3Point前時刻",
    "2Point前時刻",
    "1Point前時刻",
    "中心Point時刻",
    "1Point後時刻",
    "3Point前経度",
    "3Point前緯度",
    "2Point前経度",
    "2Point前緯度",
    "1Point前経度",
    "1Point前緯度",
    "中心Point経度",
    "中心Point緯度",
    "1Point後経度",
    "1Point後緯度",
]


@dataclass
class Branch:
    branch_no: str
    dir_deg: float


@dataclass
class Crossroad:
    crossroad_id: str
    center_lon: float
    center_lat: float
    branches: List[Branch]


@dataclass
class TripContext:
    run_date: str
    weekday: str
    run_id: str
    trip_id: str
    vehicle_type: str
    vehicle_usage: str
    file_name: str


@dataclass
class WindowData:
    lon: List[float]
    lat: List[float]
    times: List[pd.Timestamp]


# ---------------------- ユーティリティ ----------------------
def deg2rad(deg: float) -> float:
    return deg * math.pi / 180.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r, lon1_r, lat2_r, lon2_r = map(deg2rad, [lat1, lon1, lat2, lon2])
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """方位角(北=0, 時計回り)"""
    lat1_r, lon1_r, lat2_r, lon2_r = map(deg2rad, [lat1, lon1, lat2, lon2])
    dlon = lon2_r - lon1_r
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    theta = math.atan2(x, y)
    deg_val = math.degrees(theta)
    return (deg_val + 360) % 360


def angular_diff(a: float, b: float) -> float:
    diff = abs(a - b) % 360
    return diff if diff <= 180 else 360 - diff


def read_csv_flexible(path: str) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            continue
    raise RuntimeError(f"CSVを読み込めませんでした: {path}")


def get_column(df: pd.DataFrame, candidates: Sequence[str], index_fallback: int) -> pd.Series:
    for name in candidates:
        if name in df.columns:
            return df[name]
    if df.shape[1] > index_fallback:
        return df.iloc[:, index_fallback]
    raise KeyError(f"列が見つかりません: {candidates} (index {index_fallback})")


# ---------------------- 交差点処理 ----------------------
def load_crossroads(path: str) -> Dict[str, Crossroad]:
    df = read_csv_flexible(path)
    required_cols = [0, 1, 2, 3, 5]
    if any(df.shape[1] <= c for c in required_cols):
        raise ValueError("交差点CSVの列数が不足しています")
    crossroads: Dict[str, Crossroad] = {}
    for _, row in df.iterrows():
        try:
            cross_id = str(row.iloc[0])
            center_lon = float(row.iloc[1])
            center_lat = float(row.iloc[2])
            branch_no = str(row.iloc[3])
            dir_deg_val = float(row.iloc[5])
        except Exception:
            continue
        if cross_id not in crossroads:
            crossroads[cross_id] = Crossroad(cross_id, center_lon, center_lat, [])
        crossroads[cross_id].branches.append(Branch(branch_no, dir_deg_val))
    return crossroads


# ---------------------- トリップ処理 ----------------------
def list_trip_files(folder: str) -> List[str]:
    return sorted(str(p) for p in Path(folder).glob("*.csv"))


def extract_trip_context(df: pd.DataFrame, trip_id_value) -> TripContext:
    date_col = get_column(df, DATE_NAMES, COL_DATE)
    run_id_col = get_column(df, RUN_ID_NAMES, COL_RUN_ID)
    vehicle_type_col = get_column(df, VEHICLE_TYPE_NAMES, COL_VEHICLE_TYPE)
    vehicle_usage_col = get_column(df, VEHICLE_USAGE_NAMES, COL_VEHICLE_USAGE)
    trip_id_series = get_column(df, TRIP_ID_NAMES, COL_TRIP_ID)

    idx_list = df.index[trip_id_series == trip_id_value].tolist()
    if not idx_list:
        return TripContext("", "", "", str(trip_id_value), "", "", "")

    first_idx = idx_list[0]
    run_date = str(date_col.iloc[first_idx]) if len(date_col) > first_idx else ""
    run_id = str(run_id_col.iloc[first_idx]) if len(run_id_col) > first_idx else ""
    vehicle_type = str(vehicle_type_col.iloc[first_idx]) if len(vehicle_type_col) > first_idx else ""
    vehicle_usage = str(vehicle_usage_col.iloc[first_idx]) if len(vehicle_usage_col) > first_idx else ""

    weekday = ""
    try:
        dt = pd.to_datetime(run_date)
        if pd.notna(dt):
            weekday = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
    except Exception:
        weekday = ""

    return TripContext(
        run_date=run_date,
        weekday=weekday,
        run_id=run_id,
        trip_id=str(trip_id_value),
        vehicle_type=vehicle_type,
        vehicle_usage=vehicle_usage,
        file_name="",
    )


def extract_window(df_trip: pd.DataFrame, center_idx: int) -> Optional[WindowData]:
    start = center_idx - 3
    if start < 0 or (center_idx + 1) >= len(df_trip):
        return None

    lon_series = get_column(df_trip, LON_NAMES, COL_LON)
    lat_series = get_column(df_trip, LAT_NAMES, COL_LAT)
    time_series = get_column(df_trip, TIME_NAMES, COL_TIME)

    lon_vals = lon_series.iloc[start : center_idx + 2].tolist()
    lat_vals = lat_series.iloc[start : center_idx + 2].tolist()
    time_vals_raw = time_series.iloc[start : center_idx + 2]
    time_vals = pd.to_datetime(time_vals_raw, errors="coerce")
    if time_vals.isna().any():
        return None

    return WindowData(lon=lon_vals, lat=lat_vals, times=list(time_vals))


def compute_distance_time_speed(window: WindowData) -> Tuple[float, float, Optional[float]]:
    dist = 0.0
    for i in range(len(window.lon) - 1):
        dist += haversine_m(window.lat[i], window.lon[i], window.lat[i + 1], window.lon[i + 1])
    time_sec = (window.times[-1] - window.times[0]).total_seconds()
    speed = dist / time_sec if time_sec > 0 else None
    return dist, time_sec, speed


def find_nearest_branch(angle: float, branches: Iterable[Branch]) -> str:
    min_branch = None
    min_diff = float("inf")
    for br in branches:
        diff = angular_diff(angle, br.dir_deg)
        if diff < min_diff:
            min_diff = diff
            min_branch = br.branch_no
    return min_branch or ""


def process_trip(
    trip_df: pd.DataFrame,
    cross: Crossroad,
    context: TripContext,
    crossroad_file_name: str,
) -> List[List[Optional[str]]]:
    results: List[List[Optional[str]]] = []
    try:
        lon_series = get_column(trip_df, LON_NAMES, COL_LON)
        lat_series = get_column(trip_df, LAT_NAMES, COL_LAT)
        trip_id_series = get_column(trip_df, TRIP_ID_NAMES, COL_TRIP_ID)
    except Exception:
        return results

    for trip_id, group in trip_df.groupby(trip_id_series):
        group = group.sort_index()
        ctx = extract_trip_context(trip_df, trip_id)
        ctx.file_name = context.file_name
        try:
            center_idx, nearest_d = find_nearest_point(group, cross.center_lat, cross.center_lon)
        except Exception:
            continue

        if center_idx is None or nearest_d is None:
            continue

        if nearest_d > MAX_DISTANCE_M:
            results.append(build_row_missing(ctx, crossroad_file_name))
            continue

        sub_df = trip_df.loc[group.index]
        window = extract_window(sub_df.reset_index(drop=True), center_idx)
        if window is None:
            continue

        dist_m, time_s, speed = compute_distance_time_speed(window)
        inbound_angle = bearing_deg(window.lat[2], window.lon[2], window.lat[3], window.lon[3])
        outbound_angle = bearing_deg(window.lat[3], window.lon[3], window.lat[4], window.lon[4])
        inbound_branch = find_nearest_branch(inbound_angle, cross.branches)
        outbound_branch = find_nearest_branch(outbound_angle, cross.branches)

        row = [
            crossroad_file_name,
            Path(ctx.file_name).name,
            ctx.run_date,
            ctx.weekday,
            ctx.run_id,
            ctx.trip_id,
            ctx.vehicle_type,
            ctx.vehicle_usage,
            inbound_branch,
            outbound_branch,
            f"{dist_m:.3f}",
            f"{time_s:.3f}",
            f"{speed:.3f}" if speed is not None else "",
        ]
        # times
        row.extend([t.strftime("%Y-%m-%d %H:%M:%S") for t in window.times])
        # coords
        for lon, lat in zip(window.lon, window.lat):
            row.append(lon)
            row.append(lat)

        results.append(row)
    return results


def build_row_missing(ctx: TripContext, cross_name: str) -> List[str]:
    missing = "該当なし"
    base = [
        cross_name,
        ctx.file_name,
        ctx.run_date,
        ctx.weekday,
        ctx.run_id,
        ctx.trip_id,
        ctx.vehicle_type,
        ctx.vehicle_usage,
    ]
    base.extend([missing] * (len(OUTPUT_COLUMNS) - len(base)))
    return base


def find_nearest_point(group: pd.DataFrame, center_lat: float, center_lon: float) -> Tuple[Optional[int], Optional[float]]:
    lon_series = get_column(group, LON_NAMES, COL_LON)
    lat_series = get_column(group, LAT_NAMES, COL_LAT)
    distances = [
        haversine_m(lat, lon, center_lat, center_lon) if pd.notna(lat) and pd.notna(lon) else float("inf")
        for lon, lat in zip(lon_series, lat_series)
    ]
    if not distances:
        return None, None
    min_idx = int(np.argmin(distances))
    return min_idx, distances[min_idx]


# ---------------------- メイン処理 ----------------------
def process_config(conf: Dict[str, str]) -> None:
    trip_folder = conf.get("trip_folder")
    crossroad_file = conf.get("crossroad_file")

    if not trip_folder or not crossroad_file:
        print(f"[SKIP] CONFIG が不完全です: {conf}")
        return

    crossroad_path = Path(conf["crossroad_file"])
    crossroad_stem = crossroad_path.stem  # 拡張子なし
    output_csv = Path(OUTPUT_BASE_DIR) / f"{crossroad_stem}_performance.csv"

    try:
        crossroads = load_crossroads(crossroad_file)
    except Exception as e:
        print(f"[ERROR] 交差点CSV読み込み失敗: {e}")
        return

    trip_files = list_trip_files(trip_folder)
    total = len(trip_files)

    set_start = datetime.now()

    print()
    print(f"[CROSSROAD] {crossroad_path.name}")
    print(f"  folder={trip_folder}  files={total}")
    print(f"  start={set_start.strftime('%H:%M:%S')}")

    def fmt_td_short(td: timedelta) -> str:
        total_sec = max(0, int(td.total_seconds()))
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        if h > 0:
            return f"{h:d}h{m:02d}m{s:02d}s"
        elif m > 0:
            return f"{m:d}m{s:02d}s"
        else:
            return f"{s:d}s"

    output_rows: List[List[Optional[str]]] = []

    crossroad_base = Path(crossroad_file).name

    for idx, trip_path in enumerate(trip_files, start=1):
        now = datetime.now()
        elapsed = now - set_start

        if total > 0 and idx > 0:
            avg_per_file = elapsed / idx
            remaining = avg_per_file * (total - idx)
            eta = now + remaining
            percent = idx / total * 100
        else:
            remaining = timedelta(0)
            eta = now
            percent = 100.0

        msg = (
            f"  [PROC] {idx}/{total} ({percent:5.1f}%) "
            f"elapsed={fmt_td_short(elapsed)} "
            f"remain={fmt_td_short(remaining)} "
            f"ETA={eta.strftime('%H:%M:%S')} "
            f"current={Path(trip_path).name}"
        )
        print(msg, end="\r", flush=True)
        try:
            trip_df = read_csv_flexible(trip_path)
        except Exception as e:
            print(f"\n[ERROR] {Path(trip_path).name} 読み込み失敗: {e}")
            continue

        for cross in crossroads.values():
            try:
                context = TripContext("", "", "", "", "", "", Path(trip_path).name)
                rows = process_trip(trip_df, cross, context, crossroad_base)
                output_rows.extend(rows)
            except Exception as e:
                print(
                    f"\n[ERROR] {Path(trip_path).name} 処理中にエラー: {e}"
                )
                continue

    save_output(output_rows, output_csv)
    set_end = datetime.now()
    set_elapsed = set_end - set_start

    print()
    print(
        f"  [DONE] rows={len(output_rows)} "
        f"start={set_start.strftime('%H:%M:%S')} "
        f"end={set_end.strftime('%H:%M:%S')} "
        f"elapsed={fmt_td_short(set_elapsed)} "
        f"-> {output_csv}"
    )


def save_output(rows: List[List[Optional[str]]], output_path: str) -> None:
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    df_out = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    df_out.to_csv(output_path, index=False, encoding="utf-8")


# ---------------------- エントリポイント ----------------------
def main() -> None:
    from pathlib import Path

    global_start = datetime.now()

    num_sets = len(CONFIG)
    total_trip_files = 0
    for conf in CONFIG:
        folder = conf.get("trip_folder")
        if folder:
            total_trip_files += len(list(Path(folder).glob("*.csv")))

    print("=" * 60)
    print(f"[INFO] 全体開始時刻 : {global_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[INFO] CONFIG セット数 : {num_sets}")
    print(f"[INFO] 全トリップCSV数(合計) : {total_trip_files}")
    print("=" * 60)

    Path(OUTPUT_BASE_DIR).mkdir(parents=True, exist_ok=True)
    for conf in CONFIG:
        try:
            process_config(conf)
        except Exception as e:
            print(f"[ERROR] CONFIG処理失敗: {e}")
            continue

    global_end = datetime.now()
    total_elapsed = global_end - global_start

    def fmt_td(td: timedelta) -> str:
        total_sec = int(td.total_seconds())
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    print("=" * 60)
    print(f"[INFO] 全体終了時刻 : {global_end.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[INFO] 全体所要時間 : {fmt_td(total_elapsed)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
