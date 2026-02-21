from __future__ import annotations

import math
import socket
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import folium
import numpy as np
import pandas as pd

NOGUI_MODE = "--nogui" in sys.argv[1:]

if not NOGUI_MODE:
    from PyQt6.QtCore import QPropertyAnimation, Qt, QTimer, QUrl
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import (
        QApplication,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QGraphicsOpacityEffect,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSplitter,
        QVBoxLayout,
        QWidget,
        QListWidget,
        QListWidgetItem,
        QProgressDialog,
    )
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
else:
    Figure = FigureCanvas = object
    QPropertyAnimation = QTimer = Qt = QUrl = QWebEngineSettings = QWebEngineView = object
    QApplication = QFileDialog = QGraphicsOpacityEffect = QHBoxLayout = QLabel = QMainWindow = object
    QMessageBox = QPixmap = QPushButton = QSplitter = QVBoxLayout = QWidget = object
    QListWidget = QListWidgetItem = QProgressDialog = object


# ============================================================
# 05: route mapper (33-like UI)
#  - embedded Leaflet map (NO browser tabs)
#  - raw data plot (speed vs time/index)
# ============================================================

# CSV column indices (0-based)  ※05の既存仕様を維持
LON_COL = 15    # 16列目（経度）
LAT_COL = 14    # 15列目（緯度）
FLAG_COL = 12   # 13列目（フラグ）
TYPE_COL = 4    # 種別
USE_COL = 5     # 用途
TIME_COL = 6    # GPS時刻
SPEED_COL = 18  # 速度

DELIM = ","

# Geographic filter for Japan
MIN_LON, MAX_LON = 120.0, 150.0
MIN_LAT, MAX_LAT = 20.0, 50.0

TYPE_MAP = {0: "軽二輪", 1: "大型", 2: "普通", 3: "小型", 4: "軽自動車"}
USE_MAP = {0: "未使用", 1: "乗用", 2: "貨物", 3: "特殊", 4: "乗合"}


def parse_gps_time(val: object) -> Optional[datetime]:
    s = str(val).strip()
    if not s or not s.isdigit():
        return None
    try:
        if len(s) >= 14:
            return datetime.strptime(s[:14], "%Y%m%d%H%M%S")
        if len(s) >= 12:
            return datetime.strptime(s[:12], "%Y%m%d%H%M")
        if len(s) >= 10:
            return datetime.strptime(s[:10], "%Y%m%d%H")
    except ValueError:
        return None
    return None


def fmt_range(dmin: Optional[datetime], dmax: Optional[datetime]) -> str:
    if not dmin or not dmax:
        return "-"
    return (
        f"{dmin.year}年{dmin.month}月{dmin.day}日{dmin.hour}時{dmin.minute}分"
        f"～{dmax.month}月{dmax.day}日{dmax.hour}時{dmax.minute}分"
    )


def summarize_set(series: Sequence[object], mapping: dict[int, str]) -> str:
    labels: set[str] = set()
    for value in series:
        label = "その他"
        try:
            ivalue = int(float(value))
        except (TypeError, ValueError):
            ivalue = None
        if ivalue in mapping:
            label = mapping[ivalue]
        labels.add(label)
    return "-" if not labels else ", ".join(sorted(labels))


def _swap_latlon_if_needed(df: pd.DataFrame) -> pd.DataFrame:
    # 05の既存ロジック維持（lat/lonが入れ替わっている場合の救済）
    if (
        df["lon"].between(20, 50).mean() > 0.8
        and df["lat"].between(120, 150).mean() > 0.8
    ):
        df[["lon", "lat"]] = df[["lat", "lon"]]
    return df


def read_route_data(csv_path: Path) -> pd.DataFrame:
    usecols = [LON_COL, LAT_COL, FLAG_COL, TYPE_COL, USE_COL, TIME_COL, SPEED_COL]
    df = pd.read_csv(
        csv_path,
        header=None,
        usecols=usecols,
        dtype=str,
        engine="c",
        sep=DELIM,
    )
    df = df[usecols].copy()
    df.columns = ["lon", "lat", "flag", "type", "use", "time", "speed"]

    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["flag"] = pd.to_numeric(df["flag"], errors="coerce")
    df["speed"] = pd.to_numeric(df["speed"], errors="coerce")

    df = _swap_latlon_if_needed(df)

    df = df.dropna(subset=["lon", "lat", "flag"])
    df = df[(df["lon"].between(MIN_LON, MAX_LON)) & (df["lat"].between(MIN_LAT, MAX_LAT))]
    df["flag"] = df["flag"].astype(int)
    return df.reset_index(drop=True)


def split_segments(points: List[Tuple[float, float, int]]) -> List[List[Tuple[float, float]]]:
    # points: [(lat,lon,flag), ...]
    segs: List[List[Tuple[float, float]]] = []
    seg: List[Tuple[float, float]] = []
    prev_flag: Optional[int] = None

    for lat, lon, flag in points:
        pt = (lat, lon)
        if not seg:
            seg.append(pt)
        else:
            # 05既存ルール：
            #  prev_flag==1（終点）または flag==0（始点） で区切る
            if prev_flag == 1 or flag == 0:
                if len(seg) >= 2:
                    segs.append(seg)
                seg = [pt]
            else:
                seg.append(pt)
        prev_flag = flag

    if len(seg) >= 2:
        segs.append(seg)

    return segs


def is_internet_available(timeout_sec: float = 1.0) -> bool:
    try:
        with socket.create_connection(("tile.openstreetmap.org", 443), timeout=timeout_sec):
            return True
    except Exception:
        return False


def make_busy_dialog(title: str, text: str) -> QProgressDialog:
    dlg = QProgressDialog(text, None, 0, 0)  # 0..0 = 無限進捗（くるくる）
    dlg.setWindowTitle(title)
    dlg.setMinimumDuration(0)
    dlg.setCancelButton(None)
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.show()
    QApplication.processEvents()
    return dlg


def close_busy_dialog(dlg: Optional[QProgressDialog]) -> None:
    if dlg is None:
        return
    try:
        dlg.close()
    except Exception:
        pass


LEAFLET_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Route Mapper</title>
  <link rel="stylesheet" href="leaflet/leaflet.css"/>
  <script src="leaflet/leaflet.js"></script>
  <style>
    html, body { height: 100%; margin: 0; }
    #map { height: 100%; width: 100%; }
    .neon-glow {
      filter: drop-shadow(0 0 6px rgba(0, 246, 255, 0.9))
              drop-shadow(0 0 14px rgba(0, 246, 255, 0.55));
    }
    .neon-trail {
      filter: drop-shadow(0 0 4px rgba(0, 246, 255, 0.55));
    }
    .label {
      background: #fff;
      border: 2px solid #111;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 12px;
      font-family: sans-serif;
      font-weight: 800;
      white-space: nowrap;
      max-width: none;
      overflow: visible;
      text-overflow: clip;
      box-shadow: 0 1px 2px rgba(0,0,0,0.15);
      pointer-events: none;
    }
    .label-start { border-color: red; }
    .label-goal  { border-color: blue; }
  </style>
</head>
<body>
<div id="map"></div>
<script>
  let map = null;
  let base = null;
  let routeLayer = null;

  // animation (raw trajectory)
  let animMarker = null;
  let animTimer = null;
  let animPath = [];
  let animIndex = 0;
  let animSegments = [];
  let animTotalDistance = 0;
  let prevRatio = 0;

  const NEON_CYAN = '#00f6ff';
  const NEON_PINK = '#ff2bd6';
  const NEON_LIME = '#7CFF00';

  const TRAIL_MAX = 28;
  const TRAIL_RADIUS = 5;
  const TRAIL_FADE = 0.85;
  let trailLayer = null;
  let trailMarkers = [];

  function ensureTrailLayer(){
    if (!trailLayer || typeof trailLayer.addTo !== 'function'){
      trailLayer = L.layerGroup();
    }
    if (map && !map.hasLayer(trailLayer)){
      trailLayer.addTo(map);
    }
  }

  function clearTrail(){
    ensureTrailLayer();
    trailMarkers = [];
    try { trailLayer.clearLayers(); } catch(e) {}
  }

  function pushTrail(latlng){
    ensureTrailLayer();
    const m = L.circleMarker(latlng, {
      radius: TRAIL_RADIUS,
      color: NEON_CYAN,
      weight: 1,
      fillColor: NEON_CYAN,
      fillOpacity: 0.6,
      pane: 'markerPane'
    });
    trailLayer.addLayer(m);
    trailMarkers.unshift(m);

    const trailEl = m.getElement?.();
    if (trailEl) trailEl.classList.add('neon-trail');

    while (trailMarkers.length > TRAIL_MAX) {
      const old = trailMarkers.pop();
      trailLayer.removeLayer(old);
    }

    for (let i = 0; i < trailMarkers.length; i++) {
      const alpha = Math.pow(TRAIL_FADE, i);
      trailMarkers[i].setStyle({
        fillOpacity: 0.55 * alpha,
        opacity: 0.8 * alpha
      });
    }
  }

  function interpolatePathByDistance(dist){
    if (!animPath.length) return null;
    if (animPath.length === 1 || dist <= 0) return animPath[0];
    if (dist >= animTotalDistance) return animPath[animPath.length - 1];

    for (let i = 0; i < animSegments.length; i++) {
      const seg = animSegments[i];
      if (dist <= seg.accum) {
        const prevAccum = i === 0 ? 0 : animSegments[i - 1].accum;
        const local = dist - prevAccum;
        const ratio = seg.length > 0 ? (local / seg.length) : 0;
        const lat = seg.start[0] + (seg.end[0] - seg.start[0]) * ratio;
        const lon = seg.start[1] + (seg.end[1] - seg.start[1]) * ratio;
        return [lat, lon];
      }
    }
    return animPath[animPath.length - 1];
  }

  function stopAnimation(){
    try { if (animTimer) cancelAnimationFrame(animTimer); } catch(e) {}
    animTimer = null;
    animPath = [];
    animIndex = 0;
    animSegments = [];
    animTotalDistance = 0;
    prevRatio = 0;
    try { if (animMarker && routeLayer) routeLayer.removeLayer(animMarker); } catch(e) {}
    animMarker = null;
    clearTrail();
  }

  function startAnimationFromPoints(points){
    stopAnimation();

    if (!points || points.length < 2) return;

    // points: [{lat,lon,...}, ...] の順番＝生データ順
    animPath = points
      .filter(p => p && typeof p.lat === 'number' && typeof p.lon === 'number')
      .map(p => [p.lat, p.lon]);

    if (animPath.length < 2) return;

    animIndex = 0;

    animSegments = [];
    animTotalDistance = 0;
    for (let i = 1; i < animPath.length; i++) {
      const start = animPath[i - 1];
      const end = animPath[i];
      const dLat = end[0] - start[0];
      const dLon = end[1] - start[1];
      const length = Math.hypot(dLat, dLon);
      if (length <= 0) continue;
      animTotalDistance += length;
      animSegments.push({ start, end, length, accum: animTotalDistance });
    }

    if (!animSegments.length || animTotalDistance <= 0) return;
    prevRatio = 0;

    // 動く球（軌跡上を高速で一方向）
    // ※色は固定値。必要なら後で調整可能
    animMarker = L.circleMarker(animPath[0], {
      radius: 7,
      color: NEON_CYAN,
      weight: 2,
      fill: true,
      fillColor: NEON_CYAN,
      fillOpacity: 1.0,
      pane: 'markerPane'
    }).addTo(routeLayer);
    const ballEl = animMarker.getElement?.();
    if (ballEl) ballEl.classList.add('neon-glow');
    pushTrail(animPath[0]);

    const durationMs = 2400;
    const t0 = performance.now();
    function step(now){
      if (!animMarker || animPath.length < 2) return;

      const elapsed = now - t0;
      const ratio = (elapsed % durationMs) / durationMs;
      if (ratio < prevRatio) {
        clearTrail();
      }
      prevRatio = ratio;

      const dist = animTotalDistance * ratio;
      const ll = interpolatePathByDistance(dist);
      if (ll) {
        animMarker.setLatLng(ll);
        pushTrail(ll);
      }
      animTimer = requestAnimationFrame(step);
    }
    animTimer = requestAnimationFrame(step);
  }

  function ensureLayer(){
    if (!routeLayer || typeof routeLayer.addTo !== 'function'){
      routeLayer = L.layerGroup();
    }
    if (map && !map.hasLayer(routeLayer)){
      routeLayer.addTo(map);
    }
  }

  function clearLayer(){
    stopAnimation();
    ensureLayer();
    ensureTrailLayer();
    try { routeLayer.clearLayers(); } catch(e) {}
    clearTrail();
  }

  function tryAddBaseTiles(){
    if (!map) return;

    const container = map.getContainer();
    container.style.background = '#ffffff';

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
    layer.on('tileload', () => { okOnce = true; });

    layer.addTo(map);

    setTimeout(() => {
      if (!okOnce) {
        try { map.removeLayer(layer); } catch(e) {}
        base = null;
      } else {
        base = layer;
      }
    }, 6000);
  }

  function initMap(lat, lon, zoom){
    if (map) return;
    map = L.map('map', { zoomControl: true });
    ensureLayer();
    map.setView([lat, lon], zoom || 12);
    tryAddBaseTiles();
  }

  function _fmtTooltip(t, s){
    const tt = (t && (''+t).length) ? (''+t) : '-';
    const ss = (s === null || s === undefined || isNaN(Number(s))) ? '-' : (Math.round(Number(s)) + 'km/h');
    return 'GPS時刻: ' + tt + '<br/>速度: ' + ss;
  }

  function showRoute(payload){
    // payload:
    // { center:{lat,lon}, points:[{lat,lon,flag,time_text,speed}], segments:[[[lat,lon],...],...], bounds:[[s,w],[n,e]] }
    ensureLayer();
    if (!map){
      initMap(payload.center.lat, payload.center.lon, 12);
    }

    clearLayer();

    // segments (black line)
    const lineStyle = {color:'black', weight:2, opacity:1.0, dashArray:'6 6'};
    (payload.segments || []).forEach(seg => {
      if (seg.length >= 2){
        L.polyline(seg, lineStyle).addTo(routeLayer);
      }
    });

    // points (markers)
    (payload.points || []).forEach(p => {
      const tip = _fmtTooltip(p.time_text, p.speed);

      if (p.flag === 0){
        // Start: single marker (no double drawing)
        const iconS = L.divIcon({
          className: 'label label-start',
          html: 'S'
        });
        const m = L.marker([p.lat, p.lon], { icon: iconS }).addTo(routeLayer);
        m.bindTooltip(tip);
      } else if (p.flag === 1){
        // Goal: single marker (no double drawing)
        const iconG = L.divIcon({
          className: 'label label-goal',
          html: 'G'
        });
        const m = L.marker([p.lat, p.lon], { icon: iconG }).addTo(routeLayer);
        m.bindTooltip(tip);
      } else {
        // Pass point
        L.circleMarker([p.lat, p.lon], {radius:4, color:'black', weight:1, fill:true, fillColor:'black', fillOpacity:1.0})
          .bindTooltip(tip).addTo(routeLayer);
      }
    });

    // fit bounds
    try {
      if (payload.bounds && payload.bounds.length === 2){
        const b = L.latLngBounds(payload.bounds);
        map.fitBounds(b, {animate:false, padding:[10,10]});
      } else {
        map.setView([payload.center.lat, payload.center.lon], 14);
      }
    } catch(e){
      map.setView([payload.center.lat, payload.center.lon], 14);
    }

    // animate raw trajectory (one-way, fast)
    try {
      startAnimationFromPoints(payload.points || []);
    } catch(e) {}
  }

  function bootstrap(){
    if (!window.L){
      document.getElementById('map').innerHTML = 'Leafletの読み込みに失敗しました（leaflet/配置 or セキュリティ設定）';
      window._routeMapper = { initMap: ()=>{}, showRoute: ()=>{} };
      return;
    }
    window._routeMapper = { initMap, showRoute };
  }

  bootstrap();
</script>
</body>
</html>
"""


class SpeedPlot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.fig = Figure(figsize=(5, 3), dpi=100)
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

        self._set_empty("No data")

    def _set_empty(self, msg: str) -> None:
        self.ax.clear()
        self.ax.set_title(msg)
        self.ax.set_xlabel("time / index")
        self.ax.set_ylabel("speed (km/h)")
        self.canvas.draw_idle()

    def update_plot(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            self._set_empty("No data")
            return

        speed = pd.to_numeric(df["speed"], errors="coerce")
        if speed.dropna().empty:
            self._set_empty("No speed column / all NaN")
            return

        # x: time if parseable else index
        times = [parse_gps_time(v) for v in df["time"].tolist()]
        ok = [t is not None for t in times]

        self.ax.clear()
        self.ax.set_ylabel("speed (km/h)")

        if any(ok):
            x = [t if t else None for t in times]
            # matplotlib は None を含むと落ちやすいので、index fallback
            if sum(ok) >= 2 and all((t is None) or isinstance(t, datetime) for t in x):
                x2 = [t if t else datetime.fromtimestamp(0) for t in x]
                self.ax.plot(x2, speed.to_numpy(), linewidth=1)
                self.ax.set_xlabel("time")
                self.fig.autofmt_xdate()
            else:
                self.ax.plot(np.arange(len(speed)), speed.to_numpy(), linewidth=1)
                self.ax.set_xlabel("index")
        else:
            self.ax.plot(np.arange(len(speed)), speed.to_numpy(), linewidth=1)
            self.ax.set_xlabel("index")

        self.ax.grid(True)
        self.canvas.draw_idle()


class RouteMapperWindow(QMainWindow):
    def __init__(self, directory: Path, pattern: str) -> None:
        super().__init__()
        self.setWindowTitle("第１・２スクリーニングデータ　ビューアー")
        self.resize(1500, 900)

        self.directory = directory
        self.pattern = pattern

        self.files: List[Path] = []
        self._msg_loading = None
        self.current_df: Optional[pd.DataFrame] = None

        self._build_ui()
        self._corner_logo_visible = False
        self._pix_small = None
        QTimer.singleShot(0, self._init_logo_overlay)
        self._refresh_file_list()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---------------- Left panel: file list ----------------
        left = QWidget()
        left_layout = QVBoxLayout(left)

        bar = QHBoxLayout()
        self.btn_pick_dir = QPushButton("フォルダ選択…")
        bar.addWidget(self.btn_pick_dir)
        bar.addStretch(1)
        left_layout.addLayout(bar)

        dir_row = QHBoxLayout()
        self.lbl_dir = QLabel(f"DIR: {self.directory}")
        self.lbl_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_nfiles = QLabel("ファイル数：0")
        self.lbl_nfiles.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        dir_row.addWidget(self.lbl_dir, 1)
        dir_row.addWidget(self.lbl_nfiles, 0)
        left_layout.addLayout(dir_row)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.list, 1)

        info_title = QLabel("選択中CSVの情報")
        info_title.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(info_title)

        self.lbl_count = QLabel("点数: -")
        self.lbl_range = QLabel("GPS時刻: -")
        self.lbl_type = QLabel("種別: -")
        self.lbl_use = QLabel("用途: -")
        for w in (self.lbl_count, self.lbl_range, self.lbl_type, self.lbl_use):
            w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            left_layout.addWidget(w)

        self.status = QLabel("CSVファイルを選択してください。")
        left_layout.addWidget(self.status)

        self.btn_pick_dir.clicked.connect(self._pick_directory)

        # ---------------- Right panel: map + plot ----------------
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.web = QWebEngineView()
        self.web.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        # baseUrl をスクリプトフォルダにして leaflet/ を解決
        base = QUrl.fromLocalFile(str(Path(__file__).resolve().parent) + "/")
        self.web.setHtml(LEAFLET_HTML, base)
        self.web.loadFinished.connect(self._on_web_loaded)

        self.plot = SpeedPlot()

        v_split = QSplitter(Qt.Orientation.Vertical)
        v_split.addWidget(self.web)
        v_split.addWidget(self.plot)
        v_split.setSizes([650, 250])

        right_layout.addWidget(v_split, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([520, 980])

        layout = QVBoxLayout(root)
        layout.addWidget(splitter)

    def _init_logo_overlay(self) -> None:
        logo_path = Path(__file__).resolve().parent / "logo.png"
        if not logo_path.exists():
            return

        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return

        pix_big = pixmap.scaledToHeight(320, Qt.TransformationMode.SmoothTransformation)
        self._pix_small = pixmap.scaledToHeight(110, Qt.TransformationMode.SmoothTransformation)

        self.splash = QLabel(self)
        self.splash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash.setStyleSheet("background: transparent;")
        self.splash.setPixmap(pix_big)
        self.splash.adjustSize()

        x = (self.width() - self.splash.width()) // 2
        y = (self.height() - self.splash.height()) // 2
        self.splash.move(x, y)
        self.splash.show()

        effect = QGraphicsOpacityEffect(self.splash)
        self.splash.setGraphicsEffect(effect)

        fade_in = QPropertyAnimation(effect, b"opacity", self)
        fade_in.setDuration(500)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)

        def start_fade_out():
            fade_out = QPropertyAnimation(effect, b"opacity", self)
            fade_out.setDuration(500)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)

            def show_corner_logo():
                self.splash.deleteLater()
                self._show_corner_logo()

            fade_out.finished.connect(show_corner_logo)
            fade_out.start()

        fade_in.finished.connect(lambda: QTimer.singleShot(3000, start_fade_out))
        fade_in.start()

    def _show_corner_logo(self) -> None:
        if not self._pix_small:
            return

        self.splash = QLabel(self)
        self.splash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash.setStyleSheet("background: transparent;")
        self.splash.setPixmap(self._pix_small)
        self.splash.adjustSize()

        margin = 18
        x = self.width() - self.splash.width() - margin
        y = margin
        self.splash.move(x, y)
        self.splash.show()

        self._corner_logo_visible = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_refresh_about_text"):
            try:
                self._refresh_about_text()
            except Exception:
                pass

        if getattr(self, "_corner_logo_visible", False):
            margin = 18
            x = self.width() - self.splash.width() - margin
            y = margin
            self.splash.move(x, y)

    def _pick_directory(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self,
            "第1・2スクリーニングデータ格納フォルダの選択",
            str(self.directory)
        )
        if not d:
            return

        close_busy_dialog(self._msg_loading)
        self._msg_loading = make_busy_dialog("処理中", "データ読込中…")

        self.directory = Path(d)
        self.lbl_dir.setText(f"DIR: {self.directory}")
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        self.pattern = "*.csv"
        if not self.directory.exists():
            close_busy_dialog(self._msg_loading)
            self._msg_loading = None
            self.files = []
            self.list.clear()
            self.lbl_nfiles.setText("ファイル数：0")
            self.status.setText("No CSV files found.")
            self._set_info_defaults()
            self.plot.update_plot(pd.DataFrame())
            QMessageBox.warning(self, "Directory not found", f"Directory does not exist:\n{self.directory}")
            return

        self.files = sorted([p for p in self.directory.glob(self.pattern) if p.is_file()])
        self.lbl_nfiles.setText(f"ファイル数：{len(self.files)}")
        self.list.clear()
        for p in self.files:
            self.list.addItem(QListWidgetItem(p.name))

        if not self.files:
            self.status.setText("No CSV files found.")
            self._set_info_defaults()
            self.plot.update_plot(pd.DataFrame())
            close_busy_dialog(self._msg_loading)
            self._msg_loading = None
            return

        self.status.setText("Select a CSV file.")
        self.list.setCurrentRow(0)

    def _set_info_defaults(self) -> None:
        self.lbl_count.setText("点数: 0")
        self.lbl_range.setText("GPS時刻: -")
        self.lbl_type.setText("種別: -")
        self.lbl_use.setText("用途: -")

    def _on_web_loaded(self, ok: bool) -> None:
        if not ok:
            QMessageBox.warning(self, "地図の読み込み失敗", "地図の読み込みに失敗しました。leaflet/ の配置を確認してください。")
            return

        # 何も選ばれていない場合でも map 初期化だけはしておく
        if self.files:
            try:
                df0 = read_route_data(self.files[0])
                if not df0.empty:
                    self._init_map(float(df0.iloc[0]["lat"]), float(df0.iloc[0]["lon"]))
                    self._render_current()
            except Exception:
                pass

        # 地図準備完了 → 処理中メッセージを閉じる
        close_busy_dialog(self._msg_loading)
        self._msg_loading = None

    def _init_map(self, lat: float, lon: float) -> None:
        self._run_js(f"window._routeMapper.initMap({lat}, {lon}, 12);")

    def _run_js(self, js: str, retry_ms: int = 120, on_ready=None) -> None:
        wrapped = (
            "(function(){"
            "if (window._routeMapper) {"
            f"{js}"
            "return true;"
            "}"
            "return false;"
            "})();"
        )

        def _cb(ok: bool):
            if ok:
                if on_ready:
                    try:
                        on_ready()
                    except Exception:
                        pass
                return
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(retry_ms, lambda: self.web.page().runJavaScript(wrapped, _cb))

        self.web.page().runJavaScript(wrapped, _cb)

    def _on_selection_changed(self) -> None:
        self._render_current()

    def _render_current(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.files):
            return
        csv_path = self.files[row]

        try:
            df = read_route_data(csv_path)
        except Exception as exc:
            QMessageBox.critical(self, "Read error", f"Failed to load CSV:\n{csv_path}\n\n{exc}")
            self.status.setText(f"{csv_path.name}: failed to load")
            self._set_info_defaults()
            self.plot.update_plot(pd.DataFrame())
            return

        if df.empty:
            QMessageBox.information(self, "Info", "No valid points inside Japan were found in this file.")
            self.status.setText(f"{csv_path.name}: no valid points")
            self._set_info_defaults()
            self.plot.update_plot(pd.DataFrame())
            return

        # update info (05既存機能維持)
        self.lbl_count.setText(f"点数: {len(df)}")

        times = [parse_gps_time(v) for v in df["time"].tolist()]
        times2 = [t for t in times if t]
        self.lbl_range.setText(f"GPS時刻: {fmt_range(min(times2), max(times2))}" if times2 else "GPS時刻: -")

        self.lbl_type.setText(f"種別: {summarize_set(df['type'].astype(str).tolist(), TYPE_MAP)}")
        self.lbl_use.setText(f"用途: {summarize_set(df['use'].astype(str).tolist(), USE_MAP)}")

        self.status.setText(f"Rendering: {csv_path.name} ({len(df)} points)")

        # map payload
        lat0 = float(df.iloc[0]["lat"])
        lon0 = float(df.iloc[0]["lon"])

        points = []
        for r in df.itertuples(index=False):
            dt = parse_gps_time(getattr(r, "time"))
            time_text = "-"
            if dt:
                time_text = f"{dt.year}/{dt.month:02d}/{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

            points.append({
                "lat": float(getattr(r, "lat")),
                "lon": float(getattr(r, "lon")),
                "flag": int(getattr(r, "flag")),
                "time_text": time_text,
                "speed": None if (getattr(r, "speed") is None or (isinstance(getattr(r, "speed"), float) and math.isnan(getattr(r, "speed")))) else float(getattr(r, "speed")),
            })

        segs = split_segments([(p["lat"], p["lon"], p["flag"]) for p in points])
        segs2 = [[[lat, lon] for (lat, lon) in seg] for seg in segs]

        # bounds (全点)
        lats = [p["lat"] for p in points]
        lons = [p["lon"] for p in points]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]

        payload = {
            "center": {"lat": lat0, "lon": lon0},
            "points": points,
            "segments": segs2,
            "bounds": bounds,
        }

        import json
        self._run_js(
            f"window._routeMapper.showRoute({json.dumps(payload)});",
            on_ready=lambda: (close_busy_dialog(self._msg_loading), setattr(self, "_msg_loading", None)),
        )

        # plot
        self.plot.update_plot(df)


def main(argv: Sequence[str]) -> None:
    args = list(argv[1:])

    folder_arg = ""
    for arg in args:
        if not arg.startswith("-"):
            folder_arg = arg
            break

    # -----------------------------
    # NOGUI MODE
    # -----------------------------
    if "--nogui" in args:
        print("[INFO] running in --nogui mode")

        folder = folder_arg
        if "--folder" in args:
            idx = args.index("--folder")
            if idx + 1 < len(args):
                folder = args[idx + 1]

        if not folder:
            folder = select_directory_with_qt()
        if not folder:
            folder = str(Path.cwd())
            print(f"[WARN] フォルダ指定・ダイアログ選択不可のため、カレントを使用します: {folder}")

        html_path = run_without_gui(folder)

        if html_path:
            webbrowser.open(Path(html_path).resolve().as_uri())
            print("[OK] opened in browser:", html_path)
        else:
            print("[ERROR] html generation failed")

        return

    pattern = "*.csv"

    app = QApplication(sys.argv)

    busy = make_busy_dialog("起動中", "Qt初期化中…（初回は時間がかかることがあります）")

    initial = r"D:\01仕事\05 ETC2.0分析\生データ"

    close_busy_dialog(busy)
    busy = None

    d = folder_arg or QFileDialog.getExistingDirectory(
        None,
        "第1・2スクリーニングデータ格納フォルダの選択",
        initial
    )

    if not d:
        return

    busy = make_busy_dialog("起動中", "データ読込中…")

    # インターネット未接続通知（既存白背景ロジックは変更しない）
    if not is_internet_available():
        QMessageBox.information(
            None,
            "オフライン表示",
            "インターネット接続が無いため白背景で表示します。"
        )

    w = RouteMapperWindow(directory=Path(d), pattern=pattern)

    # ウィンドウ側でも閉じられるよう参照渡し
    w._msg_loading = busy

    w.showFullScreen()
    sys.exit(app.exec())


def run_without_gui(folder_path: str) -> Optional[str]:
    target = Path(folder_path).expanduser()
    if target.is_dir():
        candidates = sorted(target.glob("*.csv"))
        if not candidates:
            raise FileNotFoundError(f"CSV not found in directory: {target}")
        csv_path = candidates[0]
    else:
        csv_path = target

    df = read_route_data(csv_path)
    if df.empty:
        return None

    lat0 = float(df.iloc[0]["lat"])
    lon0 = float(df.iloc[0]["lon"])
    fmap = folium.Map(location=[lat0, lon0], zoom_start=12, tiles="OpenStreetMap")

    points: List[Tuple[float, float, int]] = []
    for r in df.itertuples(index=False):
        points.append((float(getattr(r, "lat")), float(getattr(r, "lon")), int(getattr(r, "flag"))))

    for seg in split_segments(points):
        folium.PolyLine(seg, color="blue", weight=4, opacity=0.8).add_to(fmap)

    if points:
        folium.Marker([points[0][0], points[0][1]], tooltip="Start").add_to(fmap)
        folium.Marker([points[-1][0], points[-1][1]], tooltip="Goal").add_to(fmap)

    out_path = csv_path.with_name(f"{csv_path.stem}_route_map.html")
    fmap.save(str(out_path))
    return str(out_path)


def select_directory_with_qt() -> Optional[str]:
    try:
        from PyQt6.QtWidgets import QApplication, QFileDialog
    except Exception:
        return None

    app = QApplication.instance() or QApplication([])
    selected = QFileDialog.getExistingDirectory(None, "CSVフォルダを選択してください", str(Path.cwd()))
    return selected or None


if __name__ == "__main__":
    main(sys.argv)
