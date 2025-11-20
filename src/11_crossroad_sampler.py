"""Interactive crossroad sampler for defining multi-branch intersections.

This script opens a simple Leaflet map (via Flask) where the user clicks a
crossroad center point and 3–5 branch directions. After saving, two artifacts
are produced in the output directory:

* ``crossroad{id}.csv`` – one row per branch with direction information.
* ``crossroad{id}.html`` – a Folium map visualizing the center and branches.

Usage example:
    python 11_crossroad_sampler.py --id 001 --center-lat 34.xxxx --center-lon 133.xxxx

If the center coordinates are omitted, the first click is treated as the
center. Branches are added by clicking ~200–300 m away from the center for each
approach. A small in-browser form lets you undo the last branch, reset all
branches, and save the definition.
"""

from __future__ import annotations

import argparse
import csv
import threading
import webbrowser
from pathlib import Path
from typing import Dict, List, Tuple

import folium
from folium.features import DivIcon
from folium.plugins import PolyLineTextPath
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _bearing_deg(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Return bearing degrees (north=0, east=90) from p1 to p2."""

    from math import atan2, cos, pi, radians, sin

    lat1, lon1 = map(radians, p1)
    lat2, lon2 = map(radians, p2)
    dlon = lon2 - lon1
    x = sin(dlon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    bearing = (atan2(x, y) * 180.0 / pi + 360.0) % 360.0
    return bearing


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------


_TEMPLATE = """
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Crossroad Sampler</title>
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-sA+4J5J1JdG3cGzGItL0AO4V0Tg7pR0YkG5xyM7uL8k="
    crossorigin="" />
  <style>
    body { margin: 0; font-family: sans-serif; }
    #map { height: 90vh; }
    #panel { padding: 8px 12px; background: #f7f7f7; border-bottom: 1px solid #ccc; }
    #panel button { margin-right: 8px; }
    .branch-item { margin-top: 4px; }
  </style>
</head>
<body>
  <div id="panel">
    <strong>交差点ID:</strong> {{ crossroad_id }} |
    <span>クリックで中心点→枝(3〜5本)を定義。枝を追加すると番号が付きます。</span>
    <button id="undo">直前の枝を取り消し</button>
    <button id="reset">全リセット</button>
    <button id="save">保存</button>
    <span id="status" style="margin-left:12px;color:#007b00;"></span>
    <div id="branch-list"></div>
  </div>
  <div id="map"></div>

  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-o9N1j6kG8z2v1kO0pznP6rk6JwG06fOl4p3pMG2kR28="
    crossorigin=""></script>
  <script>
    const centerDefault = {{ default_center | tojson }};
    let centerMarker = null;
    let centerLatLng = centerDefault;
    let branches = [];
    let polylines = [];
    let map = L.map('map').setView(centerLatLng || [35.0, 135.0], 16);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    function setStatus(message, color = '#007b00') {
      const status = document.getElementById('status');
      status.innerText = message;
      status.style.color = color;
    }

    function updateBranchList() {
      const list = document.getElementById('branch-list');
      list.innerHTML = branches.map((b, idx) => {
        const name = b.name ? ` (${b.name})` : '';
        const lat = Number(b.lat).toFixed(5);
        const lon = Number(b.lon).toFixed(5);
        return `<div class="branch-item">枝${idx + 1}${name} – ${lat}, ${lon}</div>`;
      }).join('');
    }

    function drawPolylines() {
      polylines.forEach(pl => pl.remove());
      polylines = [];
      if (!centerLatLng) return;
      branches.forEach((b, idx) => {
        const line = L.polyline([centerLatLng, [b.lat, b.lon]], {color: 'red', weight: 5}).addTo(map);
        line.bindTooltip(`${idx + 1}`, {permanent: true, offset: [0, -10], direction: 'center'});
        polylines.push(line);
      });
    }

    function setCenter(latlng) {
      centerLatLng = [latlng.lat, latlng.lng];
      if (centerMarker) { centerMarker.remove(); }
      centerMarker = L.marker(latlng, {draggable: true}).addTo(map).bindPopup('中心点').openPopup();
      centerMarker.on('dragend', (e) => {
        centerLatLng = [e.target.getLatLng().lat, e.target.getLatLng().lng];
        drawPolylines();
      });
      drawPolylines();
    }

    if (centerLatLng) {
      setCenter({lat: centerLatLng[0], lng: centerLatLng[1]});
    }

    map.on('click', (e) => {
      if (!centerLatLng) {
        setCenter(e.latlng);
        setStatus('中心点を設定しました。枝をクリックで追加してください。');
        return;
      }
      const name = prompt('枝ラベルを入力（任意）', '');
      branches.push({lat: e.latlng.lat, lon: e.latlng.lng, name: name || ''});
      updateBranchList();
      drawPolylines();
      setStatus(`枝を追加しました（合計 ${branches.length} 本）。`);
    });

    document.getElementById('undo').addEventListener('click', () => {
      branches.pop();
      updateBranchList();
      drawPolylines();
      setStatus('直前の枝を取り消しました。');
    });

    document.getElementById('reset').addEventListener('click', () => {
      branches = [];
      updateBranchList();
      drawPolylines();
      setStatus('中心点を除いて全てリセットしました。必要なら中心点もクリックで再設定できます。');
    });

    document.getElementById('save').addEventListener('click', async () => {
      if (!centerLatLng) { alert('中心点を先に設定してください'); return; }
      if (branches.length < 3 || branches.length > 5) {
        alert('枝の本数は3〜5本で指定してください');
        setStatus('枝の本数は3〜5本で指定してください', '#b00020');
        return;
      }
      const payload = { center: {lat: centerLatLng[0], lon: centerLatLng[1]}, branches: branches };
      const res = await fetch('/save', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
      const data = await res.json();
      setStatus(data.message || '保存しました');
      if (data.ok) { alert('保存しました: ' + data.output_csv); }
    });

  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Core save logic
# ---------------------------------------------------------------------------


def _write_csv(
    csv_path: Path, crossroad_id: str, center: Tuple[float, float], branches: List[Dict[str, object]]
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["crossroad_id", "center_lon", "center_lat", "branch_no", "branch_name", "dir_deg"])
        for idx, branch in enumerate(branches, start=1):
            dir_deg = _bearing_deg((center[0], center[1]), (float(branch["lat"]), float(branch["lon"])) )
            writer.writerow(
                [crossroad_id, center[1], center[0], idx, branch.get("name", ""), f"{dir_deg:.6f}"]
            )


def _build_map(
    html_path: Path,
    crossroad_id: str,
    center: Tuple[float, float],
    branches: List[Dict[str, object]],
) -> None:
    m = folium.Map(location=[center[0], center[1]], zoom_start=18, tiles="OpenStreetMap")
    folium.Marker([center[0], center[1]], tooltip="center", popup=f"crossroad {crossroad_id}").add_to(m)

    for idx, branch in enumerate(branches, start=1):
        b_lat = float(branch["lat"])
        b_lon = float(branch["lon"])
        dir_deg = _bearing_deg((center[0], center[1]), (b_lat, b_lon))
        line = folium.PolyLine(
            [[center[0], center[1]], [b_lat, b_lon]], color="red", weight=6, opacity=0.8
        ).add_to(m)
        PolyLineTextPath(line, "➤ " * 6, repeat=True, offset=8, attributes={"fill": "red", "font-size": "14"}).add_to(m)
        folium.Marker(
            [b_lat, b_lon],
            tooltip=f"branch {idx}",
            popup=f"branch {idx}: {branch.get('name', '')} dir={dir_deg:.1f}°",
        ).add_to(m)
        folium.Marker(
            [b_lat, b_lon],
            icon=DivIcon(
                icon_size=(20, 20),
                icon_anchor=(0, 0),
                html=f"<div style='font-size:14pt;color:red;font-weight:bold;'>{idx}</div>",
            ),
        ).add_to(m)

    html_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(html_path))


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():  # pragma: no cover - UI endpoint
    return render_template_string(
        _TEMPLATE,
        crossroad_id=app.config["CROSSROAD_ID"],
        default_center=app.config.get("DEFAULT_CENTER"),
    )


@app.route("/save", methods=["POST"])
def save():  # pragma: no cover - UI endpoint
    data = request.get_json(force=True)
    center = data.get("center") or {}
    branches = data.get("branches") or []
    if not center or "lat" not in center or "lon" not in center:
        return jsonify({"ok": False, "message": "中心点が指定されていません"}), 400
    if not (3 <= len(branches) <= 5):
        return jsonify({"ok": False, "message": "枝は3〜5本で指定してください"}), 400

    c_lat = float(center["lat"])
    c_lon = float(center["lon"])
    center_tuple = (c_lat, c_lon)
    crossroad_id = app.config["CROSSROAD_ID"]
    out_dir = Path(app.config["OUTPUT_DIR"])
    csv_path = out_dir / f"crossroad{crossroad_id}.csv"
    html_path = out_dir / f"crossroad{crossroad_id}.html"

    _write_csv(csv_path, crossroad_id, center_tuple, branches)
    _build_map(html_path, crossroad_id, center_tuple, branches)

    return jsonify(
        {
            "ok": True,
            "message": f"crossroad{crossroad_id}.csv / crossroad{crossroad_id}.html を出力しました。",
            "output_csv": str(csv_path),
            "output_html": str(html_path),
        }
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="交差点をクリックで定義するサンプラー")
    parser.add_argument("--id", required=True, help="交差点ID（ゼロ埋め3桁を推奨）")
    parser.add_argument("--output-dir", default=Path("."), type=Path, help="出力先ディレクトリ")
    parser.add_argument("--center-lat", type=float, help="初期中心 緯度")
    parser.add_argument("--center-lon", type=float, help="初期中心 経度")
    parser.add_argument("--port", type=int, default=5000, help="Flask ポート")
    parser.add_argument("--open-browser", action="store_true", help="起動時にブラウザを自動で開く")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    crossroad_id = str(args.id).zfill(3)
    default_center = None
    if args.center_lat is not None and args.center_lon is not None:
        default_center = [args.center_lat, args.center_lon]

    app.config["CROSSROAD_ID"] = crossroad_id
    app.config["OUTPUT_DIR"] = str(args.output_dir)
    app.config["DEFAULT_CENTER"] = default_center

    url = f"http://127.0.0.1:{args.port}/"
    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    print(f"[11_crossroad_sampler] crossroad{crossroad_id} の定義を開始します…")
    app.run(host="0.0.0.0", port=args.port, debug=False, use_reloader=False)
    print(
        f"[11_crossroad_sampler] crossroad{crossroad_id}.csv / crossroad{crossroad_id}.html を出力しました。"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
