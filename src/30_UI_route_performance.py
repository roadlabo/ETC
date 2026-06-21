from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QObject, QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover
    QWebEngineView = None

PERF_PATH = SRC_DIR / "30_route_performance.py"
if not PERF_PATH.exists():
    PERF_PATH = SRC_DIR / "unreleased" / "30_route_performance.py"
spec = importlib.util.spec_from_file_location("route_performance30", PERF_PATH)
perf = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = perf
spec.loader.exec_module(perf)


class Worker(QObject):
    progress = pyqtSignal(int, str, dict)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, project_dir: str, dates: set[str] | None, hours: set[int] | None, factors: dict[str, float]) -> None:
        super().__init__()
        self.project_dir = project_dir
        self.dates = dates
        self.hours = hours
        self.factors = factors

    def run(self) -> None:
        try:
            result = perf.analyze_project(
                self.project_dir,
                recursive=True,
                allowed_dates=self.dates,
                allowed_hours=self.hours,
                expansion_factors=self.factors,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("30 Route Performance")
        self.resize(1220, 840)
        self.project_dir = ""
        self.date_checks: dict[str, QCheckBox] = {}
        self.hour_checks: dict[int, QCheckBox] = {}
        self.result: dict | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        title = QLabel("30 Route Performance: ETC2.0 traffic condition viewer")
        title.setObjectName("title")
        main.addWidget(title)

        top = QHBoxLayout()
        self.project_label = QLabel("Project folder: not selected")
        choose = QPushButton("プロジェクト選択")
        choose.clicked.connect(self.choose_project)
        self.run_button = QPushButton("解析開始")
        self.run_button.clicked.connect(self.start_analysis)
        self.viewer_button = QPushButton("ビューアーを開く")
        self.viewer_button.clicked.connect(self.open_viewer)
        self.viewer_button.setEnabled(False)
        top.addWidget(self.project_label, 1)
        top.addWidget(choose)
        top.addWidget(self.run_button)
        top.addWidget(self.viewer_button)
        main.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress_label = QLabel("待機中")
        main.addWidget(self.progress)
        main.addWidget(self.progress_label)

        body = QHBoxLayout()
        main.addLayout(body, 1)

        left = QVBoxLayout()
        body.addLayout(left, 1)

        cal_box = self._box("対象日")
        self.calendar = QCalendarWidget()
        self.calendar.clicked.connect(self.toggle_calendar_date)
        cal_box.layout().addWidget(self.calendar)
        date_buttons = QHBoxLayout()
        all_dates = QPushButton("全日ON")
        no_dates = QPushButton("全日OFF")
        all_dates.clicked.connect(lambda: self.set_all_dates(True))
        no_dates.clicked.connect(lambda: self.set_all_dates(False))
        date_buttons.addWidget(all_dates)
        date_buttons.addWidget(no_dates)
        cal_box.layout().addLayout(date_buttons)
        self.date_holder = QWidget()
        self.date_layout = QVBoxLayout(self.date_holder)
        self.date_layout.setContentsMargins(4, 4, 4, 4)
        date_scroll = QScrollArea()
        date_scroll.setWidgetResizable(True)
        date_scroll.setWidget(self.date_holder)
        cal_box.layout().addWidget(date_scroll, 1)
        left.addWidget(cal_box, 2)

        hour_box = self._box("対象時間 (1時間単位)")
        hour_grid = QGridLayout()
        for hour in range(24):
            cb = QCheckBox(f"{hour:02d}")
            cb.setChecked(True)
            self.hour_checks[hour] = cb
            hour_grid.addWidget(cb, hour // 8, hour % 8)
        hour_box.layout().addLayout(hour_grid)
        hour_buttons = QHBoxLayout()
        all_hours = QPushButton("全時間ON")
        no_hours = QPushButton("全時間OFF")
        commute = QPushButton("朝夕")
        all_hours.clicked.connect(lambda: self.set_hours(range(24)))
        no_hours.clicked.connect(lambda: self.set_hours([]))
        commute.clicked.connect(lambda: self.set_hours([7, 8, 17, 18]))
        hour_buttons.addWidget(all_hours)
        hour_buttons.addWidget(no_hours)
        hour_buttons.addWidget(commute)
        hour_box.layout().addLayout(hour_buttons)
        left.addWidget(hour_box, 1)

        right = QVBoxLayout()
        body.addLayout(right, 2)
        route_box = self._box("路線別 拡大係数")
        self.route_table = QTableWidget(0, 3)
        self.route_table.setHorizontalHeaderLabels(["ルート", "CSV", "拡大係数"])
        self.route_table.horizontalHeader().setStretchLastSection(True)
        route_box.layout().addWidget(self.route_table)
        right.addWidget(route_box, 1)

        self.viewer_area = self._box("解析後ビューアー")
        if QWebEngineView is not None:
            self.web = QWebEngineView()
            self.viewer_area.layout().addWidget(self.web, 1)
        else:
            self.web = None
            self.viewer_area.layout().addWidget(QLabel("PyQt6-WebEngine が無い場合は外部ブラウザで開きます。"))
        right.addWidget(self.viewer_area, 2)

        self.setStyleSheet(
            """
            QWidget { background:#0b0f14; color:#e6f1ff; font-family:"Segoe UI","Meiryo UI"; font-size:12px; }
            QLabel#title { color:#00ff99; font-size:22px; font-weight:800; }
            QFrame#box { border:1px solid rgba(0,255,153,.45); border-radius:8px; background:#111827; }
            QLabel#boxTitle { color:#facc15; font-weight:800; }
            QPushButton { border:1px solid #00ff99; border-radius:7px; padding:7px 10px; background:#10231a; color:#e6f1ff; font-weight:700; }
            QPushButton:hover { background:#16432b; }
            QProgressBar { border:1px solid #00ff99; height:18px; text-align:center; background:#020617; }
            QProgressBar::chunk { background:#00ff99; }
            QTableWidget, QScrollArea, QCalendarWidget QAbstractItemView { background:#020617; color:#e6f1ff; border:1px solid #334155; }
            QHeaderView::section { background:#111827; color:#00ff99; padding:6px; }
            QDoubleSpinBox { background:#020617; color:#e6f1ff; border:1px solid #334155; padding:4px; }
            """
        )

    def _box(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("box")
        layout = QVBoxLayout(frame)
        label = QLabel(title)
        label.setObjectName("boxTitle")
        layout.addWidget(label)
        return frame

    def choose_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択")
        if not path:
            return
        self.project_dir = path
        self.project_label.setText(f"Project folder: {path}")
        self.load_project()

    def load_project(self) -> None:
        self.route_table.setRowCount(0)
        self.clear_dates()
        try:
            input_dir, route_dir, _out_dir = perf.resolve_project_paths(self.project_dir)
            routes = perf.list_route_csvs(route_dir)
            self.route_table.setRowCount(len(routes))
            for row, route in enumerate(routes):
                self.route_table.setItem(row, 0, QTableWidgetItem(route.stem))
                self.route_table.setItem(row, 1, QTableWidgetItem(route.name))
                spin = QDoubleSpinBox()
                spin.setRange(0.0, 1000000.0)
                spin.setDecimals(3)
                spin.setValue(1.0)
                self.route_table.setCellWidget(row, 2, spin)
            dates = perf.extract_available_dates(input_dir, True)
            for token in dates:
                cb = QCheckBox(f"{token[:4]}-{token[4:6]}-{token[6:8]}")
                cb.setChecked(True)
                cb.setProperty("token", token)
                self.date_checks[token] = cb
                self.date_layout.addWidget(cb)
            self.date_layout.addStretch(1)
            self.progress_label.setText(f"ルート {len(routes)} 件 / 対象日 {len(dates)} 日")
        except Exception as exc:
            QMessageBox.critical(self, "読み込みエラー", str(exc))

    def clear_dates(self) -> None:
        self.date_checks.clear()
        while self.date_layout.count():
            item = self.date_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_all_dates(self, checked: bool) -> None:
        for cb in self.date_checks.values():
            cb.setChecked(checked)

    def toggle_calendar_date(self, qdate) -> None:
        token = qdate.toString("yyyyMMdd")
        cb = self.date_checks.get(token)
        if cb is not None:
            cb.setChecked(not cb.isChecked())

    def set_hours(self, hours) -> None:
        hours = set(hours)
        for hour, cb in self.hour_checks.items():
            cb.setChecked(hour in hours)

    def selected_dates(self) -> set[str] | None:
        if not self.date_checks:
            return None
        return {token for token, cb in self.date_checks.items() if cb.isChecked()}

    def selected_hours(self) -> set[int] | None:
        selected = {hour for hour, cb in self.hour_checks.items() if cb.isChecked()}
        return selected or set()

    def expansion_factors(self) -> dict[str, float]:
        factors: dict[str, float] = {}
        for row in range(self.route_table.rowCount()):
            name_item = self.route_table.item(row, 0)
            spin = self.route_table.cellWidget(row, 2)
            if name_item and isinstance(spin, QDoubleSpinBox):
                factors[name_item.text()] = spin.value()
        return factors

    def start_analysis(self) -> None:
        if not self.project_dir:
            QMessageBox.warning(self, "未選択", "先にプロジェクトフォルダを選択してください。")
            return
        self.run_button.setEnabled(False)
        self.viewer_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress_label.setText("解析準備中")
        self.thread = QThread()
        self.worker = Worker(self.project_dir, self.selected_dates(), self.selected_hours(), self.expansion_factors())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.analysis_done)
        self.worker.failed.connect(self.analysis_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def update_progress(self, percent: int, message: str, stats: dict) -> None:
        self.progress.setValue(percent)
        route = stats.get("current_route_name") or stats.get("route") or "-"
        events = stats.get("events", 0)
        valid = stats.get("valid_points", 0)
        self.progress_label.setText(f"{message} / route={route} / 投影点={valid} / 投入={events}")

    def analysis_done(self, result: dict) -> None:
        self.result = result
        self.run_button.setEnabled(True)
        self.viewer_button.setEnabled(True)
        self.progress.setValue(100)
        self.progress_label.setText(f"解析完了: {result.get('output_dir')}")
        self.load_viewer()
        QMessageBox.information(self, "完了", "解析が完了しました。ビューアーを表示できます。")

    def analysis_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.progress_label.setText("解析失敗")
        QMessageBox.critical(self, "解析失敗", message)

    def load_viewer(self) -> None:
        if not self.result:
            return
        viewer = self.result.get("viewer")
        if viewer and self.web is not None:
            self.web.load(QUrl.fromLocalFile(str(Path(viewer).resolve())))

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


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
