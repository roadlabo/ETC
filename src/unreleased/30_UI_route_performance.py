from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QObject, QDate, QThread, QUrl, pyqtSignal
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
    QPushButton,
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


def date_token_to_qdate(token: str) -> QDate:
    return QDate(int(token[:4]), int(token[4:6]), int(token[6:8]))


def qdate_to_token(qdate: QDate) -> str:
    return qdate.toString("yyyyMMdd")


def iter_csv_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        yield from csv.reader(fh)


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
                    }
                )
                self.route_loaded.emit(routes[-1])
                self.progress.emit(
                    min(15, int(15 * idx / max(len(route_files), 1))),
                    f"ルート読込 {idx}/{len(route_files)}: {route.name}",
                    {"phase": "ルート読込", "routes": len(routes), "route_total": len(route_files)},
                )

            files = perf.list_input_csvs(input_dir, True)
            dates: set[str] = set()
            rows = 0
            readable_files = 0
            for file_idx, csv_path in enumerate(files, start=1):
                file_rows = 0
                self.progress.emit(
                    15 + int(80 * (file_idx - 1) / max(len(files), 1)),
                    f"第2スクリーニング日付抽出 {file_idx}/{len(files)}: {csv_path.name}",
                    {
                        "phase": "日付抽出",
                        "current_file": csv_path.name,
                        "file_index": file_idx,
                        "file_total": len(files),
                        "rows": rows,
                        "dates": len(dates),
                    },
                )
                try:
                    for row in iter_csv_rows(csv_path):
                        rows += 1
                        file_rows += 1
                        dt = perf.parse_datetime_from_row(row)
                        if dt is not None:
                            dates.add(dt.strftime("%Y%m%d"))
                        if file_rows % 20000 == 0:
                            pct = 15 + int(80 * (file_idx - 1) / max(len(files), 1))
                            self.progress.emit(
                                pct,
                                f"読込中 {csv_path.name}: {file_rows:,} 行",
                                {
                                    "phase": "日付抽出",
                                    "current_file": csv_path.name,
                                    "file_index": file_idx,
                                    "file_total": len(files),
                                    "rows": rows,
                                    "dates": len(dates),
                                },
                            )
                    readable_files += 1
                except Exception as exc:
                    self.progress.emit(
                        15 + int(80 * file_idx / max(len(files), 1)),
                        f"読込スキップ {csv_path.name}: {exc}",
                        {
                            "phase": "日付抽出",
                            "current_file": csv_path.name,
                            "file_index": file_idx,
                            "file_total": len(files),
                            "rows": rows,
                            "dates": len(dates),
                        },
                    )

            self.progress.emit(
                100,
                f"プロジェクト読込完了: ルート {len(routes)} / 日付 {len(dates)} / 行 {rows:,}",
                {"phase": "読込完了", "rows": rows, "dates": len(dates), "file_total": len(files)},
            )
            self.finished.emit(
                {
                    "input_dir": str(input_dir),
                    "route_dir": str(route_dir),
                    "output_dir": str(output_dir),
                    "routes": routes,
                    "dates": sorted(dates),
                    "files": len(files),
                    "readable_files": readable_files,
                    "rows": rows,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class AnalysisWorker(QObject):
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
        self.resize(1680, 980)
        self.project_dir = ""
        self.available_dates: set[str] = set()
        self.selected_date_tokens: set[str] = set()
        self.formatted_dates: set[str] = set()
        self.hour_checks: dict[int, QCheckBox] = {}
        self.stats: dict[str, QLabel] = {}
        self.result: dict | None = None
        self._syncing_calendars = False
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
        title_row.addWidget(title)
        title_row.addWidget(self.project_label, 1)
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

        left = QWidget()
        left.setMinimumWidth(560)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self._build_calendar_box(), 2)
        left_layout.addWidget(self._build_hour_box(), 3)
        content.addWidget(left, 0)

        center = QWidget()
        center.setMinimumWidth(520)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)
        center_layout.addWidget(self._build_route_box(), 3)
        center_layout.addWidget(self._build_log_box(), 2)
        content.addWidget(center, 1)

        right = self._box("解析後ビューアー")
        if QWebEngineView is not None:
            self.web = QWebEngineView()
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
            ("dates", "DATES", "0"),
            ("routes", "ROUTES", "0"),
            ("current", "CURRENT", "-"),
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
            layout.addWidget(box)
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
        box = self._box("路線別 拡大係数 (20路線表示)")
        self.route_table = QTableWidget(0, 4)
        self.route_table.setHorizontalHeaderLabels(["ルート名", "延長[km]", "点数", "拡大係数"])
        self.route_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.route_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.route_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.route_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.route_table.verticalHeader().setDefaultSectionSize(28)
        box.layout().addWidget(self.route_table)
        return box

    def _build_log_box(self) -> QFrame:
        box = self._box("処理ログ")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
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
        self.route_table.setRowCount(0)
        self.available_dates.clear()
        self.selected_date_tokens.clear()
        self.clear_calendar_formats()
        self.progress.setValue(0)
        self.log.clear()
        self.append_log("プロジェクト読込を開始しました。第2スクリーニングCSVをバックグラウンドで確認します。")
        self.update_stat("phase", "読込開始")
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
        file_index = stats.get("file_index", 0)
        file_total = stats.get("file_total", 0)
        self.update_stat("files", f"{file_index} / {file_total}" if file_total else "0 / 0")
        self.update_stat("rows", f"{int(stats.get('rows', 0)):,}")
        self.update_stat("dates", str(stats.get("dates", 0)))
        self.update_stat("routes", str(stats.get("routes", 0)))
        self.update_stat("current", stats.get("current_file", "-"))
        self.append_log(message)

    def project_scan_done(self, result: dict) -> None:
        self.run_button.setEnabled(True)
        self.progress.setValue(100)
        self.available_dates = set(result.get("dates", []))
        self.selected_date_tokens = set(self.available_dates)
        routes = result.get("routes", [])
        if self.route_table.rowCount() != len(routes):
            self.populate_routes(routes)
        self.refresh_calendar_formats()
        self.update_stat("phase", "読込完了")
        self.update_stat("files", f"{result.get('readable_files', 0)} / {result.get('files', 0)}")
        self.update_stat("rows", f"{int(result.get('rows', 0)):,}")
        self.update_stat("dates", str(len(self.available_dates)))
        self.update_stat("routes", str(len(routes)))
        self.progress_label.setText(
            f"読込完了: ルート {len(routes)} / 日付 {len(self.available_dates)} / 行 {int(result.get('rows', 0)):,}"
        )
        self.append_log(self.progress_label.text())

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
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1000000.0)
        spin.setDecimals(3)
        spin.setValue(1.0)
        self.route_table.setCellWidget(row, 3, spin)

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
        self.update_stat("dates", f"{len(self.selected_date_tokens)} / {len(self.available_dates)}")

    def set_all_dates(self, checked: bool) -> None:
        self.selected_date_tokens = set(self.available_dates) if checked else set()
        self.refresh_calendar_formats()
        self.update_stat("dates", f"{len(self.selected_date_tokens)} / {len(self.available_dates)}")

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
            spin = self.route_table.cellWidget(row, 3)
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
        self.append_log("解析を開始しました。ルートごとに投影、バケツ投入、Excel/JSON出力を進めます。")
        self.thread = QThread()
        self.worker = AnalysisWorker(self.project_dir, self.selected_dates(), self.selected_hours(), self.expansion_factors())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.update_analysis_progress)
        self.worker.finished.connect(self.analysis_done)
        self.worker.failed.connect(self.analysis_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def update_analysis_progress(self, percent: int, message: str, stats: dict) -> None:
        self.progress.setValue(percent)
        route = stats.get("current_route_name") or stats.get("route") or "-"
        current_file = stats.get("current_file_name") or stats.get("current_file") or "-"
        events = int(stats.get("events", 0))
        valid = int(stats.get("valid_points", 0))
        trips = int(stats.get("trips", stats.get("raw_trips", 0)))
        self.progress_label.setText(f"{message} / route={route} / file={current_file}")
        self.update_stat("phase", "解析中")
        self.update_stat("files", f"{stats.get('current_file', 0)} / {stats.get('total_files', 0)}")
        self.update_stat("routes", f"{stats.get('current_route', 0)} / {stats.get('total_routes', 0)}")
        self.update_stat("rows", f"{valid:,}")
        self.update_stat("events", f"{events:,}")
        self.update_stat("current", route)
        self.append_log(f"{percent:3d}% {message} / 有効点={valid:,} / trip={trips:,} / event={events:,}")

    def analysis_done(self, result: dict) -> None:
        self.result = result
        self.run_button.setEnabled(True)
        self.viewer_button.setEnabled(True)
        self.progress.setValue(100)
        self.update_stat("phase", "解析完了")
        self.progress_label.setText(f"解析完了: {result.get('output_dir')}")
        self.append_log(self.progress_label.text())
        self.load_viewer()
        QMessageBox.information(self, "完了", "解析が完了しました。ビューアーを表示できます。")

    def analysis_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.progress_label.setText("解析失敗")
        self.append_log(f"解析失敗: {message}")
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
