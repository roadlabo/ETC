# -*- coding: utf-8 -*-
"""
51_od_heatmap_viewer.py

16_trip_od_screening.py の出力結果CSV（様式1-3の抜粋）を読み込み、
L列〜O列に記録された起点・終点座標から Origin / Destination ヒートマップを生成する。
"""

import os, sys, webbrowser
import pandas as pd, numpy as np
import folium
from folium.plugins import HeatMap

# ============================================================
# ★★★ 設定エリア（ここだけ変更すればOK）★★★
# ============================================================

# ✅ 入力CSV（16_trip_od_screening.py の出力結果CSV）
INPUT_CSV_PATH = r"D:\\ETC\\trip_od_result.csv"

# ✅ 出力フォルダ（ヒートマップ・サマリを保存する場所）
OUTPUT_DIR = r"D:\\ETC\\trip_output"

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
# 16_trip_od_screening.py 出力CSVにおける列位置
# （0始まりのインデックスを使用）
# ------------------------------------------------------------
ORIGIN_LON_COL_INDEX = 11  # L列 起点経度
ORIGIN_LAT_COL_INDEX = 12  # M列 起点緯度
DEST_LON_COL_INDEX   = 13  # N列 終点経度
DEST_LAT_COL_INDEX   = 14  # O列 終点緯度

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

    if not os.path.isfile(INPUT_CSV_PATH):
        print(f"入力CSVが見つかりません: {INPUT_CSV_PATH}")
        sys.exit(1)

    df = _read_csv_robust(INPUT_CSV_PATH)
    if df is None or df.empty:
        print(f"入力CSVを読み込めませんでした、または空です: {INPUT_CSV_PATH}")
        sys.exit(1)

    cols = list(df.columns)
    if len(cols) <= max(ORIGIN_LON_COL_INDEX, ORIGIN_LAT_COL_INDEX,
                        DEST_LON_COL_INDEX, DEST_LAT_COL_INDEX):
        print("CSVの列数が足りません。L〜O列が存在するか確認してください。")
        sys.exit(1)

    olon_col = cols[ORIGIN_LON_COL_INDEX]
    olat_col = cols[ORIGIN_LAT_COL_INDEX]
    dlon_col = cols[DEST_LON_COL_INDEX]
    dlat_col = cols[DEST_LAT_COL_INDEX]

    origin_pts = []
    dest_pts = []
    used = 0

    for _, row in df.iterrows():
        o = _normalize_latlon(row[olat_col], row[olon_col])
        d = _normalize_latlon(row[dlat_col], row[dlon_col])
        if o is None or d is None:
            continue
        o_lat, o_lon = o
        d_lat, d_lon = d
        origin_pts.append([o_lat, o_lon, 1.0])
        dest_pts.append([d_lat, d_lon, 1.0])
        used += 1

    if not origin_pts:
        print("抽出できる起終点がありません。L〜O列の座標を確認してください。")
        sys.exit(1)

    # 中心座標を全点の平均から算出
    all_pts = [(p[0], p[1]) for p in origin_pts + dest_pts]
    lat_c = np.mean([p[0] for p in all_pts])
    lon_c = np.mean([p[1] for p in all_pts])

    # 出力ファイル生成
    create_heatmap(origin_pts, (lat_c, lon_c), OUTPUT_ORIGIN_HTML)
    create_heatmap(dest_pts, (lat_c, lon_c), OUTPUT_DEST_HTML)
    build_index_html(OUTPUT_INDEX_HTML, OUTPUT_ORIGIN_HTML, OUTPUT_DEST_HTML)

    # サマリ出力
    with open(OUTPUT_SUMMARY_TXT, "w", encoding="utf-8") as fw:
        fw.write(f"Input CSV : {INPUT_CSV_PATH}\n")
        fw.write(f"Output    : {OUTPUT_DIR}\n")
        fw.write(f"Rows      : {len(df)} / Used(有効OD) : {used}\n")
        fw.write(f"Center    : ({lat_c:.6f}, {lon_c:.6f})\n")
        fw.write(f"Index HTML: {os.path.basename(OUTPUT_INDEX_HTML)}\n")

    # ブラウザで開く
    webbrowser.open(f"file://{OUTPUT_INDEX_HTML}")

    print("=== ODヒートマップ生成が完了しました ===")
    print(f"入力CSV   : {INPUT_CSV_PATH}")
    print(f"出力フォルダ: {OUTPUT_DIR}")
    print(f"生成ファイル: {OUTPUT_INDEX_HTML}")

if __name__ == "__main__":
    main()
