import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from time import perf_counter

from PyQt6.QtCore import QPoint, QProcess, QPropertyAnimation, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QLayout,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QSpinBox,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "21_第２スクリーニング（指定交差点を通過するトリップの抽出）"

CORNER_LOGO_MARGIN = 18
CORNER_LOGO_OFFSET_TOP = -4
CORNER_LOGO_OFFSET_RIGHT = -10

FOLDER_CROSS = "11_交差点(Point)データ"
FOLDER_OUT = "20_第２スクリーニング"

RE_LEVEL = re.compile(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]")
RE_CUR_CROSS = re.compile(r"交差点開始:\s*(\S+)")
RE_FILE_DONE = re.compile(r"進捗:\s*(\d+)\s*/\s*(\d+)")
RE_HIT = re.compile(r"HIT:\s*(\S+)\s+(\d+)")
RE_NEAR = re.compile(r"中心最近接距離\(m\):\s*(\S+)\s+([0-9.]+)")
RE_OPID = re.compile(r"運行ID総数:\s*(\d+)")


def resolve_project_paths(project_dir: Path) -> tuple[Path, Path]:
    return project_dir / FOLDER_CROSS, project_dir / FOLDER_OUT


def format_hhmmss(total_sec: float) -> str:
    sec = int(total_sec + 0.5)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=10):
        super().__init__(parent)
        self.item_list = []
        self.setContentsMargins(margin, margin, margin, margin)
        self._hspace = spacing
        self._vspace = spacing

    def addItem(self, item):
        self.item_list.append(item)

    def count(self):
        return len(self.item_list)

    def itemAt(self, index):
        return self.item_list[index] if 0 <= index < len(self.item_list) else None

    def takeAt(self, index):
        return self.item_list.pop(index) if 0 <= index < len(self.item_list) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        for item in self.item_list:
            next_x = x + item.sizeHint().width() + self._hspace
            if next_x - self._hspace > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + self._vspace
                next_x = x + item.sizeHint().width() + self._hspace
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()


class SweepWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.angle = 0
        self.setMinimumHeight(140)

    def tick(self) -> None:
        self.angle = (self.angle + 7) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#050b09"))
        pen = QPen(QColor("#1b4f2f"))
        p.setPen(pen)
        r = min(self.width(), self.height()) // 2 - 8
        c = self.rect().center()
        p.drawEllipse(c, r, r)
        p.drawEllipse(c, int(r * 0.66), int(r * 0.66))
        p.drawEllipse(c, int(r * 0.33), int(r * 0.33))
        sweep_pen = QPen(QColor("#56d27f"), 2)
        p.setPen(sweep_pen)
        rad = self.angle * math.pi / 180
        x = int(c.x() + r * math.cos(rad))
        y = int(c.y() - r * math.sin(rad))
        p.drawLine(c.x(), c.y(), x, y)


class StepBox(QFrame):
    def __init__(self, title: str, content: QWidget, parent=None):
        super().__init__(parent)
        self.setObjectName("stepBox")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        t = QLabel(title)
        t.setObjectName("stepTitle")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        lay.addWidget(t)
        lay.addWidget(content)
        self.setStyleSheet(
            """
        QFrame#stepBox{border:2px solid #00ff99;border-radius:12px;background: rgba(0, 255, 153, 16);}
        QLabel#stepTitle{color:#00ff99;font-weight:700;}
        """
        )


class FlowGuide(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps: list[QWidget] = []
        self.setMinimumHeight(86)

    def set_steps(self, steps: list[QWidget]):
        self._steps = steps
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if len(self._steps) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        neon = QColor("#00ff99")
        glow = QPen(QColor(neon))
        glow.setWidth(10)
        glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        c = glow.color(); c.setAlpha(40); glow.setColor(c)
        line = QPen(neon); line.setWidth(2); line.setCapStyle(Qt.PenCapStyle.RoundCap)
        for a, b in zip(self._steps[:-1], self._steps[1:]):
            if not a.isVisible() or not b.isVisible():
                continue
            ra = a.geometry(); rb = b.geometry()
            ax, ay = ra.right() + 6, ra.center().y()
            bx, by = rb.left() - 6, rb.center().y()
            p.setPen(glow); p.drawLine(ax, ay, bx, by)
            p.setPen(line); p.drawLine(ax, ay, bx, by)


class DistHistogram(QWidget):
    def __init__(self, radius: int = 30, bins: int = 10):
        super().__init__()
        self.radius = max(1, radius)
        self.bins = bins
        self.counts = [0] * bins
        self.setMinimumHeight(64)

    def set_radius(self, radius: int) -> None:
        self.radius = max(1, radius)
        self.counts = [0] * self.bins
        self.update()

    def add_value(self, dist_m: float, radius: int | None = None) -> None:
        r = radius if radius is not None else self.radius
        if dist_m < 0 or dist_m > r:
            return
        idx = min(self.bins - 1, int((dist_m / max(1e-6, r)) * self.bins))
        self.counts[idx] += 1
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#09120f"))
        label_h = 14
        chart = r.adjusted(4, 4, -4, -(4 + label_h))
        w = max(1, chart.width())
        h = max(1, chart.height())
        maxv = max(self.counts) if self.counts else 1
        bw = w / self.bins
        p.setPen(QPen(QColor("#1d5a3a"), 1))
        for i in range(self.bins + 1):
            x = int(chart.left() + i * bw)
            p.drawLine(x, chart.top(), x, chart.bottom())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#56d27f"))
        for i, c in enumerate(self.counts):
            bh = 0 if maxv == 0 else int((c / maxv) * (h - 2))
            x = int(chart.left() + 1 + i * bw)
            p.drawRect(x, chart.bottom() - bh, max(2, int(bw) - 2), bh)

        p.setPen(QColor("#7cffc6"))
        f = p.font()
        f.setPointSize(max(8, f.pointSize() - 1))
        p.setFont(f)
        p.drawText(
            QRect(r.left() + 6, r.bottom() - label_h, 40, label_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "0",
        )
        p.drawText(
            QRect(r.right() - 60, r.bottom() - label_h, 54, label_h),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(int(self.radius)),
        )


class CrossCard(QFrame):
    def __init__(self, name: str, radius: int = 30):
        super().__init__()
        self.name = name
        self.selected = True
        self.locked = False
        self.state = "待機"
        self.setObjectName("crossCard")
        self.setMinimumWidth(260)
        self.setMaximumWidth(340)
        self.setFixedHeight(220)
        v = QVBoxLayout(self)
        self.title = QLabel(name)
        self.sel_label = QLabel("第2スクリーニング：対象")
        self.flags = QLabel("交差点定義ファイルJPG／CSV: - / -")
        self.flags2 = QLabel("20_第２スクリーニング_フォルダ／抽出済みCSV: - / -")
        self.hit = QLabel("HITトリップ数: 0")
        self.hist_title = QLabel("中心最近接距離(m) ヒストグラム")
        self.hist = DistHistogram(radius)
        for w in [self.title, self.sel_label, self.flags, self.flags2, self.hit, self.hist_title, self.hist]:
            v.addWidget(w)
        self.apply_state("待機")

    def mousePressEvent(self, _event):
        if self.locked:
            return
        self.selected = not self.selected
        self.apply_state(self.state)

    def set_locked(self, locked: bool) -> None:
        self.locked = locked

    def set_flags(self, *, has_csv: bool, has_jpg: bool, has_s2_dir: bool, has_s2_csv: bool) -> None:
        self.flags.setText(f"交差点定義ファイルJPG／CSV: {'有' if has_jpg else '無'} / {'有' if has_csv else '無'}")
        self.flags2.setText(f"20_第２スクリーニング_フォルダ／抽出済みCSV: {'有' if has_s2_dir else '無'} / {'有' if has_s2_csv else '無'}")

    def set_hit_count(self, count: int) -> None:
        self.hit.setText(f"HITトリップ数: {count:,}")

    def set_state(self, state: str) -> None:
        self.state = state
        self.apply_state(state)

    def apply_state(self, state: str) -> None:
        self.sel_label.setText("第2スクリーニング：対象" if self.selected else "第2スクリーニング：非対象")
        if state == "処理中":
            if self.selected:
                style = "border:2px solid #9cffbe;background:#0f1e17;color:#b5ffd0;"
            else:
                style = "border:2px solid #0c5a41;background:#040806;color:#2f7a5b;"
        elif state == "完了":
            if self.selected:
                style = "border:2px solid #68d088;background:#0c1712;color:#a2f0be;"
            else:
                style = "border:2px solid #0c5a41;background:#040806;color:#2f7a5b;"
        elif state == "エラー":
            if self.selected:
                style = "border:2px solid #d96f6f;background:#261010;color:#ffaaaa;"
            else:
                style = "border:2px solid #5a2b2b;background:#140808;color:#8c5a5a;"
        else:
            if self.selected:
                style = "border:1px solid #1ee6a8;background:#07120e;color:#7cffc6;"
            else:
                style = "border:1px solid #0c5a41;background:#040806;color:#2f7a5b;"
        self.setStyleSheet(f"QFrame#crossCard{{{style}}}")
        self.title.setText(f"{self.name} [{state}]")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.project_dir: Path | None = None
        self.input_dir: Path | None = None
        self.proc: QProcess | None = None
        self.current_name: str | None = None
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._last_log_line: str | None = None
        self.total_files = 0
        self.done_files = 0
        self.log_lines: list[str] = []
        self.batch_started_at: datetime | None = None
        self.batch_ended_at: datetime | None = None
        self.batch_start_perf: float | None = None
        self.is_running = False

        self.cards: dict[str, CrossCard] = {}
        self.errors = 0
        self.started_at = 0.0
        self._eta_done = 0
        self._eta_total = 0
        self._telemetry_running = False
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick_animation)
        self.anim_timer.start(120)

        self._build_ui()
        self._set_style()
        self._corner_logo_visible = False
        self.splash_logo: QLabel | None = None
        self._pix_small = None
        QTimer.singleShot(0, self._init_logo_overlay)
        self.log_info("①プロジェクト選択 → ②第1スクリーニング選択 → 21【分析スタート】")

    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        v = QVBoxLayout(root)
        top_font = QFont(); top_font.setPointSize(10)
        self.lbl_about = QLabel(
            "本ソフトは、第1スクリーニング後データから、さらに、「11_交差点(Point)データ」で指定するすべての交差点における通過トリップを一括で抽出します。\n"
            "抽出されたトリップは「20_第２スクリーニング）」フォルダへ１トリップに対し１CSVファイルで出力します。出力CSVは様式1-2のデータフォーマットを保持します。"
        )
        self.lbl_about.setWordWrap(True); self.lbl_about.setFont(top_font)
        v.addWidget(self.lbl_about)

        self.flow = FlowGuide()
        self.flow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.flow_host = QWidget()
        flow_grid = QGridLayout(self.flow_host)
        flow_grid.setContentsMargins(0, 0, 0, 0); flow_grid.setHorizontalSpacing(18)
        self.btn_project = QPushButton("プロジェクトを選ぶ"); self.btn_project.clicked.connect(self.select_project)
        self.lbl_project = QLabel("未選択")
        proj_w = QWidget(); proj_l = QHBoxLayout(proj_w); proj_l.setContentsMargins(0, 0, 0, 0); proj_l.addWidget(self.btn_project); proj_l.addWidget(self.lbl_project)
        self.btn_input = QPushButton("第1スクリーニングを選ぶ"); self.btn_input.clicked.connect(self.select_input)
        self.lbl_input = QLabel("未選択")
        in_w = QWidget(); in_l = QHBoxLayout(in_w); in_l.setContentsMargins(0, 0, 0, 0); in_l.addWidget(self.btn_input); in_l.addWidget(self.lbl_input)
        self.spin_radius = QSpinBox(); self.spin_radius.setRange(5, 200); self.spin_radius.setValue(30); self.spin_radius.valueChanged.connect(self._on_radius_changed)
        self.spin_radius.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.spin_radius.setFixedWidth(90)
        rad_w = QWidget(); rad_l = QHBoxLayout(rad_w); rad_l.setContentsMargins(0, 0, 0, 0)
        rad_l.addWidget(QLabel("半径")); rad_l.addWidget(self.spin_radius); rad_l.addWidget(QLabel("m"))
        self.btn_run = QPushButton("分析スタート"); self.btn_run.clicked.connect(self.run_screening)
        b1 = StepBox("STEP1 プロジェクトフォルダの選択", proj_w)
        b2 = StepBox("STEP2 第1スクリーニングデータの選択", in_w)
        b3 = StepBox("STEP3 交差点通過判定半径（この半径以内を通過したトリップを抽出します）", rad_w)
        b4 = StepBox("STEP4 第2スクリーニング実行", self.btn_run)
        flow_grid.addWidget(b1, 0, 0); flow_grid.addWidget(b2, 0, 1); flow_grid.addWidget(b3, 0, 2); flow_grid.addWidget(b4, 0, 3)
        self._flow_spacer = QWidget()
        self._flow_spacer.setFixedWidth(380)
        flow_grid.addWidget(self._flow_spacer, 0, 4)
        flow_grid.setColumnStretch(0, 1)
        flow_grid.setColumnStretch(1, 0)
        flow_grid.setColumnStretch(2, 0)
        flow_grid.setColumnStretch(3, 0)
        flow_grid.setColumnStretch(4, 0)
        self.flow.set_steps([b1, b2, b3, b4])
        flow_stack = QFrame()
        flow_stack.setObjectName("flowStack")
        stack_l = QStackedLayout(flow_stack)
        stack_l.setContentsMargins(0, 0, 0, 0)
        stack_l.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack_l.addWidget(self.flow_host)
        stack_l.addWidget(self.flow)
        v.addWidget(flow_stack)

        mid_split = QSplitter(Qt.Orientation.Horizontal)
        v.addWidget(mid_split, stretch=5)
        left_panel = QFrame(); lv = QVBoxLayout(left_panel)
        lv.addWidget(QLabel("交差点アイコン一覧"))
        self.cross_container = QWidget(); self.cross_flow = FlowLayout(self.cross_container, spacing=6); self.cross_container.setLayout(self.cross_flow)
        self.cross_scroll = QScrollArea(); self.cross_scroll.setWidgetResizable(True); self.cross_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.cross_scroll.setWidget(self.cross_container)
        lv.addWidget(self.cross_scroll)
        mid_split.addWidget(left_panel)

        right_panel = QFrame(); rv = QVBoxLayout(right_panel)
        rv.addWidget(QLabel("CYBER TELEMETRY"))
        self.tele = {
            "cross_total": QLabel("交差点数: 0"),
            "opid": QLabel("第1スクリーニング数（運行ID数）: -"),
            "errors": QLabel("エラー数: 0"),
            "status": QLabel("状態: IDLE"),
            "current": QLabel("現在交差点: -"),
        }
        self.lbl_progress = QLabel("進捗ファイル: 0/0（0.0%）")
        self.lbl_progress.setStyleSheet("color:#7cffc6; font-weight:600;")
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        self.time_elapsed_big = QLabel("経過 00:00:00")
        self.time_eta_big = QLabel("残り --:--:--")
        self.time_elapsed_big.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        self.time_eta_big.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        rv.addWidget(self.tele["cross_total"])
        rv.addWidget(self.tele["opid"])
        rv.addWidget(self.lbl_progress)
        rv.addWidget(self.progress_bar)
        rv.addWidget(self.time_elapsed_big)
        rv.addWidget(self.time_eta_big)
        rv.addWidget(self.tele["errors"])
        rv.addWidget(self.tele["status"])
        rv.addWidget(self.tele["current"])
        self.sweep = SweepWidget(); rv.addWidget(self.sweep)
        rv.addStretch(1)
        mid_split.addWidget(right_panel)
        mid_split.setSizes([1500, 500])
        mid_split.setStretchFactor(0, 4)
        mid_split.setStretchFactor(1, 1)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 10)); self.log.setMaximumBlockCount(2000)
        v.addWidget(self.log, stretch=2)

    def _set_style(self):
        self.setStyleSheet("""
            QWidget { background: #050908; color: #79d58f; }
            QPlainTextEdit, QSpinBox, QProgressBar, QScrollArea { background: #0a120f; border: 1px solid #1f3f2d; }
            QPushButton { background: #112116; border: 1px solid #2a6b45; padding: 6px 10px; }
            QPushButton:hover { background: #18321f; }
            QFrame { border: 1px solid #1c4f33; border-radius: 4px; }
            QFrame#crossCard { border-radius: 8px; }
        """)

    def _fmt_hms(self, sec: float) -> str:
        sec = max(0, int(sec)); h = sec // 3600; m = (sec % 3600) // 60; s = sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _update_time_boxes(self) -> None:
        if self.started_at <= 0:
            self.time_elapsed_big.setText("経過 00:00:00")
            self.time_eta_big.setText("残り --:--:--")
            return
        elapsed = time.time() - self.started_at
        self.time_elapsed_big.setText(f"経過 {self._fmt_hms(elapsed)}")
        if self._eta_total > 0 and self._eta_done > 0 and self._eta_done <= self._eta_total:
            remain = elapsed / max(1, self._eta_done) * (self._eta_total - self._eta_done)
            self.time_eta_big.setText(f"残り {self._fmt_hms(remain)}")
        else:
            self.time_eta_big.setText("残り --:--:--")

    def _tick_animation(self) -> None:
        self.sweep.tick()
        self._update_time_boxes()

    def _on_radius_changed(self, radius: int) -> None:
        for card in self.cards.values():
            card.hist.set_radius(radius)

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    def _append_ui_log(self, level: str, msg: str) -> None:
        line = f"{self._timestamp()} [{level}] {msg}"
        if line == self._last_log_line:
            return
        self.log.appendPlainText(line)
        self.log_lines.append(line)
        self._last_log_line = line

    def log_info(self, msg: str) -> None: self._append_ui_log("INFO", msg)
    def log_warn(self, msg: str) -> None: self._append_ui_log("WARN", msg)
    def log_error(self, msg: str) -> None:
        self.errors += 1
        self.tele["errors"].setText(f"エラー数: {self.errors}")
        self._append_ui_log("ERROR", msg)

    def _update_progress_label(self) -> None:
        pct = (self.done_files / self.total_files * 100.0) if self.total_files else 0.0
        self.lbl_progress.setText(f"進捗ファイル: {self.done_files:,}/{self.total_files:,}（{pct:.1f}%）")
        self.progress_bar.setValue(int(pct))

    def _clear_cards(self):
        while self.cross_flow.count():
            item = self.cross_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.cards.clear()

    def select_project(self):
        d = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択", str(Path.cwd()))
        if not d:
            return
        self.project_dir = Path(d).resolve(); self.lbl_project.setText(self.project_dir.name)
        self.log_info(f"project set: {self.project_dir}")
        self.scan_crossroads()

    def select_input(self):
        d = QFileDialog.getExistingDirectory(self, "第1スクリーニングデータフォルダを選択", str(Path.cwd()))
        if not d:
            return
        self.input_dir = Path(d).resolve(); self.lbl_input.setText(self.input_dir.name)
        self.log_info(f"input set: {self.input_dir}")

    def scan_crossroads(self):
        self._clear_cards()
        if not self.project_dir:
            self.log_warn("project not selected.")
            return
        cross_dir, out_dir = resolve_project_paths(self.project_dir)
        if not cross_dir.exists():
            QMessageBox.critical(self, "エラー", f"交差点フォルダが見つかりません:\n{cross_dir}")
            return
        csvs = sorted(cross_dir.glob("*.csv"))
        if not csvs:
            QMessageBox.warning(self, "注意", f"交差点CSVが見つかりません:\n{cross_dir}")
            return
        for csv_path in csvs:
            name = csv_path.stem
            jpg_path = cross_dir / f"{name}.jpg"
            out_path = out_dir / name
            n_s2_csv = len(list(out_path.glob("*.csv"))) if out_path.exists() else 0
            card = CrossCard(name, self.spin_radius.value())
            card.set_flags(has_csv=True, has_jpg=jpg_path.exists(), has_s2_dir=out_path.exists(), has_s2_csv=n_s2_csv > 0)
            card.set_hit_count(0)
            self.cards[name] = card
            self.cross_flow.addWidget(card)
        self.tele["cross_total"].setText(f"交差点数: {len(csvs)}")
        self.log_info(f"scanned: {len(csvs)} crossroads")

    def _collect_targets(self) -> list[str]:
        return [name for name, card in self.cards.items() if card.selected]

    def _card_dump_lines(self) -> list[str]:
        lines = ["name\tselected\thit"]
        for n, c in self.cards.items():
            lines.append(f"{n}\t{int(c.selected)}\t{c.hit.text()}")
        return lines

    def _write_batch_log_file(self, total_sec: float) -> None:
        if not self.project_dir:
            return
        _cross_dir, out_dir = resolve_project_paths(self.project_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = out_dir / f"21_batch_log_{stamp}.txt"
        started_at = self.batch_started_at.isoformat(sep=" ", timespec="seconds") if self.batch_started_at else ""
        ended_at = self.batch_ended_at.isoformat(sep=" ", timespec="seconds") if self.batch_ended_at else ""
        lines = [
            f"Project: {self.project_dir}",
            f"Input: {self.input_dir}" if self.input_dir else "",
            f"開始: {started_at}",
            f"終了: {ended_at}",
            f"総所要時間: {format_hhmmss(total_sec)}",
            "",
            "[UIカード]",
            *self._card_dump_lines(),
            "",
            "[実行ログ]",
            *self.log_lines,
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.log_info(f"batch log saved: {log_path}")

    def run_screening(self):
        if not self.project_dir:
            QMessageBox.warning(self, "未設定", "①プロジェクトフォルダを選択してください。")
            return
        if not self.input_dir:
            QMessageBox.warning(self, "未設定", "②第1スクリーニングデータフォルダを選択してください。")
            return
        targets = self._collect_targets()
        if not targets:
            QMessageBox.information(self, "対象なし", "実行対象の交差点が選択されていません。")
            return
        script21 = Path(__file__).resolve().parent / "21_point_trip_extractor.py"
        if not script21.exists():
            QMessageBox.critical(self, "エラー", f"本体スクリプトが見つかりません:\n{script21}")
            return
        if self.proc:
            self.proc.kill(); self.proc = None

        self.log_lines = []; self._last_log_line = None
        self.batch_started_at = datetime.now(); self.batch_start_perf = perf_counter(); self.batch_ended_at = None
        self.current_name = None; self._stdout_buf = ""; self._stderr_buf = ""
        self.total_files = len(list(self.input_dir.rglob("*.csv"))); self.done_files = 0
        self.errors = 0; self.tele["errors"].setText("エラー数: 0")
        self.started_at = time.time(); self._eta_done = 0; self._eta_total = self.total_files
        self.tele["status"].setText("状態: RUNNING")
        self.tele["opid"].setText("第1スクリーニング数（運行ID数）: -")
        self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        self._telemetry_running = True
        self._update_progress_label()
        for card in self.cards.values():
            card.set_state("待機")
            card.set_locked(True)

        self.is_running = True
        self.btn_run.setEnabled(False)
        self.spin_radius.setEnabled(False)
        if hasattr(self, "anim_timer"):
            self.anim_timer.start(120)
        self.log_info("①プロジェクト選択 → ②第1スクリーニング選択 → 21【分析スタート】")
        self.log_info(f"start: targets={','.join(targets)} radius={self.spin_radius.value()}m")

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(["-u", str(script21), "--project", str(self.project_dir), "--input", str(self.input_dir), "--targets", *targets, "--radius-m", str(self.spin_radius.value())])
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

    def _log_process_line(self, text: str, is_err: bool) -> None:
        m = RE_LEVEL.search(text)
        level = "WARN" if is_err else "INFO"
        if m:
            found = m.group(1)
            if found == "ERROR": level = "ERROR"
            elif found in {"WARN", "WARNING"}: level = "WARN"
            else: level = "INFO"
            text = re.sub(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]\s*", "", text, count=1).strip()
        if level == "ERROR": self.log_error(text)
        elif level == "WARN": self.log_warn(text)
        else: self.log_info(text)

    def _handle_stream_line(self, line: str, from_cr: bool, is_err: bool) -> None:
        text = line.strip()
        if not text:
            return
        m_cross = RE_CUR_CROSS.search(text)
        if m_cross:
            self.current_name = m_cross.group(1)
            self.tele["current"].setText(f"現在交差点: {self.current_name}")
            if self.current_name in self.cards:
                self.cards[self.current_name].set_state("処理中")

        m_file = RE_FILE_DONE.search(text)
        if m_file:
            self.done_files = int(m_file.group(1)); self.total_files = int(m_file.group(2))
            self._eta_done = self.done_files; self._eta_total = self.total_files
            self._update_progress_label()

        m_hit = RE_HIT.search(text)
        if m_hit:
            name, count = m_hit.group(1), int(m_hit.group(2))
            if name in self.cards:
                self.cards[name].set_hit_count(count)
            return

        m_near = RE_NEAR.search(text)
        if m_near:
            name = m_near.group(1)
            dist = float(m_near.group(2))
            if name in self.cards:
                self.cards[name].hist.add_value(dist, self.spin_radius.value())
            return

        m_opid = RE_OPID.search(text)
        if m_opid:
            self.tele["opid"].setText(f"第1スクリーニング数（運行ID数）: {int(m_opid.group(1)):,}")
            return

        if from_cr and RE_FILE_DONE.search(text):
            return
        self._log_process_line(text, is_err)

    def _maybe_update_realtime_from_buffer(self, buf: str) -> None:
        idx = buf.rfind("進捗:")
        if idx < 0:
            return
        m = RE_FILE_DONE.search(buf[idx:].strip())
        if m:
            self.done_files = int(m.group(1)); self.total_files = int(m.group(2))
            self._eta_done = self.done_files; self._eta_total = self.total_files
            self._update_progress_label()

    def _append_stream_chunk(self, chunk: str, is_err: bool) -> None:
        if not chunk:
            return
        buf = (self._stderr_buf if is_err else self._stdout_buf) + chunk
        self._maybe_update_realtime_from_buffer(buf)
        start = 0
        for idx, ch in enumerate(buf):
            if ch in ("\r", "\n"):
                prev_is_cr = idx > 0 and buf[idx - 1] == "\r"
                self._handle_stream_line(buf[start:idx], ch == "\r" or prev_is_cr, is_err)
                start = idx + 1
        if is_err:
            self._stderr_buf = buf[start:]; self._maybe_update_realtime_from_buffer(self._stderr_buf)
        else:
            self._stdout_buf = buf[start:]; self._maybe_update_realtime_from_buffer(self._stdout_buf)

    def _flush_process_buffers(self) -> None:
        if self._stdout_buf:
            self._handle_stream_line(self._stdout_buf, False, False); self._stdout_buf = ""
        if self._stderr_buf:
            self._handle_stream_line(self._stderr_buf, False, True); self._stderr_buf = ""

    def _on_stdout(self):
        if self.proc:
            self._append_stream_chunk(self._decode_qbytearray(self.proc.readAllStandardOutput()), False)

    def _on_stderr(self):
        if self.proc:
            self._append_stream_chunk(self._decode_qbytearray(self.proc.readAllStandardError()), True)

    def _on_finished(self, code: int, _status):
        self._flush_process_buffers()
        self.is_running = False
        self._telemetry_running = False
        self.tele["status"].setText("状態: DONE" if code == 0 else "状態: ERROR")
        self.time_eta_big.setText("残り 00:00:00")
        for card in self.cards.values():
            card.set_state("完了" if code == 0 else "エラー")
            card.set_locked(False)
        self.log_info(f"process finished: code={code}")
        self.log_info("🎉 おめでとうございます。全件処理完了です。")
        self.btn_run.setEnabled(True)
        self.spin_radius.setEnabled(True)
        if hasattr(self, "anim_timer"):
            self.anim_timer.stop()
        if self.started_at:
            elapsed = datetime.now() - datetime.fromtimestamp(self.started_at)
            sec = int(elapsed.total_seconds())
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            self.time_elapsed_big.setText(f"経過 {h:02d}:{m:02d}:{s:02d}")
        self.tele["status"].setText("状態: DONE" if code == 0 else "状態: ERROR")
        self.batch_ended_at = datetime.now()
        total_sec = perf_counter() - self.batch_start_perf if self.batch_start_perf else 0.0
        self.log_info(f"総所要時間: {format_hhmmss(total_sec)}")
        self._write_batch_log_file(total_sec)

    def _center_splash_logo(self) -> None:
        if not self.splash_logo:
            return
        parent_rect = self.rect(); logo_rect = self.splash_logo.rect()
        self.splash_logo.move((parent_rect.width() - logo_rect.width()) // 2, (parent_rect.height() - logo_rect.height()) // 2)

    def _init_logo_overlay(self) -> None:
        logo_path = Path(__file__).resolve().parent / "logo.png"
        if not logo_path.exists(): return
        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull(): return
        pix_big = pixmap.scaledToHeight(320, Qt.TransformationMode.SmoothTransformation)
        self._pix_small = pixmap.scaledToHeight(110, Qt.TransformationMode.SmoothTransformation)
        self.splash_logo = QLabel(self); self.splash_logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash_logo.setStyleSheet("background: transparent;"); self.splash_logo.setPixmap(pix_big); self.splash_logo.adjustSize(); self._center_splash_logo(); self.splash_logo.show()
        effect = QGraphicsOpacityEffect(self.splash_logo); self.splash_logo.setGraphicsEffect(effect)
        fade_in = QPropertyAnimation(effect, b"opacity", self); fade_in.setDuration(500); fade_in.setStartValue(0.0); fade_in.setEndValue(1.0)

        def start_fade_out():
            fade_out = QPropertyAnimation(effect, b"opacity", self)
            fade_out.setDuration(500); fade_out.setStartValue(1.0); fade_out.setEndValue(0.0)

            def show_corner_logo():
                if self.splash_logo: self.splash_logo.deleteLater(); self.splash_logo = None
                self._show_corner_logo()

            fade_out.finished.connect(show_corner_logo); fade_out.start()

        fade_in.finished.connect(lambda: QTimer.singleShot(3000, start_fade_out)); fade_in.start()

    def _show_corner_logo(self) -> None:
        if not self._pix_small: return
        self.splash = QLabel(self); self.splash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash.setStyleSheet(f"background: transparent; margin-top: {CORNER_LOGO_OFFSET_TOP}px; margin-right: {CORNER_LOGO_OFFSET_RIGHT}px;")
        self.splash.setPixmap(self._pix_small); self.splash.adjustSize()
        x = self.width() - self.splash.width() - CORNER_LOGO_MARGIN + abs(CORNER_LOGO_OFFSET_RIGHT)
        y = CORNER_LOGO_MARGIN + CORNER_LOGO_OFFSET_TOP
        self.splash.move(x, y); self.splash.show(); self._corner_logo_visible = True
        effect = QGraphicsDropShadowEffect(self); effect.setBlurRadius(26); effect.setOffset(0, 0); effect.setColor(QColor(0, 255, 180, 150))
        self.splash.setGraphicsEffect(effect)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.splash_logo and self.splash_logo.isVisible(): self._center_splash_logo()
        if getattr(self, "_corner_logo_visible", False):
            x = self.width() - self.splash.width() - CORNER_LOGO_MARGIN + abs(CORNER_LOGO_OFFSET_RIGHT)
            y = CORNER_LOGO_MARGIN + CORNER_LOGO_OFFSET_TOP
            self.splash.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        if self.splash_logo: self._center_splash_logo()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    QTimer.singleShot(0, w.showMaximized)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
