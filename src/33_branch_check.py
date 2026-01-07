import sys
import json
import math
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QGridLayout,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView


# -----------------------------
# Utilities
# -----------------------------
def read_csv_safely(path: str) -> pd.DataFrame:
    encodings = ["cp932", "shift_jis", "utf-8"]
    last_err = None
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"CSVの読み込みに失敗しました（encoding候補={encodings}）。最後のエラー: {last_err}")


def meters_to_deg(lat: float, dx_m: float, dy_m: float) -> Tuple[float, float]:
    """
    dx_m: 東方向（+） meters
    dy_m: 北方向（+） meters
    returns (dlat, dlon)
    """
    # 近似（100mスケール用途）
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))
    dlat = dy_m / m_per_deg_lat
    dlon = dx_m / m_per_deg_lon if m_per_deg_lon != 0 else 0.0
    return dlat, dlon


def ensure_columns(df: pd.DataFrame, cols: List[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"必須列が見つかりません: {missing}\nCSV列一覧: {list(df.columns)}")


# -----------------------------
# Leaflet HTML (embedded)
# -----------------------------
LEAFLET_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Branch Check</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body { height: 100%; margin: 0; }
    #map { height: 100%; width: 100%; }
    .branch-label {
      background: rgba(255,255,255,0.85);
      border: 1px solid rgba(0,0,0,0.25);
      border-radius: 10px;
      padding: 2px 6px;
      font-size: 12px;
      font-family: sans-serif;
      white-space: nowrap;
    }
    .trip-label {
      background: rgba(255,255,255,0.90);
      border: 1px solid rgba(0,0,0,0.25);
      border-radius: 10px;
      padding: 2px 6px;
      font-size: 12px;
      font-family: sans-serif;
      white-space: nowrap;
    }
  </style>
</head>
<body>
<div id="map"></div>
<script>
  let map = null;
  let base = null;

  let centerMarker = null;
  let centerCircle = null;

  let branchLayer = L.layerGroup();
  let tripLayer = L.layerGroup();

  let animTimer = null;
  let animMarker = null;

  function initMap(centerLat, centerLon, zoom){
    if (map) return;
    map = L.map('map', { zoomControl: true });
    base = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 20,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    branchLayer.addTo(map);
    tripLayer.addTo(map);

    map.setView([centerLat, centerLon], zoom);

    centerMarker = L.circleMarker([centerLat, centerLon], {radius: 6}).addTo(map);
    centerCircle = L.circle([centerLat, centerLon], {radius: 100}).addTo(map);
  }

  function clearLayer(layer){
    layer.clearLayers();
  }

  function setBranchRays(rays){
    // rays: [{label, lat1, lon1, lat2, lon2}, ...]
    clearLayer(branchLayer);
    if (!map) return;

    rays.forEach(r => {
      const line = L.polyline([[r.lat1, r.lon1], [r.lat2, r.lon2]], {dashArray: '6 6'}).addTo(branchLayer);
      const midLat = (r.lat1 + r.lat2)/2.0;
      const midLon = (r.lon1 + r.lon2)/2.0;
      L.marker([midLat, midLon], {
        icon: L.divIcon({className: 'branch-label', html: `枝${r.label}`})
      }).addTo(branchLayer);
    });
  }

  function stopAnim(){
    if (animTimer){
      clearInterval(animTimer);
      animTimer = null;
    }
    if (animMarker){
      tripLayer.removeLayer(animMarker);
      animMarker = null;
    }
  }

  function showTrip(tr){
    // tr: {center:{lat,lon}, start:{lat,lon}, end:{lat,lon}, in_branch, out_branch, in_diff, out_diff}
    if (!map) initMap(tr.center.lat, tr.center.lon, 19);

    // 100mに寄せる（fitBoundsで軽く固定）
    const dlat = 100.0 / 111320.0;
    const dlon = 100.0 / (111320.0 * Math.cos(tr.center.lat * Math.PI/180.0));
    const b = L.latLngBounds(
      [tr.center.lat - dlat, tr.center.lon - dlon],
      [tr.center.lat + dlat, tr.center.lon + dlon]
    );
    map.fitBounds(b, {padding:[20,20]});

    clearLayer(tripLayer);
    stopAnim();

    // center circle & marker refresh
    if (centerMarker) map.removeLayer(centerMarker);
    if (centerCircle) map.removeLayer(centerCircle);
    centerMarker = L.circleMarker([tr.center.lat, tr.center.lon], {radius: 6}).addTo(map);
    centerCircle = L.circle([tr.center.lat, tr.center.lon], {radius: 100}).addTo(map);

    // line
    const line = L.polyline(
      [[tr.start.lat, tr.start.lon], [tr.end.lat, tr.end.lon]],
      {}
    ).addTo(tripLayer);

    // start/end markers
    const startM = L.circleMarker([tr.start.lat, tr.start.lon], {radius: 6}).addTo(tripLayer);
    const endM   = L.circleMarker([tr.end.lat, tr.end.lon], {radius: 6}).addTo(tripLayer);

    // labels
    L.marker([tr.start.lat, tr.start.lon], {
      icon: L.divIcon({className: 'trip-label', html: `IN:枝${tr.in_branch} (Δ${tr.in_diff}°)`})
    }).addTo(tripLayer);

    L.marker([tr.end.lat, tr.end.lon], {
      icon: L.divIcon({className: 'trip-label', html: `OUT:枝${tr.out_branch} (Δ${tr.out_diff}°)`})
    }).addTo(tripLayer);

    // animation: move marker along start->end (simple interpolation)
    const steps = 60;
    let i = 0;
    animMarker = L.circleMarker([tr.start.lat, tr.start.lon], {radius: 7}).addTo(tripLayer);

    animTimer = setInterval(() => {
      i += 1;
      if (i > steps) { i = 0; }
      const t = i / steps;
      const lat = tr.start.lat + (tr.end.lat - tr.start.lat) * t;
      const lon = tr.start.lon + (tr.end.lon - tr.start.lon) * t;
      animMarker.setLatLng([lat, lon]);
    }, 40);
  }

  // expose
  window._branchCheck = {
    initMap,
    setBranchRays,
    showTrip
  };
</script>
</body>
</html>
"""


# -----------------------------
# Main GUI
# -----------------------------
REQUIRED_COLS = [
    "交差点ファイル名",
    "運行日",
    "曜日",
    "自動車の種別",
    "用途",
    "流入枝番",
    "流出枝番",
    "流入角度差(deg)",
    "流出角度差(deg)",
    "角度算出方式",
    "計測距離(m)",
    "所要時間(s)",
    "交差点通過速度(km/h)",
    "計測開始_経度(補間)",
    "計測開始_緯度(補間)",
    "計測終了_経度(補間)",
    "計測終了_緯度(補間)",
    "最近接線分_前点_経度",
    "最近接線分_前点_緯度",
    "最近接線分_後点_経度",
    "最近接線分_後点_緯度",
    "最近接線分_t(0-1)",
]

DISPLAY_COLS_IN_TABLE = [
    "運行日",
    "曜日",
    "運行ID" if True else None,
    "トリップID" if True else None,
    "自動車の種別",
    "用途",
    "流入枝番",
    "流出枝番",
    "流入角度差(deg)",
    "流出角度差(deg)",
    "交差点通過速度(km/h)",
]
DISPLAY_COLS_IN_TABLE = [c for c in DISPLAY_COLS_IN_TABLE if c is not None]


DETAIL_FIELDS = [
    ("交差点ファイル名", "交差点ファイル名"),
    ("運行日", "運行日"),
    ("曜日", "曜日"),
    ("自動車の種別", "自動車の種別"),
    ("用途", "用途"),
    ("流入枝番", "流入枝番"),
    ("流出枝番", "流出枝番"),
    ("流入角度差(deg)", "流入角度差(deg)"),
    ("流出角度差(deg)", "流出角度差(deg)"),
    ("角度算出方式", "角度算出方式"),
    ("計測距離(m)", "計測距離(m)"),
    ("所要時間(s)", "所要時間(s)"),
    ("交差点通過速度(km/h)", "交差点通過速度(km/h)"),
]


class BranchCheckWindow(QMainWindow):
    def __init__(self, csv_path: str):
        super().__init__()
        self.setWindowTitle("33_branch_check - 枝判定 目視チェッカー")
        self.resize(1400, 900)

        self.csv_path = csv_path
        self.df = read_csv_safely(csv_path)

        ensure_columns(self.df, REQUIRED_COLS)

        # 数値列をなるべく数値化
        for c in ["流入角度差(deg)", "流出角度差(deg)", "計測距離(m)", "所要時間(s)", "交差点通過速度(km/h)",
                  "計測開始_経度(補間)", "計測開始_緯度(補間)", "計測終了_経度(補間)", "計測終了_緯度(補間)",
                  "最近接線分_前点_経度", "最近接線分_前点_緯度", "最近接線分_後点_経度", "最近接線分_後点_緯度",
                  "最近接線分_t(0-1)"]:
            self.df[c] = pd.to_numeric(self.df[c], errors="coerce")

        self.df = self.df.dropna(subset=[
            "計測開始_経度(補間)", "計測開始_緯度(補間)", "計測終了_経度(補間)", "計測終了_緯度(補間)",
            "最近接線分_前点_経度", "最近接線分_前点_緯度", "最近接線分_後点_経度", "最近接線分_後点_緯度",
            "最近接線分_t(0-1)",
        ]).reset_index(drop=True)

        # 交差点中心推定（最近接点の中央値）
        t = self.df["最近接線分_t(0-1)"].to_numpy()
        lon1 = self.df["最近接線分_前点_経度"].to_numpy()
        lat1 = self.df["最近接線分_前点_緯度"].to_numpy()
        lon2 = self.df["最近接線分_後点_経度"].to_numpy()
        lat2 = self.df["最近接線分_後点_緯度"].to_numpy()

        lon_closest = lon1 + t * (lon2 - lon1)
        lat_closest = lat1 + t * (lat2 - lat1)

        self.center_lon = float(np.nanmedian(lon_closest))
        self.center_lat = float(np.nanmedian(lat_closest))

        # 枝レイ推定（流入枝番ごと：中心→開始点 代表方向）
        self.branch_rays = self._compute_branch_rays()

        # UI
        self._build_ui()

        # Map init
        self._init_map()

        # 初期選択
        if len(self.df) > 0:
            self.table.selectRow(0)
            self._on_selection_changed()

    def _compute_branch_rays(self) -> List[Dict[str, Any]]:
        rays = []
        lat0 = self.center_lat
        lon0 = self.center_lon

        # inflow branches from start points
        start_lon = self.df["計測開始_経度(補間)"].to_numpy()
        start_lat = self.df["計測開始_緯度(補間)"].to_numpy()
        in_branch = self.df["流入枝番"].astype(str).to_numpy()

        # 方向ベクトル（deg上の差分をm換算して角度を取る）
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))

        for b in sorted(set(in_branch), key=lambda x: (len(x), x)):
            mask = (in_branch == b)
            if mask.sum() < 5:
                continue
            dlon = (start_lon[mask] - lon0) * m_per_deg_lon
            dlat = (start_lat[mask] - lat0) * m_per_deg_lat
            ang = np.arctan2(dlat, dlon)  # -pi..pi
            # circular median-ish: take median of sin/cos
            vx = float(np.nanmedian(np.cos(ang)))
            vy = float(np.nanmedian(np.sin(ang)))
            norm = math.hypot(vx, vy)
            if norm == 0:
                continue
            vx /= norm
            vy /= norm
            # 120m ray
            dx = vx * 120.0
            dy = vy * 120.0
            dlat_deg, dlon_deg = meters_to_deg(lat0, dx, dy)
            rays.append({
                "label": b,
                "lat1": lat0,
                "lon1": lon0,
                "lat2": lat0 + dlat_deg,
                "lon2": lon0 + dlon_deg,
            })
        return rays

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: table
        left = QWidget()
        left_layout = QVBoxLayout(left)

        top_bar = QHBoxLayout()
        self.lbl_file = QLabel(f"CSV: {self.csv_path}")
        self.lbl_count = QLabel("")
        self.btn_reload = QPushButton("CSV再読込…")
        self.btn_reload.clicked.connect(self._reload_csv_dialog)
        top_bar.addWidget(self.lbl_file, 5)
        top_bar.addWidget(self.lbl_count, 1)
        top_bar.addWidget(self.btn_reload, 0)
        left_layout.addLayout(top_bar)

        self.table = QTableWidget()
        self.table.setColumnCount(len(DISPLAY_COLS_IN_TABLE))
        self.table.setHorizontalHeaderLabels(DISPLAY_COLS_IN_TABLE)
        self.table.setRowCount(len(self.df))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setAlternatingRowColors(True)

        for r in range(len(self.df)):
            row = self.df.iloc[r]
            for c_idx, c_name in enumerate(DISPLAY_COLS_IN_TABLE):
                val = row.get(c_name, "")
                item = QTableWidgetItem("" if pd.isna(val) else str(val))
                if c_name in ["流入角度差(deg)", "流出角度差(deg)"]:
                    try:
                        v = float(val)
                        # 角度差が大きいものを目立たせる（>=45deg）
                        if v >= 45.0:
                            item.setBackground(Qt.GlobalColor.yellow)
                    except Exception:
                        pass
                self.table.setItem(r, c_idx, item)

        left_layout.addWidget(self.table)

        # Right: map + details
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.web = QWebEngineView()
        self.web.setHtml(LEAFLET_HTML)
        right_layout.addWidget(self.web, 7)

        detail = QWidget()
        grid = QGridLayout(detail)
        self.detail_labels: Dict[str, QLabel] = {}

        for i, (title, key) in enumerate(DETAIL_FIELDS):
            lbl_t = QLabel(title)
            lbl_t.setStyleSheet("font-weight: bold;")
            lbl_v = QLabel("-")
            lbl_v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(lbl_t, i // 2, (i % 2) * 2 + 0)
            grid.addWidget(lbl_v, i // 2, (i % 2) * 2 + 1)
            self.detail_labels[key] = lbl_v

        right_layout.addWidget(detail, 3)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([520, 880])

        layout = QVBoxLayout(root)
        layout.addWidget(splitter)

        self._update_count_label()

    def _update_count_label(self):
        self.lbl_count.setText(f"{len(self.df)} trips")

    def _init_map(self):
        # init + branch rays
        payload = {
            "lat": self.center_lat,
            "lon": self.center_lon,
            "zoom": 19
        }
        rays = self.branch_rays
        js1 = f"window._branchCheck.initMap({payload['lat']}, {payload['lon']}, {payload['zoom']});"
        js2 = f"window._branchCheck.setBranchRays({json.dumps(rays)});"
        self.web.page().runJavaScript(js1)
        self.web.page().runJavaScript(js2)

    def _selected_row_index(self) -> Optional[int]:
        items = self.table.selectedItems()
        if not items:
            return None
        return items[0].row()

    def _on_selection_changed(self):
        idx = self._selected_row_index()
        if idx is None or idx < 0 or idx >= len(self.df):
            return

        row = self.df.iloc[idx]

        # details
        for _, key in DETAIL_FIELDS:
            v = row.get(key, "")
            self.detail_labels[key].setText("" if pd.isna(v) else str(v))

        # map payload
        tr = {
            "center": {"lat": self.center_lat, "lon": self.center_lon},
            "start": {
                "lat": float(row["計測開始_緯度(補間)"]),
                "lon": float(row["計測開始_経度(補間)"]),
            },
            "end": {
                "lat": float(row["計測終了_緯度(補間)"]),
                "lon": float(row["計測終了_経度(補間)"]),
            },
            "in_branch": str(row["流入枝番"]),
            "out_branch": str(row["流出枝番"]),
            "in_diff": ("" if pd.isna(row["流入角度差(deg)"]) else f"{float(row['流入角度差(deg)']):.1f}"),
            "out_diff": ("" if pd.isna(row["流出角度差(deg)"]) else f"{float(row['流出角度差(deg)']):.1f}"),
        }
        js = f"window._branchCheck.showTrip({json.dumps(tr)});"
        self.web.page().runJavaScript(js)

        self.statusBar().showMessage(f"Selected: {idx+1}/{len(self.df)}")

    def _reload_csv_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "CSVを選択",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        # 再起動方式（簡単・確実）
        QMessageBox.information(self, "再読込", "選択したCSVで再起動します。")
        python = sys.executable
        import subprocess
        subprocess.Popen([python, sys.argv[0], "--csv", path])
        QApplication.quit()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, default="")
    args = parser.parse_args()

    app = QApplication(sys.argv)

    csv_path = args.csv
    if not csv_path:
        csv_path, _ = QFileDialog.getOpenFileName(
            None,
            "03higashiitinomiya_performance.csv を選択",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
    if not csv_path:
        return

    try:
        w = BranchCheckWindow(csv_path)
        w.show()
        sys.exit(app.exec())
    except Exception as e:
        QMessageBox.critical(None, "エラー", str(e))
        raise


if __name__ == "__main__":
    main()
