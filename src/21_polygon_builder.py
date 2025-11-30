# -*- coding: utf-8 -*-
"""
polygon_builder.py

Flask + Leaflet でポリゴンを編集・保存するツール。

・http://127.0.0.1:<port>/ をブラウザで開いて操作します。
・左クリックで点追加、右クリックで直前の点を削除。
・「追加」で名前付きポリゴンとして登録。
・起動後のダイアログで既存CSVを読み込むか新規作成かを選べます
  （CSV形式は1行1ポリゴン、A列: name, B以降: lon,lat の繰り返し）。

起動例: python 21_polygon_builder.py --outdir "/tmp/out" --filename polygons.csv --port 5010
"""

from __future__ import annotations

import argparse
import csv
import threading
import webbrowser
from pathlib import Path
from typing import List

from flask import Flask, jsonify, render_template_string, request


app = Flask(__name__)

# Flaskハンドラが参照する保存先と既存ポリゴン
OUTDIR = Path(__file__).parent.resolve()
DEFAULT_FILENAME = "polygon_data.csv"
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
  <div class=\"hint\">「追加」で一覧に登録し、最後にCSV保存</div>
  <input id=\"pname\" placeholder=\"ポリゴン名\" />
  <button id=\"btnAdd\">追加</button>
  <button id=\"btnClearCurrent\">編集中の点をクリア</button>
  <div id=\"hint\" style=\"margin-top:4px; font-size:12px;\">右クリック：スナップ　　ESC：もどる（UNDO）</div>
  <div class=\"list\" id=\"polygonList\"></div>
  <button id=\"btnSave\">CSVとして保存</button>
  <input type=\"file\" id=\"fileInput\" accept=\".csv\" style=\"display:none\" />
</div>
<script>
  // ==== 初期データ ====
  var polygons = [];
  var SNAP_PX = 15;

  function parseCsvText(text) {
    var lines = text.split(/\r?\n/).filter(function(line){ return line.trim() !== ''; });
    var result = [];
    for (var i = 0; i < lines.length; i++) {
      var cols = lines[i].split(',');
      if (cols.length < 3) continue;
      var name = cols[0].trim();
      var coords = [];
      for (var j = 1; j + 1 < cols.length; j += 2) {
        var lon = parseFloat(cols[j]);
        var lat = parseFloat(cols[j + 1]);
        if (isNaN(lat) || isNaN(lon)) continue;
        coords.push([lat, lon]);
      }
      if (coords.length >= 3) {
        result.push({ name: name, coords: coords });
      }
    }
    return result;
  }

  // ==== Leaflet マップ ====
  var map = L.map('map').setView([35.069095, 134.004512], 12); // 津山市役所周辺
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  // ==== レイヤ & 状態管理 ====
  var polygonLayer = L.layerGroup().addTo(map);
  var polygonVertexLayer = L.layerGroup().addTo(map);

  var currentLayer = L.polygon([], {color:'red', weight:2, fill:false, dashArray:'4 4'}).addTo(map);
  var currentVertices = [];   // {lat, lon} の配列
  var currentMarkers  = [];   // CircleMarker
  var currentSegments = [];   // Polyline

  function createMarker(latlng, color) {
    return L.circleMarker(latlng, {
      radius: 5,
      color: color,
      weight: 2,
      fillColor: color,
      fillOpacity: 1.0
    }).addTo(map);
  }

  function resetCurrent() {
    currentVertices = [];
    for (var i = 0; i < currentMarkers.length; i++) {
      map.removeLayer(currentMarkers[i]);
    }
    for (var j = 0; j < currentSegments.length; j++) {
      map.removeLayer(currentSegments[j]);
    }
    currentMarkers = [];
    currentSegments = [];
    currentLayer.setLatLngs([]);
  }

  function redrawCurrent() {
    var latlngs = [];
    for (var i = 0; i < currentVertices.length; i++) {
      latlngs.push([currentVertices[i].lat, currentVertices[i].lon]);
    }
    currentLayer.setLatLngs(latlngs);
  }

  function addVertex(latlng) {
    currentVertices.push({lat: latlng.lat, lon: latlng.lng});
    var marker = createMarker(latlng, '#ff0000');
    currentMarkers.push(marker);

    if (currentVertices.length > 1) {
      var prev = currentVertices[currentVertices.length - 2];
      var line = L.polyline([[prev.lat, prev.lon], [latlng.lat, latlng.lng]], {
        color:'#ff0000',
        weight:2
      }).addTo(map);
      currentSegments.push(line);
    }
    redrawCurrent();
  }

  function removeLastVertex() {
    if (!currentVertices.length) return;
    var marker = currentMarkers.pop();
    if (marker) map.removeLayer(marker);
    var seg = currentSegments.pop();
    if (seg) map.removeLayer(seg);
    currentVertices.pop();
    if (!currentVertices.length) {
      resetCurrent();
    } else {
      redrawCurrent();
    }
  }

  // ==== 一覧描画 ====
  function renderList() {
    var list = document.getElementById('polygonList');
    list.innerHTML = '';
    for (var i = 0; i < polygons.length; i++) {
      var poly = polygons[i];
      var div = document.createElement('div');
      div.className = 'list-item';
      div.textContent = (i + 1) + '. ' + poly.name;
      (function(p) {
        div.onclick = function() {
          try {
            var tmp = L.polygon(p.coords);
            map.fitBounds(tmp.getBounds(), { maxZoom: 16 });
          } catch (e) {}
        };
      })(poly);
      list.appendChild(div);
    }
  }

  function refreshPolygons() {
    polygonLayer.clearLayers();
    polygonVertexLayer.clearLayers();

    for (var i = 0; i < polygons.length; i++) {
      var poly = polygons[i];
      var layer = L.polygon(poly.coords, {color:'black', weight:2, fill:false});
      layer.bindTooltip(poly.name, {
        permanent:true,
        direction:'center',
        className:'polygon-label'
      });

      (function(name) {
        layer.on('contextmenu', function(e) {
          L.DomEvent.preventDefault(e);
          var msg = 'ポリゴン「' + name + '」を削除しますか？';
          if (!window.confirm(msg)) {
            return;
          }
          var newList = [];
          for (var j = 0; j < polygons.length; j++) {
            if (polygons[j].name !== name) {
              newList.push(polygons[j]);
            }
          }
          polygons = newList;
          resetCurrent();
          refreshPolygons();
        });
      })(poly.name);
      layer.addTo(polygonLayer);

      for (var k = 0; k < poly.coords.length; k++) {
        var c = poly.coords[k];
        L.circleMarker([c[0], c[1]], {
          radius: 5,
          color: '#000000',
          weight: 2,
          fillColor: '#000000',
          fillOpacity: 1.0
        }).addTo(polygonVertexLayer);
      }
    }
    renderList();
  }

  // ==== 交差判定 ====
  function orientation(a, b, c) {
    var val = (b.lon - a.lon) * (c.lat - a.lat) - (b.lat - a.lat) * (c.lon - a.lon);
    if (Math.abs(val) < 1e-12) return 0;
    return (val > 0) ? 1 : -1;
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
    var useLon = Math.abs(a1.lon - a2.lon) >= Math.abs(a1.lat - a2.lat);
    var key = useLon ? 'lon' : 'lat';
    var aMin = Math.min(a1[key], a2[key]);
    var aMax = Math.max(a1[key], a2[key]);
    var bMin = Math.min(b1[key], b2[key]);
    var bMax = Math.max(b1[key], b2[key]);
    var overlap = Math.min(aMax, bMax) - Math.max(aMin, bMin);
    return overlap > 0;
  }

  function segmentsIntersect(a1, a2, b1, b2) {
    var o1 = orientation(a1, a2, b1);
    var o2 = orientation(a1, a2, b2);
    var o3 = orientation(b1, b2, a1);
    var o4 = orientation(b1, b2, a2);

    if (o1 * o2 < 0 && o3 * o4 < 0) {
      return true;
    }
    if (o1 === 0 && onSegment(a1, b1, a2) && !pointsEqual(b1, a1) && !pointsEqual(b1, a2)) return true;
    if (o2 === 0 && onSegment(a1, b2, a2) && !pointsEqual(b2, a1) && !pointsEqual(b2, a2)) return true;
    if (o3 === 0 && onSegment(b1, a1, b2) && !pointsEqual(a1, b1) && !pointsEqual(a1, b2)) return true;
    if (o4 === 0 && onSegment(b1, a2, b2) && !pointsEqual(a2, b1) && !pointsEqual(a2, b2)) return true;

    if (o1 === 0 && o2 === 0 && o3 === 0 && o4 === 0) {
      return colinearOverlap(a1, a2, b1, b2);
    }
    return false;
  }

  function isSelfIntersecting(points) {
    var n = points.length;
    if (n < 4) return false;
    for (var i = 0; i < n; i++) {
      var a1 = points[i];
      var a2 = points[(i + 1) % n];
      for (var j = i + 1; j < n; j++) {
        var b1 = points[j];
        var b2 = points[(j + 1) % n];
        if (i === j || j === i + 1 || i === j + 1 || (i === 0 && j === n - 1) || (j === 0 && i === n - 1)) {
          continue;
        }
        if (segmentsIntersect(a1, a2, b1, b2)) {
          return true;
        }
      }
    }
    return false;
  }

  // ==== スナップ処理 ====
  function getAllVertices() {
    var nodes = [];
    for (var i = 0; i < polygons.length; i++) {
      var poly = polygons[i];
      for (var k = 0; k < poly.coords.length; k++) {
        var c = poly.coords[k];
        nodes.push(L.latLng(c[0], c[1]));
      }
    }
    for (var j = 0; j < currentVertices.length; j++) {
      var v = currentVertices[j];
      nodes.push(L.latLng(v.lat, v.lon));
    }
    return nodes;
  }

  function findSnap(latlng) {
    var p = map.latLngToLayerPoint(latlng);
    var nearest = null;
    var minDist = Infinity;
    var nodes = getAllVertices();
    for (var i = 0; i < nodes.length; i++) {
      var q = map.latLngToLayerPoint(nodes[i]);
      var dist = p.distanceTo(q);
      if (dist <= SNAP_PX && dist < minDist) {
        minDist = dist;
        nearest = nodes[i];
      }
    }
    return nearest;
  }

  // ==== マップイベント ====
  map.on('click', function(e) {
    addVertex(e.latlng);
  });

  map.on('contextmenu', function(e) { // 右クリック=スナップ
    if (e.originalEvent) e.originalEvent.preventDefault();
    var snapped = findSnap(e.latlng);
    if (snapped) {
      addVertex(snapped);
    }
  });

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      removeLastVertex();
    }
  });

  // ==== UI ボタン ====
  document.getElementById('btnClearCurrent').onclick = function() {
    resetCurrent();
  };

  document.getElementById('btnAdd').onclick = function() {
    var name = (document.getElementById('pname').value || 'polygon').trim();
    if (currentVertices.length < 3) {
      alert('3点以上でポリゴンを登録してください。');
      return;
    }
    if (isSelfIntersecting(currentVertices)) {
      alert('ポリゴンが自己交差しています。このポリゴンは無効です。');
      resetCurrent();
      return;
    }
    var coords = [];
    for (var i = 0; i < currentVertices.length; i++) {
      coords.push([currentVertices[i].lat, currentVertices[i].lon]); // [lat, lon]
    }

    var replaced = false;
    for (var j = 0; j < polygons.length; j++) {
      if (polygons[j].name === name) {
        polygons[j] = { name: name, coords: coords };
        replaced = true;
        break;
      }
    }
    if (!replaced) {
      polygons.push({ name: name, coords: coords });
    }

    resetCurrent();
    refreshPolygons();

    document.getElementById('pname').value = '';
  };

  document.getElementById('btnSave').onclick = async function() {
    if (!polygons.length) {
      alert('保存するポリゴンがありません。');
      return;
    }
    var fname = 'polygon_data.csv';

    var lines = [];
    for (var i = 0; i < polygons.length; i++) {
      var poly = polygons[i];
      var parts = [poly.name || 'polygon'];
      for (var k = 0; k < poly.coords.length; k++) {
        var c = poly.coords[k]; // [lat, lon]
        parts.push(String(c[1])); // lon
        parts.push(String(c[0])); // lat
      }
      lines.push(parts.join(','));
    }
    var csvContent = lines.join('\\n');

    // UTF-8 BOM付きでダウンロード（Excel 文字化け対策）
    var bom = new Uint8Array([0xEF, 0xBB, 0xBF]);
    var blob = new Blob([bom, csvContent], { type: 'text/csv;charset=utf-8;' });
    try {
      if ('showSaveFilePicker' in window) {
        const handle = await window.showSaveFilePicker({
          suggestedName: fname,
          types: [
            {
              description: 'CSV ファイル',
              accept: { 'text/csv': ['.csv'] }
            }
          ]
        });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
      } else {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = fname;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      console.error(e);
    }
  };

  function initMode() {
    var msg = 'ポリゴンデータを読み込みますか？\n[OK]：ポリゴンデータを読み込む\n[キャンセル]：新規作成';
    if (!window.confirm(msg)) {
      polygons = [];
      refreshPolygons();
      return;
    }

    var fileInput = document.getElementById('fileInput');
    fileInput.onchange = function(evt) {
      var file = evt.target.files[0];
      if (!file) {
        polygons = [];
        refreshPolygons();
        return;
      }
      var reader = new FileReader();
      reader.onload = function(e) {
        var text = e.target.result;
        polygons = parseCsvText(text);
        resetCurrent();
        refreshPolygons();
      };
      reader.readAsText(file, 'utf-8');
    };
    fileInput.click();
  }

  // ==== 初期表示 ====
  initMode();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(
        INDEX_HTML,
        polygons=[],
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
    parser.add_argument("--outdir", type=str, default=str(Path(__file__).parent), help="保存先フォルダ")
    parser.add_argument("--filename", type=str, default="polygon_data.csv", help="保存ファイル名")
    parser.add_argument("--port", type=int, default=5010)
    args = parser.parse_args()

    global OUTDIR, DEFAULT_FILENAME
    OUTDIR = Path(args.outdir).expanduser().resolve()
    OUTDIR.mkdir(parents=True, exist_ok=True)

    DEFAULT_FILENAME = args.filename.strip() or "polygon_data.csv"

    url = f"http://127.0.0.1:{args.port}/"
    threading.Timer(0.5, _open_browser, args=(url,)).start()
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
