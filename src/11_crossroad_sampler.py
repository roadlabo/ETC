# -*- coding: utf-8 -*-
"""
11_crossroad_sampler.py

・Flaskや引数は使わず、単体でHTMLを生成してブラウザで開く
・地図上クリックで 交差点中心＋方向（枝）を指定
・「保存」ボタンで crossroadXXX.csv をローカルにダウンロード
"""

from pathlib import Path
import webbrowser

# ===== 設定値 =====
CROSSROAD_ID = "001"  # 交差点ID
OUTPUT_DIR = Path(__file__).parent / "crossroads"
HTML_FILENAME = f"crossroad{CROSSROAD_ID}.html"

# 初期表示位置（必要に応じて津山駅周辺などに変更）
INITIAL_LAT = 35.069095
INITIAL_LON = 134.004512
INITIAL_ZOOM = 16

# ===== ここから下は基本的にそのままでOK =====
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>Crossroad Sampler</title>
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
    crossorigin=""
  />
  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""
  ></script>
  <style>
    html, body {{
      height: 100%;
      margin: 0;
      padding: 0;
    }}
    #map {{
      width: 100%;
      height: 100%;
    }}
    .toolbar {{
      position: absolute;
      top: 10px;
      left: 10px;
      z-index: 1000;
      background: #fff;
      padding: 10px 14px;
      border-radius: 8px;
      box-shadow: 0 2px 6px rgba(0,0,0,.3);
      font-size: 13px;
    }}
    .toolbar button {{
      margin-right: 6px;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="toolbar">
    <div><strong>交差点ID: {cross_id}</strong></div>
    <div style="margin-top:4px;">
      <button onclick="saveCsv()">保存</button>
      <button onclick="clearAll()">全消去</button>
    </div>
    <div style="margin-top:4px; font-size: 12px;">
      左クリック: 中心（初回）/ 方向追加、右クリック: 直前の方向削除
    </div>
  </div>

  <script>
    // 地図の初期化
    var map = L.map('map').setView([{lat}, {lon}], {zoom});

    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '© OpenStreetMap contributors'
    }}).addTo(map);

    var centerMarker = null;
    var centerLatLng = null;
    var branchMarkers = [];
    var branchLines = [];

    // 左クリック: 中心→方向点
    map.on('click', function(e) {{
      if (!centerMarker) {{
        centerLatLng = e.latlng;
        centerMarker = L.marker(e.latlng, {{
          title: '中心'
        }}).addTo(map).bindPopup('中心').openPopup();
      }} else {{
        var latlng = e.latlng;
        var branchNo = branchMarkers.length + 1;
        var marker = L.marker(latlng, {{
          title: '方向 ' + branchNo
        }}).addTo(map).bindPopup('方向 ' + branchNo);
        branchMarkers.push(marker);

        var line = L.polyline([centerLatLng, latlng], {{
          weight: 4
        }}).addTo(map);
        line.bindTooltip(String(branchNo), {{
          permanent: true,
          direction: 'center'
        }});
        branchLines.push(line);
      }}
    }});

    // 右クリック: 直前の方向削除
    map.on('contextmenu', function(e) {{
      if (branchMarkers.length > 0) {{
        var m = branchMarkers.pop();
        map.removeLayer(m);
      }}
      if (branchLines.length > 0) {{
        var l = branchLines.pop();
        map.removeLayer(l);
      }}
    }});

    // 方位角計算
    function bearingDeg(lat1, lon1, lat2, lon2) {{
      function toRad(x) {{ return x * Math.PI / 180.0; }}
      function toDeg(x) {{ return x * 180.0 / Math.PI; }}
      var phi1 = toRad(lat1);
      var phi2 = toRad(lat2);
      var dLambda = toRad(lon2 - lon1);
      var x = Math.sin(dLambda) * Math.cos(phi2);
      var y = Math.cos(phi1) * Math.sin(phi2) -
              Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLambda);
      var brng = Math.atan2(x, y);
      brng = toDeg(brng);
      brng = (brng + 360.0) % 360.0;
      return brng;
    }}

    // CSV保存
    function saveCsv() {{
      if (!centerLatLng) {{
        alert('先に中心点を指定してください。');
        return;
      }}
      if (branchMarkers.length === 0) {{
        alert('少なくとも1つ以上、方向点を指定してください。');
        return;
      }}

      var rows = [];
      rows.push('crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg');

      var centerLon = centerLatLng.lng.toFixed(8);
      var centerLat = centerLatLng.lat.toFixed(8);

      for (var i = 0; i < branchMarkers.length; i++) {{
        var b = branchMarkers[i].getLatLng();
        var branchNo = i + 1;
        var dir = bearingDeg(centerLatLng.lat, centerLatLng.lng, b.lat, b.lng);
        var line = '{id},' + centerLon + ',' + centerLat + ',' +
                   branchNo + ',' + '' + ',' + dir.toFixed(2);
        rows.push(line);
      }}

      var csvContent = rows.join('\r\n');
      var blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'crossroad{id}.csv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }}

    // 全消去
    function clearAll() {{
      if (centerMarker) {{
        map.removeLayer(centerMarker);
        centerMarker = null;
        centerLatLng = null;
      }}
      while (branchMarkers.length > 0) {{
        map.removeLayer(branchMarkers.pop());
      }}
      while (branchLines.length > 0) {{
        map.removeLayer(branchLines.pop());
      }}
    }}
  </script>
</body>
</html>
""".format(
    lat=INITIAL_LAT,
    lon=INITIAL_LON,
    zoom=INITIAL_ZOOM,
    cross_id=CROSSROAD_ID,
    id=CROSSROAD_ID,
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUT_DIR / HTML_FILENAME
    html_path.write_text(HTML_TEMPLATE, encoding="utf-8")
    print(f"[11_crossroad_sampler] {html_path} を出力しました。")
    webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    main()
