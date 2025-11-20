"""Leaflet-based crossroad sampler without server-side dependencies.

Run the script to generate an interactive HTML map that lets you pick a
crossroad center and branch directions, then download the definition as CSV
entirely from the browser. Configuration is defined at the top of this script;
no command-line arguments or Flask server are needed.
"""

from __future__ import annotations

from pathlib import Path
import webbrowser

# ===== 設定値 =====
CROSSROAD_ID = "001"  # 出力する交差点ID
OUTPUT_DIR = Path(__file__).parent / "crossroads"  # HTMLを置くフォルダ
HTML_FILENAME = f"crossroad{CROSSROAD_ID}.html"  # 例: crossroad001.html

# 初期表示位置（中心座標とズームレベル）
INITIAL_LAT = 35.069095
INITIAL_LON = 134.004512
INITIAL_ZOOM = 16


HTML_TEMPLATE = f"""<!doctype html>
<html lang=\"ja\">
<head>
  <meta charset=\"utf-8\">
  <title>Crossroad Sampler</title>
  <link
    rel=\"stylesheet\"
    href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\"
    integrity=\"sha256-sA+4J5J1JdG3cGzGItL0AO4V0Tg7pR0YkG5xyM7uL8k=\"
    crossorigin=\"\" />
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    body {{ font-family: sans-serif; }}
    .toolbar {{
      position: absolute;
      top: 10px; left: 10px;
      z-index: 1000;
      background: #fff;
      padding: 8px 12px;
      border-radius: 6px;
      box-shadow: 0 2px 6px rgba(0, 0, 0, .2);
      font-size: 13px;
    }}
    .toolbar button {{ margin-right: 6px; }}
    .status {{ margin-left: 8px; color: #007b00; }}
    .branch-label {{
      background: #111;
      color: #fff;
      padding: 4px 6px;
      border-radius: 4px;
      font-weight: bold;
      font-size: 12px;
      border: 1px solid #fff;
    }}
    .arrow-head {{
      color: #111;
      font-size: 14px;
      transform-origin: center center;
    }}
  </style>
</head>
<body>
  <div class=\"toolbar\">
    <div><strong>交差点ID: {CROSSROAD_ID}</strong></div>
    <div style=\"margin-top: 6px;\">
      <button id=\"saveBtn\">保存</button>
      <button id=\"clearBtn\">全消去</button>
      <span class=\"status\" id=\"status\"></span>
    </div>
    <div style=\"margin-top: 4px;\">左クリック: 中心（初回）/方向追加、右クリック: 直前の方向削除</div>
  </div>
  <div id=\"map\"></div>

  <script
    src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"
    integrity=\"sha256-o9N1j6kG8z2v1kO0pznP6rk6JwG06fOl4p3pMG2kR28=\"
    crossorigin=\"\"></script>
  <script>
    const CROSSROAD_ID = "{CROSSROAD_ID}";
    const CSV_FILENAME = `crossroad${{CROSSROAD_ID}}.csv`;
    const INITIAL_CENTER = [{INITIAL_LAT}, {INITIAL_LON}];
    const INITIAL_ZOOM = {INITIAL_ZOOM};
    const MAX_BRANCHES = 5;

    let centerLatLng = INITIAL_CENTER ? [...INITIAL_CENTER] : null;
    let centerMarker = null;
    let branchMarkers = [];
    let branchLines = [];
    let branches = [];

    const map = L.map('map').setView(centerLatLng || [35.0, 135.0], INITIAL_ZOOM);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);

    function bearingDeg(lat1, lon1, lat2, lon2) {{
      const toRad = x => x * Math.PI / 180.0;
      const toDeg = x => x * 180.0 / Math.PI;
      const φ1 = toRad(lat1);
      const φ2 = toRad(lat2);
      const λ1 = toRad(lon1);
      const λ2 = toRad(lon2);
      const dλ = λ2 - λ1;
      const x = Math.sin(dλ) * Math.cos(φ2);
      const y = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(dλ);
      let brng = toDeg(Math.atan2(x, y));
      brng = (brng + 360.0) % 360.0;
      return brng;
    }}

    function setCenter(latlng) {{
      centerLatLng = [latlng.lat, latlng.lng];
      if (centerMarker) {{ centerMarker.remove(); }}
      const icon = L.divIcon({{
        html: '<div style="background:#d7263d;color:#fff;padding:8px 10px;border-radius:50%;border:2px solid #fff;font-weight:bold;">C</div>',
        className: '',
        iconAnchor: [12, 12]
      }});
      centerMarker = L.marker(latlng, {{ icon }}).addTo(map);
      redrawBranches();
      updateStatus('中心点を設定しました。方向を追加してください。');
    }}

    function clearBranches() {{
      branchMarkers.forEach(m => m.remove());
      branchLines.forEach(l => l.remove());
      branchMarkers = [];
      branchLines = [];
    }}

    function redrawBranches() {{
      clearBranches();
      if (!centerLatLng) {{ return; }}
      branches.forEach((b, idx) => {{
        const line = L.polyline([centerLatLng, [b.lat, b.lon]], {{ color: '#111', weight: 4, opacity: 0.9 }}).addTo(map);
        branchLines.push(line);

        const dir = bearingDeg(centerLatLng[0], centerLatLng[1], b.lat, b.lon);
        const arrow = L.marker([b.lat, b.lon], {{
          icon: L.divIcon({{
            html: `<div class="arrow-head" style="transform: rotate(${{dir}}deg);">▲</div>`,
            className: ''
          }}),
          interactive: false
        }}).addTo(map);
        const label = L.marker([b.lat, b.lon], {{
          icon: L.divIcon({{
            html: `<div class="branch-label">${{idx + 1}}</div>`,
            className: ''
          }}),
          interactive: false
        }}).addTo(map);
        branchMarkers.push(arrow, label);
      }});
    }}

    function addBranch(latlng) {{
      if (!centerLatLng) {{
        setCenter(latlng);
        return;
      }}
      if (branches.length >= MAX_BRANCHES) {{
        alert(`方向は最大 ${{MAX_BRANCHES}} 本までです`);
        return;
      }}
      branches.push({{ lat: latlng.lat, lon: latlng.lng }});
      redrawBranches();
      updateStatus(`方向数: ${{branches.length}}`);
    }}

    function removeLastBranch() {{
      if (branches.length === 0) {{ return; }}
      branches.pop();
      redrawBranches();
      updateStatus(`方向数: ${{branches.length}}`);
    }}

    function resetAll() {{
      branches = [];
      clearBranches();
      if (centerMarker) {{ centerMarker.remove(); centerMarker = null; }}
      centerLatLng = null;
      map.setView(INITIAL_CENTER, INITIAL_ZOOM);
      updateStatus('初期化しました。中心点を指定してください。');
    }}

    function updateStatus(msg) {{
      document.getElementById('status').innerText = msg || '';
    }}

    function saveCsv() {{
      if (!centerLatLng) {{
        alert('中心点を先に指定してください');
        return;
      }}
      if (branches.length === 0) {{
        alert('方向を最低1本指定してください');
        return;
      }}
      const header = 'crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg';
      const rows = branches.map((b, idx) => {{
        const dir = bearingDeg(centerLatLng[0], centerLatLng[1], b.lat, b.lon);
        return [
          CROSSROAD_ID,
          centerLatLng[1],
          centerLatLng[0],
          idx + 1,
          '',
          dir.toFixed(6)
        ].join(',');
      }});
      const csv = [header, ...rows].join('\n');
      const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8;' }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = CSV_FILENAME;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      updateStatus(`${{CSV_FILENAME}} をダウンロードしました`);
    }}

    document.getElementById('saveBtn').addEventListener('click', saveCsv);
    document.getElementById('clearBtn').addEventListener('click', resetAll);

    map.on('click', (e) => addBranch(e.latlng));
    map.on('contextmenu', (e) => {{ e.preventDefault(); removeLastBranch(); }});

    if (centerLatLng) {{
      setCenter({{ lat: centerLatLng[0], lng: centerLatLng[1] }});
    }} else {{
      updateStatus('中心点をクリックで指定してください');
    }}
  </script>
</body>
</html>
"""


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUT_DIR / HTML_FILENAME
    html_path.write_text(HTML_TEMPLATE, encoding="utf-8")
    webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    main()
