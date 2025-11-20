from pathlib import Path
import webbrowser

# ===== 設定 =====
CROSSROAD_ID = "001"
OUTPUT_DIR = Path(__file__).parent / "crossroads"
HTML_FILENAME = f"crossroad{CROSSROAD_ID}.html"

INITIAL_LAT = 35.069095
INITIAL_LON = 134.004512
INITIAL_ZOOM = 16

# ===== HTMLテンプレート（f-string & {{ }} エスケープ版） =====
HTML_TEMPLATE = f"""<!DOCTYPE html>
<html lang=\"ja\">
<head>
  <meta charset=\"utf-8\">
  <title>Crossroad Sampler</title>

  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\"
    integrity=\"sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=\" crossorigin=\"\"/>

  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"
    integrity=\"sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=\" crossorigin=\"\"></script>

  <script src=\"https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js\"></script>

  <style>
    html, body {{ height: 100%; margin: 0; padding: 0; }}
    #map {{ width: 100%; height: 100%; }}
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
    .toolbar button {{ margin-right: 6px; }}
  </style>
</head>

<body>
  <div id=\"map\"></div>

  <div class=\"toolbar\">
    <div><strong>交差点ID: {CROSSROAD_ID}</strong></div>
    <div style=\"margin-top:4px;\">
      出力ファイル名：
      <input id=\"outputName\" type=\"text\"
             value=\"crossroad{CROSSROAD_ID}\"
             style=\"width: 140px;\" />
    </div>
    <div style=\"margin-top:4px;\">
      <button onclick=\"saveCsv()\">保存</button>
      <button onclick=\"clearAll()\">全消去</button>
    </div>
    <div style=\"margin-top:4px;\">左クリック：中心 / 方向追加　右クリック：方向削除</div>
  </div>

  <script>
    var map = L.map('map', {{ preferCanvas: true }}).setView([{INITIAL_LAT}, {INITIAL_LON}], {INITIAL_ZOOM});

    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '© OpenStreetMap contributors'
    }}).addTo(map);

    var centerMarker = null;
    var centerLatLng = null;
    var branchMarkers = [];
    var branchLines = [];
    var canvasRenderer = L.canvas();

    // 左クリック
    map.on('click', function(e) {{
      if (!centerMarker) {{
        centerLatLng = e.latlng;
        centerMarker = L.marker(e.latlng).addTo(map).bindPopup(\"Centre\").openPopup();
      }} else {{
        // branchMarkers.length + 1 を 1,2,3,... の枝番号として振る
        var no = branchMarkers.length + 1;
        var m = L.marker(e.latlng).addTo(map).bindPopup(\"方向 \" + no);
        branchMarkers.push(m);

        var poly = L.polyline([centerLatLng, e.latlng], {{ weight: 4, renderer: canvasRenderer }}).addTo(map);
        poly.bindTooltip(String(no), {{ permanent: true, direction: \"center\" }});
        branchLines.push(poly);
      }}
    }});

    // 右クリックで直前削除
    map.on('contextmenu', function(e) {{
      if (branchMarkers.length > 0) {{
        map.removeLayer(branchMarkers.pop());
        map.removeLayer(branchLines.pop());
      }}
    }});

    // 方位角計算
    function bearing(lat1, lon1, lat2, lon2) {{
      let toRad = d => d * Math.PI / 180;
      let toDeg = r => r * 180 / Math.PI;

      let φ1 = toRad(lat1);
      let φ2 = toRad(lat2);
      let λ = toRad(lon2 - lon1);

      let x = Math.sin(λ) * Math.cos(φ2);
      let y = Math.cos(φ1) * Math.sin(φ2) -
              Math.sin(φ1) * Math.cos(φ2) * Math.cos(λ);

      let θ = Math.atan2(x, y);
      return (toDeg(θ) + 360) % 360;
    }}

    // 地図のスクリーンキャプチャを JPG で保存
    function saveMapJpg(baseName) {{
      var mapDiv = document.getElementById(\"map\");
      if (!window.html2canvas) {{
        console.error('html2canvas が見つかりません');
        return;
      }}
      if (!mapDiv) {{
        console.error('#map 要素が見つかりません');
        return;
      }}

      html2canvas(mapDiv, {{ useCORS: true, backgroundColor: null, logging: false }}).then(function(canvas) {{
        canvas.toBlob(function(blob) {{
          if (!blob) {{
            console.error('Canvas から Blob を生成できませんでした');
            return;
          }}
          var urlImg = URL.createObjectURL(blob);
          var aImg = document.createElement(\"a\");
          aImg.href = urlImg;
          aImg.download = baseName + \".jpg\";
          document.body.appendChild(aImg);
          aImg.click();
          document.body.removeChild(aImg);
          URL.revokeObjectURL(urlImg);
        }}, \"image/jpeg\", 0.9);
      }}).catch(function(err) {{
        console.error('地図キャプチャ中にエラーが発生しました', err);
      }});
    }}

    // CSV保存
    function saveCsv() {{
      if (!centerLatLng) {{
        alert(\"先に中心を指定してください\");
        return;
      }}
      if (branchMarkers.length === 0) {{
        alert(\"方向を追加してください\");
        return;
      }}

      var nameInput = document.getElementById(\"outputName\");
      var baseName = (nameInput && nameInput.value.trim()) || \"crossroad{CROSSROAD_ID}\";

      let rows = [\"crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg\"];
      let lon = centerLatLng.lng.toFixed(8);
      let lat = centerLatLng.lat.toFixed(8);

      for (let i = 0; i < branchMarkers.length; i++) {{
        let p = branchMarkers[i].getLatLng();
        let deg = bearing(centerLatLng.lat, centerLatLng.lng, p.lat, p.lng).toFixed(2);
        rows.push(\"{CROSSROAD_ID},\" + lon + \",\" + lat + \",\" + (i + 1) + \",,\" + deg);
      }}

      let blob = new Blob([rows.join(\"\\r\\n\")], {{ type: \"text/csv\" }});
      let url = URL.createObjectURL(blob);
      let a = document.createElement(\"a\");
      a.href = url;
      a.download = baseName + \".csv\";
      a.click();
      URL.revokeObjectURL(url);

      saveMapJpg(baseName);
    }}

    // 全消去
    function clearAll() {{
      if (centerMarker) {{
        map.removeLayer(centerMarker);
        centerMarker = null;
      }}
      centerLatLng = null;

      for (let m of branchMarkers) map.removeLayer(m);
      for (let l of branchLines) map.removeLayer(l);

      branchMarkers = [];
      branchLines = [];
    }}
  </script>
</body>
</html>
"""

def main():
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    html_path = OUTPUT_DIR / HTML_FILENAME
    html_path.write_text(HTML_TEMPLATE, encoding="utf-8")
    print(f"[OK] {html_path} を生成しました")
    webbrowser.open(html_path.as_uri())

if __name__ == "__main__":
    main()
