import math
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from common.ui.logo_link import ClickableLogoLabel
import importlib.util

_PERF_PATH = Path(__file__).resolve().parent / "30_build_performance.py"
_SPEC = importlib.util.spec_from_file_location("route_performance30", _PERF_PATH)
_perf = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_perf)
PERIODS = _perf.PERIODS
SHEET_NAMES = _perf.SHEET_NAMES
analyze = _perf.analyze
build_route_model = _perf.build_route_model

APP_TITLE = "30_ルートパフォーマンス（方向別KP集計）"


class RouteCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.points = []
        self.setMinimumHeight(280)

    def set_route(self, route_path: str):
        try:
            route = build_route_model(route_path)
            raw = list(zip(route.xs, route.ys))
            if len(raw) >= 2:
                sx, sy = raw[0]
                ex, ey = raw[-1]
                angle = math.atan2(ey - sy, ex - sx)
                ca, sa = math.cos(-angle), math.sin(-angle)
                self.points = [((x - sx) * ca - (y - sy) * sa, (x - sx) * sa + (y - sy) * ca) for x, y in raw]
            else:
                self.points = raw
        except Exception:
            self.points = []
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#020403"))
        grid = QPen(QColor("#16351f"), 1)
        p.setPen(grid)
        for x in range(0, self.width(), 32):
            p.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 32):
            p.drawLine(0, y, self.width(), y)
        if len(self.points) < 2:
            p.setPen(QPen(QColor("#00ff99"), 1))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "ルートファイル選択後に簡易ルートを表示\n（オンライン地図タイルは接続可能時のみ利用する想定）")
            return
        xs = [x for x, _ in self.points]; ys = [y for _, y in self.points]
        minx, maxx = min(xs), max(xs); miny, maxy = min(ys), max(ys)
        w = max(maxx - minx, 1); h = max(maxy - miny, 1)
        margin = 24
        sx = (self.width() - margin * 2) / w
        sy = (self.height() - margin * 2) / h
        s = min(sx, sy)
        def map_pt(pt):
            x, y = pt
            return int(margin + (x - minx) * s), int(self.height() - margin - (y - miny) * s)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#00ff99"), 3))
        prev = map_pt(self.points[0])
        for pt in self.points[1:]:
            cur = map_pt(pt); p.drawLine(prev[0], prev[1], cur[0], cur[1]); prev = cur
        p.setPen(QPen(QColor("#f6d36b"), 2))
        a = map_pt(self.points[0]); b = map_pt(self.points[-1])
        p.drawEllipse(a[0]-5, a[1]-5, 10, 10); p.drawText(a[0]+8, a[1], "START")
        p.drawEllipse(b[0]-5, b[1]-5, 10, 10); p.drawText(b[0]+8, b[1], "END")


class PlotPanel(FigureCanvas):
    def __init__(self):
        self.fig = Figure(figsize=(8, 3), facecolor="#050b09")
        self.ax_speed = self.fig.add_subplot(211)
        self.ax_count = self.fig.add_subplot(212)
        super().__init__(self.fig)

    def plot(self, kp, speed, count, title):
        for ax in (self.ax_speed, self.ax_count):
            ax.clear(); ax.set_facecolor("#050b09"); ax.tick_params(colors="#b7ffd8")
            for spine in ax.spines.values(): spine.set_color("#00ff99")
            ax.grid(color="#16351f")
        self.ax_speed.plot(kp, speed, color="#00ff99")
        self.ax_speed.set_ylabel("速度 km/h", color="#b7ffd8")
        self.ax_speed.set_title(title, color="#f6d36b")
        self.ax_count.bar(kp, count, color="#56d27f", width=0.01)
        self.ax_count.set_ylabel("トリップ数", color="#b7ffd8")
        self.ax_count.set_xlabel("KP[km]", color="#b7ffd8")
        self.fig.tight_layout()
        self.draw()


class Worker(QObject):
    progress = pyqtSignal(int, str, dict)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, input_dir, route_path, output_path):
        super().__init__(); self.input_dir = input_dir; self.route_path = route_path; self.output_path = output_path

    def run(self):
        try:
            analyze(self.input_dir, self.route_path, self.output_path, True, self.progress.emit)
            self.finished.emit(self.output_path)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(APP_TITLE); self.resize(1180, 820)
        self.output_path = ""; self.book = {}
        root = QWidget(); self.setCentralWidget(root); lay = QVBoxLayout(root)
        title = QLabel("APOLLO ROUTE PERFORMANCE CONSOLE")
        title.setObjectName("title"); lay.addWidget(title)
        form = QGridLayout(); lay.addLayout(form)
        self.input_edit = QLineEdit(); self.route_edit = QLineEdit()
        btn_in = QPushButton("第2スクリーニング後フォルダ選択"); btn_route = QPushButton("ルートファイル選択"); self.btn_run = QPushButton("解析開始")
        form.addWidget(QLabel("DATA FOLDER"), 0, 0); form.addWidget(self.input_edit, 0, 1); form.addWidget(btn_in, 0, 2)
        form.addWidget(QLabel("ROUTE FILE"), 1, 0); form.addWidget(self.route_edit, 1, 1); form.addWidget(btn_route, 1, 2); form.addWidget(self.btn_run, 2, 2)
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        self.progress_label = QLabel("待機中")
        self.progress_label.setObjectName("progressLabel")
        lay.addWidget(self.progress_bar); lay.addWidget(self.progress_label)
        self.stats = {}
        stats_frame = QFrame()
        stats_frame.setObjectName("telemetryFrame")
        stats_layout = QGridLayout(stats_frame)
        stats_layout.setContentsMargins(10, 8, 10, 8)
        stats_layout.setHorizontalSpacing(12)
        stats_layout.setVerticalSpacing(6)
        stat_specs = [
            ("files", "FILES", "0 / 0"),
            ("current_file", "NOW", "-"),
            ("raw_trips", "RAW TRIPS", "0"),
            ("split_count", "SPLITS", "0"),
            ("split_total_trips", "SPLIT TRIPS", "0"),
            ("events", "KP EVENTS", "0"),
        ]
        for col, (key, caption, initial) in enumerate(stat_specs):
            box = QFrame()
            box.setObjectName("telemetryBox")
            box_lay = QVBoxLayout(box)
            box_lay.setContentsMargins(8, 4, 8, 4)
            cap = QLabel(caption)
            cap.setObjectName("telemetryCaption")
            val = QLabel(initial)
            val.setObjectName("telemetryValue")
            box_lay.addWidget(cap)
            box_lay.addWidget(val)
            stats_layout.addWidget(box, 0, col)
            self.stats[key] = val
        lay.addWidget(stats_frame)
        self.route_canvas = RouteCanvas(); lay.addWidget(self.route_canvas)
        controls = QHBoxLayout(); lay.addLayout(controls)
        self.period_combo = QComboBox(); self.period_combo.addItems(PERIODS)
        self.hour_combo = QComboBox(); self.hour_combo.addItems([f"{h:02d}時台" for h in range(24)])
        self.direction_combo = QComboBox(); self.direction_combo.addItems(["順方向", "逆方向"])
        for label, widget in (("区分", self.period_combo), ("時間帯", self.hour_combo), ("方向", self.direction_combo)):
            controls.addWidget(QLabel(label)); controls.addWidget(widget)
        self.plot_panel = PlotPanel(); lay.addWidget(self.plot_panel, 1)
        btn_in.clicked.connect(self.choose_input); btn_route.clicked.connect(self.choose_route); self.btn_run.clicked.connect(self.start_analysis)
        self.period_combo.currentIndexChanged.connect(self.refresh_plot); self.hour_combo.currentIndexChanged.connect(self.refresh_plot); self.direction_combo.currentIndexChanged.connect(self.refresh_plot)
        self.setStyleSheet("""
            QWidget{background:#050b09;color:#b7ffd8;font-family:'Consolas','Yu Gothic UI';font-size:13px;}
            QLabel#title{color:#f6d36b;font-size:22px;font-weight:800;letter-spacing:2px;}
            QLineEdit,QComboBox{border:1px solid #00ff99;background:#020403;color:#b7ffd8;padding:6px;}
            QLabel#progressLabel{color:#f6d36b;font-weight:700;}
            QProgressBar{border:1px solid #00ff99;background:#020403;color:#f6d36b;text-align:center;height:18px;}
            QProgressBar::chunk{background:#00ff99;}
            QFrame#telemetryFrame{border:1px solid #214f32;background:#07110c;}
            QFrame#telemetryBox{border:1px solid #00ff99;background:#020403;}
            QLabel#telemetryCaption{color:#56d27f;font-size:11px;font-weight:700;}
            QLabel#telemetryValue{color:#f6d36b;font-size:19px;font-weight:900;}
            QPushButton{border:2px solid #00ff99;border-radius:8px;background:#102318;color:#00ff99;font-weight:700;padding:8px;}
            QPushButton:hover{background:#173d29;}
        """)

    def choose_input(self):
        d = QFileDialog.getExistingDirectory(self, "第2スクリーニング後データフォルダ")
        if d: self.input_edit.setText(d)

    def choose_route(self):
        f, _ = QFileDialog.getOpenFileName(self, "ルートCSV", "", "CSV (*.csv);;All Files (*)")
        if f:
            self.route_edit.setText(f); self.route_canvas.set_route(f)

    def start_analysis(self):
        input_dir = self.input_edit.text().strip(); route_path = self.route_edit.text().strip()
        if not input_dir or not route_path:
            QMessageBox.warning(self, "入力不足", "データフォルダとルートファイルを選択してください。")
            return
        self.output_path = str(Path(input_dir).parent / "30_ルートパフォーマンス" / "route_performance_directional.xlsx")
        self.btn_run.setEnabled(False); self.btn_run.setText("解析中...")
        self.progress_bar.setValue(0); self.progress_label.setText("解析準備中")
        self.update_progress(0, "解析準備中", {})
        self.thread = QThread(); self.worker = Worker(input_dir, route_path, self.output_path); self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run); self.worker.progress.connect(self.update_progress); self.worker.finished.connect(self.analysis_done); self.worker.failed.connect(self.analysis_failed)
        self.worker.finished.connect(self.thread.quit); self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def update_progress(self, percent, message, stats=None):
        self.progress_bar.setValue(percent)
        self.progress_label.setText(message)
        stats = stats or {}
        current = stats.get("current_file", 0)
        total = stats.get("total_files", 0)
        current_name = stats.get("current_file_name") or "-"
        self.stats["files"].setText(f"{current} / {total}")
        self.stats["current_file"].setText(current_name)
        self.stats["raw_trips"].setText(str(stats.get("raw_trips", 0)))
        self.stats["split_count"].setText(str(stats.get("split_count", 0)))
        self.stats["split_total_trips"].setText(str(stats.get("split_total_trips", 0)))
        self.stats["events"].setText(str(stats.get("events", 0)))

    def analysis_done(self, path):
        self.btn_run.setEnabled(True); self.btn_run.setText("解析開始")
        self.progress_bar.setValue(100); self.progress_label.setText("解析完了")
        self.book = pd.read_excel(path, sheet_name=None)
        QMessageBox.information(self, "完了", f"Excelを出力しました。\n{path}")
        self.refresh_plot()

    def analysis_failed(self, message):
        self.btn_run.setEnabled(True); self.btn_run.setText("解析開始")
        self.progress_label.setText("解析失敗")
        QMessageBox.critical(self, "解析失敗", message)

    def refresh_plot(self):
        if not self.book: return
        direction = "forward" if self.direction_combo.currentText() == "順方向" else "reverse"
        speed_sheet = SHEET_NAMES[f"{direction}_speed"]; count_sheet = SHEET_NAMES[f"{direction}_count"]
        period = self.period_combo.currentText(); hour = self.hour_combo.currentIndex(); col = f"{period}_{hour:02d}時台"
        if speed_sheet not in self.book or col not in self.book[speed_sheet]: return
        sp = self.book[speed_sheet]; ct = self.book[count_sheet]
        kp = sp["KP[km]"].astype(float)
        speed = pd.to_numeric(sp[col], errors="coerce")
        count = pd.to_numeric(ct[col], errors="coerce").fillna(0)
        self.plot_panel.plot(kp, speed, count, f"{self.direction_combo.currentText()} / {period} / {hour:02d}時台")


def main():
    app = QApplication(sys.argv)
    w = MainWindow(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
