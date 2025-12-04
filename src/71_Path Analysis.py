"""Path analysis script for direction-based mesh counting.
"""
from __future__ import annotations

from datetime import datetime
from math import cos, hypot, radians, sin
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple

import csv
import numpy as np
import folium
from folium.plugins import HeatMap

# =========================
# User-editable constants
# =========================
# Input
INPUT_DIR = Path(r"X:\path\to\youshiki1_2_folder")  # 第2スクリーニング後様式1-2 CSV群フォルダ
POINT_FILE = Path(r"X:\path\to\11_crossroad_sampler_output.csv")  # 単路ポイント指定ファイル

# Output
OUTPUT_DIR = Path(r"X:\path\to\output_folder")

# メッシュ・距離などのパラメータ
MESH_HALF_SIZE_M = 250.0   # ±250m → 500m四方
CELL_SIZE_M = 20.0         # 20mメッシュ
SAMPLE_STEP_M = 10.0       # 線分サンプリング間隔
CROSS_THRESHOLD_M = 50.0   # 単路ポイント通過判定の距離閾値

# 方向A（上り）基準ベクトルの定義方法
# パターン1：方位角（度）で指定（例：北方向=0, 東=90）
DIRECTION_A_AZIMUTH_DEG = 0.0

# =========================


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


def generate_heatmap_points(matrix: np.ndarray, lon0: float, lat0: float) -> list[list[float]]:
    """25×25マトリクスから folium.HeatMap 用の [lat, lon, weight] リストを生成する。"""
    points: list[list[float]] = []
    for iy in range(25):
        for ix in range(25):
            val = float(matrix[iy, ix])
            if val <= 0.0:
                continue
            # セル中心のローカルXY
            x = (ix + 0.5) * CELL_SIZE_M - MESH_HALF_SIZE_M
            y = (iy + 0.5) * CELL_SIZE_M - MESH_HALF_SIZE_M
            lon, lat = xy_to_lonlat(x, y, lon0, lat0)
            points.append([float(lat), float(lon), val])
    return points


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


def classify_direction(points_xy: np.ndarray, cross_info: Dict[str, float], v_dir: np.ndarray) -> str:
    """Classify trip direction against baseline vector."""
    idx = int(cross_info["index"])
    num_points = len(points_xy)
    if 0 < idx < num_points - 2:
        p_prev = points_xy[idx - 1]
        p_next = points_xy[idx + 2]
        v_trip = p_next - p_prev
    else:
        p1 = points_xy[idx]
        p2 = points_xy[idx + 1]
        v_trip = p2 - p1

    dot = float(v_trip[0] * v_dir[0] + v_trip[1] * v_dir[1])
    return "A" if dot >= 0 else "B"


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
            if not (-MESH_HALF_SIZE_M <= x <= MESH_HALF_SIZE_M and -MESH_HALF_SIZE_M <= y <= MESH_HALF_SIZE_M):
                continue
            ix = int((x + MESH_HALF_SIZE_M) // CELL_SIZE_M)
            iy = int((y + MESH_HALF_SIZE_M) // CELL_SIZE_M)
            if 0 <= ix < 25 and 0 <= iy < 25:
                visited.add((ix, iy))


def accumulate_mesh(points_xy: np.ndarray, cross_info: Dict[str, float], direction: str, count_arrays: Dict[str, np.ndarray]):
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

    if direction == "A":
        target_in = count_arrays["A_in"]
        target_out = count_arrays["A_out"]
    else:
        target_in = count_arrays["B_in"]
        target_out = count_arrays["B_out"]

    for ix, iy in visited_in:
        target_in[iy, ix] += 1
    for ix, iy in visited_out:
        target_out[iy, ix] += 1


def _read_point_file(point_file: Path) -> Tuple[float, float]:
    with point_file.open("r", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # first row
            row = next(reader)
        except StopIteration as exc:  # pragma: no cover - defensive
            raise ValueError("POINT_FILE must have at least two rows") from exc
    lon0 = float(row[1])
    lat0 = float(row[2])
    return lon0, lat0


def _compute_matrix(count_array: np.ndarray) -> np.ndarray:
    center = count_array[12, 12]
    if center == 0:
        return np.zeros((25, 25), dtype=float)
    return count_array.astype(float) / float(center) * 100.0


def main():
    lon0, lat0 = _read_point_file(POINT_FILE)

    direction_rad = radians(DIRECTION_A_AZIMUTH_DEG)
    v_dir = np.array([sin(direction_rad), cos(direction_rad)])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    count_arrays = {
        "A_in": np.zeros((25, 25), dtype=np.int64),
        "A_out": np.zeros((25, 25), dtype=np.int64),
        "B_in": np.zeros((25, 25), dtype=np.int64),
        "B_out": np.zeros((25, 25), dtype=np.int64),
    }

    files = sorted(INPUT_DIR.rglob("*.csv"))
    total = len(files)
    empty_files = 0
    start_time = datetime.now()

    for idx, csv_path in enumerate(files, start=1):
        points_xy = load_single_trip(csv_path, lon0, lat0)
        if len(points_xy) < 2:
            empty_files += 1
            progress = idx / total * 100 if total else 100.0
            msg = f"[71_PathAnalysis] {progress:5.1f}% ({idx}/{total}) empty={empty_files} started={start_time.strftime('%H:%M:%S')}"
            print("\r" + msg, end="", flush=True)
            continue

        found, cross_info = find_crossing_point(points_xy)
        if not found or cross_info is None:
            empty_files += 1
            progress = idx / total * 100 if total else 100.0
            msg = f"[71_PathAnalysis] {progress:5.1f}% ({idx}/{total}) empty={empty_files} started={start_time.strftime('%H:%M:%S')}"
            print("\r" + msg, end="", flush=True)
            continue

        direction = classify_direction(points_xy, cross_info, v_dir)
        accumulate_mesh(points_xy, cross_info, direction, count_arrays)

        progress = idx / total * 100 if total else 100.0
        msg = f"[71_PathAnalysis] {progress:5.1f}% ({idx}/{total}) empty={empty_files} started={start_time.strftime('%H:%M:%S')}"
        print("\r" + msg, end="", flush=True)

    end_time = datetime.now()
    elapsed = end_time - start_time
    print()
    print(f"Finished at {end_time.strftime('%H:%M:%S')} (elapsed {elapsed})")
    print(f"Total files={total}  Valid={total - empty_files}  Empty={empty_files}")

    matrices = {
        "A_in": _compute_matrix(count_arrays["A_in"]),
        "A_out": _compute_matrix(count_arrays["A_out"]),
        "B_in": _compute_matrix(count_arrays["B_in"]),
        "B_out": _compute_matrix(count_arrays["B_out"]),
    }

    np.savetxt(OUTPUT_DIR / "71_path_matrix_A_in.csv", matrices["A_in"], delimiter=",", fmt="%.6f")
    np.savetxt(OUTPUT_DIR / "71_path_matrix_A_out.csv", matrices["A_out"], delimiter=",", fmt="%.6f")
    np.savetxt(OUTPUT_DIR / "71_path_matrix_B_in.csv", matrices["B_in"], delimiter=",", fmt="%.6f")
    np.savetxt(OUTPUT_DIR / "71_path_matrix_B_out.csv", matrices["B_out"], delimiter=",", fmt="%.6f")

    # ---- folium HeatMap 用データ生成 ----
    A_in_points = generate_heatmap_points(matrices["A_in"], lon0, lat0)
    A_out_points = generate_heatmap_points(matrices["A_out"], lon0, lat0)
    B_in_points = generate_heatmap_points(matrices["B_in"], lon0, lat0)
    B_out_points = generate_heatmap_points(matrices["B_out"], lon0, lat0)

    # ---- 個別ヒートマップ（A/B × in/out） ----
    def _save_folium_map(points: list[list[float]], filename: str, title: str) -> None:
        m = folium.Map(location=[lat0, lon0], zoom_start=16, tiles="OpenStreetMap")
        if points:
            HeatMap(points, radius=25, blur=30, max_zoom=18).add_to(m)
        folium.map.LayerControl().add_to(m)
        m.get_root().html.add_child(folium.Element(f"<h3>{title}</h3>"))
        m.save(str(OUTPUT_DIR / filename))

    _save_folium_map(A_in_points,  "71_heatmap_A_in.html",  "Direction A - In")
    _save_folium_map(A_out_points, "71_heatmap_A_out.html", "Direction A - Out")
    _save_folium_map(B_in_points,  "71_heatmap_B_in.html",  "Direction B - In")
    _save_folium_map(B_out_points, "71_heatmap_B_out.html", "Direction B - Out")

    # ---- A/B を左右に並べた HTML（in / out それぞれ） ----
    in_html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Direction A & B - In</title></head>
<body>
<h3>Direction A &amp; B - In</h3>
<div style="display:flex; flex-direction:row; width:100%; height:600px;">
  <iframe src="71_heatmap_A_in.html" style="flex:1; border:none;"></iframe>
  <iframe src="71_heatmap_B_in.html" style="flex:1; border:none;"></iframe>
</div>
</body>
</html>
"""
    (OUTPUT_DIR / "71_heatmap_in_AB.html").write_text(in_html, encoding="utf-8")

    out_html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Direction A & B - Out</title></head>
<body>
<h3>Direction A &amp; B - Out</h3>
<div style="display:flex; flex-direction:row; width:100%; height:600px;">
  <iframe src="71_heatmap_A_out.html" style="flex:1; border:none;"></iframe>
  <iframe src="71_heatmap_B_out.html" style="flex:1; border:none;"></iframe>
</div>
</body>
</html>
"""
    (OUTPUT_DIR / "71_heatmap_out_AB.html").write_text(out_html, encoding="utf-8")


if __name__ == "__main__":
    main()
