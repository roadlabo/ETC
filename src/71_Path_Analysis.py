"""Path analysis script for inflow-side (A/B) mesh counting and in/out heatmaps toward a center point."""
from __future__ import annotations

import math
from math import cos, hypot, radians, sin
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple

from datetime import datetime
import time

import csv
import numpy as np
import folium

# =========================
# User-editable constants
# =========================
# Input
INPUT_DIR = Path(r"X:\path\to\youshiki1_2_folder")  # 第2スクリーニング後様式1-2 CSV群フォルダ
POINT_FILE = Path(r"X:\path\to\11_crossroad_sampler_output.csv")  # 単路ポイント指定ファイル

# Output
OUTPUT_DIR = Path(r"X:\path\to\output_folder")

# 解析範囲（中心からの距離）
# 2km四方 = 半径1km
HALF_SIDE_M = 1000.0

# メッシュ・距離などのパラメータ
CELL_SIZE_M = 10.0         # 10m メッシュ
SAMPLE_STEP_M = 10.0       # 線分サンプリング間隔（10m）
CROSS_THRESHOLD_M = 50.0   # 単路ポイント通過判定の距離閾値

# A/B方向判定の最小一致度（cos類似度しきい値）
# 0.70～0.90で調整。厳しくするとU（除外）が増える。
DIR_MATCH_MIN_COS = 0.80

# =========================
# Heatmap display settings (見やすさ調整)
# =========================
# ヒートマップは「値→色」「値→透明度」を別々に制御して、少ない所を薄く・多い所を赤く強調する。

# vmax を「最大値」ではなく「上位パーセンタイル」で決める（外れ値があるときの白飛び防止）
HEATMAP_VMAX_PERCENTILE = 99.0   # 例: 99 → 上位1%を飽和として扱う（強調が出やすい）

# 濃淡の強調（小さい値をより薄く、大きい値をより目立たせる）
HEATMAP_GAMMA = 0.55             # 小さめ(0.4～0.8)にすると“赤いところ”が強調されやすい

# 透明度（薄い所をより薄くする）
HEATMAP_MIN_OPACITY = 0.03       # 0に近いほど薄く（0.02～0.08推奨）
HEATMAP_MAX_OPACITY = 0.85       # 最大の濃さ（0.7～0.95推奨）

# 色（低→中→高）※青系はやめて「薄黄→オレンジ→赤」の王道ヒートマップにする
HEATMAP_COLOR_STOPS = [
    (0.00, (255, 255, 204)),  # very low: light yellow
    (0.50, (253, 141,  60)),  # mid: orange
    (1.00, (189,   0,  38)),  # high: red
]

# =========================
# Arrow UI
# =========================
ARROW_HEAD_ROTATE_OFFSET_DEG = -90  # まずは -90 をデフォルト。合わなければ 0/90 を調整。
ARROW_LINE_LENGTH_M = 90.0
ARROW_LINE_WEIGHT = 6
ARROW_HEAD_RADIUS_PX = 18

# ラベルは矢印の中央
ARROW_LABEL_ALONG_RATIO = 0.50
ARROW_LABEL_SIZE_PX = 44
ARROW_LABEL_BORDER_PX = 3
ARROW_LABEL_FONT_REM = 2.6

# 中心点の強調
CENTER_MARKER_RADIUS = 8
CENTER_MARKER_COLOR = "black"
CENTER_MARKER_BORDER_COLOR = "white"
CENTER_MARKER_BORDER_WEIGHT = 3

# グリッドサイズ（セル数）
GRID_SIZE = int((2 * HALF_SIDE_M) / CELL_SIZE_M)

# =========================


def write_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Column indices for Youshiki1-2
COL_LON = 14
COL_LAT = 15


def lonlat_to_xy(lon: np.ndarray, lat: np.ndarray, lon0: float, lat0: float) -> Tuple[np.ndarray, np.ndarray]:
    """Convert lon/lat to local XY in meters using a simple equirectangular approximation."""
    r_earth = 6_371_000.0
    lat0_rad = radians(lat0)
    dlon = np.radians(lon - lon0)
    dlat = np.radians(lat - lat0)
    x = r_earth * dlon * cos(lat0_rad)
    y = r_earth * dlat
    return x, y


def xy_to_lonlat(x: float | np.ndarray, y: float | np.ndarray, lon0: float, lat0: float) -> Tuple[np.ndarray, np.ndarray]:
    """ローカルXY(m) → 緯度経度の簡易逆変換。lon, lat を返す。"""
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * cos(radians(lat0))
    lat = lat0 + (y / m_per_deg_lat)
    lon = lon0 + (x / m_per_deg_lon)
    return lon, lat


def _interp_color_stops(t: float) -> tuple[int, int, int]:
    """0..1 を HEATMAP_COLOR_STOPS で RGB 補間して返す。"""
    t = max(0.0, min(1.0, t))
    stops = HEATMAP_COLOR_STOPS
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t0 <= t <= t1:
            if t1 == t0:
                return c1
            u = (t - t0) / (t1 - t0)
            r = int(c0[0] + (c1[0] - c0[0]) * u)
            g = int(c0[1] + (c1[1] - c0[1]) * u)
            b = int(c0[2] + (c1[2] - c0[2]) * u)
            return r, g, b
    return stops[-1][1]


def _compute_vmax(matrix: np.ndarray) -> int:
    """上位パーセンタイルで vmax を決める（外れ値対策）。"""
    vals = matrix[matrix > 0]
    if vals.size == 0:
        return 0
    vmax = int(np.percentile(vals, HEATMAP_VMAX_PERCENTILE))
    return max(1, vmax)


def value_to_style(value: int, vmax: int) -> tuple[str, float] | None:
    """
    value(>0) を (fill_color, fill_opacity) に変換。
    - 少ない所は薄く（低opacity）
    - 多い所は赤く（色もopacityも増える）
    """
    if vmax <= 0 or value <= 0:
        return None

    x = min(1.0, float(value) / float(vmax))
    # gammaで強調（小さい値はより薄く、上位はより目立つ）
    t = x ** HEATMAP_GAMMA

    r, g, b = _interp_color_stops(t)
    color = f"#{r:02x}{g:02x}{b:02x}"

    opacity = HEATMAP_MIN_OPACITY + t * (HEATMAP_MAX_OPACITY - HEATMAP_MIN_OPACITY)
    return color, float(opacity)


def add_direction_arrow(
    m: folium.Map,
    lon0: float,
    lat0: float,
    azimuth_deg_to_center: float,
    color: str,
    label: str,
) -> None:
    rad = math.radians(azimuth_deg_to_center)
    ux = math.sin(rad)
    uy = math.cos(rad)

    # 外側点（始点）
    sx = -ARROW_LINE_LENGTH_M * ux
    sy = -ARROW_LINE_LENGTH_M * uy
    lon_start, lat_start = xy_to_lonlat(sx, sy, lon0, lat0)

    # 線（外側→中心）
    folium.PolyLine(
        locations=[[lat_start, lon_start], [lat0, lon0]],
        color=color,
        weight=ARROW_LINE_WEIGHT,
    ).add_to(m)

    # 矢じり（三角）＝中心
    folium.RegularPolygonMarker(
        location=[lat0, lon0],
        number_of_sides=3,
        radius=ARROW_HEAD_RADIUS_PX,
        rotation=azimuth_deg_to_center + ARROW_HEAD_ROTATE_OFFSET_DEG,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=1.0,
        weight=1,
    ).add_to(m)

    # ラベル＝矢印の中央
    mx = -ARROW_LINE_LENGTH_M * ARROW_LABEL_ALONG_RATIO * ux
    my = -ARROW_LINE_LENGTH_M * ARROW_LABEL_ALONG_RATIO * uy
    lon_mid, lat_mid = xy_to_lonlat(mx, my, lon0, lat0)

    label_html = (
        "<div style='"
        "display:flex;align-items:center;justify-content:center;"
        f"width:{ARROW_LABEL_SIZE_PX}px;height:{ARROW_LABEL_SIZE_PX}px;"
        "border-radius:50%;"
        f"border:{ARROW_LABEL_BORDER_PX}px solid {color};"
        "background-color:white;"
        "font-weight:bold;"
        f"color:{color};"
        f"font-size:{ARROW_LABEL_FONT_REM}rem;"
        "text-shadow:0 0 4px white;"
        "'>"
        f"{label}</div>"
    )
    folium.Marker(
        location=[lat_mid, lon_mid],
        icon=folium.DivIcon(html=label_html),
    ).add_to(m)


def create_mesh_map(matrix: np.ndarray, lon0: float, lat0: float,
                    filename: str, title: str,
                    dirA_deg: float, dirB_deg: float,
                    show_A: bool = True, show_B: bool = True) -> None:
    """
    GRID_SIZE×GRID_SIZE のマトリクスを 10m メッシュの矩形として描画し、
    中心黒丸と A/B 方向矢印を最前面に重ねる。
    """
    m = folium.Map(location=[lat0, lon0], zoom_start=16, tiles="OpenStreetMap")
    vmax = _compute_vmax(matrix)

    # メッシュ矩形
    for iy in range(GRID_SIZE):
        for ix in range(GRID_SIZE):
            val = int(matrix[iy, ix])
            if val <= 0:
                continue

            x_min = ix * CELL_SIZE_M - HALF_SIDE_M
            x_max = (ix + 1) * CELL_SIZE_M - HALF_SIDE_M
            y_min = iy * CELL_SIZE_M - HALF_SIDE_M
            y_max = (iy + 1) * CELL_SIZE_M - HALF_SIDE_M

            lon_min, lat_min = xy_to_lonlat(x_min, y_min, lon0, lat0)
            lon_max, lat_max = xy_to_lonlat(x_max, y_max, lon0, lat0)

            style = value_to_style(val, vmax)
            if style is None:
                continue
            color, opacity = style

            folium.Rectangle(
                bounds=[[lat_min, lon_min], [lat_max, lon_max]],
                fill=True,
                fill_color=color,
                fill_opacity=opacity,
                weight=0,
            ).add_to(m)

    # A/B 方向矢印（必要なものだけ表示）
    if show_A:
        add_direction_arrow(m, lon0, lat0, dirA_deg, "red", "A")
    if show_B:
        add_direction_arrow(m, lon0, lat0, dirB_deg, "blue", "B")

    # 中心点の強調（矢印の最後に重ねる）
    folium.CircleMarker(
        location=[lat0, lon0],
        radius=CENTER_MARKER_RADIUS,
        color=CENTER_MARKER_BORDER_COLOR,
        weight=CENTER_MARKER_BORDER_WEIGHT,
        fill=True,
        fill_color=CENTER_MARKER_COLOR,
        fill_opacity=1.0,
    ).add_to(m)

    folium.map.LayerControl().add_to(m)
    m.get_root().html.add_child(folium.Element(f"<h3>{title}</h3>"))
    m.save(str(OUTPUT_DIR / filename))


def load_single_trip(csv_path: Path, lon0: float, lat0: float) -> np.ndarray:
    """Load a single trip CSV and return points in XY coordinates."""
    try:
        data = np.loadtxt(csv_path, delimiter=",", usecols=(COL_LON, COL_LAT))
    except ValueError:
        return np.empty((0, 2))

    if data.ndim == 1:
        data = data.reshape(1, -1)

    if data.size == 0:
        return np.empty((0, 2))

    lon = data[:, 0]
    lat = data[:, 1]
    x, y = lonlat_to_xy(lon, lat, lon0, lat0)
    return np.column_stack((x, y))


def segment_distance_to_origin(x1: float, y1: float, x2: float, y2: float) -> Tuple[float, float]:
    """Return the shortest distance from the segment to origin and parameter t of closest point."""
    vx = x2 - x1
    vy = y2 - y1
    seg_len_sq = vx * vx + vy * vy
    if seg_len_sq == 0:
        return hypot(x1, y1), 0.0
    t = -(x1 * vx + y1 * vy) / seg_len_sq
    t_clamped = max(0.0, min(1.0, t))
    closest_x = x1 + t_clamped * vx
    closest_y = y1 + t_clamped * vy
    dist = hypot(closest_x, closest_y)
    return dist, t_clamped


def find_crossing_point(points_xy: np.ndarray) -> Tuple[bool, Optional[Dict[str, float]]]:
    """Find the first segment crossing within threshold from origin."""
    num_points = len(points_xy)
    for i in range(num_points - 1):
        x1, y1 = points_xy[i]
        x2, y2 = points_xy[i + 1]
        dist, t = segment_distance_to_origin(x1, y1, x2, y2)
        if dist <= CROSS_THRESHOLD_M:
            cross_x = x1 + t * (x2 - x1)
            cross_y = y1 + t * (y2 - y1)
            return True, {"index": i, "t": t, "point": (cross_x, cross_y)}
    return False, None


def _sample_segment(p1: np.ndarray, p2: np.ndarray, step: float) -> Iterable[np.ndarray]:
    vx, vy = p2 - p1
    seg_len = hypot(vx, vy)
    if seg_len == 0:
        return []
    n_steps = max(1, int(seg_len // step) + 1)
    ts = np.linspace(0.0, 1.0, n_steps)
    return (p1 + t * (p2 - p1) for t in ts)


def _record_samples(points: np.ndarray, visited: Set[Tuple[int, int]]):
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        for sample in _sample_segment(p1, p2, SAMPLE_STEP_M):
            x, y = float(sample[0]), float(sample[1])
            if not (-HALF_SIDE_M <= x <= HALF_SIDE_M and -HALF_SIDE_M <= y <= HALF_SIDE_M):
                continue
            ix = int((x + HALF_SIDE_M) // CELL_SIZE_M)
            iy = int((y + HALF_SIDE_M) // CELL_SIZE_M)
            if 0 <= ix < GRID_SIZE and 0 <= iy < GRID_SIZE:
                visited.add((ix, iy))


def accumulate_mesh(points_xy: np.ndarray, cross_info: Dict[str, float],
                    in_direction: str, out_direction: str,
                    count_arrays: Dict[str, np.ndarray]):
    idx = int(cross_info["index"])
    cross_x, cross_y = cross_info["point"]
    cross_point = np.array([[cross_x, cross_y]], dtype=float)

    visited_in: Set[Tuple[int, int]] = set()
    visited_out: Set[Tuple[int, int]] = set()

    # --- 進入側: 0 ～ idx まで + 仮想通過点 ---
    if idx >= 0:
        in_head = points_xy[: idx + 1]
        if len(in_head) == 0:
            in_points = cross_point.copy()
        else:
            in_points = np.vstack([in_head, cross_point])
    else:
        in_points = np.empty((0, 2), dtype=float)

    # --- 退出側: 仮想通過点 + idx+1 以降 ---
    out_tail = points_xy[idx + 1 :]
    if len(out_tail) == 0:
        out_points = cross_point.copy()
    else:
        out_points = np.vstack([cross_point, out_tail])

    if len(in_points) >= 2:
        _record_samples(in_points, visited_in)
    if len(out_points) >= 2:
        _record_samples(out_points, visited_out)

    if in_direction == "A":
        target_in = count_arrays["A_in"]
    else:
        target_in = count_arrays["B_in"]

    if out_direction == "A":
        target_out = count_arrays["A_out"]
    else:
        target_out = count_arrays["B_out"]

    for ix, iy in visited_in:
        target_in[iy, ix] += 1
    for ix, iy in visited_out:
        target_out[iy, ix] += 1


def _read_point_file(path: Path) -> tuple[float, float, float, float]:
    """
    11_crossroad_sampler.py が出力した CSV から
    中心の経度・緯度と、枝1(=A方向)・枝2(=B方向)の方位角を取得する。

    CSV 形式:
    crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg
    2行目 branch_no=1 → A方向
    3行目 branch_no=2 → B方向
    """
    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)  # 1行目
        row_a = next(reader)   # 2行目（branch 1）
        row_b = next(reader)   # 3行目（branch 2）

    lon0 = float(row_a[1])
    lat0 = float(row_a[2])
    dirA_deg = float(row_a[5])
    dirB_deg = float(row_b[5])

    return lon0, lat0, dirA_deg, dirB_deg


def _compute_matrix(count_array: np.ndarray, denom: int) -> np.ndarray:
    """
    方向別ヒット数 denom で正規化した整数％マトリクスを返す。
    denom==0 の場合は、count_array の合計を分母に使う。
    count_array に値が全く無ければゼロ行列。
    """
    if denom <= 0:
        denom = int(count_array.sum())

    if denom <= 0:
        return np.zeros_like(count_array, dtype=int)

    ratio = count_array.astype(float) / float(denom)
    return np.rint(ratio * 100.0).astype(int)


def classify_direction(points_xy: np.ndarray, cross_info: Dict[str, float],
                       v_dir_A: np.ndarray, v_dir_B: np.ndarray) -> str:
    """
    A/B は「中心ポイントにどちら側から来たか（流入側）」で判定する。
    判定ベクトルは「交差点直前点 → 仮想通過点（cross_point）」(outside→center)。
    v_dir_A / v_dir_B も outside→center の基準ベクトル。
    """
    idx = int(cross_info["index"])
    cross_x, cross_y = cross_info["point"]
    cross_point = np.array([cross_x, cross_y], dtype=float)

    n = len(points_xy)
    if n < 2:
        return "U"

    # 交差点直前点（基本は idx）
    i0 = max(0, min(idx, n - 1))
    p0 = points_xy[i0]

    v = cross_point - p0
    norm = float(np.hypot(v[0], v[1]))

    # もし直前点がほぼ通過点と同じなら、さらに一つ前へ
    if norm == 0.0 and i0 - 1 >= 0:
        p0 = points_xy[i0 - 1]
        v = cross_point - p0
        norm = float(np.hypot(v[0], v[1]))

    if norm == 0.0:
        return "U"

    v_in = v / norm  # outside→center

    cosA = float(np.dot(v_in, v_dir_A))
    cosB = float(np.dot(v_in, v_dir_B))

    if max(cosA, cosB) < DIR_MATCH_MIN_COS:
        return "U"
    return "A" if cosA >= cosB else "B"


def classify_out_direction(points_xy: np.ndarray, cross_info: Dict[str, float],
                           v_dir_A: np.ndarray, v_dir_B: np.ndarray) -> str:
    """
    流出方向は「交差点直後点 → 仮想通過点（outside→center に反転）」で判定する。
    """
    idx = int(cross_info["index"])
    cross_x, cross_y = cross_info["point"]
    cross_point = np.array([cross_x, cross_y], dtype=float)

    n = len(points_xy)
    if n < 2:
        return "A"

    i1 = min(idx + 1, n - 1)
    p1 = points_xy[i1]
    v = cross_point - p1  # outside→center（直後点から見たベクトル）
    norm = float(np.hypot(v[0], v[1]))

    if norm == 0.0 and i1 + 1 < n:
        p1 = points_xy[i1 + 1]
        v = cross_point - p1
        norm = float(np.hypot(v[0], v[1]))

    if norm == 0.0:
        return "A"

    v_out = v / norm  # outside→center に揃える
    cosA = float(np.dot(v_out, v_dir_A))
    cosB = float(np.dot(v_out, v_dir_B))
    return "A" if cosA >= cosB else "B"


def main():
    started_dt = datetime.now()
    t0 = time.time()

    lon0, lat0, dirA_deg, dirB_deg = _read_point_file(POINT_FILE)
    stem = POINT_FILE.stem

    # A方向 / B方向の基準ベクトル（outside→center の方位角。北=0度, 東=90度）
    # ※交差点ファイルの dir_deg は「中心が終点（外側→中心）」の向きとして扱う
    dirA_rad = radians(dirA_deg)
    dirB_rad = radians(dirB_deg)
    v_dir_A = np.array([sin(dirA_rad), cos(dirA_rad)])
    v_dir_B = np.array([sin(dirB_rad), cos(dirB_rad)])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 解析対象ファイルを列挙
    target_files = sorted(INPUT_DIR.glob("*.csv"))
    total_files = len(target_files)

    count_arrays = {
        "A_in": np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.int64),
        "A_out": np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.int64),
        "B_in": np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.int64),
        "B_out": np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.int64),
    }

    # 方向別HITトリップ数カウンタ
    total_A_in_hits = 0
    total_B_in_hits = 0
    total_A_out_hits = 0
    total_B_out_hits = 0
    total_A_hits = 0
    total_B_hits = 0
    total_unknown = 0

    total_trips_checked = 0
    total_trips_crossed = 0
    total_trips_excluded = 0

    # in/out の遷移チェック用
    inA_to_outA = 0
    inA_to_outB = 0
    inB_to_outA = 0
    inB_to_outB = 0

    empty_files = 0

    for idx, csv_path in enumerate(target_files, start=1):
        total_trips_checked += 1
        points_xy = load_single_trip(csv_path, lon0, lat0)
        if len(points_xy) < 2:
            empty_files += 1
            total_trips_excluded += 1
            progress = idx / total_files * 100 if total_files else 100.0
            msg = (
                f"[71_PathAnalysis] {progress:5.1f}% ({idx}/{total_files}) "
                f"empty={empty_files} started={started_dt.strftime('%H:%M:%S')}"
            )
            print("\r" + msg, end="", flush=True)
            continue

        found, cross_info = find_crossing_point(points_xy)
        if not found or cross_info is None:
            empty_files += 1
            total_trips_excluded += 1
            progress = idx / total_files * 100 if total_files else 100.0
            msg = (
                f"[71_PathAnalysis] {progress:5.1f}% ({idx}/{total_files}) "
                f"empty={empty_files} started={started_dt.strftime('%H:%M:%S')}"
            )
            print("\r" + msg, end="", flush=True)
            continue

        total_trips_crossed += 1
        in_direction = classify_direction(points_xy, cross_info, v_dir_A, v_dir_B)
        if in_direction == "U":
            total_unknown += 1
            total_trips_excluded += 1
            progress = idx / total_files * 100 if total_files else 100.0
            msg = (
                f"[71_PathAnalysis] {progress:5.1f}% ({idx}/{total_files}) "
                f"empty={empty_files} started={started_dt.strftime('%H:%M:%S')}"
            )
            print("\r" + msg, end="", flush=True)
            continue

        out_direction = classify_out_direction(points_xy, cross_info, v_dir_A, v_dir_B)

        # 方向別 HIT トリップ数カウンタ
        if in_direction == "A":
            total_A_in_hits += 1
            total_A_hits += 1
        else:
            total_B_in_hits += 1
            total_B_hits += 1

        if out_direction == "A":
            total_A_out_hits += 1
        else:
            total_B_out_hits += 1

        if in_direction == "A" and out_direction == "A":
            inA_to_outA += 1
        elif in_direction == "A" and out_direction == "B":
            inA_to_outB += 1
        elif in_direction == "B" and out_direction == "A":
            inB_to_outA += 1
        else:
            inB_to_outB += 1

        accumulate_mesh(points_xy, cross_info, in_direction, out_direction, count_arrays)

        progress = idx / total_files * 100 if total_files else 100.0
        msg = (
            f"[71_PathAnalysis] {progress:5.1f}% ({idx}/{total_files}) "
            f"empty={empty_files} started={started_dt.strftime('%H:%M:%S')}"
        )
        print("\r" + msg, end="", flush=True)

    ended_dt = datetime.now()
    elapsed_sec = time.time() - t0
    elapsed = ended_dt - started_dt
    print()
    print(f"Finished at {ended_dt.strftime('%H:%M:%S')} (elapsed {elapsed})")
    print(f"Total files={total_files}  Valid={total_files - empty_files}  Empty={empty_files}")

    matrices = {
        "A_in": _compute_matrix(count_arrays["A_in"], total_A_in_hits),
        "A_out": _compute_matrix(count_arrays["A_out"], total_A_out_hits),
        "B_in": _compute_matrix(count_arrays["B_in"], total_B_in_hits),
        "B_out": _compute_matrix(count_arrays["B_out"], total_B_out_hits),
    }

    for key, arr in matrices.items():
        nz = int((arr > 0).sum())
        vmax = int(arr.max())
        print(f"[71_PathAnalysis] {key}: nonzero_cells={nz}, max={vmax}%")

    print(f"[71_PathAnalysis] total_A_in_hits={total_A_in_hits} total_B_in_hits={total_B_in_hits}")
    print(f"[71_PathAnalysis] total_A_out_hits={total_A_out_hits} total_B_out_hits={total_B_out_hits}")
    print(
        "[71_PathAnalysis] transitions: "
        f"inA->outA={inA_to_outA}, inA->outB={inA_to_outB}, "
        f"inB->outA={inB_to_outA}, inB->outB={inB_to_outB}"
    )

    def _save_matrix_csv(name: str, matrix: np.ndarray):
        # 北が上になるように上下反転（iy大きい=北 → 1行目）
        flipped = np.flipud(matrix)
        np.savetxt(OUTPUT_DIR / name, flipped, delimiter=",", fmt="%d")

    _save_matrix_csv("71_path_matrix_A_in.csv",  matrices["A_in"])
    _save_matrix_csv("71_path_matrix_A_out.csv", matrices["A_out"])
    _save_matrix_csv("71_path_matrix_B_in.csv",  matrices["B_in"])
    _save_matrix_csv("71_path_matrix_B_out.csv", matrices["B_out"])

    # ---- 10mメッシュ塗りのマップを出力（A/B × in/out） ----
    a_in_html = f"{stem}_heatmap_A（流入）.html"
    a_out_html = f"{stem}_heatmap_A（流出）.html"
    b_in_html = f"{stem}_heatmap_B（流入）.html"
    b_out_html = f"{stem}_heatmap_B（流出）.html"

    create_mesh_map(matrices["A_in"],  lon0, lat0, a_in_html,  "A方向交通（流入経路）", dirA_deg, dirB_deg, show_A=True,  show_B=False)
    create_mesh_map(matrices["A_out"], lon0, lat0, a_out_html, "A方向交通（流出経路）", dirA_deg, dirB_deg, show_A=True,  show_B=False)
    create_mesh_map(matrices["B_in"],  lon0, lat0, b_in_html,  "B方向交通（流入経路）", dirA_deg, dirB_deg, show_A=False, show_B=True)
    create_mesh_map(matrices["B_out"], lon0, lat0, b_out_html, "B方向交通（流出経路）", dirA_deg, dirB_deg, show_A=False, show_B=True)

    # ---- A/B の in/out を左右に並べた HTML（方向別） ----
    a_pair = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{stem} / A方向交通</title></head>
<body>
<h3>{stem} / A方向交通（流入経路・流出経路）</h3>
<div style="display:flex; flex-direction:row; width:100%; height:600px;">
  <iframe src="{a_in_html}" style="flex:1; border:none;"></iframe>
  <iframe src="{a_out_html}" style="flex:1; border:none;"></iframe>
</div>
</body>
</html>
"""
    (OUTPUT_DIR / f"{stem}_heatmap_A方向交通.html").write_text(a_pair, encoding="utf-8")

    b_pair = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{stem} / B方向交通</title></head>
<body>
<h3>{stem} / B方向交通（流入経路・流出経路）</h3>
<div style="display:flex; flex-direction:row; width:100%; height:600px;">
  <iframe src="{b_in_html}" style="flex:1; border:none;"></iframe>
  <iframe src="{b_out_html}" style="flex:1; border:none;"></iframe>
</div>
</body>
</html>
"""
    (OUTPUT_DIR / f"{stem}_heatmap_B方向交通.html").write_text(b_pair, encoding="utf-8")

    print("[71_PathAnalysis] 判定定義: A/B=中心へどちら側から来たか（流入側）, dir_deg=outside→center")
    print("[71_PathAnalysis] 表記: in=流入経路, out=流出経路")
    print(f"[71_PathAnalysis] 出力: {stem}_heatmap_A方向交通.html / {stem}_heatmap_B方向交通.html")

    log_lines: list[str] = []
    log_lines.append("－－－解析LOG－－－")
    log_lines.append(f"開始時刻: {started_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    log_lines.append(f"終了時刻: {ended_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    log_lines.append(f"所要時間: {elapsed_sec:.1f} 秒")
    log_lines.append("")
    log_lines.append("－－－入力概要－－－")
    log_lines.append(f"入力フォルダ: {INPUT_DIR}")
    log_lines.append(f"対象CSV数: {total_files}")
    log_lines.append(f"交差点ファイル: {POINT_FILE}")
    log_lines.append(f"解析範囲: 2km四方（±{HALF_SIDE_M:.0f}m）")
    log_lines.append("")
    log_lines.append("－－－トリップ集計－－－")
    log_lines.append(f"チェックしたトリップ数: {total_trips_checked}")
    log_lines.append(f"交差点通過トリップ数: {total_trips_crossed}")
    log_lines.append(f"A方向交通（流入側A）: {total_A_hits}")
    log_lines.append(f"B方向交通（流入側B）: {total_B_hits}")
    log_lines.append(f"方向不明で除外（U）: {total_unknown}")
    log_lines.append(f"総除外数: {total_trips_excluded}")
    log_lines.append("")
    log_lines.append("－－－備考（定義）－－－")
    log_lines.append("A/B判定は『中心にどちら側から到達したか（流入側）』で行う。")
    log_lines.append("矢印は外側→中心の向きで描画する。in=流入経路、out=流出経路。")

    write_log(OUTPUT_DIR / "LOG.txt", log_lines)
    print("\n".join(log_lines[-12:]))  # 末尾の要約だけ標準出力に出す（冗長防止）


if __name__ == "__main__":
    main()
