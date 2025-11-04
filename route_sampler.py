# -*- coding: utf-8 -*-
"""
route_sampler.py

クリックでポイントを打ってサンプルルートを作成し、
route_mapper_simple.py がそのまま読めるCSV（O列=緯度, P列=経度, M列=flag）を出力します。

・ブラウザで地図（Leaflet）を開き、クリックで点追加／右クリックで直前の点を削除。
・「保存」ボタンでCSVを出力（ヘッダなし、カンマ区切り）。
・flag列（M=0-based idx 12）は **すべて中間=2** を出力します。
・種別(TYPE=E列=idx4), 用途(USE=F列=idx5), GPS時刻(TIME=G列=idx6), 速度(SPEED=S列=idx18) は
  ダミー値を自動付与（時刻は先頭基準で +10秒刻み）。

依存: Flask (pip install flask)
起動: python route_sampler.py --outdir "/path/to/save" --filename sample.csv
省略時は本スクリプトと同じフォルダに保存します。
"""
from __future__ import annotations

import argparse
import csv
import threading
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

from flask import Flask, jsonify, render_template_string, request

# ------------------------------
# 出力CSVの列定義（0-based index）
# ------------------------------
# A=0, B=1, ... O=14(lat), P=15(lon), M=12(flag), E=4(type), F=5(use), G=6(time), S=18(speed)
LAT_COL = 14
LON_COL = 15
FLAG_COL = 12
TYPE_COL = 4
USE_COL = 5
TIME_COL = 6
SPEED_COL = 18

TOTAL_COLS = max(LAT_COL, LON_COL, FLAG_COL, TYPE_COL, USE_COL, TIME_COL, SPEED_COL) + 1

DEFAULT_TYPE = 2  # 普通
DEFAULT_USE = 1   # 乗用
DEFAULT_SPEED = 30.0  # km/h（ダミー）
TIME_STEP_SEC = 10     # 各点を+10秒でダミー時刻生成

app = Flask(__name__)

# Flaskのエンドポイントから参照する出力先ディレクトリ。
# main() 実行時に上書きされるが、インポート時にも有効なパスを持たせておく。
OUTDIR = Path(__file__).parent.resolve()

INDEX_HTML = """
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>サンプルルート作成</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map { height: 100%; margin: 0; }
    .toolbar { position:absolute; top:10px; left:10px; z-index:1000; background:#fff; padding:8px; border-radius:6px; box-shadow:0 2px 6px rgba(0,0,0,.2); }
    .toolbar input { margin: 4px 0; width: 240px; }
    .hint { font-size: 12px; color:#555; }
  </style>
</head>
<body>
<div id="map"></div>
<div class="toolbar">
  <div><strong>サンプルルート作成</strong></div>
  <div class="hint">左クリックで追加／右クリックで直前の点を削除</div>
  <div><input id="fname" placeholder="保存ファイル名 (例: sample.csv)"/></div>
  <button id="btnSave">保存</button>
  <button id="btnClear">全消去</button>
</div>
<script>
  const map = L.map('map').setView([35.069095, 134.004512], 12); // 津山市役所周辺
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  const points = []; // {lat, lon}
  const markers = [];
  const poly = L.polyline([], {color: 'black', weight: 2, opacity: 1.0}).addTo(map);

  function redraw() {
    poly.setLatLngs(points.map(p => [p.lat, p.lon]));
    markers.forEach(m => m.remove());
    markers.length = 0;
    if (points.length > 0) {
      const s = points[0];
      const start = L.circleMarker([s.lat, s.lon], {radius:8, color:'red', weight:2, fill:true, fillColor:'white'}).addTo(map);
      L.marker([s.lat, s.lon], {icon: L.divIcon({className:'', html:'<div style="color:red;font-weight:700;">S</div>'})}).addTo(map);
      markers.push(start);
    }
    if (points.length >= 2) {
      for (let i=1; i<points.length-1; i++) {
        const p = points[i];
        const m = L.circleMarker([p.lat, p.lon], {radius:4, color:'black', weight:1, fill:true, fillColor:'black'}).addTo(map);
        markers.push(m);
      }
      const g = points[points.length-1];
      const goal = L.circleMarker([g.lat, g.lon], {radius:8, color:'blue', weight:2, fill:true, fillColor:'white'}).addTo(map);
      L.marker([g.lat, g.lon], {icon: L.divIcon({className:'', html:'<div style="color:blue;font-weight:700;">G</div>'})}).addTo(map);
      markers.push(goal);
    }
  }

  map.on('click', (e) => {
    points.push({lat: e.latlng.lat, lon: e.latlng.lng});
    redraw();
  });
  map.on('contextmenu', (e) => { // 右クリック=ひとつ戻す
    points.pop();
    redraw();
  });

  document.getElementById('btnClear').onclick = () => { points.length = 0; redraw(); };

  document.getElementById('btnSave').onclick = async () => {
    if (points.length < 2) {
      alert('2点以上を指定してください。');
      return;
    }
    const fname = document.getElementById('fname').value || 'sample.csv';
    const res = await fetch('/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ filename: fname, points: points })
    });
    const data = await res.json();
    if (data.ok) {
      alert('保存しました: ' + data.path);
    } else {
      alert('保存に失敗: ' + data.error);
    }
  };
</script>
</body>
</html>
"""


def build_rows(points: List[Tuple[float, float]], start_time: datetime) -> List[List[str]]:
    """Leafletからの [ {lat, lon}, ... ] を route_mapper_simple.py 互換の行に変換。
    各行は "TOTAL_COLS" 長のリスト（文字列）で、未使用セルは "0" を入れる。
    """
    rows: List[List[str]] = []
    for idx, (lat, lon) in enumerate(points):
        row = ["0"] * TOTAL_COLS
        # ダミー: 種別/用途/速度
        row[TYPE_COL] = str(DEFAULT_TYPE)
        row[USE_COL] = str(DEFAULT_USE)
        row[SPEED_COL] = f"{DEFAULT_SPEED:.1f}"
        # フラグは常に中間=2 を設定
        flag = 2
        row[FLAG_COL] = str(flag)
        # 時刻（YYYYMMDDHHMMSS 形式）
        t = start_time + timedelta(seconds=TIME_STEP_SEC * idx)
        row[TIME_COL] = t.strftime("%Y%m%d%H%M%S")
        # 座標: O列=lat, P列=lon
        row[LAT_COL] = f"{lat:.10f}"
        row[LON_COL] = f"{lon:.10f}"
        rows.append(row)
    return rows


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/save", methods=["POST"])
def save():
    try:
        data = request.get_json(force=True)
        filename = (data.get("filename") or "sample.csv").strip()
        pts = data.get("points") or []
        if not isinstance(pts, list) or len(pts) < 2:
            return jsonify(ok=False, error="points must be >= 2")
        # 正規化
        points: List[Tuple[float, float]] = []
        for p in pts:
            lat = float(p.get("lat"))
            lon = float(p.get("lon"))
            points.append((lat, lon))

        start_time = datetime.now()
        rows = build_rows(points, start_time)

        # 保存先を引数の outdir に
        out_path = OUTDIR / filename
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerows(rows)
        return jsonify(ok=True, path=str(out_path))
    except Exception as e:
        return jsonify(ok=False, error=str(e))


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url, new=1)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="クリックでサンプルルートCSVを生成")
    parser.add_argument("--outdir", type=str, default=str(Path(__file__).parent), help="出力先フォルダ")
    parser.add_argument("--filename", type=str, default="sample.csv", help="初期ファイル名")
    parser.add_argument("--port", type=int, default=5009)
    args = parser.parse_args()

    global OUTDIR
    OUTDIR = Path(args.outdir).expanduser().resolve()
    OUTDIR.mkdir(parents=True, exist_ok=True)

    # indexで初期ファイル名を使いたければテンプレに差し込みも可（簡潔のため割愛）

    url = f"http://127.0.0.1:{args.port}/"
    threading.Timer(0.5, _open_browser, args=(url,)).start()
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
