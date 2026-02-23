import os
import re
import sys
import math
import time
from datetime import datetime
from pathlib import Path
from time import perf_counter

os.environ.setdefault("QT_LOGGING_RULES", "qt.text.font.db=false")

from PyQt6.QtCore import QProcess, QPropertyAnimation, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QFont, QFontMetrics, QPainter, QPixmap, QColor, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QSpinBox,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "31_交差点パフォーマンス分析ツール + 32レポート作成ツール（一括実行）"
FOLDER_CROSS = "11_交差点(Point)データ"
FOLDER_S2 = "20_第２スクリーニング"
FOLDER_31OUT = "31_交差点パフォーマンス"
FOLDER_32OUT = "32_交差点レポート"
WEEKDAY_KANJI = ["月", "火", "水", "木", "金", "土", "日"]
WEEKDAY_KANJI_TO_ABBR = {"月": "MON", "火": "TUE", "水": "WED", "木": "THU", "金": "FRI", "土": "SAT", "日": "SUN"}

RE_PROGRESS = re.compile(r"進捗:\s*(\d+)\s*/\s*(\d+)")
RE_STATS = re.compile(r"曜日後:\s*(\d+).*?行数:\s*(\d+).*?成功:\s*(\d+).*?不明:\s*(\d+).*?不通過:\s*(\d+)")
RE_DONE = re.compile(r"完了:\s*ファイル=(\d+).*?曜日後=(\d+).*?行数=(\d+).*?成功=(\d+).*?不明=(\d+).*?不通過=(\d+)")
RE_LEVEL = re.compile(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]")


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




def format_hhmmss(total_sec: float) -> str:
    sec = max(0, int(total_sec + 0.5))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=4):
        super().__init__(parent)
        self.item_list = []
        self.setContentsMargins(margin, margin, margin, margin)
        self._hspace = spacing
        self._vspace = spacing

    def addItem(self, item): self.item_list.append(item)
    def count(self): return len(self.item_list)
    def itemAt(self, index): return self.item_list[index] if 0 <= index < len(self.item_list) else None
    def takeAt(self, index): return self.item_list.pop(index) if 0 <= index < len(self.item_list) else None
    def expandingDirections(self): return Qt.Orientation(0)
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self.do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.do_layout(rect, False)

    def sizeHint(self): return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def do_layout(self, rect, test_only):
        x = rect.x(); y = rect.y(); line_height = 0
        for item in self.item_list:
            next_x = x + item.sizeHint().width() + self._hspace
            if next_x - self._hspace > rect.right() and line_height > 0:
                x = rect.x(); y += line_height + self._vspace
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
        p.setPen(QPen(QColor("#1b4f2f")))
        r = min(self.width(), self.height()) // 2 - 8
        c = self.rect().center()
        p.drawEllipse(c, r, r)
        p.drawEllipse(c, int(r * 0.66), int(r * 0.66))
        p.drawEllipse(c, int(r * 0.33), int(r * 0.33))
        p.setPen(QPen(QColor("#56d27f"), 2))
        rad = self.angle * math.pi / 180
        x = int(c.x() + r * math.cos(rad))
        y = int(c.y() - r * math.sin(rad))
        p.drawLine(c.x(), c.y(), x, y)


class CrossCardPerf(QFrame):
    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.selected = True
        self.locked = False
        self.data = {"done": 0, "total": 0, "weekday": 0, "split": 0, "target": 0, "ok": 0, "unk": 0, "notpass": 0}
        self.flags = {"has_csv": False, "has_jpg": False, "has_s2_dir": False, "s2_csv": 0, "has_out31": False, "has_out32": False}
        self.paths: dict[str, str] = {}
        self.state = "待機"
        self.setObjectName("crossCard")
        self.setMinimumWidth(270); self.setMaximumWidth(270); self.setFixedHeight(300)
        v = QVBoxLayout(self); v.setSpacing(6); v.setContentsMargins(8, 8, 8, 8)
        self.title = QLabel(name)
        ft = self.title.font(); ft.setPointSize(max(12, ft.pointSize() + 2)); ft.setBold(True); self.title.setFont(ft)
        self.lbl_select = QLabel()
        self.lbl_flags = QLabel(); self.lbl_s2 = QLabel(); self.lbl_out = QLabel(); self.lbl_progress = QLabel(); self.lbl_stats = QLabel(); self.lbl_state = QLabel()
        self.btn_report = QPushButton("report.xlsx を開く")
        self.btn_branch = QPushButton("IN/OUT枝 判定ビューアー起動")
        for b in (self.btn_report, self.btn_branch):
            b.setMinimumHeight(36)
            b.setEnabled(False)
            b.setStyleSheet(
                """
                QPushButton {
                    padding: 6px 10px;
                    margin: 0px;
                }
                """
            )
        self.btn_report.clicked.connect(lambda: self._open_path("out32", "report.xlsx"))
        self.btn_branch.clicked.connect(self._launch_branch_viewer)
        self.lbl_stats.setWordWrap(True)
        for w in [self.title, self.lbl_select, self.lbl_flags, self.lbl_s2, self.lbl_out, self.lbl_progress, self.lbl_stats, self.lbl_state, self.btn_report, self.btn_branch]:
            v.addWidget(w)
        self.apply_state("待機")

    def mousePressEvent(self, event):
        w = self.childAt(event.pos())
        if isinstance(w, QPushButton) or self.locked:
            return
        self.selected = not self.selected
        self.apply_state(self.state)

    def _open_path(self, key: str, label: str):
        p = Path(self.paths.get(key, ""))
        if not p.exists():
            QMessageBox.information(self, "情報", f"{label} が見つかりません。")
            return
        os.startfile(str(p))

    def set_locked(self, locked: bool):
        self.locked = locked

    def set_buttons_enabled(self, enabled: bool):
        if not enabled:
            self.btn_report.setEnabled(False)
            self.btn_branch.setEnabled(False)
            return

        has_out32 = Path(self.paths.get("out32", "")).exists()
        has_out31 = Path(self.paths.get("out31", "")).exists()
        self.btn_report.setEnabled(has_out32)
        self.btn_branch.setEnabled(has_out31)

    def set_flags(self, *, has_csv: bool, has_jpg: bool, has_s2_dir: bool, s2_csv: int, has_out31: bool, has_out32: bool):
        self.flags.update(locals())
        self.flags.pop('self',None)
        self.lbl_flags.setText(f"交差点CSV/JPG: {'有' if has_csv else '無'} / {'有' if has_jpg else '無'}")
        self.lbl_s2.setText(f"S2フォルダ/S2 CSV数: {'有' if has_s2_dir else '無'} / {s2_csv:,}")
        self.lbl_out.setText(f"performance.csv/report.xlsx: {'有' if has_out31 else '無'} / {'有' if has_out32 else '無'}")

    def set_progress(self, done: int, total: int):
        self.data['done']=done; self.data['total']=total
        pct = (done / total * 100.0) if total else 0.0
        self.lbl_progress.setText(f"進捗ファイル: {done:,}/{total:,} ({pct:.1f}%)")

    def set_stats(self, weekday: int, split: int, target: int, ok: int, unk: int, notpass: int):
        self.data.update({"weekday": weekday, "split": split, "target": target, "ok": ok, "unk": unk, "notpass": notpass})
        line1 = f"対象曜日のCSVファイル数{weekday:,}／分割トリップ数{split:,}"
        line2 = f"対象トリップ数{target:,}／成功{ok:,}／枝不明{unk:,}／不通過{notpass:,}"
        self.lbl_stats.setText(line1 + "\n" + line2)

    def _launch_branch_viewer(self):
        perf = Path(self.paths.get("out31", ""))
        if not perf.exists():
            QMessageBox.information(self, "情報", "performance.csv が見つかりません。先に31を実行してください。")
            return

        bat = Path(__file__).resolve().parent.parent / "bat" / "33_branch_check.bat"
        if not bat.exists():
            QMessageBox.critical(self, "エラー", f"33_branch_check.bat が見つかりません:\n{bat}")
            return

        ok = QProcess.startDetached(str(bat), [str(perf)])
        if not ok:
            QMessageBox.critical(self, "エラー", "33の起動に失敗しました。")

    def set_state(self, state: str):
        self.state = state
        self.apply_state(state)

    def apply_state(self, state: str):
        self.lbl_select.setText("分析対象" if self.selected else "非対象")
        self.lbl_state.setText(f"状態: {state}")
        if self.selected:
            style = "border:1px solid #1ee6a8;background:#07120e;color:#7cffc6;"
            if state in {"31 実行中", "32 実行中"}: style = "border:2px solid #9cffbe;background:#0f1e17;color:#b5ffd0;"
            if state == "完了": style = "border:2px solid #68d088;background:#0c1712;color:#a2f0be;"
            if "failed" in state or "エラー" in state: style = "border:2px solid #d96f6f;background:#261010;color:#ffaaaa;"
        else:
            style = "border:1px solid #0c5a41;background:#040806;color:#2f7a5b;"
        self.setStyleSheet(f"QFrame#crossCard{{{style}}}")


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
        self.cards: dict[str, CrossCardPerf] = {}
        self.target_count = 0
        self.started_at = 0.0
        self._eta_done = 0
        self._eta_total = 0
        self._telemetry_running = False
        self._global_total_files = 0
        self._global_done_files = 0
        self._elapsed_frozen_text = "経過 00:00:00"

        self._waiting_lock_dialog: QDialog | None = None
        self._waiting_lock_timer: QTimer | None = None
        self._waiting_lock_path: Path | None = None
        self.splash_logo = None
        self.corner_logo = None
        self._logo_phase = "none"

        self._build_ui()
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick_animation)
        self.anim_timer.start(120)
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
        root = QWidget(); self.setCentralWidget(root)
        v = QVBoxLayout(root)
        top_font = QFont(); top_font.setPointSize(10)

        self.lbl_about = QLabel(
            "本ソフトは、「11_交差点(Point)データ」と「20_第２スクリーニング」の様式1-2由来第2スクリーニング後データを用い、複数交差点を一括解析するETC2.0交差点分析ツールです。1トリップで指定交差点を複数回通過する場合は、トリップを分割して分析します。\n"
            "トリップを進入方向→退出方向別に分類し通過トリップ数を集計するとともに、スムーズ通過時間との差からトリップ毎の遅れ時間を算出し、方向別および総遅れ時間（交差点負荷指標）を算出します。その結果を1交差点につき1レポートとしてExcel形式で出力します。"
        )
        self.lbl_about.setWordWrap(True); self.lbl_about.setFont(top_font)
        v.addWidget(self.lbl_about)

        self.flow = FlowGuide(); self.flow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.flow_host = QWidget()
        flow_grid = QGridLayout(self.flow_host); flow_grid.setContentsMargins(0, 0, 0, 0); flow_grid.setHorizontalSpacing(18)

        self.btn_project = QPushButton("プロジェクトを選ぶ"); self.btn_project.setFont(top_font); self.btn_project.clicked.connect(self.select_project)
        self.btn_project.setMinimumHeight(36)
        self.lbl_project = QLabel("未選択"); self.lbl_project.setFont(top_font)
        proj_w = QWidget(); proj_l = QHBoxLayout(proj_w); proj_l.setContentsMargins(0, 0, 0, 0); proj_l.addWidget(self.btn_project); proj_l.addWidget(self.lbl_project)

        wd_w = QWidget(); wd_l = QHBoxLayout(wd_w); wd_l.setContentsMargins(0, 0, 0, 0); wd_l.setSpacing(8)
        self.chk_all = QCheckBox("ALL"); self.chk_all.stateChanged.connect(self._on_all_weekday_changed); wd_l.addWidget(self.chk_all)
        self.weekday_checks: dict[str, QCheckBox] = {}
        for wd in WEEKDAY_KANJI:
            chk = QCheckBox(wd); chk.stateChanged.connect(self._on_single_weekday_changed)
            self.weekday_checks[wd] = chk; wd_l.addWidget(chk)
        self._set_weekdays_from_all(True)

        self.spin_radius = QSpinBox(); self.spin_radius.setRange(5, 200); self.spin_radius.setValue(30)
        self.spin_radius.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons); self.spin_radius.setFixedWidth(90)
        rad_w = QWidget(); rad_l = QHBoxLayout(rad_w); rad_l.setContentsMargins(0, 0, 0, 0); rad_l.setSpacing(4)
        m_lbl = QLabel("m（第2スクリーニング時と同一として下さい・既定30m）"); m_lbl.setStyleSheet("border:none;")
        rad_l.addWidget(QLabel("半径")); rad_l.addWidget(self.spin_radius); rad_l.addWidget(QLabel("m")); rad_l.addWidget(m_lbl)

        self.btn_run = QPushButton("31→32 一括実行（分析スタート）"); self.btn_run.clicked.connect(self.start_batch)
        self.btn_run.setMinimumHeight(36)
        run_w = QWidget(); run_l = QHBoxLayout(run_w); run_l.setContentsMargins(0, 0, 0, 0); run_l.addWidget(self.btn_run)

        box1 = StepBox("STEP 1  プロジェクトフォルダの選択", proj_w); box1.setMinimumWidth(360)
        box2 = StepBox("STEP 2  分析対象とする曜日を選択", wd_w); box2.setFixedWidth(410)
        box3 = StepBox("STEP 3  交差点通過判定半径（この半径以内を通過したらHIT）", rad_w); box3.setFixedWidth(410)
        box4 = StepBox("STEP 4  実行", run_w); box4.setFixedWidth(280)
        flow_grid.addWidget(box1, 0, 0); flow_grid.addWidget(box2, 0, 1); flow_grid.addWidget(box3, 0, 2); flow_grid.addWidget(box4, 0, 3)
        self._flow_spacer = QWidget(); self._flow_spacer.setFixedWidth(260); flow_grid.addWidget(self._flow_spacer, 0, 4)

        flow_stack_host = QWidget(); stack = QStackedLayout(flow_stack_host); stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.addWidget(self.flow_host); stack.addWidget(self.flow)
        self.flow.set_steps([box1, box2, box3, box4])
        v.addWidget(flow_stack_host)

        self.lbl_summary = QLabel(""); v.addWidget(self.lbl_summary)

        mid_split = QSplitter(Qt.Orientation.Horizontal)
        v.addWidget(mid_split, stretch=1)
        left_panel = QFrame(); lv = QVBoxLayout(left_panel)
        self.cross_container = QWidget(); self.cross_flow = FlowLayout(self.cross_container, margin=0, spacing=4); self.cross_container.setLayout(self.cross_flow)
        self.cross_scroll = QScrollArea(); self.cross_scroll.setWidgetResizable(True); self.cross_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.cross_scroll.setWidget(self.cross_container)
        lv.addWidget(self.cross_scroll); mid_split.addWidget(left_panel)

        right_panel = QFrame(); rv = QVBoxLayout(right_panel)
        rv.addWidget(QLabel("CYBER TELEMETRY"))
        self.tele = {"cross_total": QLabel("交差点数: 0"), "status": QLabel("状態: IDLE"), "current": QLabel("現在: -")}
        self.lbl_progress = QLabel("進捗ファイル: 0/0（0.0%）")
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        self.time_elapsed_big = QLabel("経過 00:00:00"); self.time_eta_big = QLabel("残り --:--:--")
        self.time_elapsed_big.setFont(QFont("Consolas", 16, QFont.Weight.Bold)); self.time_eta_big.setFont(QFont("Consolas", 16, QFont.Weight.Bold))
        for k in ["cross_total", "status", "current"]: rv.addWidget(self.tele[k])
        rv.addWidget(self.lbl_progress); rv.addWidget(self.progress_bar); rv.addWidget(self.time_elapsed_big); rv.addWidget(self.time_eta_big)
        self.sweep = SweepWidget(); rv.addWidget(self.sweep); rv.addStretch(1); mid_split.addWidget(right_panel)
        mid_split.setSizes([1700, 380]); mid_split.setStretchFactor(0, 4); mid_split.setStretchFactor(1, 1)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setFont(QFont("Consolas", 10)); self.log.setMaximumBlockCount(5000); self.log.setFixedHeight(160)
        v.addWidget(self.log, stretch=0)
        self._set_style()

    def _set_style(self) -> None:
        self.setStyleSheet("""
            QWidget { background: #050908; color: #79d58f; }
            QPlainTextEdit, QSpinBox, QProgressBar, QScrollArea { background: #0a120f; border: 1px solid #1f3f2d; }
            QPushButton { background: #0f2a1c; border: 2px solid #00ff99; padding: 8px 12px; border-radius: 12px; color: #eafff4; font-weight: 900; }
            QPushButton:hover { background: #153a26; }
            QPushButton:pressed { background: #0b1f14; }
            QPushButton:disabled { background: #0a120f; border: 2px solid #2a6b45; color: #3d6a55; }
            QFrame { border: 1px solid #1c4f33; border-radius: 4px; }
            QFrame#crossCard { border-radius: 8px; }
        """)



    def _update_time_boxes(self) -> None:
        if self.started_at <= 0:
            self.time_elapsed_big.setText("経過 00:00:00")
            self.time_eta_big.setText("残り --:--:--")
            return
        elapsed = time.time() - self.started_at
        self.time_elapsed_big.setText(f"経過 {format_hhmmss(elapsed)}")
        if self._eta_total > 0 and self._eta_done > 0 and self._eta_done <= self._eta_total:
            remain = elapsed / max(1, self._eta_done) * (self._eta_total - self._eta_done)
            self.time_eta_big.setText(f"残り {format_hhmmss(remain)}")
        else:
            self.time_eta_big.setText("残り --:--:--")

    def _tick_animation(self) -> None:
        if self._telemetry_running:
            if hasattr(self, "sweep"):
                self.sweep.tick()
            self._update_time_boxes()
            self._elapsed_frozen_text = self.time_elapsed_big.text()
        else:
            self.time_elapsed_big.setText(self._elapsed_frozen_text)

    def _refresh_telemetry(self) -> None:
        selected_names = [n for n, c in self.cards.items() if c.selected]
        total_cross = len(selected_names)
        self.tele["cross_total"].setText(f"交差点数: {total_cross}")

        current = self.current_name if (self._telemetry_running and self.current_name) else "---"
        self.tele["current"].setText(f"現在: {current}")

        done_f = sum(self.cards[n].data.get("done", 0) for n in selected_names)
        total_f = sum(self.cards[n].flags.get("s2_csv", 0) for n in selected_names)
        if total_f <= 0:
            total_f = max(1, self._global_total_files)

        self._global_done_files = done_f
        pct = (done_f / total_f * 100.0) if total_f else 0.0
        self.lbl_progress.setText(f"進捗ファイル: {done_f:,}/{total_f:,}（{pct:.1f}%）")
        self.progress_bar.setValue(max(0, min(100, int(pct))))
        self._eta_done = done_f
        self._eta_total = total_f

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
            self._update_card_progress(text)
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

    def _clear_cards(self) -> None:
        while self.cross_flow.count():
            item = self.cross_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.cards.clear()

    def _set_run_controls_enabled(self, enabled: bool) -> None:
        self.btn_project.setEnabled(enabled)
        self.btn_run.setEnabled(enabled)
        self.chk_all.setEnabled(enabled)
        self.spin_radius.setEnabled(enabled)
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
        self._clear_cards()
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
            card = CrossCardPerf(name)
            card.paths = {"out31": str(out31), "out32": str(out32), "cross_csv": str(csv_path), "cross_jpg": str(jpg), "s2_dir": str(s2_cross)}
            card.set_flags(has_csv=csv_path.exists(), has_jpg=jpg.exists(), has_s2_dir=s2_cross.exists(), s2_csv=n_csv, has_out31=out31.exists(), has_out32=out32.exists())
            card.set_buttons_enabled(True)
            card.set_progress(0, n_csv)
            card.set_stats(0, 0, 0, 0, 0, 0)
            self.cards[name] = card
            self.cross_flow.addWidget(card)
        self.lbl_summary.setText(f"Crossroads: {len(csvs)} / S2 CSV total: {sum_s2}")
        self._refresh_telemetry()
        self.log_info(f"scanned: {len(csvs)} crossroads")
        self.log_info(f"s2 total csv files: {sum_s2}")

    def _collect_targets(self) -> list[str]:
        return [name for name, card in self.cards.items() if card.selected]

    def _set_status_for_current_card(self, status: str) -> None:
        if self.current_name and self.current_name in self.cards:
            self.cards[self.current_name].set_state(status)
            self._refresh_telemetry()

    def _is_file_locked(self, path: Path) -> bool:
        try:
            with open(path, "a", encoding="utf-8"):
                return False
        except OSError:
            return True

    def start_batch(self) -> None:
        if not self.project_dir:
            QMessageBox.warning(self, "未設定", "①プロジェクトフォルダを選択してください。")
            return
        targets = self._collect_targets()
        if not targets:
            QMessageBox.information(self, "対象なし", "実行対象の交差点が選択されていません。")
            return

        locked_reports: list[str] = []
        for name in targets:
            p = Path(self.cards[name].paths.get("out32", ""))
            if p.exists() and self._is_file_locked(p):
                locked_reports.append(p.name)

        if locked_reports:
            msg = "Excelを閉じて下さい。\n\n開いている可能性があるファイル:\n" + "\n".join(locked_reports[:20])
            if len(locked_reports) > 20:
                msg += f"\n... 他 {len(locked_reports)-20} 件"
            QMessageBox.warning(self, "ファイルが開かれています", msg)
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
        self.target_count = len(targets)
        self._global_total_files = sum(self.cards[n].flags.get("s2_csv", 0) for n in targets)
        self._global_done_files = 0
        self.started_at = time.time()
        self._elapsed_frozen_text = "経過 00:00:00"
        self._telemetry_running = True
        self.tele["status"].setText("状態: RUNNING")
        for card in self.cards.values():
            card.set_locked(True)
            card.set_buttons_enabled(False)
            if card.selected:
                card.set_state("待機")
        self._refresh_telemetry()
        self._start_next_crossroad()

    def _start_next_crossroad(self) -> None:
        if not self.queue:
            self._finish_batch()
            return
        self.current_name = self.queue.pop(0)
        self.current_step = "31"
        self.cross_start_perf[self.current_name] = perf_counter()
        self.log_info(f"交差点開始: {self.current_name}")
        self._set_status_for_current_card("31 実行中")
        self._start_step31(self.current_name)

    def _ensure_file_unlock(self, path: Path, on_ok) -> None:
        if not path.exists():
            on_ok(); return
        try:
            with open(path, "a", encoding="utf-8"):
                pass
            on_ok(); return
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
            self._waiting_lock_timer.stop(); self._waiting_lock_timer.deleteLater(); self._waiting_lock_timer = None
        if self._waiting_lock_dialog:
            self._waiting_lock_dialog.accept(); self._waiting_lock_dialog.deleteLater(); self._waiting_lock_dialog = None
        self._waiting_lock_path = None
        on_ok()

    def _start_step31(self, name: str) -> None:
        card = self.cards.get(name)
        if not card:
            self._start_next_crossroad(); return
        out31 = Path(card.paths["out31"])
        script31 = Path(__file__).resolve().parent / "31_crossroad_trip_performance.py"
        if not script31.exists():
            self.log_error(f"31 script not found: {script31}")
            self._start_next_crossroad(); return

        def _launch():
            args = [str(script31), "--project", str(self.project_dir), "--targets", name, "--progress-step", "1", "--radius-m", str(self.spin_radius.value())]
            selected = self._selected_weekdays_for_cli()
            if selected:
                args.extend(["--weekdays", *selected])
            self._launch_process(args)

        self._ensure_file_unlock(out31, _launch)

    def _start_step32(self, name: str) -> None:
        card = self.cards.get(name)
        if not card:
            self._start_next_crossroad(); return
        out32 = Path(card.paths["out32"])
        script32 = Path(__file__).resolve().parent / "32_crossroad_report.py"
        if not script32.exists():
            self.log_error(f"32 script not found: {script32}")
            self._start_next_crossroad(); return

        def _launch():
            self._launch_process([str(script32), "--project", str(self.project_dir), "--targets", name])

        self._ensure_file_unlock(out32, _launch) if out32.exists() else _launch()

    def _launch_process(self, args: list[str]) -> None:
        if self.proc:
            self.proc.kill(); self.proc = None
        self.proc = QProcess(self)
        self._stdout_buf = ""; self._stderr_buf = ""; self._recent_process_lines = []
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.setProgram(sys.executable); self.proc.setArguments(["-u", *args])
        self.log_info(f"launch: {sys.executable} -u {' '.join(args)}")
        self.proc.readyReadStandardOutput.connect(self._on_proc_stdout)
        self.proc.readyReadStandardError.connect(self._on_proc_stderr)
        self.proc.errorOccurred.connect(self._on_proc_error)
        self.proc.finished.connect(self._on_finished)
        self.proc.start()
        if not self.proc.waitForStarted(3000):
            self.log_error(f"QProcess failed to start: {self.proc.errorString()}")
            self._set_status_for_current_card(f"{self.current_step} failed (start error)")
            self._start_next_crossroad()

    def _on_proc_stdout(self) -> None:
        if self.proc: self._append_stream_chunk(self._decode_qbytearray(self.proc.readAllStandardOutput()), False)

    def _on_proc_stderr(self) -> None:
        if self.proc: self._append_stream_chunk(self._decode_qbytearray(self.proc.readAllStandardError()), True)

    def _on_proc_error(self, err) -> None:
        if self.proc: self.log_error(f"QProcess errorOccurred: {err} / {self.proc.errorString()}")

    def _update_card_outputs(self, name: str) -> None:
        card = self.cards.get(name)
        if not card:
            return
        out31 = Path(card.paths["out31"]); out32 = Path(card.paths["out32"])
        card.set_flags(has_csv=Path(card.paths["cross_csv"]).exists(), has_jpg=Path(card.paths["cross_jpg"]).exists(), has_s2_dir=Path(card.paths["s2_dir"]).exists(), s2_csv=card.flags.get("s2_csv", 0), has_out31=out31.exists(), has_out32=out32.exists())

    def _extract_last_error_line(self) -> str:
        for line in reversed(self._recent_process_lines):
            if "[ERROR]" in line or "Traceback" in line or "PermissionError" in line:
                return line
        for line in reversed(self._recent_process_lines):
            if line.strip():
                return line.strip()
        return ""

    def _on_finished(self, code: int, status) -> None:
        self._flush_process_buffers(); self.lbl_progress.setText("")
        if self.current_name is None:
            self._start_next_crossroad(); return
        if code != 0:
            reason = self._extract_last_error_line()
            msg = f"{self.current_step} failed (code={code})"
            if reason: msg = f"{msg} / {reason}"
            self._set_status_for_current_card(msg); self.log_error(msg); self._start_next_crossroad(); return
        if self.current_step == "31":
            self._update_card_outputs(self.current_name)
            self._set_status_for_current_card("32 実行中")
            self.current_step = "32"
            self._start_step32(self.current_name)
            return

        self._update_card_outputs(self.current_name)
        card = self.cards.get(self.current_name)
        if not card or not Path(card.paths["out32"]).exists():
            msg = f"32 failed: report not created: {self.current_name}"
            self._set_status_for_current_card(msg); self.log_error(msg); self._start_next_crossroad(); return

        self._set_status_for_current_card("完了")
        card.set_buttons_enabled(True)
        dt = perf_counter() - self.cross_start_perf.get(self.current_name, perf_counter())
        self.log_info(f"交差点: {self.current_name} 所要時間: {dt:.1f}s")
        self.log_info(f"交差点完了: {self.current_name}")
        self._start_next_crossroad()

    def _update_card_progress(self, text: str) -> None:
        if not self.current_name or self.current_name not in self.cards:
            return
        card = self.cards[self.current_name]
        m = RE_PROGRESS.search(text)
        if m:
            card.set_progress(int(m.group(1)), int(m.group(2)))
        m2 = RE_STATS.search(text)
        if m2:
            weekday, rows, ok, unk, notpass = map(int, m2.groups())
            target = rows + notpass
            split = rows + notpass - weekday
            if ok + unk != rows:
                self.log_warn(f"rows mismatch: ok({ok}) + unk({unk}) != rows({rows}) for {self.current_name}")
            card.set_stats(weekday, split, target, ok, unk, notpass)
        self._refresh_telemetry()

    def _maybe_update_realtime_from_buffer(self, buf: str) -> None:
        idx = buf.rfind("進捗:")
        if idx < 0:
            return
        tail = buf[idx:].strip()
        if RE_PROGRESS.search(tail) or RE_STATS.search(tail):
            self._update_card_progress(tail)

    def _apply_done_summary(self, text: str) -> None:
        if not self.current_name or self.current_name not in self.cards:
            return
        m = RE_DONE.search(text)
        if not m:
            return
        total, weekday, rows, ok, unk, notpass = map(int, m.groups())
        target = rows + notpass
        split = rows + notpass - weekday
        card = self.cards[self.current_name]
        card.set_progress(total, total)
        card.set_stats(weekday, split, target, ok, unk, notpass)
        self._refresh_telemetry()

    def _card_dump_lines(self) -> list[str]:
        lines = ["name	selected	status	done/total	weekday_after	split	target	ok	unk	notpass	has_out31	has_out32"]
        for n, c in self.cards.items():
            d = c.data
            lines.append(f"{n}	{int(c.selected)}	{c.state}	{d['done']}/{d['total']}	{d['weekday']}	{d['split']}	{d['target']}	{d['ok']}	{d['unk']}	{d['notpass']}	{int(c.flags.get('has_out31', False))}	{int(c.flags.get('has_out32', False))}")
        return lines

    def _write_batch_log_files(self, total_sec: float) -> None:
        if not self.project_dir:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        text_lines = [
            f"Project: {self.project_dir}",
            f"開始時刻: {self.batch_started_at.strftime('%Y/%m/%d %H:%M:%S') if self.batch_started_at else ''}",
            f"終了時刻: {self.batch_ended_at.strftime('%Y/%m/%d %H:%M:%S') if self.batch_ended_at else ''}",
            f"総所要時間: {format_hhmmss(total_sec)}",
            "",
            "[UIカード]",
            *self._card_dump_lines(),
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
        self.log_info("🎉 おめでとうございます。全件処理完了です。")
        self.log_info(f"総所要時間: {format_hhmmss(total_sec)}")
        self._write_batch_log_files(total_sec)
        self.current_name = None; self.current_step = ""
        self._set_run_controls_enabled(True)
        for card in self.cards.values():
            card.set_locked(False)
            card.set_buttons_enabled(True)
        self._telemetry_running = False
        self.tele["status"].setText("状態: DONE")
        self.tele["current"].setText("現在: ---")
        self.time_eta_big.setText("残り 00:00:00")
        self._refresh_telemetry()
        self.progress_bar.setValue(100)


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
