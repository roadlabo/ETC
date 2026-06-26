from __future__ import annotations

import csv
import faulthandler
import importlib.util
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QObject, QThread, QTimer, QUrl, pyqtSignal, qInstallMessageHandler
from PyQt6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
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
    PERF_PATH = SRC_DIR.parent / "30_route_performance.py"
if not PERF_PATH.exists():
    PERF_PATH = SRC_DIR / "unreleased" / "30_route_performance.py"
spec = importlib.util.spec_from_file_location("route_performance30", PERF_PATH)
perf = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = perf
spec.loader.exec_module(perf)

APP_ROOT = SRC_DIR.parent.parent if SRC_DIR.name.lower() == "unreleased" else SRC_DIR.parent
LOG_DIR = APP_ROOT / "logs"
RUNTIME_LOG = LOG_DIR / "30_UI_route_performance_runtime.log"
_LOG_HANDLE = None


def append_runtime_log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with RUNTIME_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {message}\n")


def install_runtime_logging() -> None:
    global _LOG_HANDLE
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_HANDLE = RUNTIME_LOG.open("a", encoding="utf-8", buffering=1)
    _LOG_HANDLE.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] 30 UI start\n")
    faulthandler.enable(_LOG_HANDLE, all_threads=True)

    def excepthook(exc_type, exc, tb) -> None:
        append_runtime_log("UNHANDLED PYTHON EXCEPTION\n" + "".join(traceback.format_exception(exc_type, exc, tb)))

    def qt_message_handler(mode, context, message) -> None:
        append_runtime_log(f"QT MESSAGE {mode}: {message}")

    sys.excepthook = excepthook
    qInstallMessageHandler(qt_message_handler)


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
function setStatus(text) { const el = document.getElementById('mapStatus'); if (el) el.textContent = text; }
function validPoint(pt) { return Array.isArray(pt) && Number.isFinite(Number(pt[0])) && Number.isFinite(Number(pt[1])); }
function allPoints(routes) { return routes.flatMap(route => (route.coords || []).filter(validPoint).map(pt => [Number(pt[0]), Number(pt[1])])); }
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
  if (typeof L === 'undefined') { showFallback(lastRoutes); return false; }
  if (map) return true;
  try {
    map = L.map('map', { zoomControl:true, preferCanvas:true }).setView([35.069095, 134.004512], 12);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors © CARTO' }).addTo(map);
    routeLayer = L.layerGroup().addTo(map);
    setStatus('WEB');
    return true;
  } catch (e) { showFallback(lastRoutes); return false; }
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
  } catch (e) { showFallback(lastRoutes); return false; }
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
                item = {
                    "name": route.name,
                    "path": str(route_path),
                    "length_km": round(route.length_m / 1000, 3),
                    "points": len(route.kp_m),
                    "coords": list(zip(route.lats, route.lons)),
                }
                routes.append(item)
                self.route_loaded.emit(item)
                self.progress.emit(
                    int(100 * idx / max(len(route_files), 1)),
                    f"ルート読込 {idx}/{len(route_files)}",
                    {"phase": "ルート読込", "routes": len(routes), "route_total": len(route_files)},
                )
            self.finished.emit({"input_dir": str(input_dir), "route_dir": str(route_dir), "output_dir": str(output_dir), "routes": routes})
        except Exception as exc:
            self.failed.emit(str(exc))


class AnalysisWorker(QObject):
    progress = pyqtSignal(int, str, dict)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, project_dir: str, factors: dict[str, float], max_off_route_m: float) -> None:
        super().__init__()
        self.project_dir = project_dir
        self.factors = factors
        self.max_off_route_m = max_off_route_m

    def run(self) -> None:
        try:
            result = perf.analyze_project(
                self.project_dir,
                recursive=True,
                allowed_dates=None,
                allowed_hours=None,
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
        self.result: dict | None = None
        self.pending_route_map_payload: list[dict[str, object]] | None = None
        self.stats: dict[str, QLabel] = {}
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
        title = QLabel("30 Route Performance: ETC2.0 analysis")
        title.setObjectName("title")
        self.project_label = QLabel("Project folder: not selected")
        self.project_label.setObjectName("projectLabel")
        choose = QPushButton("プロジェクト選択")
        choose.clicked.connect(self.choose_project)
        self.run_button = QPushButton("解析開始")
        self.run_button.clicked.connect(self.start_analysis)
        self.run_button.setEnabled(False)
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

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self._build_route_box(), 5)
        left_layout.addWidget(self._build_log_box(), 1)
        content.addWidget(left, 1)

        right = self._box("ルートマップ")
        if QWebEngineView is not None:
            self.web = QWebEngineView()
            if QWebEngineSettings is not None:
                settings = self.web.settings()
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            self.web.loadFinished.connect(self.route_map_load_finished)
            if hasattr(self.web, "renderProcessTerminated"):
                self.web.renderProcessTerminated.connect(self.web_render_process_terminated)
            right.layout().addWidget(self.web, 1)
        else:
            self.web = None
            right.layout().addWidget(QLabel("PyQt6-WebEngine が無いため、この画面ではルートマップを表示できません。"))
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
            QTableWidget, QPlainTextEdit { background:#020617; color:#e6f1ff; border:1px solid #334155; }
            QHeaderView::section { background:#111827; color:#00ff99; padding:6px; }
            QDoubleSpinBox { background:#020617; color:#e6f1ff; border:1px solid #334155; padding:4px; }
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
        for key, caption, initial in [
            ("phase", "PHASE", "-"),
            ("files", "FILES", "0 / 0"),
            ("rows", "ROWS", "0"),
            ("time", "TIME", "00:00 / --:--"),
            ("events", "EVENTS", "0"),
        ]:
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

    def _build_route_box(self) -> QFrame:
        box = self._box("路線別 拡大係数")
        self.route_table = QTableWidget(0, 6)
        self.route_table.setHorizontalHeaderLabels(["ルート名", "延長[km]", "点数", "有効点", "HIT比", "拡大係数"])
        self.route_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for idx in (1, 2, 3, 5):
            self.route_table.horizontalHeader().setSectionResizeMode(idx, QHeaderView.ResizeMode.ResizeToContents)
        self.route_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
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
        box = self._box("処理ログ")
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
        self.run_button.setEnabled(False)
        self.route_table.setRowCount(0)
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
        self.progress_label.setText(message)
        self.update_stat("phase", stats.get("phase", "-"))

    def add_route_row(self, route: dict) -> None:
        row = self.route_table.rowCount()
        self.route_table.insertRow(row)
        for col, value in enumerate([route.get("name", ""), route.get("length_km", ""), route.get("points", ""), "-", "-"]):
            self.route_table.setItem(row, col, QTableWidgetItem(str(value)))
        spin = QDoubleSpinBox()
        spin.setRange(0.001, 9999.0)
        spin.setDecimals(3)
        spin.setValue(1.0)
        self.route_table.setCellWidget(row, 5, spin)

    def project_scan_done(self, info: dict) -> None:
        routes = info.get("routes", [])
        self.run_button.setEnabled(bool(routes))
        self.progress.setValue(100)
        self.progress_label.setText(f"ルート読込完了: {len(routes)}路線")
        self.append_log(self.progress_label.text())
        self.load_route_map(routes)

    def project_scan_failed(self, message: str) -> None:
        self.progress_label.setText("プロジェクト読込失敗")
        self.append_log(f"プロジェクト読込失敗: {message}")
        QMessageBox.critical(self, "プロジェクト読込失敗", message)

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
        path, _ = QFileDialog.getSaveFileName(self, "拡大係数を保存", self.default_factor_path(), "CSV files (*.csv);;All files (*.*)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["route", "expansion_factor"])
            for route, factor in self.expansion_factors().items():
                writer.writerow([route, factor])
        self.append_log(f"拡大係数を保存しました: {path}")

    def load_expansion_factors(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "拡大係数を読み込む", self.default_factor_path(), "CSV files (*.csv);;All files (*.*)")
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
        self.set_factor_inputs_enabled(False)
        self.max_off_route_spin.setEnabled(False)
        self.analysis_start_time = time.monotonic()
        self.progress.setValue(0)
        self.progress_label.setText("解析準備中")
        self._last_analysis_log_bucket = -1
        self.append_log(f"解析を開始しました。第2スクリーニングCSVを読み込み、全日・全時間を一度だけ集計します。離れ閾値={self.max_off_route_spin.value():.1f}m")
        self.thread = QThread()
        self.worker = AnalysisWorker(self.project_dir, self.expansion_factors(), self.max_off_route_spin.value())
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
        events = int(stats.get("events", 0) or 0)
        valid = int(stats.get("valid_points", 0) or 0)
        trips = int(stats.get("trips", stats.get("raw_trips", 0)) or 0)
        elapsed, remaining = self.elapsed_remaining_text(visual_percent)
        self.update_route_hit_bars(stats.get("route_valid_points", []))
        self.progress_label.setText(f"解析中: CSV {file_index}/{file_total} / 有効点 {valid:,} / 投入 {events:,} / 経過 {elapsed} / 残り {remaining}")
        self.update_stat("phase", "解析中")
        self.update_stat("files", f"{file_index} / {file_total}")
        self.update_stat("rows", f"{valid:,}")
        self.update_stat("time", f"{elapsed} / {remaining}")
        self.update_stat("events", f"{events:,}")
        bucket = percent // 5
        if bucket != self._last_analysis_log_bucket or percent >= 100:
            self._last_analysis_log_bucket = bucket
            self.append_log(f"{percent:3d}% / 有効点={valid:,} / trip={trips:,} / event={events:,}")

    def analysis_done(self, result: dict) -> None:
        self.result = result
        self.run_button.setEnabled(True)
        self.set_factor_inputs_enabled(True)
        self.max_off_route_spin.setEnabled(True)
        self.progress.setValue(100)
        self.update_stat("phase", "解析完了")
        elapsed = self.format_duration(time.monotonic() - self.analysis_start_time) if self.analysis_start_time else "00:00"
        self.update_stat("time", f"{elapsed} / 00:00")
        self.progress_label.setText(f"解析完了: {result.get('output_dir')}")
        self.append_log(self.progress_label.text())
        self.append_log("ビューアー表示とExcel抽出は 30-2_route_performance_viewer.py を使用してください。")
        QMessageBox.information(self, "完了", "解析が完了しました。ビューアー表示とExcel抽出は30-2のビューアーで行ってください。")

    def analysis_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.set_factor_inputs_enabled(True)
        self.max_off_route_spin.setEnabled(True)
        self.progress_label.setText("解析失敗")
        self.append_log(f"解析失敗: {message}")
        QMessageBox.critical(self, "解析失敗", message)

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
            {"name": route.get("name", ""), "length_km": route.get("length_km", 0), "points": route.get("points", 0), "coords": route.get("coords", [])}
            for route in routes
        ]

    def run_route_map_js(self, js: str, retry_ms: int = 120) -> None:
        if self.web is None:
            return
        wrapped = "(function(){if (window._routePerformanceMap) {" + js + "return true;}return false;})();"

        def callback(ok):
            if ok:
                return
            if self.web is not None:
                QTimer.singleShot(retry_ms, lambda: self.web.page().runJavaScript(wrapped, callback))

        self.web.page().runJavaScript(wrapped, callback)

    def web_render_process_terminated(self, *args) -> None:
        append_runtime_log(f"WEBENGINE RENDER PROCESS TERMINATED: {args}")
        self.append_log("ルートマップ表示エンジンが停止しました。")

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
            elif bar is None:
                new_bar = QProgressBar()
                new_bar.setRange(0, 100)
                new_bar.setValue(pct)
                new_bar.setFormat(f"{pct}%")
                self.route_table.setCellWidget(row, 4, new_bar)

    def update_stat(self, key: str, value: object) -> None:
        label = self.stats.get(key)
        if label is not None:
            label.setText(str(value))

    def append_log(self, message: str) -> None:
        self.log.appendPlainText(str(message))

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


def main() -> None:
    install_runtime_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
