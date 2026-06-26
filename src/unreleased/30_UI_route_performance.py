from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QObject, Qt, QDate, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat
from PyQt6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover
    QWebEngineSettings = None
    QWebEngineView = None

PERF_PATH = SRC_DIR / "30_route_performance.py"
if not PERF_PATH.exists():
    PERF_PATH = SRC_DIR / "unreleased" / "30_route_performance.py"
spec = importlib.util.spec_from_file_location("route_performance30", PERF_PATH)
perf = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = perf
spec.loader.exec_module(perf)


def date_token_to_qdate(token: str) -> QDate:
    return QDate(int(token[:4]), int(token[4:6]), int(token[6:8]))


def qdate_to_token(qdate: QDate) -> str:
    return qdate.toString("yyyyMMdd")


ROUTE_MAP_HTML = r"""
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Route Map</title>
  <link rel="stylesheet" href="leaflet/leaflet.css"/>
  <script src="leaflet/leaflet.js"></script>
  <style>
    html, body { height:100%; margin:0; background:#fff; overflow:hidden; font-family:"Segoe UI","Meiryo UI",sans-serif; }
    #map, #fallback { position:absolute; inset:0; background:#fff; }
    #fallback svg { width:100%; height:100%; display:block; }
    #fallback { display:none; }
    .panel { position:absolute; z-index:1000; left:10px; top:10px; background:#ffffffee; border-radius:6px; padding:8px 10px; box-shadow:0 4px 18px #0003; font-weight:700; }
    .status { font-size:11px; color:#334155; margin-top:2px; font-weight:600; }
    .leaflet-container { background:#fff; }
  </style>
</head>
<body>
<div id="map"></div>
<div id="fallback"><svg id="fallbackSvg" role="img" aria-label="route map"></svg></div>
<div class="panel">ルートマップ<div class="status" id="mapStatus">SIMPLE</div></div>
<script>
const COLORS = ['#00a2ff','#22c55e','#f97316','#a855f7','#ef4444','#14b8a6','#eab308','#ec4899','#84cc16','#6366f1'];
let map = null;
let routeLayer = null;
let lastRoutes = [];
let loadingLeaflet = false;

function setStatus(text) {
  const el = document.getElementById('mapStatus');
  if (el) el.textContent = text;
}

function validPoint(pt) {
  return Array.isArray(pt) && Number.isFinite(Number(pt[0])) && Number.isFinite(Number(pt[1]));
}
function allPoints(routes) {
  return routes.flatMap(route => (route.coords || []).filter(validPoint).map(pt => [Number(pt[0]), Number(pt[1])]));
}
function showFallback(routes) {
  setStatus('SIMPLE');
  lastRoutes = routes || lastRoutes || [];
  document.getElementById('map').style.display = 'none';
  const fallback = document.getElementById('fallback');
  const svg = document.getElementById('fallbackSvg');
  fallback.style.display = 'block';
  svg.replaceChildren();
  const width = Math.max(svg.clientWidth || window.innerWidth, 200);
  const height = Math.max(svg.clientHeight || window.innerHeight, 200);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const pts = allPoints(lastRoutes);
  if (!pts.length) return;
  let minLat = Math.min(...pts.map(pt => pt[0]));
  let maxLat = Math.max(...pts.map(pt => pt[0]));
  let minLon = Math.min(...pts.map(pt => pt[1]));
  let maxLon = Math.max(...pts.map(pt => pt[1]));
  if (minLat === maxLat) { minLat -= 0.001; maxLat += 0.001; }
  if (minLon === maxLon) { minLon -= 0.001; maxLon += 0.001; }
  const pad = 34;
  const project = ([lat, lon]) => {
    const x = pad + (lon - minLon) / (maxLon - minLon) * (width - pad * 2);
    const y = pad + (maxLat - lat) / (maxLat - minLat) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };
  lastRoutes.forEach((route, idx) => {
    const coords = (route.coords || []).filter(validPoint).map(pt => [Number(pt[0]), Number(pt[1])]);
    if (coords.length < 2) return;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('points', coords.map(project).join(' '));
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', COLORS[idx % COLORS.length]);
    line.setAttribute('stroke-width', '4.5');
    line.setAttribute('stroke-linecap', 'round');
    line.setAttribute('stroke-linejoin', 'round');
    line.setAttribute('opacity', '0.95');
    svg.appendChild(line);
  });
}
function initMap() {
  if (typeof L === 'undefined') {
    loadLeafletFromCdn();
    showFallback(lastRoutes);
    return false;
  }
  if (map) return true;
  try {
    map = L.map('map', { zoomControl:true, preferCanvas:true }).setView([35.069095, 134.004512], 12);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap contributors © CARTO'
    }).addTo(map);
    routeLayer = L.layerGroup().addTo(map);
    setStatus('WEB');
    return true;
  } catch (e) {
    showFallback(lastRoutes);
    return false;
  }
}
function loadLeafletFromCdn() {
  if (loadingLeaflet || typeof L !== 'undefined') return;
  loadingLeaflet = true;
  setStatus('Leaflet読込中');
  const css = document.createElement('link');
  css.rel = 'stylesheet';
  css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
  document.head.appendChild(css);
  const script = document.createElement('script');
  script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
  script.onload = () => { loadingLeaflet = false; setRoutes(lastRoutes); };
  script.onerror = () => { loadingLeaflet = false; showFallback(lastRoutes); };
  document.head.appendChild(script);
}
function setRoutes(routes) {
  lastRoutes = routes || [];
  showFallback(lastRoutes);
  if (!initMap()) return false;
  try {
    document.getElementById('map').style.display = 'block';
    document.getElementById('fallback').style.display = 'none';
    setStatus('WEB');
    routeLayer.clearLayers();
    const bounds = [];
    lastRoutes.forEach((route, idx) => {
      const coords = (route.coords || []).filter(validPoint).map(pt => [Number(pt[0]), Number(pt[1])]);
      if (coords.length < 2) return;
      L.polyline(coords, { color: COLORS[idx % COLORS.length], weight: 5, opacity: 0.9 })
        .bindTooltip(`${idx + 1}. ${route.name}<br>延長 ${route.length_km} km / 点数 ${route.points}`)
        .addTo(routeLayer);
      coords.forEach(pt => bounds.push(pt));
    });
    if (bounds.length) map.fitBounds(bounds, { padding:[24,24] });
    setTimeout(() => map.invalidateSize(), 0);
    return true;
  } catch (e) {
    showFallback(lastRoutes);
    return false;
  }
}
window.addEventListener('resize', () => {
  if (document.getElementById('fallback').style.display !== 'none') showFallback(lastRoutes);
  if (map) setTimeout(() => map.invalidateSize(), 0);
});
window._routePerformanceMap = { initMap, setRoutes, showFallback };
</script>
</body>
</html>
"""


class ProjectScanWorker(QObject):
    progress = pyqtSignal(int, str, dict)
    route_loaded = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, project_dir: str) -> None:
        super().__init__()
        self.project_dir = project_dir

    def run(self) -> None:
        try:
            input_dir, route_dir, output_dir = perf.resolve_project_paths(self.project_dir)
            route_files = perf.list_route_csvs(route_dir)
            routes: list[dict[str, object]] = []
            for idx, route_path in enumerate(route_files, start=1):
                route = perf.load_route(route_path)
                routes.append(
                    {
                        "name": route.name,
                        "path": str(route_path),
                        "length_km": round(route.length_m / 1000, 3),
                        "points": len(route.kp_m),
                        "coords": list(zip(route.lats, route.lons)),
                    }
                )
                self.route_loaded.emit(routes[-1])
                self.progress.emit(
                    int(100 * idx / max(len(route_files), 1)),
                    f"ルート読込 {idx}/{len(route_files)}",
                    {"phase": "ルート読込", "routes": len(routes), "route_total": len(route_files)},
                )

            self.progress.emit(
                100,
                f"ルート読込完了: {len(routes)}路線",
                {"phase": "ルート読込完了", "routes": len(routes), "route_total": len(route_files)},
            )
            self.finished.emit(
                {
                    "input_dir": str(input_dir),
                    "route_dir": str(route_dir),
                    "output_dir": str(output_dir),
                    "routes": routes,
                    "dates": [],
                    "files": 0,
                    "readable_files": 0,
                    "rows": 0,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class AnalysisWorker(QObject):
    progress = pyqtSignal(int, str, dict)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, project_dir: str, dates: set[str] | None, hours: set[int] | None, factors: dict[str, float], max_off_route_m: float) -> None:
        super().__init__()
        self.project_dir = project_dir
        self.dates = dates
        self.hours = hours
        self.factors = factors
        self.max_off_route_m = max_off_route_m

    def run(self) -> None:
        try:
            result = perf.analyze_project(
                self.project_dir,
                recursive=True,
                allowed_dates=self.dates,
                allowed_hours=self.hours,
                expansion_factors=self.factors,
                max_off_route_m=self.max_off_route_m,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("30 Route Performance")
        self.resize(1680, 980)
        self.project_dir = ""
        self.available_dates: set[str] = set()
        self.selected_date_tokens: set[str] = set()
        self.formatted_dates: set[str] = set()
        self.hour_checks: dict[int, QCheckBox] = {}
        self.stats: dict[str, QLabel] = {}
        self.result: dict | None = None
        self.pending_route_map_payload: list[dict[str, object]] | None = None
        self.viewer_load_dialog: QProgressDialog | None = None
        self._syncing_calendars = False
        self._last_scan_log_bucket = -1
        self._last_analysis_log_bucket = -1
        self.analysis_start_time: float | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(6)

        title_row = QHBoxLayout()
        title = QLabel("30 Route Performance: ETC2.0 traffic condition viewer")
        title.setObjectName("title")
        self.project_label = QLabel("Project folder: not selected")
        self.project_label.setObjectName("projectLabel")
        choose = QPushButton("プロジェクト選択")
        choose.clicked.connect(self.choose_project)
        self.run_button = QPushButton("解析開始")
        self.run_button.clicked.connect(self.start_analysis)
        self.viewer_button = QPushButton("ビューアーを開く")
        self.viewer_button.clicked.connect(self.open_viewer)
        self.viewer_button.setEnabled(False)
        self.max_off_route_spin = QDoubleSpinBox()
        self.max_off_route_spin.setRange(1.0, 500.0)
        self.max_off_route_spin.setDecimals(1)
        self.max_off_route_spin.setValue(30.0)
        self.max_off_route_spin.setSuffix(" m")
        self.max_off_route_spin.setToolTip("GPS点をルート上とみなす最大離れ距離です。")
        title_row.addWidget(title)
        title_row.addWidget(self.project_label, 1)
        title_row.addWidget(QLabel("離れ閾値"))
        title_row.addWidget(self.max_off_route_spin)
        title_row.addWidget(choose)
        title_row.addWidget(self.run_button)
        title_row.addWidget(self.viewer_button)
        main.addLayout(title_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress_label = QLabel("待機中")
        main.addWidget(self.progress)
        main.addWidget(self.progress_label)
        main.addWidget(self._build_stats_bar())

        content = QHBoxLayout()
        content.setSpacing(8)
        main.addLayout(content, 1)

        center = QWidget()
        center.setMinimumWidth(620)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)
        center_layout.addWidget(self._build_route_box(), 5)
        center_layout.addWidget(self._build_log_box(), 1)
        content.addWidget(center, 1)

        right = self._box("ルートマップ")
        if QWebEngineView is not None:
            self.web = QWebEngineView()
            if QWebEngineSettings is not None:
                settings = self.web.settings()
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            self.web.loadFinished.connect(self.route_map_load_finished)
            self.web.loadFinished.connect(self.viewer_load_finished)
            right.layout().addWidget(self.web, 1)
        else:
            self.web = None
            right.layout().addWidget(QLabel("PyQt6-WebEngine が無い場合は外部ブラウザで開きます。"))
        content.addWidget(right, 2)

        self.setStyleSheet(
            """
            QWidget { background:#0b0f14; color:#e6f1ff; font-family:"Segoe UI","Meiryo UI"; font-size:12px; }
            QLabel#title { color:#00ff99; font-size:22px; font-weight:800; }
            QLabel#projectLabel { color:#b7c4d4; }
            QFrame#box { border:1px solid rgba(0,255,153,.48); border-radius:8px; background:#111827; }
            QLabel#boxTitle { color:#facc15; font-weight:800; }
            QFrame#statBox { border:1px solid #334155; border-radius:6px; background:#020617; }
            QLabel#statCaption { color:#00ff99; font-size:10px; }
            QLabel#statValue { color:#facc15; font-size:14px; font-weight:800; }
            QPushButton { border:1px solid #00ff99; border-radius:7px; padding:7px 10px; background:#10231a; color:#e6f1ff; font-weight:700; }
            QPushButton:hover { background:#16432b; }
            QProgressBar { border:1px solid #00ff99; height:18px; text-align:center; background:#020617; }
            QProgressBar::chunk { background:#00ff99; }
            QTableWidget, QScrollArea, QPlainTextEdit, QCalendarWidget QAbstractItemView { background:#020617; color:#e6f1ff; border:1px solid #334155; }
            QHeaderView::section { background:#111827; color:#00ff99; padding:6px; }
            QDoubleSpinBox { background:#020617; color:#e6f1ff; border:1px solid #334155; padding:4px; }
            QCheckBox { spacing:8px; min-height:24px; }
            """
        )

    def _box(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("box")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("boxTitle")
        layout.addWidget(label)
        return frame

    def _build_stats_bar(self) -> QWidget:
        frame = QWidget()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        specs = [
            ("phase", "PHASE", "-"),
            ("files", "FILES", "0 / 0"),
            ("rows", "ROWS", "0"),
            ("time", "TIME", "00:00 / --:--"),
            ("events", "EVENTS", "0"),
        ]
        for key, caption, initial in specs:
            box = QFrame()
            box.setObjectName("statBox")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(8, 4, 8, 4)
            cap = QLabel(caption)
            cap.setObjectName("statCaption")
            val = QLabel(initial)
            val.setObjectName("statValue")
            box_layout.addWidget(cap)
            box_layout.addWidget(val)
            layout.addWidget(box, 1)
            self.stats[key] = val
        return frame

    def _build_calendar_box(self) -> QFrame:
        box = self._box("対象日 - 2か月表示")
        calendars = QHBoxLayout()
        self.calendar_a = QCalendarWidget()
        self.calendar_b = QCalendarWidget()
        for cal in (self.calendar_a, self.calendar_b):
            cal.setGridVisible(True)
            cal.clicked.connect(self.toggle_calendar_date)
        self.calendar_a.currentPageChanged.connect(self.sync_calendar_b)
        self.calendar_b.currentPageChanged.connect(self.sync_calendar_a)
        self.calendar_b.setCurrentPage(QDate.currentDate().addMonths(1).year(), QDate.currentDate().addMonths(1).month())
        calendars.addWidget(self.calendar_a)
        calendars.addWidget(self.calendar_b)
        box.layout().addLayout(calendars)

        buttons = QHBoxLayout()
        all_dates = QPushButton("全日ON")
        no_dates = QPushButton("全日OFF")
        all_dates.clicked.connect(lambda: self.set_all_dates(True))
        no_dates.clicked.connect(lambda: self.set_all_dates(False))
        buttons.addWidget(all_dates)
        buttons.addWidget(no_dates)
        box.layout().addLayout(buttons)
        return box

    def _build_hour_box(self) -> QFrame:
        box = self._box("対象時間 (1時間単位・縦1列)")
        holder = QWidget()
        hour_layout = QVBoxLayout(holder)
        hour_layout.setContentsMargins(6, 4, 6, 4)
        hour_layout.setSpacing(2)
        for hour in range(24):
            cb = QCheckBox(f"{hour:02d}:00 - {hour:02d}:59")
            cb.setChecked(True)
            self.hour_checks[hour] = cb
            hour_layout.addWidget(cb)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        box.layout().addWidget(scroll, 1)
        buttons = QHBoxLayout()
        all_hours = QPushButton("全時間ON")
        no_hours = QPushButton("全時間OFF")
        commute = QPushButton("朝夕")
        all_hours.clicked.connect(lambda: self.set_hours(range(24)))
        no_hours.clicked.connect(lambda: self.set_hours([]))
        commute.clicked.connect(lambda: self.set_hours([7, 8, 17, 18]))
        buttons.addWidget(all_hours)
        buttons.addWidget(no_hours)
        buttons.addWidget(commute)
        box.layout().addLayout(buttons)
        return box

    def _build_route_box(self) -> QFrame:
        box = self._box("路線別 拡大係数")
        self.route_table = QTableWidget(0, 6)
        self.route_table.setHorizontalHeaderLabels(["ルート名", "延長[km]", "点数", "有効点", "HIT比", "拡大係数"])
        self.route_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.route_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.route_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.route_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.route_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.route_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.route_table.verticalHeader().setDefaultSectionSize(28)
        box.layout().addWidget(self.route_table)
        buttons = QHBoxLayout()
        save_factors = QPushButton("拡大係数を保存")
        load_factors = QPushButton("拡大係数を読込")
        save_factors.clicked.connect(self.save_expansion_factors)
        load_factors.clicked.connect(self.load_expansion_factors)
        buttons.addWidget(save_factors)
        buttons.addWidget(load_factors)
        box.layout().addLayout(buttons)
        return box

    def _build_log_box(self) -> QFrame:
        box = self._box("処理ログ (節目のみ)")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(120)
        box.layout().addWidget(self.log)
        return box

    def choose_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択")
        if not path:
            return
        self.project_dir = path
        self.project_label.setText(f"Project folder: {path}")
        self.start_project_scan()

    def start_project_scan(self) -> None:
        self.result = None
        self.viewer_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self._last_scan_log_bucket = -1
        self._last_analysis_log_bucket = -1
        self.route_table.setRowCount(0)
        self.set_factor_inputs_enabled(True)
        self.max_off_route_spin.setEnabled(True)
        self.available_dates.clear()
        self.selected_date_tokens.clear()
        self.clear_calendar_formats()
        self.progress.setValue(0)
        self.log.clear()
        self.append_log("プロジェクト読込を開始しました。ここではルートファイルだけを確認します。")
        self.update_stat("phase", "ルート読込")
        self.scan_thread = QThread()
        self.scan_worker = ProjectScanWorker(self.project_dir)
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.progress.connect(self.update_scan_progress)
        self.scan_worker.route_loaded.connect(self.add_route_row)
        self.scan_worker.finished.connect(self.project_scan_done)
        self.scan_worker.failed.connect(self.project_scan_failed)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.failed.connect(self.scan_thread.quit)
        self.scan_thread.start()

    def update_scan_progress(self, percent: int, message: str, stats: dict) -> None:
        self.progress.setValue(percent)
        phase = stats.get("phase", "-")
        file_index = int(stats.get("file_index", 0) or 0)
        file_total = int(stats.get("file_total", 0) or 0)
        rows = int(stats.get("rows", 0) or 0)
        dates = int(stats.get("dates", 0) or 0)
        route_total = int(stats.get("route_total", 0) or 0)
        routes = int(stats.get("routes", 0) or 0)
        if file_total:
            self.progress_label.setText(f"{phase}: {file_index}/{file_total} ファイル / {rows:,} 行 / 検出日 {dates}")
        elif route_total:
            self.progress_label.setText(f"{phase}: {routes}/{route_total} 路線")
        else:
            self.progress_label.setText(message)
        self.update_stat("phase", stats.get("phase", "-"))
        self.update_stat("files", f"{file_index} / {file_total}" if file_total else "0 / 0")
        self.update_stat("rows", f"{rows:,}")
        bucket = percent // 5
        if bucket != self._last_scan_log_bucket or percent >= 100:
            self._last_scan_log_bucket = bucket
            self.append_log(self.progress_label.text())

    def project_scan_done(self, result: dict) -> None:
        self.run_button.setEnabled(True)
        self.progress.setValue(100)
        self.available_dates = set()
        self.selected_date_tokens = set()
        routes = result.get("routes", [])
        if self.route_table.rowCount() != len(routes):
            self.populate_routes(routes)
        self.refresh_calendar_formats()
        self.update_stat("phase", "読込完了")
        self.update_stat("files", "解析時に読込")
        self.update_stat("rows", "解析時に集計")
        self.update_stat("time", "00:00 / --:--")
        self.progress_label.setText(f"ルート読込完了: {len(routes)}路線。拡大係数を確認してから解析開始してください。")
        self.append_log(self.progress_label.text())
        self.load_route_map(routes)

    def project_scan_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.progress_label.setText("読込失敗")
        self.append_log(f"読込失敗: {message}")
        QMessageBox.critical(self, "読み込みエラー", message)

    def populate_routes(self, routes: list[dict[str, object]]) -> None:
        self.route_table.setRowCount(len(routes))
        for row, route in enumerate(routes):
            self.set_route_row(row, route)

    def add_route_row(self, route: dict[str, object]) -> None:
        row = self.route_table.rowCount()
        self.route_table.insertRow(row)
        self.set_route_row(row, route)

    def set_route_row(self, row: int, route: dict[str, object]) -> None:
        self.route_table.setItem(row, 0, QTableWidgetItem(str(route.get("name", ""))))
        self.route_table.setItem(row, 1, QTableWidgetItem(f"{float(route.get('length_km', 0.0)):.3f}"))
        self.route_table.setItem(row, 2, QTableWidgetItem(str(route.get("points", 0))))
        self.route_table.setItem(row, 3, QTableWidgetItem("0"))
        hit_bar = QProgressBar()
        hit_bar.setRange(0, 100)
        hit_bar.setValue(0)
        hit_bar.setFormat("0%")
        self.route_table.setCellWidget(row, 4, hit_bar)
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1000000.0)
        spin.setDecimals(3)
        spin.setValue(1.0)
        self.route_table.setCellWidget(row, 5, spin)

    def sync_calendar_b(self, year: int, month: int) -> None:
        if self._syncing_calendars:
            return
        self._syncing_calendars = True
        next_month = QDate(year, month, 1).addMonths(1)
        self.calendar_b.setCurrentPage(next_month.year(), next_month.month())
        self._syncing_calendars = False

    def sync_calendar_a(self, year: int, month: int) -> None:
        if self._syncing_calendars:
            return
        self._syncing_calendars = True
        prev_month = QDate(year, month, 1).addMonths(-1)
        self.calendar_a.setCurrentPage(prev_month.year(), prev_month.month())
        self._syncing_calendars = False

    def clear_calendar_formats(self) -> None:
        if not hasattr(self, "calendar_a") or not hasattr(self, "calendar_b"):
            self.formatted_dates.clear()
            return
        default_format = QTextCharFormat()
        for token in self.formatted_dates:
            qdate = date_token_to_qdate(token)
            self.calendar_a.setDateTextFormat(qdate, default_format)
            self.calendar_b.setDateTextFormat(qdate, default_format)
        self.formatted_dates.clear()

    def refresh_calendar_formats(self) -> None:
        self.clear_calendar_formats()
        selected_format = QTextCharFormat()
        selected_format.setBackground(QColor("#0f766e"))
        selected_format.setForeground(QColor("#ffffff"))
        available_format = QTextCharFormat()
        available_format.setBackground(QColor("#334155"))
        available_format.setForeground(QColor("#e6f1ff"))
        for token in self.available_dates:
            qdate = date_token_to_qdate(token)
            fmt = selected_format if token in self.selected_date_tokens else available_format
            self.calendar_a.setDateTextFormat(qdate, fmt)
            self.calendar_b.setDateTextFormat(qdate, fmt)
            self.formatted_dates.add(token)

    def toggle_calendar_date(self, qdate: QDate) -> None:
        token = qdate_to_token(qdate)
        if token not in self.available_dates:
            return
        if token in self.selected_date_tokens:
            self.selected_date_tokens.remove(token)
        else:
            self.selected_date_tokens.add(token)
        self.refresh_calendar_formats()

    def set_all_dates(self, checked: bool) -> None:
        self.selected_date_tokens = set(self.available_dates) if checked else set()
        self.refresh_calendar_formats()

    def set_hours(self, hours) -> None:
        hours = set(hours)
        for hour, cb in self.hour_checks.items():
            cb.setChecked(hour in hours)

    def selected_dates(self) -> set[str] | None:
        if not self.available_dates:
            return None
        return set(self.selected_date_tokens)

    def selected_hours(self) -> set[int] | None:
        selected = {hour for hour, cb in self.hour_checks.items() if cb.isChecked()}
        return selected or set()

    def expansion_factors(self) -> dict[str, float]:
        factors: dict[str, float] = {}
        for row in range(self.route_table.rowCount()):
            name_item = self.route_table.item(row, 0)
            spin = self.route_table.cellWidget(row, 5)
            if name_item and isinstance(spin, QDoubleSpinBox):
                factors[name_item.text()] = spin.value()
        return factors

    def default_factor_path(self) -> str:
        if self.project_dir:
            return str(Path(self.project_dir) / "30_route_expansion_factors.csv")
        return "30_route_expansion_factors.csv"

    def save_expansion_factors(self) -> None:
        if self.route_table.rowCount() == 0:
            QMessageBox.warning(self, "保存不可", "先にプロジェクトフォルダを選択してルートを読み込んでください。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "拡大係数を保存",
            self.default_factor_path(),
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["route", "expansion_factor"])
            for route, factor in self.expansion_factors().items():
                writer.writerow([route, factor])
        self.append_log(f"拡大係数を保存しました: {path}")

    def load_expansion_factors(self) -> None:
        for row in range(self.route_table.rowCount()):
            spin = self.route_table.cellWidget(row, 5)
            if isinstance(spin, QDoubleSpinBox) and not spin.isEnabled():
                QMessageBox.warning(self, "読込不可", "解析中は拡大係数を変更できません。")
                return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "拡大係数を読み込む",
            self.default_factor_path(),
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path:
            return
        loaded: dict[str, float] = {}
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                route = (row.get("route") or row.get("ルート名") or row.get("route_name") or "").strip()
                value = row.get("expansion_factor") or row.get("拡大係数") or row.get("factor") or ""
                if not route:
                    continue
                try:
                    loaded[route] = float(value)
                except Exception:
                    continue
        applied = 0
        for row in range(self.route_table.rowCount()):
            name_item = self.route_table.item(row, 0)
            spin = self.route_table.cellWidget(row, 5)
            if name_item and isinstance(spin, QDoubleSpinBox) and name_item.text() in loaded:
                spin.setValue(loaded[name_item.text()])
                applied += 1
        self.append_log(f"拡大係数を読み込みました: {applied}/{self.route_table.rowCount()} 路線 ({path})")

    def set_factor_inputs_enabled(self, enabled: bool) -> None:
        for row in range(self.route_table.rowCount()):
            spin = self.route_table.cellWidget(row, 5)
            if isinstance(spin, QDoubleSpinBox):
                spin.setEnabled(enabled)

    def start_analysis(self) -> None:
        if not self.project_dir:
            QMessageBox.warning(self, "未選択", "先にプロジェクトフォルダを選択してください。")
            return
        reply = QMessageBox.question(
            self,
            "拡大係数確認",
            "拡大係数を入力しましたか？\n「はい」で解析開始、「いいえ」でキャンセルします。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.append_log("解析開始をキャンセルしました。拡大係数を確認してください。")
            return
        self.run_button.setEnabled(False)
        self.viewer_button.setEnabled(False)
        self.set_factor_inputs_enabled(False)
        self.max_off_route_spin.setEnabled(False)
        self.analysis_start_time = time.monotonic()
        self.progress.setValue(0)
        self.progress_label.setText("解析準備中")
        self.append_log(f"解析を開始しました。第2スクリーニングCSVを読み込み、全日・全時間を一度だけ集計します。離れ閾値={self.max_off_route_spin.value():.1f}m")
        self.thread = QThread()
        self.worker = AnalysisWorker(self.project_dir, None, None, self.expansion_factors(), self.max_off_route_spin.value())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.update_analysis_progress)
        self.worker.finished.connect(self.analysis_done)
        self.worker.failed.connect(self.analysis_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def update_analysis_progress(self, percent: int, message: str, stats: dict) -> None:
        file_index = int(stats.get("current_file", 0) or 0)
        file_total = int(stats.get("total_files", 0) or 0)
        visual_percent = percent
        if file_total and file_index:
            file_percent = max(1, min(100, int(file_index / file_total * 100)))
            if str(stats.get("phase", "")).startswith("CSV"):
                visual_percent = file_percent
            else:
                visual_percent = max(visual_percent, file_percent)
        self.progress.setValue(visual_percent)
        route = stats.get("current_route_name") or stats.get("route") or "-"
        events = int(stats.get("events", 0))
        valid = int(stats.get("valid_points", 0))
        trips = int(stats.get("trips", stats.get("raw_trips", 0)))
        elapsed, remaining = self.elapsed_remaining_text(visual_percent)
        self.update_route_hit_bars(stats.get("route_valid_points", []))
        self.progress_label.setText(
            f"解析中: CSV {file_index}/{file_total} / 有効点 {valid:,} / 投入 {events:,} / 経過 {elapsed} / 残り {remaining}"
        )
        self.update_stat("phase", "解析中")
        self.update_stat("files", f"{file_index} / {file_total}")
        self.update_stat("rows", f"{valid:,}")
        self.update_stat("time", f"{elapsed} / {remaining}")
        self.update_stat("events", f"{events:,}")
        bucket = percent // 5
        if bucket != self._last_analysis_log_bucket or percent >= 100:
            self._last_analysis_log_bucket = bucket
            self.append_log(f"{percent:3d}% {route} / 有効点={valid:,} / trip={trips:,} / event={events:,}")

    def analysis_done(self, result: dict) -> None:
        self.result = result
        self.run_button.setEnabled(True)
        self.viewer_button.setEnabled(True)
        self.set_factor_inputs_enabled(True)
        self.max_off_route_spin.setEnabled(True)
        self.progress.setValue(100)
        self.update_stat("phase", "解析完了")
        self.update_stat("time", f"{self.format_duration(time.monotonic() - self.analysis_start_time) if self.analysis_start_time else '00:00'} / 00:00")
        self.progress_label.setText(f"解析完了: {result.get('output_dir')}")
        self.append_log(self.progress_label.text())
        self.load_viewer()
        QMessageBox.information(self, "完了", "解析が完了しました。ビューアーを表示できます。")

    def analysis_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.set_factor_inputs_enabled(True)
        self.max_off_route_spin.setEnabled(True)
        self.progress_label.setText("解析失敗")
        self.append_log(f"解析失敗: {message}")
        QMessageBox.critical(self, "解析失敗", message)

    def load_viewer(self) -> None:
        if not self.result:
            return
        viewer = self.result.get("viewer")
        if viewer and self.web is not None:
            self.show_viewer_loading_dialog("ビューアーデータを読み込み中です。\n地図と全路線の表示データをWebViewへ読み込んでいます。")
            self.web.load(QUrl.fromLocalFile(str(Path(viewer).resolve())))

    def show_viewer_loading_dialog(self, message: str) -> None:
        self.close_viewer_loading_dialog()
        dialog = QProgressDialog(message, None, 0, 0, self)
        dialog.setWindowTitle("データ読み込み中")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.setCancelButton(None)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.show()
        QApplication.processEvents()
        self.viewer_load_dialog = dialog

    def close_viewer_loading_dialog(self) -> None:
        if self.viewer_load_dialog is not None:
            self.viewer_load_dialog.close()
            self.viewer_load_dialog.deleteLater()
            self.viewer_load_dialog = None

    def viewer_load_finished(self, ok: bool) -> None:
        self.close_viewer_loading_dialog()
        if not ok and self.result:
            self.append_log("ビューアーHTMLの読み込みに失敗しました。外部ブラウザまたはビューアー専用バッチで確認してください。")

    def load_route_map(self, routes: list[dict[str, object]]) -> None:
        if self.web is None or not routes:
            return
        self.pending_route_map_payload = self.route_map_payload(routes)
        base = QUrl.fromLocalFile(str(SRC_DIR) + "/")
        self.web.setHtml(ROUTE_MAP_HTML, base)

    def route_map_load_finished(self, ok: bool) -> None:
        if not ok or not self.pending_route_map_payload:
            return
        payload = self.pending_route_map_payload
        self.pending_route_map_payload = None
        self.run_route_map_js(f"window._routePerformanceMap.setRoutes({json.dumps(payload, ensure_ascii=False)});")

    def route_map_payload(self, routes: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {
                "name": route.get("name", ""),
                "length_km": route.get("length_km", 0),
                "points": route.get("points", 0),
                "coords": route.get("coords", []),
            }
            for route in routes
        ]

    def run_route_map_js(self, js: str, retry_ms: int = 120) -> None:
        if self.web is None:
            return
        wrapped = (
            "(function(){"
            "if (window._routePerformanceMap) {"
            f"{js}"
            "return true;"
            "}"
            "return false;"
            "})();"
        )

        def callback(ok):
            if ok:
                return
            QTimer.singleShot(retry_ms, lambda: self.web.page().runJavaScript(wrapped, callback))

        self.web.page().runJavaScript(wrapped, callback)

    def open_viewer(self) -> None:
        if not self.result:
            return
        viewer = self.result.get("viewer")
        if not viewer:
            return
        if self.web is not None:
            self.load_viewer()
            return
        subprocess.Popen([sys.executable, "-m", "webbrowser", str(Path(viewer).resolve())])

    def format_duration(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, sec = divmod(rem, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    def elapsed_remaining_text(self, percent: int) -> tuple[str, str]:
        if not self.analysis_start_time:
            return "00:00", "--:--"
        elapsed_sec = time.monotonic() - self.analysis_start_time
        if percent <= 0:
            return self.format_duration(elapsed_sec), "--:--"
        remaining_sec = elapsed_sec * max(0, 100 - percent) / max(percent, 1)
        return self.format_duration(elapsed_sec), self.format_duration(remaining_sec)

    def update_route_hit_bars(self, values: object) -> None:
        if not isinstance(values, list):
            return
        counts: list[int] = []
        for value in values:
            try:
                counts.append(int(value))
            except Exception:
                counts.append(0)
        if not counts:
            return
        max_count = max(counts) or 1
        for row, count in enumerate(counts[: self.route_table.rowCount()]):
            item = self.route_table.item(row, 3)
            if item is None:
                item = QTableWidgetItem()
                self.route_table.setItem(row, 3, item)
            item.setText(f"{count:,}")
            bar = self.route_table.cellWidget(row, 4)
            pct = int(round(count / max_count * 100)) if max_count else 0
            if isinstance(bar, QProgressBar):
                bar.setValue(pct)
                bar.setFormat(f"{pct}%")

    def update_stat(self, key: str, value: object) -> None:
        label = self.stats.get(key)
        if label is not None:
            label.setText(str(value))

    def append_log(self, message: str) -> None:
        self.log.appendPlainText(str(message))


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
