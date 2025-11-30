# -*- coding: utf-8 -*-
"""
polygon_builder.py

Flask + Leaflet でポリゴンを編集・保存するツール。

・http://127.0.0.1:<port>/ をブラウザで開いて操作します。
・左クリックで点追加、右クリックで直前の点を削除。
・「ポリゴンを追加/更新」で名前付きポリゴンとして登録。
・既存CSVがあれば読み込み、同じ形式で保存します
  （1行1ポリゴン、A列: name, B以降: lon,lat の繰り返し）。

起動例: python 21_polygon_builder.py --outdir "/tmp/out" --filename polygons.csv --port 5010
"""

from __future__ import annotations

import argparse
import csv
import json
import threading
import webbrowser
from pathlib import Path
from typing import List

from flask import Flask, jsonify, render_template_string, request


app = Flask(__name__)

# Flaskハンドラが参照する保存先と既存ポリゴン
OUTDIR = Path(__file__).parent.resolve()
DEFAULT_FILENAME = "polygon_data.csv"
INITIAL_POLYGONS: List[dict] = []


def load_polygons(csv_path: Path) -> List[dict]:
    """既存CSVからポリゴンを読み込む。"""

    polygons: List[dict] = []
    if not csv_path.exists():
        return polygons

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3 or len(row) % 2 == 0:
                continue
            name = row[0].strip() or "polygon"
            try:
                values = list(map(float, row[1:]))
            except ValueError:
                continue
            coords = []
            for i in range(0, len(values), 2):
                lon, lat = values[i], values[i + 1]
                coords.append([lat, lon])
            polygons.append({"name": name, "coords": coords})
    return polygons


INDEX_HTML = """
<!doctype html>
<html lang=\"ja\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>ポリゴン編集ツール</title>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" />
  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
  <style>
    html, body, #map { height: 100%; margin: 0; }
    .toolbar { position:absolute; top:10px; left:10px; z-index:1000; background:#fff; padding:10px; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,.2); width: 320px; }
    .toolbar input { width: 100%; margin: 4px 0; }
    .toolbar button { margin: 2px 0; width: 100%; }
    .hint { font-size: 12px; color:#555; }
    .list { max-height: 160px; overflow-y: auto; border:1px solid #ccc; padding:4px; margin-top:6px; }
    .list-item { cursor:pointer; padding:2px 4px; border-radius:4px; }
    .list-item:hover { background:#eef; }
    .polygon-label { font-weight: 700; color: #111; text-shadow: 0 1px 2px #fff; }
  </style>
</head>
<body>
<div id=\"map\"></div>
<div class=\"toolbar\">
  <div><strong>ポリゴン編集</strong></div>
  <div class=\"hint\">左クリック=点追加 / 右クリック=直前の点を削除</div>
  <div class=\"hint\">「ポリゴンを追加/更新」で一覧に登録し、最後にCSV保存</div>
  <input id=\"pname\" placeholder=\"ポリゴン名\" />
  <button id=\"btnAdd\">ポリゴンを追加/更新</button>
  <button id=\"btnClearCurrent\">編集中の点をクリア</button>
  <button id=\"btnClearAll\">一覧をすべて削除</button>
  <div class=\"hint\" style=\"margin-top:6px;\">一覧をクリックすると編集用に読み込みます</div>
  <div class=\"list\" id=\"polygonList\"></div>
  <input id=\"fname\" placeholder=\"保存ファイル名 (例: polygons.csv)\" value=\"{{ default_filename }}\" />
  <button id=\"btnSave\">CSVとして保存</button>
</div>
<script>
  const initialPolygons = {{ polygons_json | safe }};
  const map = L.map('map').setView([35.069095, 134.004512], 12); // 津山市役所周辺
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  const currentPoints = []; // {lat, lon}
  const polygons = initialPolygons.slice();
  const polygonLayer = L.layerGroup().addTo(map);
  const currentLayer = L.polygon([], {color:'red', weight:2, fill:false, dashArray:'4 4'}).addTo(map);

  function redrawCurrent() {
    currentLayer.setLatLngs(currentPoints.map(p => [p.lat, p.lon]));
  }

  function refreshPolygons() {
    polygonLayer.clearLayers();
    polygons.forEach(poly => {
      const layer = L.polygon(poly.coords, {color:'black', weight:2, fill:false});
      layer.bindTooltip(poly.name, {permanent:true, direction:'center', className:'polygon-label'});
      layer.addTo(polygonLayer);
    });
    renderList();
  }

  function renderList() {
    const list = document.getElementById('polygonList');
    list.innerHTML = '';
    polygons.forEach((poly, idx) => {
      const div = document.createElement('div');
      div.className = 'list-item';
      div.textContent = `${idx + 1}. ${poly.name}`;
      div.onclick = () => {
        document.getElementById('pname').value = poly.name;
        currentPoints.length = 0;
        poly.coords.forEach(c => currentPoints.push({lat: c[0], lon: c[1]}));
        redrawCurrent();
        try {
          map.fitBounds(L.polygon(poly.coords).getBounds(), { maxZoom: 16 });
        } catch (e) {
          // ignore
        }
      };
      list.appendChild(div);
    });
  }

  map.on('click', (e) => {
    currentPoints.push({lat: e.latlng.lat, lon: e.latlng.lng});
    redrawCurrent();
  });
  map.on('contextmenu', () => { // 右クリック=ひとつ戻す
    currentPoints.pop();
    redrawCurrent();
  });

  document.getElementById('btnClearCurrent').onclick = () => {
    currentPoints.length = 0;
    redrawCurrent();
  };

  document.getElementById('btnClearAll').onclick = () => {
    if (!confirm('一覧を全て削除しますか？')) return;
    polygons.length = 0;
    refreshPolygons();
  };

  document.getElementById('btnAdd').onclick = () => {
    const name = (document.getElementById('pname').value || 'polygon').trim();
    if (currentPoints.length < 3) {
      alert('3点以上でポリゴンを登録してください。');
      return;
    }
    const coords = currentPoints.map(p => [p.lat, p.lon]);
    const existingIdx = polygons.findIndex(p => p.name === name);
    if (existingIdx >= 0) {
      polygons[existingIdx] = { name, coords };
    } else {
      polygons.push({ name, coords });
    }
    currentPoints.length = 0;
    redrawCurrent();
    refreshPolygons();
  };

  document.getElementById('btnSave').onclick = async () => {
    if (polygons.length === 0) {
      alert('保存するポリゴンがありません。');
      return;
    }
    const fname = (document.getElementById('fname').value || '{{ default_filename }}').trim();
    const res = await fetch('/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ filename: fname, polygons: polygons })
    });
    const data = await res.json();
    if (data.ok) {
      alert('保存しました: ' + data.path);
    } else {
      alert('保存に失敗: ' + data.error);
    }
  };

  // 初期表示
  refreshPolygons();
  renderList();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(
        INDEX_HTML,
        polygons_json=json.dumps(INITIAL_POLYGONS, ensure_ascii=False),
        default_filename=DEFAULT_FILENAME,
    )


@app.route("/save", methods=["POST"])
def save():
    try:
        data = request.get_json(force=True) or {}
        filename = (data.get("filename") or DEFAULT_FILENAME).strip() or DEFAULT_FILENAME
        polygons = data.get("polygons") or []
        if not isinstance(polygons, list) or len(polygons) == 0:
            return jsonify(ok=False, error="polygons must be non-empty list")

        rows: List[List[str]] = []
        for poly in polygons:
            name = (poly.get("name") or "polygon").strip() if isinstance(poly, dict) else "polygon"
            coords = poly.get("coords") if isinstance(poly, dict) else None
            if not isinstance(coords, list) or len(coords) < 3:
                return jsonify(ok=False, error=f"polygon '{name}' must have at least 3 points")
            row: List[str] = [name]
            try:
                for lat, lon in coords:
                    row.extend([f"{float(lon):.10f}", f"{float(lat):.10f}"])
            except Exception:
                return jsonify(ok=False, error=f"invalid coords in polygon '{name}'")
            rows.append(row)

        if not filename.lower().endswith(".csv"):
            filename = f"{filename}.csv"
        safe_name = Path(filename).name or DEFAULT_FILENAME
        out_path = OUTDIR / safe_name
        with out_path.open("w", newline="", encoding="utf-8") as f:
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
    parser = argparse.ArgumentParser(description="Leafletでポリゴンを編集・保存")
    parser.add_argument("--csv", type=str, default="polygon_data.csv", help="初期読み込み用CSV")
    parser.add_argument("--outdir", type=str, default=str(Path(__file__).parent), help="保存先フォルダ")
    parser.add_argument("--filename", type=str, default="polygon_data.csv", help="保存ファイル名")
    parser.add_argument("--port", type=int, default=5010)
    args = parser.parse_args()

    global OUTDIR, DEFAULT_FILENAME, INITIAL_POLYGONS
    OUTDIR = Path(args.outdir).expanduser().resolve()
    OUTDIR.mkdir(parents=True, exist_ok=True)

    DEFAULT_FILENAME = args.filename.strip() or "polygon_data.csv"

    csv_path = Path(args.csv).expanduser()
    if not csv_path.is_absolute():
        csv_path = OUTDIR / csv_path
    INITIAL_POLYGONS = load_polygons(csv_path)

    url = f"http://127.0.0.1:{args.port}/"
    threading.Timer(0.5, _open_browser, args=(url,)).start()
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
