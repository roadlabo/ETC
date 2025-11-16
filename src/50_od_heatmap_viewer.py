# -*- coding: utf-8 -*-
"""
50_od_heatmap_viewer.py
trip_extractor.py の出力CSV群から、起点(Origin)と終点(Destination)を抽出し、
O列=経度, P列=緯度 の固定列を使用してヒートマップを生成する。
"""

import os, sys, glob, webbrowser
import pandas as pd, numpy as np
from tqdm import tqdm
import folium
from folium.plugins import HeatMap

# ============================================================
# ★★★ 設定エリア（ここだけ変更すればOK）★★★
# ============================================================

# ✅ 入力フォルダ（trip_extractor出力CSVの場所）
INPUT_DIR = r"D:\ETC\trip_csvs"

# ✅ 出力フォルダ（ヒートマップ・サマリを保存する場所）
OUTPUT_DIR = r"D:\ETC\trip_output"

# 出力ファイル（OUTPUT_DIR内に生成される）
OUTPUT_ORIGIN_HTML = os.path.join(OUTPUT_DIR, "origin_map.html")
OUTPUT_DEST_HTML   = os.path.join(OUTPUT_DIR, "destination_map.html")
OUTPUT_INDEX_HTML  = os.path.join(OUTPUT_DIR, "index_od_heatmap.html")
OUTPUT_SUMMARY_TXT = os.path.join(OUTPUT_DIR, "od_summary.txt")

# ヒートマップの描画設定
RADIUS = 16
BLUR = 18
MIN_OPACITY = 0.15
MAX_ZOOM = 12

# ------------------------------------------------------------
# 固定列設定（経度=O列, 緯度=P列）
# ------------------------------------------------------------
LON_COL_INDEX = 14  # O列 → 0始まりで14
LAT_COL_INDEX = 15  # P列 → 0始まりで15

# ============================================================
# 以下、処理ロジック
# ============================================================

def _to_float(v):
    """文字列をfloatに変換（カンマ付き・NaN対応）"""
    try:
        return float(v)
    except Exception:
        try:
            return float(str(v).replace(",", ""))
        except Exception:
            return np.nan

def _normalize_latlon(lat, lon):
    """緯度経度を正規化・範囲チェック（逆転補正含む）"""
    lat = _to_float(lat); lon = _to_float(lon)
    if np.isnan(lat) or np.isnan(lon):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        if -90 <= lon <= 90 and -180 <= lat <= 180:
            lat, lon = lon, lat
        else:
            return None
    return (lat, lon)

def _read_csv_robust(path):
    """文字コード・区切り自動検出"""
    for kwargs in (
        dict(),
        dict(encoding="utf-8-sig"),
        dict(encoding="cp932"),
        dict(sep=None, engine="python"),
        dict(sep=None, engine="python", encoding="cp932"),
    ):
        try:
            return pd.read_csv(path, **kwargs)
        except Exception:
            continue
    return None

def extract_origin_destination(df):
    """先頭行=Origin, 最終行=Destination を O/P列から抽出"""
    if df is None or df.empty:
        return None
    df = df.dropna(how="all")
    if df.empty:
        return None

    cols = list(df.columns)
    if len(cols) <= max(LON_COL_INDEX, LAT_COL_INDEX):
        return None

    lon_col = cols[LON_COL_INDEX]
    lat_col = cols[LAT_COL_INDEX]

    head = df.iloc[0]
    tail = df.iloc[-1]

    p_o = _normalize_latlon(head[lat_col], head[lon_col])
    p_d = _normalize_latlon(tail[lat_col], tail[lon_col])

    if p_o is None or p_d is None:
        return None
    return p_o, p_d

def create_heatmap(points, center, out_html):
    """Foliumでヒートマップを作成"""
    if not points:
        m = folium.Map(location=center, zoom_start=9, control_scale=True)
        folium.Marker(center, tooltip="No points").add_to(m)
    else:
        m = folium.Map(location=center, zoom_start=9, control_scale=True)
        HeatMap(points, radius=RADIUS, blur=BLUR,
                min_opacity=MIN_OPACITY, max_zoom=MAX_ZOOM).add_to(m)
    m.save(out_html)

def build_index_html(index_path, origin_path, dest_path):
    """Origin/Destination 2画面を横並び表示するHTMLを生成"""
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>OD Heatmaps</title>
<style>
body {{margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans JP";}}
header {{padding:12px;background:#222;color:#fff;font-weight:bold;}}
.container {{display:grid;grid-template-columns:1fr 1fr;height:calc(100vh - 50px);}}
.panel {{display:flex;flex-direction:column;}}
.title {{padding:8px;font-weight:bold;border-bottom:1px solid #ddd;text-align:center;}}
iframe {{flex:1;border:0;}}
</style>
</head>
<body>
<header>Trip Origins & Destinations Heatmaps</header>
<div class="container">
<section class="panel"><div class="title">Origin</div><iframe src="{os.path.basename(origin_path)}"></iframe></section>
<section class="panel"><div class="title">Destination</div><iframe src="{os.path.basename(dest_path)}"></iframe></section>
</div></body></html>"""
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

def main():
    # 出力フォルダ作成
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.isdir(INPUT_DIR):
        print(f"フォルダが見つかりません: {INPUT_DIR}")
        sys.exit(1)

    files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.csv")))
    origin_pts = []
    dest_pts = []
    used = 0

    for f in tqdm(files, desc="Scanning CSV"):
        df = _read_csv_robust(f)
        if df is None:
            continue
        od = extract_origin_destination(df)
        if od is None:
            continue
        (o_lat, o_lon), (d_lat, d_lon) = od
        origin_pts.append([o_lat, o_lon, 1.0])
        dest_pts.append([d_lat, d_lon, 1.0])
        used += 1

    if not origin_pts:
        print("抽出できる起終点がありません。O/P列を確認してください。")
        sys.exit(1)

    # 中心座標を全点の平均から算出
    all_pts = [(p[0], p[1]) for p in origin_pts + dest_pts]
    lat_c, lon_c = np.mean([p[0] for p in all_pts]), np.mean([p[1] for p in all_pts])

    # 出力ファイル生成
    create_heatmap(origin_pts, (lat_c, lon_c), OUTPUT_ORIGIN_HTML)
    create_heatmap(dest_pts, (lat_c, lon_c), OUTPUT_DEST_HTML)
    build_index_html(OUTPUT_INDEX_HTML, OUTPUT_ORIGIN_HTML, OUTPUT_DEST_HTML)

    # サマリ出力
    with open(OUTPUT_SUMMARY_TXT, "w", encoding="utf-8") as fw:
        fw.write(f"Input  : {INPUT_DIR}\n")
        fw.write(f"Output : {OUTPUT_DIR}\n")
        fw.write(f"Files  : {len(files)} / Used : {used}\n")
        fw.write(f"Center : ({lat_c:.6f}, {lon_c:.6f})\n")
        fw.write(f"Index  : {os.path.basename(OUTPUT_INDEX_HTML)}\n")

    # ブラウザで開く
    webbrowser.open(f"file://{OUTPUT_INDEX_HTML}")

    print("=== 完了しました ===")
    print(f"出力フォルダ: {OUTPUT_DIR}")
    print(f"生成ファイル: {OUTPUT_INDEX_HTML}")

if __name__ == "__main__":
    main()
