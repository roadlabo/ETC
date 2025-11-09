# -*- coding: utf-8 -*-
"""
od_heatmap_viewer.py
trip_extractor.py の出力CSV群から、起点(Origin)と終点(Destination)を抽出し、
別々のヒートマップにして1画面で横並び表示するツール。

Usage:
  python od_heatmap_viewer.py [INPUT_DIR] [--radius 16] [--blur 18] [--min-opacity 0.1]
                              [--max-zoom 12] [--recursive] [--pattern "*.csv"]

Output:
  origin_map.html
  destination_map.html
  index_od_heatmap.html  ← 2つの地図を1画面に埋め込み（見出し表示）
  od_summary.txt
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import webbrowser
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from folium import Map
from folium.plugins import HeatMap
from tqdm import tqdm

LAT_CANDIDATES: Sequence[str] = ("lat", "latitude", "Lat", "Latitude", "LAT", "緯度")
LON_CANDIDATES: Sequence[str] = ("lon", "lng", "longitude", "Long", "LON", "経度")


def _find_lat_lon_columns(df: pd.DataFrame) -> Optional[Tuple[str, str]]:
    """列名から緯度・経度列を推定する。"""
    lowered = {c.lower(): c for c in df.columns}
    lat_col = next((lowered[c.lower()] for c in LAT_CANDIDATES if c.lower() in lowered), None)
    lon_col = next((lowered[c.lower()] for c in LON_CANDIDATES if c.lower() in lowered), None)
    if lat_col and lon_col:
        return lat_col, lon_col
    return None


def _numeric_like_columns(df: pd.DataFrame) -> List[str]:
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if len(numeric_cols) >= 2:
        return numeric_cols[:2]

    def is_numeric_like(series: pd.Series) -> bool:
        try:
            pd.to_numeric(series.dropna().head(100), errors="raise")
            return True
        except Exception:
            return False

    text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    numeric_like = [c for c in text_cols if is_numeric_like(df[c])]
    candidates = numeric_cols + numeric_like
    return candidates[:2]


def _to_float(value: object) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except Exception:
        try:
            return float(str(value).replace(",", ""))
        except Exception:
            return float("nan")


def _normalize_latlon(lat: object, lon: object) -> Optional[Tuple[float, float]]:
    lat_val = _to_float(lat)
    lon_val = _to_float(lon)
    if np.isnan(lat_val) or np.isnan(lon_val):
        return None

    def in_global_range(value: float, lower: float, upper: float) -> bool:
        return lower <= value <= upper

    if not in_global_range(lat_val, -90, 90) or not in_global_range(lon_val, -180, 180):
        if in_global_range(lon_val, -90, 90) and in_global_range(lat_val, -180, 180):
            lat_val, lon_val = lon_val, lat_val
            if not in_global_range(lat_val, -90, 90) or not in_global_range(lon_val, -180, 180):
                return None
        else:
            return None
    return lat_val, lon_val


def extract_origin_destination(df: pd.DataFrame) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    if df.empty:
        return None

    columns = _find_lat_lon_columns(df)
    if columns is None:
        candidates = _numeric_like_columns(df)
        if len(candidates) < 2:
            return None
        columns = (candidates[0], candidates[1])

    lat_col, lon_col = columns
    head = df.iloc[0]
    tail = df.iloc[-1]
    origin = _normalize_latlon(head[lat_col], head[lon_col])
    destination = _normalize_latlon(tail[lat_col], tail[lon_col])
    if origin is None or destination is None:
        return None
    return origin, destination


def _mean_center(points: Iterable[Tuple[float, float]]) -> Tuple[float, float]:
    latitudes = [p[0] for p in points]
    longitudes = [p[1] for p in points]
    return float(np.mean(latitudes)), float(np.mean(longitudes))


def create_heatmap_html(
    points: Sequence[Sequence[float]],
    center: Tuple[float, float],
    out_html: str,
    *,
    radius: int,
    blur: int,
    min_opacity: float,
    max_zoom: int,
) -> None:
    if points:
        fmap = Map(location=center, zoom_start=9, control_scale=True, prefer_canvas=True)
        HeatMap(
            points,
            radius=radius,
            blur=blur,
            min_opacity=min_opacity,
            max_zoom=max_zoom,
        ).add_to(fmap)
    else:
        fmap = Map(location=center, zoom_start=9, control_scale=True, prefer_canvas=True)
        folium_popup = "No points"
        from folium import Marker

        Marker(center, tooltip=folium_popup).add_to(fmap)
    fmap.save(out_html)


def build_index_html(index_path: str, origin_path: str, dest_path: str) -> None:
    html = f"""<!DOCTYPE html>
<html lang=\"ja\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>OD Heatmaps</title>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, \"Noto Sans JP\", \"Hiragino Sans\", \"Yu Gothic\", \"Helvetica Neue\", Arial, sans-serif; }}
  header {{ padding: 12px 16px; background: #111; color: #fff; font-weight: 600; }}
  .container {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
    height: calc(100vh - 52px);
  }}
  .panel {{ display: flex; flex-direction: column; min-height: 0; }}
  .title {{ padding: 10px 12px; font-weight: 700; border-bottom: 1px solid #ddd; }}
  iframe {{ border: 0; width: 100%; height: 100%; }}
  @media (max-width: 960px) {{
    .container {{ grid-template-columns: 1fr; grid-auto-rows: 50vh; height: auto; }}
  }}
</style>
</head>
<body>
<header>Trip Origins & Destinations Heatmaps</header>
<div class=\"container\">
  <section class=\"panel\">
    <div class=\"title\">Origin</div>
    <iframe src=\"{os.path.basename(origin_path)}\"></iframe>
  </section>
  <section class=\"panel\">
    <div class=\"title\">Destination</div>
    <iframe src=\"{os.path.basename(dest_path)}\"></iframe>
  </section>
</div>
</body>
</html>"""
    with open(index_path, "w", encoding="utf-8") as fp:
        fp.write(html)


def _collect_csv_files(base_dir: str, pattern: str, recursive: bool) -> List[str]:
    if recursive:
        return sorted(glob.glob(os.path.join(base_dir, "**", pattern), recursive=True))
    return sorted(glob.glob(os.path.join(base_dir, pattern)))


def _read_csv(path: str) -> Optional[pd.DataFrame]:
    try:
        return pd.read_csv(path)
    except Exception:
        try:
            return pd.read_csv(path, encoding="cp932")
        except Exception:
            return None


def _ensure_directory(path: str) -> str:
    if not os.path.isdir(path):
        raise FileNotFoundError(f"入力フォルダが見つかりません: {path}")
    return path


def _select_directory_via_dialog() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        return filedialog.askdirectory(title="trip_extractor 出力CSVフォルダを選択")
    except Exception:
        return ""


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trip CSV から起終点ヒートマップを生成（左右2分割HTMLで表示）")
    parser.add_argument("input_dir", nargs="?", default=None, help="CSVフォルダのパス（未指定ならダイアログで選択）")
    parser.add_argument("--pattern", default="*.csv", help="CSVファイルの検索パターン（既定: *.csv）")
    parser.add_argument("--recursive", action="store_true", help="サブフォルダも再帰検索する")
    parser.add_argument("--radius", type=int, default=16, help="HeatMapのradius（既定: 16）")
    parser.add_argument("--blur", type=int, default=18, help="HeatMapのblur（既定: 18）")
    parser.add_argument("--min-opacity", type=float, default=0.15, help="HeatMapのmin_opacity（既定: 0.15）")
    parser.add_argument("--max-zoom", type=int, default=12, help="HeatMapのmax_zoom（既定: 12）")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    input_dir = args.input_dir
    if input_dir is None:
        input_dir = _select_directory_via_dialog()
    if not input_dir:
        print("キャンセルされました。")
        return 0

    try:
        _ensure_directory(input_dir)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    files = _collect_csv_files(input_dir, args.pattern, args.recursive)
    origin_points: List[List[float]] = []
    dest_points: List[List[float]] = []
    csv_found = 0
    csv_used = 0

    for csv_path in tqdm(files, desc="Scanning CSV"):
        if not csv_path.lower().endswith(".csv"):
            continue
        csv_found += 1
        df = _read_csv(csv_path)
        if df is None:
            continue
        od = extract_origin_destination(df)
        if od is None:
            continue
        origin, destination = od
        origin_points.append([origin[0], origin[1], 1.0])
        dest_points.append([destination[0], destination[1], 1.0])
        csv_used += 1

    all_points = [(p[0], p[1]) for p in origin_points] + [(p[0], p[1]) for p in dest_points]
    if all_points:
        center = _mean_center(all_points)
    else:
        center = (35.7, 137.0)

    origin_html = os.path.join(input_dir, "origin_map.html")
    destination_html = os.path.join(input_dir, "destination_map.html")
    index_html = os.path.join(input_dir, "index_od_heatmap.html")
    summary_txt = os.path.join(input_dir, "od_summary.txt")

    create_heatmap_html(
        origin_points,
        center,
        origin_html,
        radius=args.radius,
        blur=args.blur,
        min_opacity=args.min_opacity,
        max_zoom=args.max_zoom,
    )
    create_heatmap_html(
        dest_points,
        center,
        destination_html,
        radius=args.radius,
        blur=args.blur,
        min_opacity=args.min_opacity,
        max_zoom=args.max_zoom,
    )

    build_index_html(index_html, origin_html, destination_html)

    with open(summary_txt, "w", encoding="utf-8") as fp:
        fp.write("OD Heatmap Summary\n")
        fp.write(f"Input folder  : {input_dir}\n")
        fp.write(f"CSV found     : {csv_found}\n")
        fp.write(f"CSV used      : {csv_used}\n")
        fp.write(f"Origin points : {len(origin_points)}\n")
        fp.write(f"Dest points   : {len(dest_points)}\n")
        fp.write(f"Center(lat,lon): ({center[0]:.6f}, {center[1]:.6f})\n")
        fp.write("Files:\n")
        fp.write(f"  - {os.path.basename(origin_html)}\n")
        fp.write(f"  - {os.path.basename(destination_html)}\n")
        fp.write(f"  - {os.path.basename(index_html)}\n")

    try:
        webbrowser.open(f"file://{index_html}")
    except Exception:
        pass

    print("\n=== DONE ===")
    print(f"Index : {index_html}")
    print(f"Origin: {origin_html}")
    print(f"Dest  : {destination_html}")
    print(f"Summary: {summary_txt}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
