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
  <div class=\"hint\">左クリック=点追加 / 右クリック=既存点へスナップ</div>
  <div class=\"hint\">「ポリゴンを追加/更新」で一覧に登録し、最後にCSV保存</div>
  <input id=\"pname\" placeholder=\"ポリゴン名\" />
  <button id=\"btnAdd\">ポリゴンを追加/更新</button>
  <button id=\"btnClearCurrent\">編集中の点をクリア</button>
  <button id=\"btnClearAll\">一覧をすべて削除</button>
  <div id=\"hint\" style=\"margin-top:4px; font-size:12px;\">右クリック：スナップ　　ESC：もどる（UNDO）</div>
  <div class=\"hint\" style=\"margin-top:6px;\">一覧をクリックすると編集用に読み込みます</div>
  <div class=\"list\" id=\"polygonList\"></div>
  <input id=\"fname\" placeholder=\"保存ファイル名 (例: polygons.csv)\" value=\"{{ default_filename }}\" />
  <button id=\"btnSave\">CSVとして保存</button>
</div>
<script>
  const initialPolygons = {{ polygons_json | safe }};
  const SNAP_PX = 15;
  const map = L.map('map').setView([35.069095, 134.004512], 12); // 津山市役所周辺
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  const polygons = initialPolygons.slice();
  const polygonLayer = L.layerGroup().addTo(map);
  const polygonVertexLayer = L.layerGroup().addTo(map);
  const currentLayer = L.polygon([], {color:'red', weight:2, fill:false, dashArray:'4 4'}).addTo(map);
  const currentVertices = [];
  const currentMarkers = [];
  const currentSegments = [];

  function createMarker(latlng, color) {
    return L.circleMarker(latlng, {
      radius: 8,
      color: color,
      weight: 2,
      fillColor: '#ffffff',
      fillOpacity: 1.0,
    }).addTo(map);
  }

  function resetCurrent() {
    currentVertices.length = 0;
    currentMarkers.forEach(m => map.removeLayer(m));
    currentSegments.forEach(l => map.removeLayer(l));
    currentMarkers.length = 0;
    currentSegments.length = 0;
    currentLayer.setLatLngs([]);
  }

  function redrawCurrent() {
    currentLayer.setLatLngs(currentVertices.map(p => [p.lat, p.lon]));
  }

  function addVertex(latlng) {
    currentVertices.push({lat: latlng.lat, lon: latlng.lng});
    const marker = createMarker(latlng, '#ff0000');
    currentMarkers.push(marker);
    if (currentVertices.length > 1) {
      const prev = currentVertices[currentVertices.length - 2];
      const line = L.polyline([[prev.lat, prev.lon], [latlng.lat, latlng.lng]], {color:'#ff0000', weight:2}).addTo(map);
      currentSegments.push(line);
    }
    redrawCurrent();
  }

  function removeLastVertex() {
    if (!currentVertices.length) return;
    const marker = currentMarkers.pop();
    if (marker) map.removeLayer(marker);
    const segment = currentSegments.pop();
    if (segment) map.removeLayer(segment);
    currentVertices.pop();
    if (!currentVertices.length) {
      resetCurrent();
    } else {
      redrawCurrent();
    }
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
        resetCurrent();
        poly.coords.forEach(c => addVertex(L.latLng(c[0], c[1])));
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

  function refreshPolygons() {
    polygonLayer.clearLayers();
    polygonVertexLayer.clearLayers();
    polygons.forEach(poly => {
      const layer = L.polygon(poly.coords, {color:'black', weight:2, fill:false});
      layer.bindTooltip(poly.name, {permanent:true, direction:'center', className:'polygon-label'});
      layer.addTo(polygonLayer);
      poly.coords.forEach(c => {
        L.circleMarker([c[0], c[1]], {
          radius: 8,
          color: '#000000',
          weight: 2,
          fillColor: '#ffffff',
          fillOpacity: 1.0,
        }).addTo(polygonVertexLayer);
      });
    });
    renderList();
  }

  function orientation(a, b, c) {
    const val = (b.lon - a.lon) * (c.lat - a.lat) - (b.lat - a.lat) * (c.lon - a.lon);
    if (Math.abs(val) < 1e-12) return 0;
    return val > 0 ? 1 : -1;
  }

  function onSegment(a, b, c) {
    return (
      Math.min(a.lon, c.lon) <= b.lon + 1e-12 && b.lon <= Math.max(a.lon, c.lon) + 1e-12 &&
      Math.min(a.lat, c.lat) <= b.lat + 1e-12 && b.lat <= Math.max(a.lat, c.lat) + 1e-12
    );
  }

  function pointsEqual(p, q) {
    return Math.abs(p.lat - q.lat) < 1e-12 && Math.abs(p.lon - q.lon) < 1e-12;
  }

  function colinearOverlap(a1, a2, b1, b2) {
    const useLon = Math.abs(a1.lon - a2.lon) >= Math.abs(a1.lat - a2.lat);
    const aMin = Math.min(a1[useLon ? 'lon' : 'lat'], a2[useLon ? 'lon' : 'lat']);
    const aMax = Math.max(a1[useLon ? 'lon' : 'lat'], a2[useLon ? 'lon' : 'lat']);
    const bMin = Math.min(b1[useLon ? 'lon' : 'lat'], b2[useLon ? 'lon' : 'lat']);
    const bMax = Math.max(b1[useLon ? 'lon' : 'lat'], b2[useLon ? 'lon' : 'lat']);
    const overlap = Math.min(aMax, bMax) - Math.max(aMin, bMin);
    return overlap > 0; // 重なりが線分長として存在する場合のみ
  }

  function segmentsIntersect(a1, a2, b1, b2) {
    const o1 = orientation(a1, a2, b1);
    const o2 = orientation(a1, a2, b2);
    const o3 = orientation(b1, b2, a1);
    const o4 = orientation(b1, b2, a2);

    if (o1 * o2 < 0 && o3 * o4 < 0) {
      return true; // proper intersection
    }

    if (o1 === 0 && onSegment(a1, b1, a2) && !pointsEqual(b1, a1) && !pointsEqual(b1, a2)) {
      return true;
    }
    if (o2 === 0 && onSegment(a1, b2, a2) && !pointsEqual(b2, a1) && !pointsEqual(b2, a2)) {
      return true;
    }
    if (o3 === 0 && onSegment(b1, a1, b2) && !pointsEqual(a1, b1) && !pointsEqual(a1, b2)) {
      return true;
    }
    if (o4 === 0 && onSegment(b1, a2, b2) && !pointsEqual(a2, b1) && !pointsEqual(a2, b2)) {
      return true;
    }

    if (o1 === 0 && o2 === 0 && o3 === 0 && o4 === 0) {
      return colinearOverlap(a1, a2, b1, b2);
    }

    return false; // only share endpoints or no intersection
  }

  function isSelfIntersecting(latlngs) {
    const n = latlngs.length;
    if (n < 4) return false;
    for (let i = 0; i < n; i++) {
      const a1 = latlngs[i];
      const a2 = latlngs[(i + 1) % n];
      for (let j = i + 1; j < n; j++) {
        const b1 = latlngs[j];
        const b2 = latlngs[(j + 1) % n];
        if (i === j || (j === i + 1) || (i === j + 1) || (i === 0 && j === n - 1) || (j === 0 && i === n - 1)) {
          continue; // 隣接辺はスキップ
        }
        if (segmentsIntersect(a1, a2, b1, b2)) {
          return true;
        }
      }
    }
    return false;
  }

  function getAllVertices() {
    const nodes = [];
    polygons.forEach(poly => {
      poly.coords.forEach(c => nodes.push(L.latLng(c[0], c[1])));
    });
    currentVertices.forEach(v => nodes.push(L.latLng(v.lat, v.lon)));
    return nodes;
  }

  function findSnap(latlng) {
    const p = map.latLngToLayerPoint(latlng);
    let nearest = null;
    let minDist = Infinity;
    getAllVertices().forEach(node => {
      const q = map.latLngToLayerPoint(node);
      const dist = p.distanceTo(q);
      if (dist <= SNAP_PX && dist < minDist) {
        minDist = dist;
        nearest = node;
      }
    });
    return nearest;
  }

  map.on('click', (e) => {
    addVertex(e.latlng);
  });

  map.on('contextmenu', (e) => { // 右クリック=スナップ
    if (e.originalEvent) e.originalEvent.preventDefault();
    const snapped = findSnap(e.latlng);
    if (snapped) {
      addVertex(snapped);
    }
  });

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      removeLastVertex();
    }
  });

  document.getElementById('btnClearCurrent').onclick = () => {
    resetCurrent();
  };

  document.getElementById('btnClearAll').onclick = () => {
    if (!confirm('一覧を全て削除しますか？')) return;
    polygons.length = 0;
    resetCurrent();
    refreshPolygons();
  };

  document.getElementById('btnAdd').onclick = () => {
    const name = (document.getElementById('pname').value || 'polygon').trim();
    if (currentVertices.length < 3) {
      alert('3点以上でポリゴンを登録してください。');
      return;
    }
    if (isSelfIntersecting(currentVertices)) {
      alert('ポリゴンが自己交差しています。このポリゴンは無効です。');
      resetCurrent();
      return;
    }
    const coords = currentVertices.map(p => [p.lat, p.lon]);
    const existingIdx = polygons.findIndex(p => p.name === name);
    if (existingIdx >= 0) {
      polygons[existingIdx] = { name, coords };
    } else {
      polygons.push({ name, coords });
    }
    resetCurrent();
    refreshPolygons();
  };

  document.getElementById('btnSave').onclick = () => {
    if (polygons.length === 0) {
      alert('保存するポリゴンがありません。');
      return;
    }
    let fname = (document.getElementById('fname').value || '{{ default_filename }}').trim();
    if (!fname.toLowerCase().endsWith('.csv')) {
      fname = `${fname}.csv`;
    }
    const lines = polygons.map(poly => {
      const parts = [poly.name || 'polygon'];
      poly.coords.forEach(c => {
        parts.push(`${c[1]}`); // lon
        parts.push(`${c[0]}`); // lat
      });
      return parts.join(',');
    });
    const csvContent = lines.join('\n');
    const bom = new Uint8Array([0xEF, 0xBB, 0xBF]);
    const blob = new Blob([bom, csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fname;
    a.click();
    URL.revokeObjectURL(url);
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
