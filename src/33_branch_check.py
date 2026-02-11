import sys
import json
import math
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from PyQt6.QtCore import Qt
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEngineSettings
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
        message_lines = [
            f"必須列が見つかりません: {missing}",
            f"CSV列一覧: {list(df.columns)}",
            "入力は *_performance.csv を想定しています。",
            "第2スクリーニング工程の出力を指定してください。",
        ]
        raise ValueError("\n".join(message_lines))


def find_point_csv(perf_csv_path: str) -> Optional[Path]:
    perf_path = Path(perf_csv_path)
    perf_dir = perf_path.parent
    proj_dir = perf_dir.parent
    cross_name = perf_path.stem
    if cross_name.endswith("_performance"):
        cross_name = cross_name[: -len("_performance")]

    point_dirs = [
        proj_dir / "11_交差点(Point)データ",
        perf_dir / "11_交差点(Point)データ",
    ]

    for point_dir in point_dirs:
        if not point_dir.exists():
            continue
        candidates = [
            point_dir / f"{cross_name}.csv",
            point_dir / f"{cross_name}.CSV",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        lowered = cross_name.lower()
        for candidate in point_dir.glob("*.csv"):
            if lowered in candidate.stem.lower():
                return candidate
        for candidate in point_dir.glob("*.CSV"):
            if lowered in candidate.stem.lower():
                return candidate
    return None


def first_numeric_value(df: pd.DataFrame, candidates: List[str]) -> Optional[float]:
    for col in candidates:
        if col in df.columns:
            series = pd.to_numeric(df[col], errors="coerce")
            val = series.dropna()
            if not val.empty:
                return float(val.iloc[0])
    return None


def find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    return None


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
  <link rel="stylesheet" href="leaflet/leaflet.css"/>
  <script src="leaflet/leaflet.js"></script>
  <style>
    html, body { height: 100%; margin: 0; }
    #map { height: 100%; width: 100%; }
    .branch-label, .trip-label {
      background: #fff;
      border: 2px solid #d00000;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-family: sans-serif;
      font-weight: 700;
      white-space: nowrap;
      max-width: 180px;
      overflow: hidden;
      text-overflow: ellipsis;
      box-shadow: 0 1px 2px rgba(0,0,0,0.15);
      pointer-events: none;
    }
    .branch-label.point {
      color: #a00000;
    }
    .trip-label {
      color: #a00000;
    }
  </style>
</head>
<body>
<div id="map"></div>
<script>
  let map = null;
  let base = null;

  let centerMarker = null;
  let calcMarker = null;

  let branchLayer = null;
  let tripLayer = null;
  let branchPoints = [];

  let animTimer = null;
  let animMarker = null;

  function tryAddBaseTiles(){
    if (!map) return;

    // 白背景をデフォルトに（タイルが無い場合でも見やすい）
    const container = map.getContainer();
    container.style.background = '#ffffff';

    // 既存の base があれば消す
    try { if (base) map.removeLayer(base); } catch(e) {}
    base = null;

    const layer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 20,
      attribution: '&copy; OpenStreetMap contributors',
      crossOrigin: true,
      updateWhenIdle: true,
      keepBuffer: 2,
    });

    let okOnce = false;
    let firstOkAt = 0;

    layer.on('tileload', () => {
      if (!okOnce) {
        okOnce = true;
        firstOkAt = Date.now();
      }
    });

    // tileerror は数えるだけ。成功していれば剥がさない
    let errCount = 0;
    layer.on('tileerror', () => {
      errCount += 1;
    });

    layer.addTo(map);

    // 6秒待っても1枚も成功しなければ、オフライン扱いでタイルを外す
    setTimeout(() => {
      if (!okOnce) {
        try { map.removeLayer(layer); } catch(e) {}
        base = null;
      } else {
        base = layer;
      }
    }, 6000);
  }

  function initMap(centerLat, centerLon, zoom){
    ensureLayers();
    if (map) return;
    map = L.map('map', { zoomControl: true });

    branchLayer.addTo(map);
    tripLayer.addTo(map);

    map.setView([centerLat, centerLon], zoom || 17);

    centerMarker = L.circleMarker([centerLat, centerLon], {
      radius: 7, color: 'red', fillColor: 'red', fillOpacity: 1.0
    }).addTo(map);

    tryAddBaseTiles();
  }

  function clearLayer(layer){
    if (!layer) return;
    if (typeof layer.clearLayers === 'function'){
      layer.clearLayers();
    }
  }

  function ensureLayers(){
    if (!branchLayer || typeof branchLayer.addTo !== 'function') {
      branchLayer = L.layerGroup();
    }
    if (!tripLayer || typeof tripLayer.addTo !== 'function') {
      tripLayer = L.layerGroup();
    }
  }

  function setBranchRays(rays){
    // rays: [{label, lat1, lon1, lat2, lon2}, ...]
    clearLayer(branchLayer);
    branchPoints = [];
    if (!map) return;

    rays.forEach((r, idx) => {
      branchPoints.push([r.lat1, r.lon1]);
      branchPoints.push([r.lat2, r.lon2]);
      const isPoint = (r.source && r.source === 'point');
      const style = isPoint ? {color: 'red', dashArray: '6 6'} : {dashArray: '6 6'};
      const line = L.polyline([[r.lat1, r.lon1], [r.lat2, r.lon2]], style).addTo(branchLayer);
      const t = 0.82;
      const labelLat = r.lat1 + (r.lat2 - r.lat1) * t;
      const labelLon = r.lon1 + (r.lon2 - r.lon1) * t;
      const jig = (idx % 5) - 2;
      const off = 0.00002 * jig;
      const labelLat2 = labelLat + off;
      const labelLon2 = labelLon - off;
      const labelText = Number.isFinite(Number(r.label)) ? `${parseInt(r.label, 10)}` : `${r.label}`;
      L.marker([labelLat2, labelLon2], {
        icon: L.divIcon({className: isPoint ? 'branch-label point' : 'branch-label', html: `${labelText}`})
      }).addTo(branchLayer);
    });
  }

  function stopAnim(){
    if (animTimer){
      clearInterval(animTimer);
      animTimer = null;
    }
    if (!animMarker){
      return;
    }

    // tripLayer が壊れていても落ちないように、まず map から外す
    try {
      if (map && typeof map.removeLayer === 'function') {
        // map.removeLayer は LayerGroup に入っている marker でも外せる
        map.removeLayer(animMarker);
      }
    } catch(e) {}

    // それでも残る環境向けに、tripLayer が正しければ追加で外す（保険）
    try {
      if (tripLayer && typeof tripLayer.removeLayer === 'function') {
        tripLayer.removeLayer(animMarker);
      }
    } catch(e) {}

    animMarker = null;
  }

  function showTrip(tr){
    // tr: {center_spec:{lat,lon}, center_calc:{lat,lon}, start:{lat,lon}, end:{lat,lon}, ...}
    ensureLayers();
    if (!map) initMap(tr.center_spec.lat, tr.center_spec.lon, 18);

    clearLayer(tripLayer);
    stopAnim();

    // center circle & marker refresh
    if (centerMarker) map.removeLayer(centerMarker);
    if (calcMarker) map.removeLayer(calcMarker);
    centerMarker = L.circleMarker([tr.center_spec.lat, tr.center_spec.lon], {
      radius: 7, color: 'red', fillColor: 'red', fillOpacity: 1.0
    }).addTo(map);
    calcMarker = L.circleMarker([tr.center_calc.lat, tr.center_calc.lon], {radius: 6}).addTo(map);

    // 2-segment line: start -> center_calc -> end
    const line1 = L.polyline([[tr.start.lat, tr.start.lon], [tr.center_calc.lat, tr.center_calc.lon]], {}).addTo(tripLayer);
    const line2 = L.polyline([[tr.center_calc.lat, tr.center_calc.lon], [tr.end.lat, tr.end.lon]], {}).addTo(tripLayer);

    // start/end markers
    const startM = L.circleMarker([tr.start.lat, tr.start.lon], {radius: 6}).addTo(tripLayer);
    const endM   = L.circleMarker([tr.end.lat, tr.end.lon], {radius: 6}).addTo(tripLayer);

    // raw points overlay (optional)
    if (tr.raw_points && tr.raw_points.length >= 2){
      L.polyline(tr.raw_points.map(p => [p.lat, p.lon]), {color: 'black'}).addTo(tripLayer);
      tr.raw_points.forEach((p, idx) => {
        L.circleMarker([p.lat, p.lon], {
          radius: (idx === 4 ? 6 : 4),
          color: 'black',
          fillColor: 'black',
          fillOpacity: 1.0,
        }).addTo(tripLayer);
      });
    }

    const hasCenter = tr.center_calc && Number.isFinite(tr.center_calc.lat) && Number.isFinite(tr.center_calc.lon);
    const destPoint = (origin, bearingDeg, distM) => {
      const R = 6371000.0;
      const toRad = (d) => d * Math.PI / 180.0;
      const toDeg = (r) => r * 180.0 / Math.PI;
      const br = toRad(bearingDeg);
      const lat1 = toRad(origin.lat);
      const lon1 = toRad(origin.lon);
      const dr = distM / R;
      const lat2 = Math.asin(Math.sin(lat1) * Math.cos(dr) + Math.cos(lat1) * Math.sin(dr) * Math.cos(br));
      const lon2 = lon1 + Math.atan2(Math.sin(br) * Math.sin(dr) * Math.cos(lat1), Math.cos(dr) - Math.sin(lat1) * Math.sin(lat2));
      return {lat: toDeg(lat2), lon: toDeg(lon2)};
    };
    const makeRayLabel = (prefix, branch, delta) => {
      const branchText = (branch ?? '') === '' ? '?' : branch;
      if (Number.isFinite(delta)) {
        return `${prefix}:枝${branchText}(Δ${Math.round(delta)}°)`;
      }
      return `${prefix}:枝${branchText}`;
    };

    if (hasCenter && Number.isFinite(tr.in_angle_deg)) {
      const pin = destPoint(tr.center_calc, tr.in_angle_deg, 26.0);
      const pinLabel = destPoint({lat: pin.lat, lon: pin.lon}, (tr.in_angle_deg + 90.0) % 360.0, 6.0);
      L.polyline([[tr.center_calc.lat, tr.center_calc.lon], [pin.lat, pin.lon]], {color:'red', weight:4}).addTo(tripLayer);
      L.marker([pinLabel.lat, pinLabel.lon], {
        icon: L.divIcon({className: 'trip-label', html: makeRayLabel('IN', tr.in_branch, tr.in_delta_deg)}),
        zIndexOffset: 1100,
      }).addTo(tripLayer);
    }
    if (hasCenter && Number.isFinite(tr.out_angle_deg)) {
      const pout = destPoint(tr.center_calc, tr.out_angle_deg, 26.0);
      const poutLabel = destPoint({lat: pout.lat, lon: pout.lon}, (tr.out_angle_deg + 270.0) % 360.0, 6.0);
      L.polyline([[tr.center_calc.lat, tr.center_calc.lon], [pout.lat, pout.lon]], {color:'red', weight:4}).addTo(tripLayer);
      L.marker([poutLabel.lat, poutLabel.lon], {
        icon: L.divIcon({className: 'trip-label', html: makeRayLabel('OUT', tr.out_branch, tr.out_delta_deg)}),
        zIndexOffset: 1100,
      }).addTo(tripLayer);
    }

    // animation: move marker along start->end (simple interpolation)
    const steps = 80;
    let i = 0;
    animMarker = L.circleMarker([tr.start.lat, tr.start.lon], {radius: 7}).addTo(tripLayer);

    function havDist(a, b){
      const R = 6371000.0;
      const toRad = (x)=>x*Math.PI/180.0;
      const dLat = toRad(b.lat - a.lat);
      const dLon = toRad(b.lon - a.lon);
      const la1 = toRad(a.lat);
      const la2 = toRad(b.lat);
      const s = Math.sin(dLat/2)**2 + Math.cos(la1)*Math.cos(la2)*Math.sin(dLon/2)**2;
      return 2*R*Math.atan2(Math.sqrt(s), Math.sqrt(1-s));
    }
    const A = {lat: tr.start.lat, lon: tr.start.lon};
    const C = {lat: tr.center_calc.lat, lon: tr.center_calc.lon};
    const B = {lat: tr.end.lat, lon: tr.end.lon};
    const d1 = havDist(A, C);
    const d2 = havDist(C, B);
    const total = Math.max(1.0, d1 + d2);
    const steps1 = Math.max(1, Math.round(steps * (d1 / total)));
    const steps2 = Math.max(1, steps - steps1);

    animTimer = setInterval(() => {
      i += 1;
      if (i > (steps1 + steps2)) { i = 0; }

      let lat, lon;
      if (i <= steps1){
        const t = i / steps1;
        lat = tr.start.lat + (tr.center_calc.lat - tr.start.lat) * t;
        lon = tr.start.lon + (tr.center_calc.lon - tr.start.lon) * t;
      }else{
        const j = i - steps1;
        const t = j / steps2;
        lat = tr.center_calc.lat + (tr.end.lat - tr.center_calc.lat) * t;
        lon = tr.center_calc.lon + (tr.end.lon - tr.center_calc.lon) * t;
      }
      animMarker.setLatLng([lat, lon]);
    }, 40);

    // view fit: branchPoints + trip points
    try {
      const pts = [];
      if (branchPoints && branchPoints.length) {
        branchPoints.forEach(p => pts.push(p));
      }
      pts.push([tr.start.lat, tr.start.lon]);
      pts.push([tr.center_calc.lat, tr.center_calc.lon]);
      pts.push([tr.end.lat, tr.end.lon]);
      if (tr.raw_points && tr.raw_points.length){
        tr.raw_points.forEach(p => pts.push([p.lat, p.lon]));
      }
      const b = L.latLngBounds(pts);
      map.fitBounds(b.pad(0.25));
    } catch(e) {
      if (hasCenter) map.setView([tr.center_calc.lat, tr.center_calc.lon], 18);
    }
  }

  function bootstrap(){
    if (!window.L){
      document.getElementById('map').innerHTML = 'Leafletの読み込みに失敗しました（JS読込/セキュリティ設定を確認）';
      window._branchCheck = { initMap: ()=>{}, setBranchRays: ()=>{}, showTrip: ()=>{} };
      return;
    }

    ensureLayers();

    // expose
    window._branchCheck = {
      initMap,
      setBranchRays,
      showTrip
    };
  }

  bootstrap();
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
    "計測開始_経度(補間)",
    "計測開始_緯度(補間)",
    "計測終了_経度(補間)",
    "計測終了_緯度(補間)",
    "交差点中心_経度",
    "交差点中心_緯度",
    "算出中心_経度",
    "算出中心_緯度",
]

DISPLAY_COLS_IN_TABLE = [
    "運行日",
    "曜日",
    "運行ID" if True else None,
    "トリップID" if True else None,
    "自動車の種別",
    "用途",
    "所要時間算出可否",
    "遅れ時間(s)",
    "流入枝番",
    "流出枝番",
    "流入角度差(deg)",
    "流出角度差(deg)",
]
DISPLAY_COLS_IN_TABLE = [c for c in DISPLAY_COLS_IN_TABLE if c is not None]


DETAIL_FIELDS = [
    ("交差点ファイル名", "交差点ファイル名"),
    ("運行日", "運行日"),
    ("曜日", "曜日"),
    ("自動車の種別", "自動車の種別"),
    ("用途", "用途"),
    ("所要時間(s)", "所要時間(s)"),
    ("閑散時所要時間(s)", "閑散時所要時間(s)"),
    ("遅れ時間(s)", "遅れ時間(s)"),
    ("所要時間算出可否", "所要時間算出可否"),
    ("所要時間算出不可理由", "所要時間算出不可理由"),
    ("流入枝番", "流入枝番"),
    ("流出枝番", "流出枝番"),
    ("流入角度差(deg)", "流入角度差(deg)"),
    ("流出角度差(deg)", "流出角度差(deg)"),
    ("角度算出方式", "角度算出方式"),
    ("計測距離(m)", "計測距離(m)"),
    ("RAW中央GPS時刻", "【中央】GPS時刻"),
]


class BranchCheckWindow(QMainWindow):
    def __init__(self, csv_path: str):
        super().__init__()
        self.setWindowTitle("33_branch_check - 枝判定 目視チェッカー")
        self.resize(1400, 900)
        self._angle_zero_east_ccw = True

        self.csv_path = csv_path
        self.df = read_csv_safely(csv_path)

        ensure_columns(self.df, REQUIRED_COLS)

        # 数値列をなるべく数値化
        numeric_cols = [
            "流入角度deg",
            "流出角度deg",
            "流入角度差(deg)",
            "流出角度差(deg)",
            "計測距離(m)",
            "所要時間(s)",
            "交差点通過速度(km/h)",
            "閑散時所要時間(s)",
            "遅れ時間(s)",
            "計測開始_経度(補間)",
            "計測開始_緯度(補間)",
            "計測終了_経度(補間)",
            "計測終了_緯度(補間)",
            "交差点中心_経度",
            "交差点中心_緯度",
            "算出中心_経度",
            "算出中心_緯度",
            "中心最近接距離(m)",
        ]
        for c in numeric_cols:
            if c in self.df.columns:
                self.df[c] = pd.to_numeric(self.df[c], errors="coerce")

        self._ensure_speed_column()
        self._sort_trips()

        self.df = self.df.dropna(subset=[
            "計測開始_経度(補間)", "計測開始_緯度(補間)", "計測終了_経度(補間)", "計測終了_緯度(補間)",
            "交差点中心_経度", "交差点中心_緯度", "算出中心_経度", "算出中心_緯度",
        ]).reset_index(drop=True)

        # 交差点中心（パフォーマンスCSV側）
        self.performance_center_lon = float(np.nanmedian(self.df["交差点中心_経度"].to_numpy()))
        self.performance_center_lat = float(np.nanmedian(self.df["交差点中心_緯度"].to_numpy()))

        self.center_lon = self.performance_center_lon
        self.center_lat = self.performance_center_lat

        self.point_df: Optional[pd.DataFrame] = None
        self.point_csv_path: Optional[Path] = None
        self.branch_rays: List[Dict[str, Any]] = []

        self._load_point_data()

        if not self.branch_rays and self.point_df is None:
            # 枝レイ推定（流入枝番ごと：中心→開始点 代表方向）
            self.branch_rays = self._compute_branch_rays()

        # UI
        self._build_ui()

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

    def _load_point_data(self) -> None:
        point_path = find_point_csv(self.csv_path)
        if not point_path:
            QMessageBox.warning(
                self,
                "基準枝なし",
                "Point CSV が見つかりません。基準枝なしモードで起動します。",
            )
            return

        self.point_csv_path = point_path
        try:
            self.point_df = read_csv_safely(str(point_path))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Point CSV 読み込み失敗",
                f"Point CSV の読み込みに失敗しました。\n{point_path}\n{exc}\n基準枝なしモードで起動します。",
            )
            self.point_df = None
            return

        center_lat = first_numeric_value(
            self.point_df,
            ["中心_緯度", "中心緯度", "lat", "緯度", "交差点中心_緯度", "center_lat"],
        )
        center_lon = first_numeric_value(
            self.point_df,
            ["中心_経度", "中心経度", "lon", "経度", "交差点中心_経度", "center_lon"],
        )
        if center_lat is not None and center_lon is not None:
            self.center_lat = center_lat
            self.center_lon = center_lon

        self.branch_rays = self._compute_branch_rays_from_point()
        if not self.branch_rays:
            QMessageBox.warning(
                self,
                "基準枝なし",
                "Point CSV から枝方向が取得できませんでした。基準枝なしモードで起動します。",
            )

    def _compute_branch_rays_from_point(self) -> List[Dict[str, Any]]:
        if self.point_df is None:
            return []

        branch_col = find_column(self.point_df, ["枝番", "branch", "branch_no", "No", "番号"])
        angle_col = find_column(
            self.point_df,
            ["角度", "方位角", "bearing", "azimuth", "F列", "angle_deg", "dir_deg"],
        )

        dx_col = find_column(self.point_df, ["dx", "東西(m)", "東西", "x", "X"])
        dy_col = find_column(self.point_df, ["dy", "南北(m)", "南北", "y", "Y"])

        if angle_col is None and (dx_col is None or dy_col is None):
            return []

        rays = []
        for idx, row in self.point_df.iterrows():
            if branch_col:
                label_val = row.get(branch_col, "")
                label = str(label_val) if not pd.isna(label_val) else str(idx + 1)
            else:
                label = str(idx + 1)

            dx = None
            dy = None
            if angle_col:
                ang_val = pd.to_numeric(row.get(angle_col), errors="coerce")
                if pd.isna(ang_val):
                    continue
                angle_deg = float(ang_val)
                bearing_like = angle_col in ["dir_deg", "bearing", "azimuth", "方位角"]
                if bearing_like:
                    angle_deg = (90.0 - angle_deg) % 360.0
                elif not self._angle_zero_east_ccw:
                    angle_deg = (90.0 - angle_deg) % 360.0
                rad = math.radians(angle_deg)
                dx = math.cos(rad) * 120.0
                dy = math.sin(rad) * 120.0
            else:
                dx_val = pd.to_numeric(row.get(dx_col), errors="coerce")
                dy_val = pd.to_numeric(row.get(dy_col), errors="coerce")
                if pd.isna(dx_val) or pd.isna(dy_val):
                    continue
                norm = math.hypot(float(dx_val), float(dy_val))
                if norm == 0:
                    continue
                dx = float(dx_val) / norm * 120.0
                dy = float(dy_val) / norm * 120.0

            if dx is None or dy is None:
                continue

            dlat_deg, dlon_deg = meters_to_deg(self.center_lat, dx, dy)
            rays.append({
                "label": label,
                "lat1": self.center_lat,
                "lon1": self.center_lon,
                "lat2": self.center_lat + dlat_deg,
                "lon2": self.center_lon + dlon_deg,
                "source": "point",
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
        self.web.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self.web.setHtml(LEAFLET_HTML, QUrl.fromLocalFile(str((Path(__file__).resolve().parent)) + "/"))
        self.web.loadFinished.connect(self._on_web_loaded)
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

    def _on_web_loaded(self, ok: bool) -> None:
        if not ok:
            QMessageBox.warning(self, "地図の読み込み失敗", "地図の読み込みに失敗しました。")
            return
        self._init_map()

    def _ensure_speed_column(self) -> None:
        speed_col = "交差点通過速度(km/h)"
        if speed_col in self.df.columns:
            return

        self.df[speed_col] = np.nan

        if "計測距離(m)" not in self.df.columns or "所要時間(s)" not in self.df.columns:
            return

        dist = pd.to_numeric(self.df["計測距離(m)"], errors="coerce")
        duration = pd.to_numeric(self.df["所要時間(s)"], errors="coerce")
        valid = (dist > 0) & (duration > 0)

        if "所要時間算出可否" in self.df.columns:
            valid = valid & (self.df["所要時間算出可否"] == "OK")

        speed = dist / duration * 3.6
        speed = speed.where(valid, np.nan)
        self.df.loc[:, speed_col] = speed

    def _sort_trips(self) -> None:
        ok_col = "所要時間算出可否"
        delay_col = "遅れ時間(s)"

        self.df["_ok_sort"] = 0
        if ok_col in self.df.columns:
            self.df["_ok_sort"] = (self.df[ok_col] == "OK").astype(int)

        if delay_col in self.df.columns:
            delay_vals = pd.to_numeric(self.df[delay_col], errors="coerce")
            delay_vals = delay_vals.fillna(-np.inf)
        else:
            delay_vals = pd.Series([-np.inf] * len(self.df), index=self.df.index)
        self.df["_delay_sort"] = delay_vals

        self.df = self.df.sort_values(
            by=["_ok_sort", "_delay_sort", "運行日"],
            ascending=[False, False, True],
            kind="mergesort",
        ).drop(columns=["_ok_sort", "_delay_sort"])

    def _update_count_label(self):
        self.lbl_count.setText(f"{len(self.df)} trips")

    def _init_map(self):
        # init + branch rays
        payload = {
            "lat": self.center_lat,
            "lon": self.center_lon,
            "zoom": 18
        }
        rays = self.branch_rays
        js1 = f"window._branchCheck.initMap({payload['lat']}, {payload['lon']}, {payload['zoom']});"
        js2 = f"window._branchCheck.setBranchRays({json.dumps(rays)});"
        self._run_branch_js(js1)
        self._run_branch_js(js2)

    def _run_branch_js(self, js_code: str, retry_ms: int = 120) -> None:
        wrapped = (
            "(function(){"
            "if (window._branchCheck) {"
            f"{js_code}"
            "return true;"
            "}"
            "return false;"
            "})();"
        )

        def _callback(ok):
            if ok:
                return
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(retry_ms, lambda: self.web.page().runJavaScript(wrapped))

        self.web.page().runJavaScript(wrapped, _callback)

    @staticmethod
    def _format_branch(value: Any) -> str:
        num = pd.to_numeric(value, errors="coerce")
        if pd.isna(num):
            text = "" if value is None else str(value).strip()
            return "" if text in {"", "nan", "None"} else text
        return str(int(num))

    @staticmethod
    def _format_day(value: Any) -> str:
        text = "" if value is None else str(value).strip()
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}/{text[4:6]}/{text[6:8]}"
        return text

    @staticmethod
    def _format_timestamp14(value: Any) -> str:
        text = "" if value is None else str(value).strip()
        if len(text) == 14 and text.isdigit():
            return f"{text[:4]}/{text[4:6]}/{text[6:8]} {text[8:10]}:{text[10:12]}:{text[12:14]}"
        return text

    def _selected_row_index(self) -> Optional[int]:
        items = self.table.selectedItems()
        if not items:
            return None
        return items[0].row()

    def _on_selection_changed(self):
        idx = self._selected_row_index()
        if idx is None or idx < 0 or idx >= len(self.df):
            return

        self._run_branch_js(
            f"window._branchCheck.setBranchRays({json.dumps(self.branch_rays)});"
        )

        row = self.df.iloc[idx]

        # details
        for _, key in DETAIL_FIELDS:
            v = row.get(key, "")
            text = "" if pd.isna(v) else str(v)
            if key == "運行日":
                text = self._format_day(v)
            elif key == "【中央】GPS時刻":
                text = self._format_timestamp14(v)
            elif key in {"流入枝番", "流出枝番"}:
                text = self._format_branch(v)
            self.detail_labels[key].setText(text)

        # map payload
        raw_cols = [
            ("point-4経度", "point-4緯度", "point-4GPS時刻"),
            ("point-3経度", "point-3緯度", "point-3GPS時刻"),
            ("point-2経度", "point-2緯度", "point-2GPS時刻"),
            ("point-1経度", "point-1緯度", "point-1GPS時刻"),
            ("【中央】経度", "【中央】緯度", "【中央】GPS時刻"),
            ("point+1経度", "point+1緯度", "point+1GPS時刻"),
            ("point+2経度", "point+2緯度", "point+2GPS時刻"),
            ("point+3経度", "point+3緯度", "point+3GPS時刻"),
            ("point+4経度", "point+4緯度", "point+4GPS時刻"),
        ]
        raw_points = []
        for lon_col, lat_col, _ in raw_cols:
            if lon_col not in self.df.columns or lat_col not in self.df.columns:
                continue
            try:
                lon_val = float(row.get(lon_col, np.nan))
                lat_val = float(row.get(lat_col, np.nan))
                if math.isnan(lon_val) or math.isnan(lat_val):
                    continue
                raw_points.append({"lat": lat_val, "lon": lon_val})
            except Exception:
                continue

        in_angle_deg = pd.to_numeric(row.get("流入角度deg"), errors="coerce") if "流入角度deg" in self.df.columns else np.nan
        out_angle_deg = pd.to_numeric(row.get("流出角度deg"), errors="coerce") if "流出角度deg" in self.df.columns else np.nan
        in_delta_deg = pd.to_numeric(row.get("流入角度差(deg)"), errors="coerce") if "流入角度差(deg)" in self.df.columns else np.nan
        out_delta_deg = pd.to_numeric(row.get("流出角度差(deg)"), errors="coerce") if "流出角度差(deg)" in self.df.columns else np.nan

        tr = {
            "center_spec": {"lat": self.center_lat, "lon": self.center_lon},
            "center_calc": {
                "lat": float(row["算出中心_緯度"]),
                "lon": float(row["算出中心_経度"]),
            },
            "start": {
                "lat": float(row["計測開始_緯度(補間)"]),
                "lon": float(row["計測開始_経度(補間)"]),
            },
            "end": {
                "lat": float(row["計測終了_緯度(補間)"]),
                "lon": float(row["計測終了_経度(補間)"]),
            },
            "in_branch": self._format_branch(row.get("流入枝番")),
            "out_branch": self._format_branch(row.get("流出枝番")),
            "in_angle_deg": (None if pd.isna(in_angle_deg) else float(in_angle_deg)),
            "out_angle_deg": (None if pd.isna(out_angle_deg) else float(out_angle_deg)),
            "in_delta_deg": (None if pd.isna(in_delta_deg) else float(in_delta_deg)),
            "out_delta_deg": (None if pd.isna(out_delta_deg) else float(out_delta_deg)),
            "raw_points": raw_points,
        }
        js = f"window._branchCheck.showTrip({json.dumps(tr)});"
        self._run_branch_js(js)

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
            "交差点パフォーマンスCSV（*_performance.csv）を選択",
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
