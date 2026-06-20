import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from time import perf_counter

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QProcess, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "20_第２スクリーニング（ルート通過トリップの抽出）"
FOLDER_ROUTE = "10_ルート(Route)データ"
FOLDER_OUT = "20_第２スクリーニング(ルート)"

RE_LEVEL = re.compile(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]")
RE_FILE_PROCESSED = re.compile(r"進捗ファイル:\s*([0-9,]+)\s*files\s*processed")
RE_HIT = re.compile(r"^HIT:\s*(.+)\s+(\d+)\s*$")
RE_ROUTE = re.compile(r"^ROUTE:\s*(.+)\s+(\d+)\s*$")


def resolve_project_paths(project_dir: Path) -> tuple[Path, Path]:
    return project_dir / FOLDER_ROUTE, project_dir / FOLDER_OUT


def format_hhmmss(total_sec: float) -> str:
    sec = int(total_sec + 0.5)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=8):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        l, t, r, b = self.getContentsMargins()
        size += QSize(l + r, t + b)
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        for item in self._items:
            wid = item.widget()
            space_x = self.spacing()
            space_y = self.spacing()
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, item.sizeHint().width(), item.sizeHint().height()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()


class RouteCard(QFrame):
    def __init__(self, name: str, point_count: int):
        super().__init__()
        self.name = name
        self.setObjectName("routeCard")
        self.setMinimumWidth(220)
        self.setMaximumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        self.title = QLabel(name)
        self.title.setWordWrap(True)
        self.title.setFont(QFont("Meiryo UI", 10, QFont.Weight.Bold))
        self.points = QLabel(f"ルート構成ポイント数: {point_count:,}")
        self.hit = QLabel("HITトリップ数: 0")
        self.state = QLabel("状態: 待機")
        for w in (self.title, self.points, self.hit, self.state):
            w.setStyleSheet("border:none;")
            v.addWidget(w)

    def set_point_count(self, count: int) -> None:
        self.points.setText(f"ルート構成ポイント数: {count:,}")

    def set_hit_count(self, count: int) -> None:
        self.hit.setText(f"HITトリップ数: {count:,}")

    def set_state(self, text: str) -> None:
        self.state.setText(f"状態: {text}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 820)
        self.project_dir: Path | None = None
        self.input_dir: Path | None = None
        self.proc: QProcess | None = None
        self.total_files = 0
        self.done_files = 0
        self.started_at = 0.0
        self.batch_start_perf: float | None = None
        self.cards: dict[str, RouteCard] = {}
        self._next_pct_log = 10
        self._telemetry_running = False

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_time_boxes)
        self.timer.start(1000)

        self._build_ui()
        self._set_style()
        self.log_info("プロジェクトフォルダと第1スクリーニングフォルダを選択してください。")

    def _get_root_dir(self) -> str:
        here = os.path.abspath(os.path.dirname(__file__))
        return os.path.abspath(os.path.join(here, ".."))

    def _get_embedded_python(self, root_dir: str) -> str:
        py = os.path.join(root_dir, "runtime", "python", "python.exe")
        return py if os.path.isfile(py) else ""

    def _find_05_script(self, root_dir: str) -> str:
        candidates = [
            os.path.join(root_dir, "src", "05_trip_viewer.py"),
            os.path.join(root_dir, "src", "05_route_mapper_simple.py"),
            os.path.join(root_dir, "src", "05_2nd_screening_trip_viewer.py"),
            os.path.join(root_dir, "src", "05_UI_second_screening_trip_viewer.py"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def _launch_05_viewer(self, input_dir: str) -> bool:
        root = self._get_root_dir()
        py = self._get_embedded_python(root)
        script = self._find_05_script(root)
        if not py:
            self.log_error("embedded python not found: <ROOT>\\runtime\\python\\python.exe")
            return False
        if not script:
            self.log_error("05 trip viewer script not found under <ROOT>\\src")
            return False
        if not input_dir or not os.path.isdir(input_dir):
            self.log_error(f"viewer input folder not found: {input_dir}")
            return False
        ok = QProcess.startDetached(py, [script, input_dir], root)
        if ok:
            self.log_info(f"trip viewer launched: {input_dir}")
        else:
            self.log_error("failed to launch trip viewer")
        return ok

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        about = QLabel(
            "第1スクリーニング後データを先頭から順に確認し、10_ルート(Route)データ内のいずれかのルートで、"
            "半径内に入ったルート構成ポイントが3点以上あるトリップを抽出します。"
            "抽出結果は第2スクリーニング（ルート）フォルダへ1トリップ1CSVで保存します。"
        )
        about.setWordWrap(True)
        v.addWidget(about)

        steps = QGridLayout()
        steps.setHorizontalSpacing(10)
        self.btn_project = QPushButton("選択")
        self.btn_project.clicked.connect(self.select_project)
        self.lbl_project = QLabel("未選択")
        self.btn_input = QPushButton("選択")
        self.btn_input.clicked.connect(self.select_input)
        self.lbl_input = QLabel("未選択")
        self.chk_recursive = QCheckBox("サブフォルダも含める")
        self.spin_radius = QSpinBox()
        self.spin_radius.setRange(5, 500)
        self.spin_radius.setValue(30)
        self.spin_min_points = QSpinBox()
        self.spin_min_points.setRange(1, 20)
        self.spin_min_points.setValue(3)
        self.btn_run = QPushButton("分析スタート")
        self.btn_run.clicked.connect(self.run_screening)
        self.btn_run.setEnabled(False)

        steps.addWidget(QLabel("STEP 1 プロジェクトフォルダ"), 0, 0)
        steps.addWidget(self.btn_project, 0, 1)
        steps.addWidget(self.lbl_project, 0, 2)
        steps.addWidget(QLabel("STEP 2 第1スクリーニングフォルダ"), 1, 0)
        steps.addWidget(self.btn_input, 1, 1)
        steps.addWidget(self.lbl_input, 1, 2)
        steps.addWidget(self.chk_recursive, 1, 3)
        steps.addWidget(QLabel("STEP 3 半径(m)"), 2, 0)
        steps.addWidget(self.spin_radius, 2, 1)
        steps.addWidget(QLabel("同一ルート必要点数"), 2, 2)
        steps.addWidget(self.spin_min_points, 2, 3)
        steps.addWidget(QLabel("STEP 4 実行"), 3, 0)
        steps.addWidget(self.btn_run, 3, 1, 1, 3)
        steps.setColumnStretch(2, 1)
        v.addLayout(steps)

        content = QHBoxLayout()
        left = QFrame()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("ルートファイル一覧"))
        self.route_container = QWidget()
        self.route_flow = FlowLayout(self.route_container, margin=0, spacing=8)
        self.route_container.setLayout(self.route_flow)
        route_scroll = QScrollArea()
        route_scroll.setWidgetResizable(True)
        route_scroll.setWidget(self.route_container)
        lv.addWidget(route_scroll)
        content.addWidget(left, 4)

        right = QFrame()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("進捗"))
        self.lbl_route_total = QLabel("ルート数: 0")
        self.lbl_file_total = QLabel("第1スクリーニングCSV数: -")
        self.lbl_progress = QLabel("進捗ファイル: 0/0（0.0%）")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.time_elapsed = QLabel("経過 00:00:00")
        self.time_eta = QLabel("残り --:--:--")
        for label in (self.time_elapsed, self.time_eta):
            label.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        self.lbl_status = QLabel("状態: IDLE")
        self.lbl_saved = QLabel("保存先: -")
        for w in (self.lbl_route_total, self.lbl_file_total, self.lbl_progress, self.progress_bar, self.time_elapsed, self.time_eta, self.lbl_status, self.lbl_saved):
            rv.addWidget(w)
        rv.addStretch(1)
        content.addWidget(right, 1)
        v.addLayout(content, stretch=1)

        bottom = QHBoxLayout()
        self.btn_viewer = QPushButton("第2スクリーニング（ルート）トリップビューアー")
        self.btn_viewer.clicked.connect(self.open_trip_viewer)
        self.btn_viewer.setEnabled(False)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_viewer)
        v.addLayout(bottom)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 10))
        self.log.setMaximumBlockCount(2000)
        self.log.setFixedHeight(150)
        v.addWidget(self.log)

    def _set_style(self):
        self.setStyleSheet("""
            QWidget { background:#050908; color:#d6ffe8; font-family:"Meiryo UI","Segoe UI"; font-size:12px; }
            QPushButton { background:#0a1b14; border:1px solid #2ef29a; border-radius:8px; padding:7px 12px; font-weight:700; }
            QPushButton:hover { background:#103322; }
            QPushButton:disabled { color:#597262; border-color:#224432; }
            QFrame { border:1px solid #1c4f33; border-radius:6px; }
            QFrame#routeCard { background:rgba(0,255,153,0.06); border:1px solid #208956; border-radius:8px; }
            QLabel { border:none; }
            QPlainTextEdit, QSpinBox, QProgressBar, QScrollArea { background:#0a120f; border:1px solid #1f3f2d; border-radius:6px; }
            QProgressBar::chunk { background:#00ff99; border-radius:4px; }
        """)

    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _append_log(self, level: str, msg: str) -> None:
        self.log.appendPlainText(f"[{self._timestamp()}] [{level}] {msg}")

    def log_info(self, msg: str) -> None:
        self._append_log("INFO", msg)

    def log_warn(self, msg: str) -> None:
        self._append_log("WARN", msg)

    def log_error(self, msg: str) -> None:
        self._append_log("ERROR", msg)

    def _clear_cards(self):
        while self.route_flow.count():
            item = self.route_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.cards.clear()

    def _read_route_point_count(self, path: Path) -> int:
        count = 0
        for enc in ("utf-8-sig", "cp932", "utf-8"):
            try:
                with path.open("r", encoding=enc, errors="ignore", newline="") as f:
                    for line in f:
                        cols = line.rstrip("\n").split(",")
                        if len(cols) <= 15:
                            continue
                        try:
                            float(cols[14])
                            float(cols[15])
                        except ValueError:
                            continue
                        count += 1
                return count
            except Exception:
                count = 0
        return 0

    def scan_routes(self):
        self._clear_cards()
        if not self.project_dir:
            return
        route_dir, out_dir = resolve_project_paths(self.project_dir)
        self.lbl_saved.setText(f"保存先: {out_dir}")
        if not route_dir.exists():
            QMessageBox.critical(self, "エラー", f"ルートフォルダが見つかりません:\n{route_dir}")
            return
        csvs = sorted(route_dir.glob("*.csv"))
        if not csvs:
            QMessageBox.warning(self, "注意", f"ルートCSVが見つかりません:\n{route_dir}")
            return
        for csv_path in csvs:
            count = self._read_route_point_count(csv_path)
            card = RouteCard(csv_path.stem, count)
            self.cards[csv_path.stem] = card
            self.route_flow.addWidget(card)
        self.lbl_route_total.setText(f"ルート数: {len(csvs):,}")
        self.btn_run.setEnabled(self.input_dir is not None)
        self._update_viewer_button()
        self.log_info(f"scanned routes: {len(csvs):,}")

    def _count_input_files(self) -> int:
        if not self.input_dir:
            return 0
        iterator = self.input_dir.rglob("*.csv") if self.chk_recursive.isChecked() else self.input_dir.glob("*.csv")
        return sum(1 for _ in iterator)

    def select_project(self):
        selected = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択")
        if not selected:
            return
        self.project_dir = Path(selected).resolve()
        self.lbl_project.setText(str(self.project_dir))
        self.scan_routes()

    def select_input(self):
        selected = QFileDialog.getExistingDirectory(self, "第1スクリーニングフォルダを選択")
        if not selected:
            return
        self.input_dir = Path(selected).resolve()
        self.lbl_input.setText(str(self.input_dir))
        self.total_files = self._count_input_files()
        self.lbl_file_total.setText(f"第1スクリーニングCSV数: {self.total_files:,}")
        self.btn_run.setEnabled(bool(self.project_dir and self.cards))
        self._update_progress_label()

    def _update_progress_label(self):
        pct = (self.done_files / self.total_files * 100.0) if self.total_files else 0.0
        self.lbl_progress.setText(f"進捗ファイル: {self.done_files:,}/{self.total_files:,}（{pct:.1f}%）")
        self.progress_bar.setValue(int(pct))

    def _update_time_boxes(self):
        if not self._telemetry_running or not self.started_at:
            return
        elapsed = time.time() - self.started_at
        self.time_elapsed.setText(f"経過 {format_hhmmss(elapsed)}")
        if self.done_files > 0 and self.total_files > 0:
            rate = elapsed / self.done_files
            remain = max(0.0, rate * (self.total_files - self.done_files))
            self.time_eta.setText(f"残り {format_hhmmss(remain)}")
        else:
            self.time_eta.setText("残り --:--:--")

    def run_screening(self):
        if not self.project_dir:
            QMessageBox.warning(self, "未設定", "プロジェクトフォルダを選択してください。")
            return
        if not self.input_dir:
            QMessageBox.warning(self, "未設定", "第1スクリーニングフォルダを選択してください。")
            return
        route_dir, out_dir = resolve_project_paths(self.project_dir)
        if out_dir.exists() and any(out_dir.glob("*.csv")):
            ret = QMessageBox.question(
                self,
                "確認",
                f"既に第2スクリーニング（ルート）CSVが存在します。\n同名ファイルは上書きされます。\n\n{out_dir}\n\n続行しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        if not route_dir.exists() or not any(route_dir.glob("*.csv")):
            QMessageBox.warning(self, "対象なし", "ルートCSVがありません。")
            return

        script = Path(__file__).resolve().parent / "20_route_trip_extractor.py"
        if not script.exists():
            QMessageBox.critical(self, "エラー", f"処理スクリプトが見つかりません:\n{script}")
            return

        if self.proc:
            self.proc.kill()
            self.proc = None

        self.total_files = self._count_input_files()
        self.done_files = 0
        self._next_pct_log = 10
        self.started_at = time.time()
        self.batch_start_perf = perf_counter()
        self._telemetry_running = True
        self.lbl_file_total.setText(f"第1スクリーニングCSV数: {self.total_files:,}")
        self._update_progress_label()
        self.time_elapsed.setText("経過 00:00:00")
        self.time_eta.setText("残り --:--:--")
        self.lbl_status.setText("状態: RUNNING")
        for card in self.cards.values():
            card.set_hit_count(0)
            card.set_state("分析中")

        self.btn_run.setEnabled(False)
        self.btn_project.setEnabled(False)
        self.btn_input.setEnabled(False)
        self.chk_recursive.setEnabled(False)
        self.spin_radius.setEnabled(False)
        self.spin_min_points.setEnabled(False)
        self.btn_viewer.setEnabled(False)
        self.log_info(f"start: routes={len(self.cards):,} radius={self.spin_radius.value()}m min_points={self.spin_min_points.value()}")

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.setProgram(sys.executable)
        args = [
            "-u",
            str(script),
            "--project",
            str(self.project_dir),
            "--input",
            str(self.input_dir),
            "--radius-m",
            str(self.spin_radius.value()),
            "--min-route-points",
            str(self.spin_min_points.value()),
        ]
        if self.chk_recursive.isChecked():
            args.append("--recursive")
        self.proc.setArguments(args)
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.start()

    def _decode_qbytearray(self, ba) -> str:
        raw = bytes(ba)
        if not raw:
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp932", errors="replace")

    def _handle_stream_line(self, line: str, is_err: bool) -> None:
        text = line.strip()
        if not text:
            return
        m_route = RE_ROUTE.search(text)
        if m_route and m_route.group(1) in self.cards:
            self.cards[m_route.group(1)].set_point_count(int(m_route.group(2)))
            return
        m_hit = RE_HIT.search(text)
        if m_hit:
            name, count = m_hit.group(1), int(m_hit.group(2))
            if name in self.cards:
                self.cards[name].set_hit_count(count)
                self.cards[name].set_state("HITあり" if count else "分析中")
            return
        m_proc = RE_FILE_PROCESSED.search(text)
        if m_proc:
            self.done_files = int(m_proc.group(1).replace(",", ""))
            self._update_progress_label()
            if self.total_files > 0:
                pct = int((self.done_files / self.total_files) * 100)
                while self._next_pct_log <= 100 and pct >= self._next_pct_log:
                    self.log_info(f"{self._next_pct_log}%完了")
                    self._next_pct_log += 10
            return
        if is_err or "[ERROR]" in text:
            self.log_error(re.sub(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]\s*", "", text, count=1))
        elif "[WARN]" in text:
            self.log_warn(re.sub(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]\s*", "", text, count=1))
        elif text.startswith("TOTAL ") or text.startswith("[INFO]"):
            self.log_info(re.sub(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]\s*", "", text, count=1))

    def _on_stdout(self):
        if not self.proc:
            return
        text = self._decode_qbytearray(self.proc.readAllStandardOutput()).replace("\r", "\n")
        for line in text.split("\n"):
            if line.strip():
                self._handle_stream_line(line, False)

    def _on_stderr(self):
        if not self.proc:
            return
        text = self._decode_qbytearray(self.proc.readAllStandardError()).replace("\r", "\n")
        for line in text.split("\n"):
            if line.strip():
                self._handle_stream_line(line, True)

    def _on_finished(self, code: int, _status):
        self._telemetry_running = False
        self.lbl_status.setText("状態: DONE" if code == 0 else "状態: ERROR")
        self.done_files = self.total_files if code == 0 else self.done_files
        self._update_progress_label()
        self.time_eta.setText("残り 00:00:00" if code == 0 else "残り --:--:--")
        elapsed = perf_counter() - self.batch_start_perf if self.batch_start_perf else 0.0
        self.time_elapsed.setText(f"経過 {format_hhmmss(elapsed)}")
        for card in self.cards.values():
            if "HITトリップ数: 0" in card.hit.text():
                card.set_state("完了")
        self.log_info(f"process finished: code={code}")
        self.btn_run.setEnabled(True)
        self.btn_project.setEnabled(True)
        self.btn_input.setEnabled(True)
        self.chk_recursive.setEnabled(True)
        self.spin_radius.setEnabled(True)
        self.spin_min_points.setEnabled(True)
        self._update_viewer_button()

    def _update_viewer_button(self):
        enabled = False
        if self.project_dir:
            _route_dir, out_dir = resolve_project_paths(self.project_dir)
            enabled = out_dir.exists() and any(out_dir.glob("*.csv"))
        self.btn_viewer.setEnabled(enabled)

    def open_trip_viewer(self):
        if not self.project_dir:
            return
        _route_dir, out_dir = resolve_project_paths(self.project_dir)
        if not out_dir.exists() or not any(out_dir.glob("*.csv")):
            QMessageBox.information(self, "CSVなし", f"第2スクリーニング（ルート）CSVがありません:\n{out_dir}")
            return
        self._launch_05_viewer(str(out_dir.resolve()))


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
