import re
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

from PyQt6.QtCore import QProcess, QPropertyAnimation, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "21_指定交差点を通過するトリップの抽出ツール（第２スクリーニング）"

CORNER_LOGO_MARGIN = 18
CORNER_LOGO_OFFSET_TOP = -4
CORNER_LOGO_OFFSET_RIGHT = -10

FOLDER_CROSS = "11_交差点(Point)データ"
FOLDER_OUT = "20_第２スクリーニング"

COL_RUN = 0
COL_NAME = 1
COL_CROSS_CSV = 2
COL_CROSS_JPG = 3
COL_S2_DIR = 4
COL_S2_CSV = 5
COL_HIT_TRIPS = 6

CENTER_ALIGN_COLS = {COL_RUN, COL_CROSS_CSV, COL_CROSS_JPG, COL_S2_DIR, COL_S2_CSV}
RIGHT_ALIGN_COLS = {COL_HIT_TRIPS}

RE_LEVEL = re.compile(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]")
RE_CUR_CROSS = re.compile(r"交差点開始:\s*(\S+)")
RE_FILE_DONE = re.compile(r"進捗:\s*(\d+)\s*/\s*(\d+)")
RE_HIT = re.compile(r"HIT:\s*(\S+)\s+(\d+)")


def resolve_project_paths(project_dir: Path) -> tuple[Path, Path]:
    return project_dir / FOLDER_CROSS, project_dir / FOLDER_OUT


def format_hhmmss(total_sec: float) -> str:
    sec = int(total_sec + 0.5)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


class RunHeaderView(QHeaderView):
    toggle_all_requested = pyqtSignal(bool)

    def __init__(self, orientation, parent=None, run_col=0):
        super().__init__(orientation, parent)
        self.run_col = run_col
        self._state = Qt.CheckState.Unchecked
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSectionsClickable(True)

    def set_run_state(self, state: Qt.CheckState):
        self._state = state
        self.viewport().update()

    def _checkbox_rect(self, rect: QRect) -> QRect:
        return QRect(rect.center().x() - 26, rect.center().y() + 6, 16, 16)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int):
        super().paintSection(painter, rect, logicalIndex)
        if logicalIndex != self.run_col:
            return
        painter.save()
        painter.drawText(
            rect.adjusted(2, 2, -2, -2),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "抽出対象",
        )
        opt = QStyleOptionButton()
        opt.state = QStyle.StateFlag.State_Enabled
        opt.state |= {
            Qt.CheckState.Checked: QStyle.StateFlag.State_On,
            Qt.CheckState.PartiallyChecked: QStyle.StateFlag.State_NoChange,
            Qt.CheckState.Unchecked: QStyle.StateFlag.State_Off,
        }[self._state]
        cb_rect = self._checkbox_rect(rect)
        opt.rect = cb_rect
        self.style().drawControl(QStyle.ControlElement.CE_CheckBox, opt, painter, self)
        painter.drawText(
            QRect(
                cb_rect.right() + 4,
                cb_rect.top() - 1,
                rect.right() - cb_rect.right() - 6,
                cb_rect.height() + 2,
            ),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "ALL",
        )
        painter.restore()

    def _section_rect(self, logical_index: int) -> QRect:
        return QRect(self.sectionViewportPosition(logical_index), 0, self.sectionSize(logical_index), self.height())

    def mousePressEvent(self, event):
        idx = self.logicalIndexAt(event.pos())
        if idx == self.run_col:
            sec_rect = self._section_rect(idx)
            if self._checkbox_rect(sec_rect).contains(event.pos()) or sec_rect.contains(event.pos()):
                self.toggle_all_requested.emit(self._state != Qt.CheckState.Checked)
                event.accept()
                return
        super().mousePressEvent(event)


class StepBox(QFrame):
    """ネオン枠の角丸ボックス（中に任意のウィジェットを入れる）"""

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
        QFrame#stepBox{
            border: 2px solid #00ff99;
            border-radius: 12px;
            background: rgba(0, 255, 153, 16);
        }
        QLabel#stepTitle{
            color: #00ff99;
            font-weight: 700;
        }
        """
        )


class FlowGuide(QWidget):
    """複数のStepBoxを配置し、マインドマップ風の接続線を描画する"""

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
        glow.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        c = glow.color()
        c.setAlpha(40)
        glow.setColor(c)

        line = QPen(neon)
        line.setWidth(2)
        line.setCapStyle(Qt.PenCapStyle.RoundCap)
        line.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        for a, b in zip(self._steps[:-1], self._steps[1:]):
            if not a.isVisible() or not b.isVisible():
                continue
            ra = a.geometry()
            rb = b.geometry()

            ax = ra.right()
            ay = ra.center().y()
            bx = rb.left()
            by = rb.center().y()

            ax += 6
            bx -= 6

            p.setPen(glow)
            p.drawLine(ax, ay, bx, by)

            p.setPen(line)
            p.drawLine(ax, ay, bx, by)

        p.end()


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

        self._build_ui()
        self._corner_logo_visible = False
        self.splash_logo: QLabel | None = None
        self._pix_small = None
        QTimer.singleShot(0, self._init_logo_overlay)
        self.log_info("①プロジェクト選択 → ②第1スクリーニング選択 → 21【分析スタート】")

    def _center_splash_logo(self) -> None:
        if not self.splash_logo:
            return

        parent_rect = self.rect()
        logo_rect = self.splash_logo.rect()

        x = (parent_rect.width() - logo_rect.width()) // 2
        y = (parent_rect.height() - logo_rect.height()) // 2
        self.splash_logo.move(x, y)

    def _init_logo_overlay(self) -> None:
        logo_path = Path(__file__).resolve().parent / "logo.png"
        if not logo_path.exists():
            return

        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return

        pix_big = pixmap.scaledToHeight(320, Qt.TransformationMode.SmoothTransformation)
        self._pix_small = pixmap.scaledToHeight(110, Qt.TransformationMode.SmoothTransformation)

        self.splash_logo = QLabel(self)
        self.splash_logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash_logo.setStyleSheet("background: transparent;")
        self.splash_logo.setPixmap(pix_big)
        self.splash_logo.adjustSize()
        self._center_splash_logo()
        self.splash_logo.show()

        effect = QGraphicsOpacityEffect(self.splash_logo)
        self.splash_logo.setGraphicsEffect(effect)

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
                if self.splash_logo:
                    self.splash_logo.deleteLater()
                    self.splash_logo = None
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
        self.splash.setStyleSheet(
            f"background: transparent; margin-top: {CORNER_LOGO_OFFSET_TOP}px; margin-right: {CORNER_LOGO_OFFSET_RIGHT}px;"
        )
        self.splash.setPixmap(self._pix_small)
        self.splash.adjustSize()

        x = self.width() - self.splash.width() - CORNER_LOGO_MARGIN + abs(CORNER_LOGO_OFFSET_RIGHT)
        y = CORNER_LOGO_MARGIN + CORNER_LOGO_OFFSET_TOP
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

        if self.splash_logo and self.splash_logo.isVisible():
            self._center_splash_logo()

        if getattr(self, "_corner_logo_visible", False):
            x = self.width() - self.splash.width() - CORNER_LOGO_MARGIN + abs(CORNER_LOGO_OFFSET_RIGHT)
            y = CORNER_LOGO_MARGIN + CORNER_LOGO_OFFSET_TOP
            self.splash.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        if self.splash_logo:
            self._center_splash_logo()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        top_font = QFont()
        top_font.setPointSize(10)

        self.lbl_about = QLabel(
            "本ソフトは、第1スクリーニング後データから、さらに、「11_交差点(Point)データ」で指定するすべての交差点における通過トリップを一括で抽出します。\n"
            "抽出されたトリップは「20_第２スクリーニング）」フォルダへ１トリップに対し１CSVファイルで出力します。出力CSVは様式1-2のデータフォーマットを保持します。"
        )
        self.lbl_about.setWordWrap(True)
        self.lbl_about.setStyleSheet("color: #00ff99; font-weight: 600;")
        self.lbl_about.setFont(top_font)
        v.addWidget(self.lbl_about)

        self.flow = FlowGuide()
        flow_grid = QGridLayout(self.flow)
        flow_grid.setContentsMargins(0, 0, 0, 0)
        flow_grid.setHorizontalSpacing(18)
        flow_grid.setVerticalSpacing(8)

        self.btn_project = QPushButton("プロジェクトを選ぶ")
        self.btn_project.setFont(top_font)
        self.btn_project.clicked.connect(self.select_project)

        self.lbl_project = QLabel("未選択")
        self.lbl_project.setFont(top_font)
        self.lbl_project.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.lbl_project.setMinimumWidth(0)

        proj_w = QWidget()
        proj_l = QHBoxLayout(proj_w)
        proj_l.setContentsMargins(0, 0, 0, 0)
        proj_l.setSpacing(8)
        proj_l.addWidget(self.btn_project)
        proj_l.addWidget(self.lbl_project)

        self.btn_input = QPushButton("第1スクリーニングを選ぶ")
        self.btn_input.setFont(top_font)
        self.btn_input.clicked.connect(self.select_input)

        self.lbl_input = QLabel("未選択")
        self.lbl_input.setFont(top_font)

        in_w = QWidget()
        in_l = QHBoxLayout(in_w)
        in_l.setContentsMargins(0, 0, 0, 0)
        in_l.setSpacing(8)
        in_l.addWidget(self.btn_input)
        in_l.addWidget(self.lbl_input)

        self.spin_radius = QSpinBox()
        self.spin_radius.setFont(top_font)
        self.spin_radius.setRange(5, 200)
        self.spin_radius.setValue(30)

        rad_w = QWidget()
        rad_l = QHBoxLayout(rad_w)
        rad_l.setContentsMargins(0, 0, 0, 0)
        rad_l.setSpacing(2)
        rad_l.addWidget(QLabel("半径"))
        rad_l.addWidget(self.spin_radius)
        rad_l.addWidget(QLabel("m（デフォルト30m）"))

        self.btn_run = QPushButton("21 第2スクリーニング開始（分析スタート）")
        self.btn_run.setFont(top_font)
        self.btn_run.clicked.connect(self.run_screening)

        run_w = QWidget()
        run_l = QHBoxLayout(run_w)
        run_l.setContentsMargins(0, 0, 0, 0)
        run_l.addWidget(self.btn_run)

        box1 = StepBox("STEP 1 プロジェクトフォルダの選択", proj_w)
        box1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        box1.setMinimumWidth(360)
        box1.setMaximumWidth(720)
        box2 = StepBox("STEP 2 第1スクリーニングデータの選択", in_w)
        box2.setFixedWidth(410)
        box3 = StepBox("STEP 3 交差点通過判定半径（この半径以内を通過したらHIT）", rad_w)
        box3.setFixedWidth(410)
        box4 = StepBox("STEP 4 実行", run_w)
        box4.setFixedWidth(260)

        flow_grid.addWidget(box1, 0, 0)
        flow_grid.addWidget(box2, 0, 1)
        flow_grid.addWidget(box3, 0, 2)
        flow_grid.addWidget(box4, 0, 3)
        self._flow_spacer = QWidget()
        self._flow_spacer.setFixedWidth(260)
        flow_grid.addWidget(self._flow_spacer, 0, 4)
        flow_grid.setColumnStretch(0, 1)
        flow_grid.setColumnStretch(1, 0)
        flow_grid.setColumnStretch(2, 0)
        flow_grid.setColumnStretch(3, 0)
        flow_grid.setColumnStretch(4, 0)

        self.flow.set_steps([box1, box2, box3, box4])
        v.addWidget(self.flow)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "",
            "交差点名",
            "交差点CSV",
            "交差点jpg",
            "第2スクリーニング\n(フォルダ)",
            "第2スクリーニング\n(CSV)",
            "HITした\nトリップ数",
        ])
        run_header = RunHeaderView(Qt.Orientation.Horizontal, self.table, run_col=COL_RUN)
        self.table.setHorizontalHeader(run_header)
        run_header.toggle_all_requested.connect(self._toggle_all_runs_from_header)
        self._run_header = run_header
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, stretch=3)

        self.lbl_progress = QLabel("調査中ファイル 0,000/0,000ファイル（0.0％）")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("background-color: black; color: #00ff66;")
        self.log.setFont(QFont("Consolas", 10))
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setMaximumBlockCount(5000)
        v.addWidget(self.lbl_progress)
        v.addWidget(self.log, stretch=2)

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    def _append_ui_log(self, level: str, msg: str) -> None:
        line = f"{self._timestamp()} [{level}] {msg}"
        if line == self._last_log_line:
            return
        self.log.appendPlainText(line)
        self.log_lines.append(line)
        self._last_log_line = line

    def log_info(self, msg: str) -> None:
        self._append_ui_log("INFO", msg)

    def log_warn(self, msg: str) -> None:
        self._append_ui_log("WARN", msg)

    def log_error(self, msg: str) -> None:
        self._append_ui_log("ERROR", msg)

    def _column_alignment(self, column: int) -> Qt.AlignmentFlag:
        if column in CENTER_ALIGN_COLS:
            return Qt.AlignmentFlag.AlignCenter
        if column in RIGHT_ALIGN_COLS:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    def _set_text_item(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(self._column_alignment(col))
        self.table.setItem(row, col, item)

    def _set_run_item(self, row: int, checked: bool) -> None:
        cb = QCheckBox()
        cb.setChecked(checked)
        cb.stateChanged.connect(self._sync_run_header_state)
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(cb)
        self.table.setCellWidget(row, COL_RUN, w)

    def _toggle_all_runs_from_header(self, check_all: bool):
        for row in range(self.table.rowCount()):
            cell = self.table.cellWidget(row, COL_RUN)
            cb = cell.findChild(QCheckBox) if cell else None
            if cb:
                cb.setChecked(check_all)
        self._sync_run_header_state()

    def _sync_run_header_state(self):
        total = checked = 0
        for row in range(self.table.rowCount()):
            cell = self.table.cellWidget(row, COL_RUN)
            cb = cell.findChild(QCheckBox) if cell else None
            if cb:
                total += 1
                checked += int(cb.isChecked())
        if total == 0 or checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == total:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self._run_header.set_run_state(state)

    def _row_index_by_name(self, name: str) -> int:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            if item and item.text() == name:
                return row
        return -1

    def _update_progress_label(self) -> None:
        pct = (self.done_files / self.total_files * 100.0) if self.total_files else 0.0
        self.lbl_progress.setText(f"調査中ファイル {self.done_files:,.0f}/{self.total_files:,.0f}ファイル（{pct:.1f}％）")

    def select_project(self):
        d = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択", str(Path.cwd()))
        if not d:
            return
        self.project_dir = Path(d).resolve()
        self.lbl_project.setText(self.project_dir.name)
        self.log_info(f"project set: {self.project_dir}")
        self.scan_crossroads()

    def select_input(self):
        d = QFileDialog.getExistingDirectory(self, "第1スクリーニングデータフォルダを選択", str(Path.cwd()))
        if not d:
            return
        self.input_dir = Path(d).resolve()
        self.lbl_input.setText(self.input_dir.name)
        self.log_info(f"input set: {self.input_dir}")

    def scan_crossroads(self):
        self.table.setRowCount(0)

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

            has_csv = True
            has_jpg = jpg_path.exists()
            has_out = out_path.exists()
            n_s2_csv = len(list(out_path.glob("*.csv"))) if out_path.exists() else 0
            default_run = has_csv and has_jpg and has_out and (n_s2_csv > 0)

            r = self.table.rowCount()
            self.table.insertRow(r)
            self._set_run_item(r, default_run)
            self._set_text_item(r, COL_NAME, name)
            self._set_text_item(r, COL_CROSS_CSV, "✔")
            self._set_text_item(r, COL_CROSS_JPG, "✔" if has_jpg else "×")
            self._set_text_item(r, COL_S2_DIR, "✔" if has_out else "×")
            self._set_text_item(r, COL_S2_CSV, "✔" if n_s2_csv > 0 else "×")
            self._set_text_item(r, COL_HIT_TRIPS, "0")

        self._sync_run_header_state()
        self.log_info(f"scanned: {len(csvs)} crossroads")
        self.log_info(f"cross_dir: {cross_dir}")
        self.log_info(f"out_dir  : {out_dir}")

    def _collect_targets(self) -> list[str]:
        targets: list[str] = []
        for r in range(self.table.rowCount()):
            cell = self.table.cellWidget(r, COL_RUN)
            cb = cell.findChild(QCheckBox) if cell else None
            name_item = self.table.item(r, COL_NAME)
            if cb and cb.isChecked() and name_item:
                targets.append(name_item.text())
        return targets

    def _table_dump_lines(self) -> list[str]:
        lines: list[str] = []

        headers = []
        for c in range(self.table.columnCount()):
            header_item = self.table.horizontalHeaderItem(c)
            headers.append(header_item.text() if header_item else "")
        lines.append("\t".join(headers))

        for r in range(self.table.rowCount()):
            row_values: list[str] = []
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if c == COL_RUN:
                    cell = self.table.cellWidget(r, COL_RUN)
                    cb = cell.findChild(QCheckBox) if cell else None
                    run_flag = "1" if cb and cb.isChecked() else "0"
                    row_values.append(f"RUN={run_flag}")
                else:
                    row_values.append(item.text() if item else "")
            lines.append("\t".join(row_values))

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

        lines: list[str] = [f"Project: {self.project_dir}"]

        if self.input_dir:
            lines.append(f"Input: {self.input_dir}")

        lines.extend(
            [
                f"開始: {started_at}",
                f"終了: {ended_at}",
                f"総所要時間: {format_hhmmss(total_sec)}",
                "",
                "[UI表]",
                *self._table_dump_lines(),
                "",
                "[実行ログ]",
                *self.log_lines,
            ]
        )

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

        ui_py = Path(__file__).resolve()
        script21 = ui_py.parent / "21_point_trip_extractor.py"
        if not script21.exists():
            QMessageBox.critical(self, "エラー", f"本体スクリプトが見つかりません:\n{script21}")
            return

        if self.proc:
            self.proc.kill()
            self.proc = None

        self.log_lines = []
        self._last_log_line = None
        self.batch_started_at = datetime.now()
        self.batch_start_perf = perf_counter()
        self.batch_ended_at = None
        self.current_name = None
        self._stdout_buf = ""
        self._stderr_buf = ""

        self.total_files = len(list(self.input_dir.rglob("*.csv")))
        self.done_files = 0
        self._update_progress_label()

        self.btn_run.setEnabled(False)

        self.log_info("①プロジェクト選択 → ②第1スクリーニング選択 → 21【分析スタート】")
        self.log_info(
            f"start: targets={','.join(targets)} radius={self.spin_radius.value()}m"
        )

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments([
            "-u",
            str(script21),
            "--project",
            str(self.project_dir),
            "--input",
            str(self.input_dir),
            "--targets",
            *targets,
            "--radius-m",
            str(self.spin_radius.value()),
        ])

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
            if found == "ERROR":
                level = "ERROR"
            elif found in {"WARN", "WARNING"}:
                level = "WARN"
            else:
                level = "INFO"
            text = re.sub(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]\s*", "", text, count=1).strip()
        if level == "ERROR":
            self.log_error(text)
        elif level == "WARN":
            self.log_warn(text)
        else:
            self.log_info(text)

    def _handle_stream_line(self, line: str, from_cr: bool, is_err: bool) -> None:
        text = line.strip()
        if not text:
            return

        m_cross = RE_CUR_CROSS.search(text)
        if m_cross:
            self.current_name = m_cross.group(1)

        m_file = RE_FILE_DONE.search(text)
        if m_file:
            self.done_files = int(m_file.group(1))
            self.total_files = int(m_file.group(2))
            self._update_progress_label()

        m_hit = RE_HIT.search(text)
        if m_hit:
            name, count = m_hit.group(1), m_hit.group(2)
            row = self._row_index_by_name(name)
            if row >= 0:
                self._set_text_item(row, COL_HIT_TRIPS, count)
            # NOTE: HIT途中経過はログに不要（表更新だけ行い、ログには流さない）
            return

        if from_cr and RE_FILE_DONE.search(text):
            return

        self._log_process_line(text, is_err)

    def _maybe_update_realtime_from_buffer(self, buf: str) -> None:
        idx = buf.rfind("進捗:")
        if idx < 0:
            return
        tail = buf[idx:].strip()
        m = RE_FILE_DONE.search(tail)
        if m:
            self.done_files = int(m.group(1))
            self.total_files = int(m.group(2))
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
            self._stderr_buf = buf[start:]
            self._maybe_update_realtime_from_buffer(self._stderr_buf)
        else:
            self._stdout_buf = buf[start:]
            self._maybe_update_realtime_from_buffer(self._stdout_buf)

    def _flush_process_buffers(self) -> None:
        if self._stdout_buf:
            self._handle_stream_line(self._stdout_buf, False, False)
            self._stdout_buf = ""
        if self._stderr_buf:
            self._handle_stream_line(self._stderr_buf, False, True)
            self._stderr_buf = ""

    def _on_stdout(self):
        if not self.proc:
            return
        self._append_stream_chunk(self._decode_qbytearray(self.proc.readAllStandardOutput()), False)

    def _on_stderr(self):
        if not self.proc:
            return
        self._append_stream_chunk(self._decode_qbytearray(self.proc.readAllStandardError()), True)

    def _on_finished(self, code: int, _status):
        self._flush_process_buffers()
        self.log_info(f"process finished: code={code}")
        self.log_info("全件処理完了")
        self.btn_run.setEnabled(True)
        self.batch_ended_at = datetime.now()
        total_sec = perf_counter() - self.batch_start_perf if self.batch_start_perf else 0.0
        hms = format_hhmmss(total_sec)
        self.log_info(f"総所要時間: {hms}")
        self._write_batch_log_file(total_sec)
        # NOTE: 解析結果表示（HIT数など）を保持するため、解析終了時の自動再スキャンは行わない


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    QTimer.singleShot(0, w.showMaximized)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
