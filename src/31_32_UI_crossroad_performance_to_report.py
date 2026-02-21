import os
import re
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

os.environ.setdefault("QT_LOGGING_RULES", "qt.text.font.db=false")

from PyQt6.QtCore import QProcess, QPropertyAnimation, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QPainter, QPixmap, QColor, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
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

APP_TITLE = "31交差点パフォーマンス分析　+　32レポート作成（一括実行）"
FOLDER_CROSS = "11_交差点(Point)データ"
FOLDER_S2 = "20_第２スクリーニング"
FOLDER_31OUT = "31_交差点パフォーマンス"
FOLDER_32OUT = "32_交差点レポート"
WEEKDAY_KANJI = ["月", "火", "水", "木", "金", "土", "日"]
WEEKDAY_KANJI_TO_ABBR = {"月": "MON", "火": "TUE", "水": "WED", "木": "THU", "金": "FRI", "土": "SAT", "日": "SUN"}

COL_RUN = 0
COL_NAME = 1
COL_CROSS_CSV = 2
COL_CROSS_JPG = 3
COL_S2_DIR = 4
COL_S2_CSV = 5
COL_OUT31 = 6
COL_OUT32 = 7
COL_STATUS = 8
COL_DONE_FILES = 9
COL_TOTAL_FILES = 10
COL_WEEKDAY = 11
COL_SPLIT = 12
COL_TARGET = 13
COL_OK = 14
COL_UNK = 15
COL_NOTPASS = 16

CENTER_ALIGN_COLS = {COL_RUN, COL_CROSS_CSV, COL_CROSS_JPG, COL_S2_DIR, COL_S2_CSV, COL_OUT31, COL_OUT32}
RIGHT_ALIGN_COLS = {COL_DONE_FILES, COL_TOTAL_FILES, COL_WEEKDAY, COL_SPLIT, COL_TARGET, COL_OK, COL_UNK, COL_NOTPASS}
RE_PROGRESS = re.compile(r"進捗:\s*(\d+)\s*/\s*(\d+)")
RE_STATS = re.compile(r"曜日後:\s*(\d+).*?行数:\s*(\d+).*?成功:\s*(\d+).*?不明:\s*(\d+).*?不通過:\s*(\d+)")
RE_DONE = re.compile(r"完了:\s*ファイル=(\d+).*?曜日後=(\d+).*?行数=(\d+).*?成功=(\d+).*?不明=(\d+).*?不通過=(\d+)")
RE_LEVEL = re.compile(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]")


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
        painter.drawText(rect.adjusted(2, 2, -2, -2), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "分析対象")
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
        painter.drawText(QRect(cb_rect.right() + 4, cb_rect.top() - 1, rect.right() - cb_rect.right() - 6, cb_rect.height() + 2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "ALL")
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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)

        self.project_dir: Path | None = None
        self.proc: QProcess | None = None
        self.queue: list[str] = []
        self.current_name: str | None = None
        self.current_step = ""
        self._weekday_updating = False
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._recent_process_lines: list[str] = []
        self._last_log_line: str | None = None

        self.log_lines: list[str] = []
        self.batch_started_at: datetime | None = None
        self.batch_ended_at: datetime | None = None
        self.batch_start_perf: float | None = None
        self.cross_start_perf: dict[str, float] = {}

        self._waiting_lock_dialog: QDialog | None = None
        self._waiting_lock_timer: QTimer | None = None
        self._waiting_lock_path: Path | None = None
        self.splash_logo = None
        self.corner_logo = None
        self._logo_phase = "none"

        self._build_ui()
        self._corner_logo_visible = False
        self._pix_small = None
        self.LOGO_CORNER_PAD = 8
        self.LOGO_CORNER_DX = -10
        self.LOGO_CORNER_DY = -4
        QTimer.singleShot(0, self._init_logo_overlay)
        self.log_info("＊＊＊枝判定基準＊＊＊")
        self.log_info("第1判定：20-50mで角度算出　基準値との誤差30°")
        self.log_info("第2判定：20-70mで角度算出　基準値との誤差35°")
        self.log_info("第3判定：20-100mで角度算出　基準値との誤差40°")
        self.log_info("その他：枝不明")
        self.log_info("①プロジェクト選択 → ②曜日選択 → 31→32一括実行【分析スタート】")

        # 起動直後はレイアウトが未確定になりがちなので、短い遅延で数回リフレッシュ
        QTimer.singleShot(0, self._force_layout_refresh)
        QTimer.singleShot(50, self._force_layout_refresh)
        QTimer.singleShot(150, self._force_layout_refresh)
        QTimer.singleShot(0, self._update_flow_spacer_for_logo)
        QTimer.singleShot(50, self._update_flow_spacer_for_logo)
        QTimer.singleShot(150, self._update_flow_spacer_for_logo)

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

        x, y = self._logo_center_pos(self.splash_logo.width(), self.splash_logo.height())
        self.splash_logo.move(x, y)
        self._logo_phase = "center"
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

        self.corner_logo = QLabel(self)
        self.corner_logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.corner_logo.setStyleSheet("background: transparent;")
        self.corner_logo.setPixmap(self._pix_small)
        self.corner_logo.adjustSize()

        x, y = self._logo_corner_pos(self.corner_logo.width(), self.corner_logo.height())
        self.corner_logo.move(x, y)
        self.corner_logo.show()

        self._corner_logo_visible = True
        self._logo_phase = "corner"

        # ロゴ表示で右端条件が変わるので、ここでも再計算
        QTimer.singleShot(0, self._force_layout_refresh)
        QTimer.singleShot(0, self._update_flow_spacer_for_logo)
        QTimer.singleShot(50, self._update_flow_spacer_for_logo)

    def _logo_center_pos(self, w: int, h: int):
        r = self.rect()
        x = (r.width() - w) // 2
        y = (r.height() - h) // 2
        return x, y

    def _logo_corner_pos(self, w: int, h: int):
        r = self.rect()
        pad = getattr(self, "LOGO_CORNER_PAD", 8)
        dx = getattr(self, "LOGO_CORNER_DX", -10)
        dy = getattr(self, "LOGO_CORNER_DY", -4)
        x = r.width() - w - pad + dx
        y = pad + dy
        return x, y

    def _force_layout_refresh(self) -> None:
        """起動直後のレイアウト未確定を吸収し、幅依存UIを再計算する。"""
        try:
            cw = self.centralWidget()
            if cw and cw.layout():
                cw.layout().activate()
        except Exception:
            pass

        try:
            if hasattr(self, "flow") and self.flow:
                self.flow.update()
        except Exception:
            pass

        try:
            self.resize(self.size())
        except Exception:
            pass

    def _update_flow_spacer_for_logo(self) -> None:
        """STEP4右端がロゴ左端より少し左になるように、flow右側スペーサ幅を更新する（flow座標系で計算）。"""
        try:
            if not hasattr(self, "_flow_spacer") or not self._flow_spacer:
                return
            if not hasattr(self, "flow") or not self.flow:
                return

            corner = getattr(self, "corner_logo", None)
            if not corner or not corner.isVisible():
                # ロゴが無い時のデフォルト（必要最小限）
                self._flow_spacer.setFixedWidth(60)
                return

            # ロゴ左上のグローバル座標 → flowローカル座標へ変換
            logo_global = corner.mapToGlobal(QPoint(0, 0))
            logo_in_flow = self.flow.mapFromGlobal(logo_global)
            logo_left_in_flow = logo_in_flow.x()

            margin = 12  # “少し左”の量（好みで 8〜20）
            # flow内で右側に確保すべき幅
            reserve = max(0, self.flow.width() - (logo_left_in_flow - margin))
            reserve = max(reserve, 60)  # 下限
            self._flow_spacer.setFixedWidth(reserve)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_refresh_about_text"):
            try:
                self._refresh_about_text()
            except Exception:
                pass

        phase = getattr(self, "_logo_phase", "")

        splash = getattr(self, "splash_logo", None)
        if phase == "center" and splash and splash.isVisible():
            w, h = splash.width(), splash.height()
            x, y = self._logo_center_pos(w, h)
            splash.move(x, y)

        corner = getattr(self, "corner_logo", None)
        if phase == "corner" and corner and corner.isVisible():
            w, h = corner.width(), corner.height()
            x, y = self._logo_corner_pos(w, h)
            corner.move(x, y)

        if hasattr(self, "flow") and self.flow:
            self.flow.update()

        # 右スペーサ幅をロゴ位置に合わせて調整（STEP4右端がロゴ左端より少し左）
        self._update_flow_spacer_for_logo()

        if getattr(self, "project_dir", None) and hasattr(self, "lbl_project"):
            name = self.project_dir.name
            fm = QFontMetrics(self.lbl_project.font())
            max_px = self.lbl_project.width()
            self.lbl_project.setText(fm.elidedText(name, Qt.TextElideMode.ElideRight, max_px))
            self.lbl_project.setToolTip(name)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        top_font = QFont()
        top_font.setPointSize(10)

        # --- 説明文（ネオン色／2行・改行は1回のみ） ---
        self.lbl_about = QLabel(
            "本ソフトは、「11_交差点(Point)データ」と「20_第２スクリーニング」の様式1-2由来第2スクリーニング後データを用い、複数交差点を一括解析するETC2.0交差点分析ツールです。1トリップで指定交差点を複数回通過する場合は、トリップを分割して分析します。\n"
            "トリップを進入方向→退出方向別に分類し通過トリップ数を集計するとともに、スムーズ通過時間との差からトリップ毎の遅れ時間を算出し、方向別および総遅れ時間（交差点負荷指標）を算出します。その結果を1交差点につき1レポートとしてExcel形式で出力します。"
        )
        self.lbl_about.setWordWrap(True)
        self.lbl_about.setStyleSheet("color: #00ff99; font-weight: 600;")
        self.lbl_about.setFont(top_font)
        v.addWidget(self.lbl_about)

        # --- フローUI（ネオン四角＋接続線） ---
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

        wd_w = QWidget()
        wd_l = QHBoxLayout(wd_w)
        wd_l.setContentsMargins(0, 0, 0, 0)
        wd_l.setSpacing(8)

        self.chk_all = QCheckBox("ALL")
        self.chk_all.setFont(top_font)
        self.chk_all.setMinimumWidth(50)
        self.chk_all.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.chk_all.stateChanged.connect(self._on_all_weekday_changed)
        wd_l.addWidget(self.chk_all)

        self.weekday_checks: dict[str, QCheckBox] = {}
        for wd in WEEKDAY_KANJI:
            chk = QCheckBox(wd)
            chk.setFont(top_font)
            chk.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            chk.stateChanged.connect(self._on_single_weekday_changed)
            self.weekday_checks[wd] = chk
            wd_l.addWidget(chk)

        self._set_weekdays_from_all(True)

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
        rad_l.addWidget(QLabel("m（第2スクリーニング時と同一として下さい・既定30m）"))

        self.btn_run = QPushButton("31→32 一括実行（分析スタート）")
        self.btn_run.setFont(top_font)
        self.btn_run.clicked.connect(self.start_batch)

        run_w = QWidget()
        run_l = QHBoxLayout(run_w)
        run_l.setContentsMargins(0, 0, 0, 0)
        run_l.addWidget(self.btn_run)

        box1 = StepBox("STEP 1  プロジェクトフォルダの選択", proj_w)
        box1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        box1.setMinimumWidth(360)
        box1.setMaximumWidth(720)
        box2 = StepBox("STEP 2  分析対象とする曜日を選択", wd_w)
        box2.setFixedWidth(410)
        box3 = StepBox("STEP 3  交差点通過判定半径（この半径以内を通過したらHIT）", rad_w)
        box3.setFixedWidth(410)
        box4 = StepBox("STEP 4  実行", run_w)
        box4.setFixedWidth(260)

        flow_grid.addWidget(box1, 0, 0)
        flow_grid.addWidget(box2, 0, 1)
        flow_grid.addWidget(box3, 0, 2)
        flow_grid.addWidget(box4, 0, 3)
        # 右側に余白（ロゴ干渉回避）※固定幅で制御
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

        self.lbl_summary = QLabel("")
        v.addWidget(self.lbl_summary)

        self.table = QTableWidget(0, 17)
        self.table.setHorizontalHeaderLabels(["", "交差点名", "交差点CSV", "交差点jpg", "第2スクリーニング\n(フォルダ)", "第2スクリーニング\n(CSV)", "出力\n(performance.csv)", "出力\n(report)", "状態", "分析済み\nファイル数", "対象\nファイル数", "曜日フィルター後\nファイル数", "トリップ分割数", "対象トリップ数", "枝判定成功", "枝不明", "交差点不通過"])
        run_header = RunHeaderView(Qt.Orientation.Horizontal, self.table, run_col=COL_RUN)
        self.table.setHorizontalHeader(run_header)
        run_header.toggle_all_requested.connect(self._toggle_all_runs_from_header)
        self._run_header = run_header
        header = self.table.horizontalHeader()
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(24)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setFixedHeight(44)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, stretch=3)

        self.lbl_progress = QLabel("")
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

    def _decode_qbytearray(self, ba) -> str:
        raw = bytes(ba)
        if not raw:
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp932", errors="replace")

    def _handle_stream_line(self, line: str, from_cr: bool, is_err: bool) -> None:
        text = line.strip()
        if not text or text.startswith("qt.text.font.db:"):
            return
        if "完了:" in text and "ファイル=" in text:
            self._apply_done_summary(text)
        if from_cr or "進捗:" in text:
            self.lbl_progress.setText(text)
            self._update_table_progress(text)
            return
        self._recent_process_lines.append(text)
        self._recent_process_lines = self._recent_process_lines[-200:]
        self._log_process_line(text, is_err)

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

    def _set_run_controls_enabled(self, enabled: bool) -> None:
        self.btn_project.setEnabled(enabled)
        self.btn_run.setEnabled(enabled)
        self.chk_all.setEnabled(enabled)
        for chk in self.weekday_checks.values():
            chk.setEnabled(enabled)

    def _set_weekdays_from_all(self, checked: bool) -> None:
        self._weekday_updating = True
        st = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.chk_all.setCheckState(st)
        for chk in self.weekday_checks.values():
            chk.setCheckState(st)
        self._weekday_updating = False

    def _on_all_weekday_changed(self, state) -> None:
        if self._weekday_updating:
            return
        self._weekday_updating = True
        st = Qt.CheckState(state)
        target = Qt.CheckState.Checked if st == Qt.CheckState.Checked else Qt.CheckState.Unchecked
        for chk in self.weekday_checks.values():
            chk.setCheckState(target)
        self._weekday_updating = False

    def _on_single_weekday_changed(self, _state) -> None:
        if self._weekday_updating:
            return
        self._weekday_updating = True
        self.chk_all.setCheckState(Qt.CheckState.Checked if all(c.isChecked() for c in self.weekday_checks.values()) else Qt.CheckState.Unchecked)
        self._weekday_updating = False

    def _selected_weekdays_for_cli(self) -> list[str]:
        if self.chk_all.isChecked():
            return []
        return [WEEKDAY_KANJI_TO_ABBR[wd] for wd, chk in self.weekday_checks.items() if chk.isChecked()]

    def _selected_weekdays_for_log(self) -> str:
        selected = self._selected_weekdays_for_cli()
        return " ".join(selected) if selected else "(none)"

    def select_project(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択", str(Path.cwd()))
        if not d:
            return
        self.project_dir = Path(d).resolve()
        name = self.project_dir.name
        fm = QFontMetrics(self.lbl_project.font())
        max_px = self.lbl_project.width() if self.lbl_project.width() > 20 else 360
        elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, max_px)
        self.lbl_project.setText(elided)
        self.lbl_project.setToolTip(name)
        self.log_info(f"project set: {self.project_dir}")
        self.scan_crossroads()

    def _report_output_path(self, name: str) -> Path:
        return self.project_dir / FOLDER_32OUT / f"{name}_report.xlsx"

    def scan_crossroads(self) -> None:
        self.table.setRowCount(0)
        if not self.project_dir:
            self.log_warn("project not selected.")
            return
        cross_dir = self.project_dir / FOLDER_CROSS
        s2_dir = self.project_dir / FOLDER_S2
        out31_dir = self.project_dir / FOLDER_31OUT
        out32_dir = self.project_dir / FOLDER_32OUT
        out31_dir.mkdir(parents=True, exist_ok=True)
        out32_dir.mkdir(parents=True, exist_ok=True)

        csvs = sorted(cross_dir.glob("*.csv")) if cross_dir.exists() else []
        if not cross_dir.exists():
            QMessageBox.critical(self, "エラー", f"交差点フォルダが見つかりません:\n{cross_dir}")
            return
        if not csvs:
            QMessageBox.warning(self, "注意", f"交差点CSVが見つかりません:\n{cross_dir}")
            return

        sum_s2 = 0
        for csv_path in csvs:
            name = csv_path.stem
            jpg = cross_dir / f"{name}.jpg"
            s2_cross = s2_dir / name
            n_csv = len(list(s2_cross.glob("*.csv"))) if s2_cross.exists() else 0
            sum_s2 += n_csv
            out31 = out31_dir / f"{name}_performance.csv"
            out32 = self._report_output_path(name)

            r = self.table.rowCount()
            self.table.insertRow(r)
            self._set_run_item(r, csv_path.exists() and jpg.exists() and s2_cross.exists() and n_csv > 0)
            self._set_text_item(r, COL_NAME, name)
            self._set_text_item(r, COL_CROSS_CSV, "✔" if csv_path.exists() else "×")
            self._set_text_item(r, COL_CROSS_JPG, "✔" if jpg.exists() else "×")
            self._set_text_item(r, COL_S2_DIR, "✔" if s2_cross.exists() else "×")
            self._set_text_item(r, COL_S2_CSV, "✔" if n_csv > 0 else "×")
            self._set_text_item(r, COL_OUT31, "✔" if out31.exists() else "×")
            self._set_text_item(r, COL_OUT32, "✔" if out32.exists() else "×")
            self._set_text_item(r, COL_STATUS, "")
            for col in [COL_DONE_FILES, COL_WEEKDAY, COL_SPLIT, COL_TARGET, COL_OK, COL_UNK, COL_NOTPASS]:
                self._set_text_item(r, col, "0")
            self._set_text_item(r, COL_TOTAL_FILES, str(n_csv))
            info = {"cross_csv": str(csv_path), "cross_jpg": str(jpg), "s2_dir": str(s2_cross), "out31": str(out31), "out32": str(out32), "name": name}
            self.table.item(r, COL_NAME).setData(Qt.ItemDataRole.UserRole, info)

        self.lbl_summary.setText(f"Crossroads: {len(csvs)} / S2 CSV total: {sum_s2}")
        self.log_info(f"scanned: {len(csvs)} crossroads")
        self.log_info(f"s2 total csv files: {sum_s2}")
        self._sync_run_header_state()

    def _collect_targets(self) -> list[str]:
        out: list[str] = []
        for r in range(self.table.rowCount()):
            cell = self.table.cellWidget(r, COL_RUN)
            cb = cell.findChild(QCheckBox) if cell else None
            name_item = self.table.item(r, COL_NAME)
            if cb and name_item and cb.isChecked():
                out.append(name_item.text())
        return out

    def _row_index_by_name(self, name: str) -> int:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, COL_NAME)
            if item and item.text() == name:
                return r
        return -1

    def _set_status_for_current_row(self, status: str) -> None:
        if self.current_name is None:
            return
        row = self._row_index_by_name(self.current_name)
        if row >= 0:
            self._set_text_item(row, COL_STATUS, status)

    def start_batch(self) -> None:
        if not self.project_dir:
            QMessageBox.warning(self, "未設定", "①プロジェクトフォルダを選択してください。")
            return
        targets = self._collect_targets()
        if not targets:
            QMessageBox.information(self, "対象なし", "実行対象の交差点が選択されていません。")
            return

        # 設計原則：
        # ・出力ファイル名は完全固定
        # ・完全一致のみが上書き対象
        # ・拡張子一致や部分一致は絶対に行わない
        # ・UI表示ロジックと同じ判定方法を使用する
        performance_dir = self.project_dir / FOLDER_31OUT
        report_dir = self.project_dir / FOLDER_32OUT
        existing_targets: list[str] = []
        for name in targets:
            perf_path = performance_dir / f"{name}_performance.csv"
            report_path = report_dir / f"{name}_report.xlsx"
            if perf_path.exists() or report_path.exists():
                existing_targets.append(name)

        if existing_targets:
            msg = "既に出力ファイルが存在する交差点があります。\n\n" + "\n".join(existing_targets) + "\n\n上書きしますか？"
            reply = QMessageBox.question(
                self,
                "上書き確認",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.log_info("ユーザーによりキャンセルされました。")
                return

        self.batch_started_at = datetime.now()
        self.batch_start_perf = perf_counter()
        self.batch_ended_at = None
        self.queue = targets
        self.log_info("========================================")
        self.log_info(f"weekdays: {self._selected_weekdays_for_log()}")
        self.log_info(f"radius: {self.spin_radius.value()}m")
        self.log_info(f"start: targets={len(targets)}")
        self.log_info("========================================")
        self._set_run_controls_enabled(False)
        self._start_next_crossroad()

    def _start_next_crossroad(self) -> None:
        if not self.queue:
            self._finish_batch()
            return
        self.current_name = self.queue.pop(0)
        self.current_step = "31"
        self.cross_start_perf[self.current_name] = perf_counter()
        self.log_info(f"交差点開始: {self.current_name}")
        self._set_status_for_current_row("31 実行中")
        self._start_step31(self.current_name)

    def _ensure_file_unlock(self, path: Path, on_ok) -> None:
        if not path.exists():
            on_ok()
            return
        try:
            with open(path, "a", encoding="utf-8"):
                pass
            on_ok()
            return
        except PermissionError:
            self._waiting_lock_path = path
            self._waiting_lock_dialog = QDialog(self)
            self._waiting_lock_dialog.setWindowTitle("上書き待機")
            self._waiting_lock_dialog.setModal(True)
            self._waiting_lock_dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
            lay = QVBoxLayout(self._waiting_lock_dialog)
            lay.addWidget(QLabel("出力ファイルが開かれているため上書きできません。ファイルを閉じて下さい。閉じると自動で続行します。"))
            self._waiting_lock_timer = QTimer(self)
            self._waiting_lock_timer.timeout.connect(lambda: self._retry_unlock(on_ok))
            self._waiting_lock_timer.start(700)
            self._waiting_lock_dialog.show()

    def _retry_unlock(self, on_ok) -> None:
        if not self._waiting_lock_path:
            return
        try:
            with open(self._waiting_lock_path, "a", encoding="utf-8"):
                pass
        except PermissionError:
            return
        if self._waiting_lock_timer:
            self._waiting_lock_timer.stop()
            self._waiting_lock_timer.deleteLater()
            self._waiting_lock_timer = None
        if self._waiting_lock_dialog:
            self._waiting_lock_dialog.accept()
            self._waiting_lock_dialog.deleteLater()
            self._waiting_lock_dialog = None
        self._waiting_lock_path = None
        on_ok()

    def _start_step31(self, name: str) -> None:
        row = self._row_index_by_name(name)
        if row < 0:
            self._start_next_crossroad()
            return
        info = self.table.item(row, COL_NAME).data(Qt.ItemDataRole.UserRole) or {}
        out31 = Path(info["out31"])
        script31 = Path(__file__).resolve().parent / "31_crossroad_trip_performance.py"
        if not script31.exists():
            self.log_error(f"31 script not found: {script31}")
            self._start_next_crossroad()
            return

        def _launch():
            args = [
                str(script31),
                "--project",
                str(self.project_dir),
                "--targets",
                name,
                "--progress-step",
                "1",
                "--radius-m",
                str(self.spin_radius.value()),
            ]
            selected = self._selected_weekdays_for_cli()
            if selected:
                args.extend(["--weekdays", *selected])
            self._launch_process(args)

        self._ensure_file_unlock(out31, _launch)

    def _start_step32(self, name: str) -> None:
        row = self._row_index_by_name(name)
        if row < 0:
            self._start_next_crossroad()
            return
        info = self.table.item(row, COL_NAME).data(Qt.ItemDataRole.UserRole) or {}
        out32 = Path(info["out32"])
        script32 = Path(__file__).resolve().parent / "32_crossroad_report.py"
        if not script32.exists():
            self.log_error(f"32 script not found: {script32}")
            self._start_next_crossroad()
            return

        def _launch():
            self._launch_process([str(script32), "--project", str(self.project_dir), "--targets", name])

        if out32.exists():
            self._ensure_file_unlock(out32, _launch)
        else:
            _launch()

    def _launch_process(self, args: list[str]) -> None:
        if self.proc:
            self.proc.kill()
            self.proc = None
        self.proc = QProcess(self)
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._recent_process_lines = []
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(["-u", *args])
        self.log_info(f"launch: {sys.executable} -u {' '.join(args)}")
        self.proc.readyReadStandardOutput.connect(self._on_proc_stdout)
        self.proc.readyReadStandardError.connect(self._on_proc_stderr)
        self.proc.errorOccurred.connect(self._on_proc_error)
        self.proc.finished.connect(self._on_finished)
        self.proc.start()
        if not self.proc.waitForStarted(3000):
            self.log_error(f"QProcess failed to start: {self.proc.errorString()}")
            self._set_status_for_current_row(f"{self.current_step} failed (start error)")
            self._start_next_crossroad()

    def _on_proc_stdout(self) -> None:
        if self.proc:
            self._append_stream_chunk(self._decode_qbytearray(self.proc.readAllStandardOutput()), False)

    def _on_proc_stderr(self) -> None:
        if self.proc:
            self._append_stream_chunk(self._decode_qbytearray(self.proc.readAllStandardError()), True)

    def _on_proc_error(self, err) -> None:
        if self.proc:
            self.log_error(f"QProcess errorOccurred: {err} / {self.proc.errorString()}")

    def _update_row_outputs(self, name: str) -> None:
        row = self._row_index_by_name(name)
        if row < 0:
            return
        info = self.table.item(row, COL_NAME).data(Qt.ItemDataRole.UserRole) or {}
        self._set_text_item(row, COL_OUT31, "✔" if Path(info["out31"]).exists() else "×")
        has_report = Path(info["out32"]).exists()
        self._set_text_item(row, COL_OUT32, "✔" if has_report else "×")

    def _extract_last_error_line(self) -> str:
        for line in reversed(self._recent_process_lines):
            if "[ERROR]" in line or "Traceback" in line or "PermissionError" in line:
                return line
        for line in reversed(self._recent_process_lines):
            if line.strip():
                return line.strip()
        return ""

    def _on_finished(self, code: int, status) -> None:
        self._flush_process_buffers()
        self.lbl_progress.setText("")
        if self.current_name is None:
            self._start_next_crossroad()
            return

        if code != 0:
            reason = self._extract_last_error_line()
            msg = f"{self.current_step} failed (code={code})"
            if reason:
                msg = f"{msg} / {reason}"
            self._set_status_for_current_row(msg)
            self.log_error(msg)
            self._start_next_crossroad()
            return

        if self.current_step == "31":
            self._update_row_outputs(self.current_name)
            self._set_status_for_current_row("32 実行中")
            self.current_step = "32"
            self._start_step32(self.current_name)
            return

        self._update_row_outputs(self.current_name)
        row = self._row_index_by_name(self.current_name)
        info = self.table.item(row, COL_NAME).data(Qt.ItemDataRole.UserRole) or {}
        if not Path(info["out32"]).exists():
            msg = f"32 failed: report not created: {self.current_name}"
            self._set_status_for_current_row(msg)
            self.log_error(msg)
            self._start_next_crossroad()
            return

        self._set_text_item(row, COL_OUT32, "✔")
        self._set_status_for_current_row("完了")
        dt = perf_counter() - self.cross_start_perf.get(self.current_name, perf_counter())
        self.log_info(f"交差点: {self.current_name} 所要時間: {dt:.1f}s")
        self.log_info(f"交差点完了: {self.current_name}")
        self._start_next_crossroad()

    def _update_table_progress(self, text: str) -> None:
        if not self.current_name:
            return
        row = self._row_index_by_name(self.current_name)
        if row < 0:
            return
        m = RE_PROGRESS.search(text)
        if m:
            self._set_text_item(row, COL_DONE_FILES, m.group(1))
            self._set_text_item(row, COL_TOTAL_FILES, m.group(2))
        m2 = RE_STATS.search(text)
        if m2:
            weekday, rows, ok, unk, notpass = map(int, m2.groups())
            target = rows + notpass
            split = rows + notpass - weekday
            if ok + unk != rows:
                self.log_warn(f"rows mismatch: ok({ok}) + unk({unk}) != rows({rows}) for {self.current_name}")
            self._set_text_item(row, COL_WEEKDAY, str(weekday))
            self._set_text_item(row, COL_SPLIT, str(split))
            self._set_text_item(row, COL_TARGET, str(target))
            self._set_text_item(row, COL_OK, str(ok))
            self._set_text_item(row, COL_UNK, str(unk))
            self._set_text_item(row, COL_NOTPASS, str(notpass))

    def _maybe_update_realtime_from_buffer(self, buf: str) -> None:
        idx = buf.rfind("進捗:")
        if idx < 0:
            return
        tail = buf[idx:].strip()
        if RE_PROGRESS.search(tail) or RE_STATS.search(tail):
            self.lbl_progress.setText(tail)
            self._update_table_progress(tail)

    def _apply_done_summary(self, text: str) -> None:
        if not self.current_name:
            return
        row = self._row_index_by_name(self.current_name)
        if row < 0:
            return
        m = RE_DONE.search(text)
        if not m:
            return
        total, weekday, rows, ok, unk, notpass = map(int, m.groups())
        target = rows + notpass
        split = rows + notpass - weekday
        self._set_text_item(row, COL_DONE_FILES, str(total))
        self._set_text_item(row, COL_TOTAL_FILES, str(total))
        self._set_text_item(row, COL_WEEKDAY, str(weekday))
        self._set_text_item(row, COL_SPLIT, str(split))
        self._set_text_item(row, COL_TARGET, str(target))
        self._set_text_item(row, COL_OK, str(ok))
        self._set_text_item(row, COL_UNK, str(unk))
        self._set_text_item(row, COL_NOTPASS, str(notpass))

    def _format_hms(self, sec: float) -> str:
        total = int(sec)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _table_dump_lines(self) -> list[str]:
        headers = [self.table.horizontalHeaderItem(i).text().replace("\n", " ").strip() for i in range(self.table.columnCount())]
        out = ["\t".join(headers)]
        for r in range(self.table.rowCount()):
            row_vals: list[str] = []
            for c in range(self.table.columnCount()):
                if c == COL_RUN:
                    cell = self.table.cellWidget(r, COL_RUN)
                    cb = cell.findChild(QCheckBox) if cell else None
                    row_vals.append("1" if cb and cb.isChecked() else "0")
                else:
                    item = self.table.item(r, c)
                    row_vals.append(item.text() if item else "")
            out.append("\t".join(row_vals))
        return out

    def _write_batch_log_files(self, total_sec: float) -> None:
        if not self.project_dir:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        text_lines = [
            f"Project: {self.project_dir}",
            f"開始時刻: {self.batch_started_at.strftime('%Y/%m/%d %H:%M:%S') if self.batch_started_at else ''}",
            f"終了時刻: {self.batch_ended_at.strftime('%Y/%m/%d %H:%M:%S') if self.batch_ended_at else ''}",
            f"総所要時間: {self._format_hms(total_sec)}",
            "",
            "[UI表]",
            *self._table_dump_lines(),
            "",
            "[実行ログ]",
            *self.log_lines,
            "",
        ]
        content = "\n".join(text_lines)
        for folder in [self.project_dir / FOLDER_31OUT, self.project_dir / FOLDER_32OUT]:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"31_32_batch_log_{stamp}.txt").write_text(content, encoding="utf-8")

    def _finish_batch(self) -> None:
        self.batch_ended_at = datetime.now()
        total_sec = perf_counter() - self.batch_start_perf if self.batch_start_perf else 0.0
        self.log_info("========================================")
        self.log_info("🎉🎉🎉 全件処理完了 🎉🎉🎉")
        self.log_info("========================================")
        self.log_info(f"総所要時間: {self._format_hms(total_sec)}")
        self._write_batch_log_files(total_sec)
        self.current_name = None
        self.current_step = ""
        self._set_run_controls_enabled(True)


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()

    # いきなり showMaximized() しない。まず show() して polish/サイズヒント/レイアウトを確定させる
    w.show()

    # イベントループに入ってから最大化（初回から幅が安定する）
    QTimer.singleShot(0, w.showMaximized)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
