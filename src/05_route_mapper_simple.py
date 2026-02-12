from __future__ import annotations

import math
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Any, Dict, List, Optional, Sequence, Tuple

import folium
import numpy as np
import pandas as pd

from matplotlib.figure import Figure

NOGUI_MODE = "--nogui" in sys.argv[1:]

if not NOGUI_MODE:
    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import (
        QApplication,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSplitter,
        QVBoxLayout,
        QWidget,
        QListWidget,
        QListWidgetItem,
        QLineEdit,
    )
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
else:
    FigureCanvas = object
    Qt = QUrl = QWebEngineSettings = QWebEngineView = object
    QApplication = QFileDialog = QHBoxLayout = QLabel = QMainWindow = object
    QMessageBox = QPushButton = QSplitter = QVBoxLayout = QWidget = object
    QListWidget = QListWidgetItem = QLineEdit = object


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

  function stopAnimation(){
    try { if (animTimer) clearInterval(animTimer); } catch(e) {}
    animTimer = null;
    animPath = [];
    animIndex = 0;
    try { if (animMarker && routeLayer) routeLayer.removeLayer(animMarker); } catch(e) {}
    animMarker = null;
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

    // 動く球（軌跡上を高速で一方向）
    // ※色は固定値。必要なら後で調整可能
    animMarker = L.circleMarker(animPath[0], {
      radius: 7,
      color: 'deepskyblue',
      weight: 3,
      fill: true,
      fillColor: 'deepskyblue',
      fillOpacity: 1.0,
      pane: 'markerPane'
    }).addTo(routeLayer);

    // 高速：20ms間隔 / 1tickで複数点進める
    const intervalMs = 20;
    const stepPerTick = 3; // 速さ調整（大きいほど速い）
    animTimer = setInterval(() => {
      if (!animMarker || animPath.length < 2) return;

      animIndex += stepPerTick;
      if (animIndex >= animPath.length) animIndex = animPath.length - 1;

      animMarker.setLatLng(animPath[animIndex]);

      // 終点に到達したら停止（往復しない）
      if (animIndex >= animPath.length - 1){
        stopAnimation();
      }
    }, intervalMs);
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
    try { routeLayer.clearLayers(); } catch(e) {}
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
    const lineStyle = {color:'black', weight:2, opacity:1.0};
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
        self.setWindowTitle("05_route_mapper - ルート可視化（33型UI）")
        self.resize(1500, 900)

        self.directory = directory
        self.pattern = pattern

        self.files: List[Path] = []
        self.current_df: Optional[pd.DataFrame] = None

        self._build_ui()
        self._refresh_file_list()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---------------- Left panel: file list ----------------
        left = QWidget()
        left_layout = QVBoxLayout(left)

        bar = QHBoxLayout()
        self.ed_pattern = QLineEdit(self.pattern)
        self.ed_pattern.setPlaceholderText("pattern (e.g. *.csv)")
        self.btn_pick_dir = QPushButton("フォルダ選択…")
        self.btn_reload = QPushButton("再読込")
        bar.addWidget(QLabel("pattern:"))
        bar.addWidget(self.ed_pattern, 1)
        bar.addWidget(self.btn_pick_dir)
        bar.addWidget(self.btn_reload)
        left_layout.addLayout(bar)

        self.lbl_dir = QLabel(f"DIR: {self.directory}")
        self.lbl_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        left_layout.addWidget(self.lbl_dir)

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
        self.btn_reload.clicked.connect(self._refresh_file_list)
        self.ed_pattern.returnPressed.connect(self._refresh_file_list)

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

    def _pick_directory(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "CSVフォルダを選択", str(self.directory))
        if not d:
            return
        self.directory = Path(d)
        self.lbl_dir.setText(f"DIR: {self.directory}")
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        self.pattern = self.ed_pattern.text().strip() or "*.csv"
        if not self.directory.exists():
            QMessageBox.warning(self, "Directory not found", f"Directory does not exist:\n{self.directory}")
            return

        self.files = sorted([p for p in self.directory.glob(self.pattern) if p.is_file()])
        self.list.clear()
        for p in self.files:
            self.list.addItem(QListWidgetItem(p.name))

        if not self.files:
            self.status.setText("No CSV files found.")
            self._set_info_defaults()
            self.plot.update_plot(pd.DataFrame())
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

    def _init_map(self, lat: float, lon: float) -> None:
        self._run_js(f"window._routeMapper.initMap({lat}, {lon}, 12);")

    def _run_js(self, js: str, retry_ms: int = 120) -> None:
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
                return
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(retry_ms, lambda: self.web.page().runJavaScript(wrapped))

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
        self._run_js(f"window._routeMapper.showRoute({json.dumps(payload)});")

        # plot
        self.plot.update_plot(df)


def main(argv: Sequence[str]) -> None:
    args = list(argv[1:])

    # -----------------------------
    # NOGUI MODE
    # -----------------------------
    if "--nogui" in args:
        print("[INFO] running in --nogui mode")

        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title="CSVフォルダを選択してください")
        root.destroy()

        if not folder:
            print("[INFO] no folder selected")
            return

        html_path = run_without_gui(folder)

        if html_path:
            webbrowser.open(Path(html_path).resolve().as_uri())
            print("[OK] opened in browser:", html_path)
        else:
            print("[ERROR] html generation failed")

        return

    pattern = args[0] if args else "*.csv"

    app = QApplication(sys.argv)

    # 初回：フォルダ選択
    initial = r"D:\01仕事\05 ETC2.0分析\生データ"
    d = QFileDialog.getExistingDirectory(None, "CSVフォルダを選択してください", initial)
    if not d:
        return

    w = RouteMapperWindow(directory=Path(d), pattern=pattern)
    w.show()
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


if __name__ == "__main__":
    main(sys.argv)
