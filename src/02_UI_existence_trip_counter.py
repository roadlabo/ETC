from __future__ import annotations

import csv
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
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
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "02_時間帯存在トリップ集計（第1スクリーニング済みデータから30分別存在トリップ数を集計）"
UI_LOGO_FILENAME = "logo_02_UI_existence_trip_counter.png"

RE_FILE_DONE = re.compile(r"進捗ファイル:\s*([0-9,]+)\s*/\s*([0-9,]+)")
RE_SLOT = re.compile(r"^SLOTCOUNT:(\d+):(\d+)\s*$")


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
        from PyQt6.QtCore import QSize
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def do_layout(self, rect, test_only):
        x = rect.x(); y = rect.y(); line_h = 0
        for item in self.item_list:
            next_x = x + item.sizeHint().width() + self._hspace
            if next_x - self._hspace > rect.right() and line_h > 0:
                x = rect.x(); y += line_h + self._vspace; line_h = 0
                next_x = x + item.sizeHint().width() + self._hspace
            if not test_only:
                item.setGeometry(QRect(x, y, item.sizeHint().width(), item.sizeHint().height()))
            x = next_x
            line_h = max(line_h, item.sizeHint().height())
        return y + line_h - rect.y()


class StepBox(QFrame):
    def __init__(self, title: str, content: QWidget):
        super().__init__()
        self.setObjectName("stepBox")
        lay = QVBoxLayout(self); lay.setContentsMargins(10, 8, 10, 8); lay.setSpacing(6)
        t = QLabel(title); t.setObjectName("stepTitle")
        lay.addWidget(t); lay.addWidget(content)


class SweepWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.angle = 0
        self._running = False
        self.setMinimumHeight(140)

    def tick(self):
        if not self._running:
            return
        self.angle = (self.angle + 7) % 360
        self.update()

    def set_running(self, running: bool):
        self._running = running
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#050b09"))
        c = self.rect().center()
        r = min(self.width(), self.height()) // 2 - 8
        p.setPen(QPen(QColor("#1b4f2f")))
        p.drawEllipse(c, r, r); p.drawEllipse(c, int(r * 0.66), int(r * 0.66)); p.drawEllipse(c, int(r * 0.33), int(r * 0.33))
        beam_color = QColor("#56d27f") if self._running else QColor("#2c6a45")
        p.setPen(QPen(beam_color, 2))
        rad = self.angle * math.pi / 180
        p.drawLine(c.x(), c.y(), int(c.x() + r * math.cos(rad)), int(c.y() - r * math.sin(rad)))


class RealtimeSlotChart(QWidget):
    def __init__(self):
        super().__init__()
        self.slot_counts = [0] * 48
        self._dirty = False
        self._last_paint_t = 0.0
        self.setMinimumHeight(170)

    def set_slot(self, i: int, count: int):
        if 0 <= i < 48 and self.slot_counts[i] != count:
            self.slot_counts[i] = count
            now = time.time()
            if now - self._last_paint_t > 0.06:
                self._last_paint_t = now
                self.update()
            else:
                self._dirty = True

    def clear(self):
        self.slot_counts = [0] * 48
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#09120f"))
        chart = r.adjusted(54, 18, -12, -64)
        if chart.width() <= 0 or chart.height() <= 0:
            return

        mx = max(1, max(self.slot_counts))
        p.setPen(QPen(QColor(25, 90, 70, 120), 1))
        for ratio in (0.25, 0.5, 0.75, 1.0):
            y = chart.bottom() - int(chart.height() * ratio)
            p.drawLine(chart.left(), y, chart.right(), y)

        p.setPen(QPen(QColor("#9ef4ff")))
        for ratio, label in ((0.0, "0"), (0.25, f"{int(mx*0.25):,}"), (0.5, f"{int(mx*0.5):,}"), (0.75, f"{int(mx*0.75):,}"), (1.0, f"{mx:,}")):
            y = chart.bottom() - int(chart.height() * ratio)
            p.drawText(6, y - 8, 42, 16, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        am_counts = self.slot_counts[:24]
        pm_counts = self.slot_counts[24:]
        am_peak_i = max(range(24), key=lambda i: am_counts[i]) if max(am_counts) > 0 else None
        pm_peak_local = max(range(24), key=lambda i: pm_counts[i]) if max(pm_counts) > 0 else None
        pm_peak_i = 24 + pm_peak_local if pm_peak_local is not None else None
        bar_w = max(2, int(chart.width() / 48) - 1)
        for i, v in enumerate(self.slot_counts):
            h = int((v / mx) * (chart.height() - 2))
            x = chart.left() + int(i * chart.width() / 48)
            y = chart.bottom() - h
            if i == am_peak_i:
                col = QColor("#76ff8e")
            elif i == pm_peak_i:
                col = QColor("#d8ff6a")
            else:
                col = QColor("#11b3ff")
            p.fillRect(x, y, bar_w, h, col)

        axis_font = QFont(p.font())
        axis_font.setPointSize(max(8, axis_font.pointSize() - 1))
        p.setFont(axis_font)
        for idx in range(0, 49, 4):
            x = chart.left() + int(idx * chart.width() / 48)
            txt = f"{idx // 2}:00"
            p.drawLine(x, chart.bottom(), x, chart.bottom() + 4)
            text_w = 50 if idx in (0, 48) else 44
            p.drawText(x - text_w // 2, chart.bottom() + 8, text_w, 18, Qt.AlignmentFlag.AlignCenter, txt)

        p.drawText(r.adjusted(6, 1, -8, -4), Qt.AlignmentFlag.AlignLeft, "縦軸：時間帯別レコード数（日平均）")
        p.drawText(r.adjusted(10, r.height() - 24, -10, -4), Qt.AlignmentFlag.AlignCenter, "時間帯（30分スロット）")

        info_rect = r.adjusted(int(r.width() * 0.42), 4, -8, -6)
        am_text = "午前ピーク（日平均レコード数）：--:-- / 0"
        if am_peak_i is not None:
            hh, mm = divmod(am_peak_i * 30, 60)
            am_text = f"午前ピーク（日平均レコード数）：{hh:02d}:{mm:02d}-{hh:02d}:{mm + 29:02d} / {self.slot_counts[am_peak_i]:,}"
        pm_text = "午後ピーク（日平均レコード数）：--:-- / 0"
        if pm_peak_i is not None:
            hh, mm = divmod(pm_peak_i * 30, 60)
            pm_text = f"午後ピーク（日平均レコード数）：{hh:02d}:{mm:02d}-{hh:02d}:{mm + 29:02d} / {self.slot_counts[pm_peak_i]:,}"
        p.setPen(QPen(QColor("#b8ffd6")))
        p.drawText(info_rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, f"{am_text}\n{pm_text}")


@dataclass
class DayCell:
    d: date
    btn: QPushButton


class MainWindow(QMainWindow):
    ETA_INTERVAL_SEC = 10.0

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1700, 980)
        self.showMaximized()

        self.proc: QProcess | None = None
        self.input_folder: Path | None = None
        self.csv_files: list[Path] = []
        self.available_dates: list[date] = []
        self.available_meshes: list[str] = []
        self.selected_dates: set[date] = set()
        self.day_cells: dict[date, QPushButton] = {}
        self.slot_counts = [0] * 48
        self.total_files = 0
        self.done_files = 0
        self.error_count = 0
        self.started_at = 0.0
        self.finished_at = None
        self._frozen_elapsed_sec = 0.0
        self._run_completed = False
        self._run_result = "idle"
        self.run_state_text = "IDLE"
        self.last_output_csv: Path | None = None

        self._eta_done = 0; self._eta_total = 0; self._eta_last_calc_t = 0.0
        self._eta_last_text = "残り --:--:--"; self._eta_countdown_sec = None; self._eta_countdown_last_t = 0.0
        self._eta_last_t = None; self._eta_last_done_obs = None; self._eta_rate_ema = None; self._eta_prev_remain = None
        self._eta_start_t = None; self._eta_start_done = None

        self._build_ui()
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self.anim_timer = QTimer(self); self.anim_timer.timeout.connect(self.sweep.tick)

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw); root.setContentsMargins(12, 12, 12, 10); root.setSpacing(8)

        title = QLabel(APP_TITLE); title.setObjectName("title"); title.setFont(QFont("Meiryo UI", 14, QFont.Weight.Bold)); root.addWidget(title)

        body = QHBoxLayout(); root.addLayout(body, 1)
        left = QVBoxLayout(); left.setSpacing(8)
        body.addLayout(left, 5)

        # STEP 1
        step1_frame = QFrame(); step1_frame.setObjectName("stepBox"); step1_frame.setMaximumHeight(60)
        step1_layout = QHBoxLayout(step1_frame); step1_layout.setContentsMargins(10, 8, 10, 8); step1_layout.setSpacing(8)
        step1_title = QLabel("STEP 1：第1スクリーニングフォルダの選択"); step1_title.setObjectName("stepTitle")
        self.btn_pick = QPushButton("第1スクリーニングフォルダ選択"); self.btn_pick.clicked.connect(self.pick_folder)
        self.lbl_folder = QLabel("未選択")
        self.chk_recursive = QCheckBox("サブフォルダも含める"); self.chk_recursive.stateChanged.connect(self.on_recursive_changed)
        step1_layout.addWidget(step1_title)
        step1_layout.addStretch(1)
        step1_layout.addWidget(self.btn_pick)
        step1_layout.addWidget(self.lbl_folder, 1)
        step1_layout.addWidget(self.chk_recursive)
        left.addWidget(step1_frame)

        # STEP 2
        s3w = QWidget(); s3 = QVBoxLayout(s3w); s3.setContentsMargins(0, 0, 0, 0)
        togg = QHBoxLayout(); self.btn_all = QPushButton("ALL"); self.btn_all.clicked.connect(self.toggle_all_dates); togg.addWidget(self.btn_all)
        self.wday_buttons = []
        for i, wd in enumerate(["月", "火", "水", "木", "金", "土", "日"]):
            b = QPushButton(wd); b.clicked.connect(lambda _=False, x=i: self.toggle_weekday(x)); self.wday_buttons.append(b); togg.addWidget(b)
        togg.addStretch(1)
        self.lbl_date_stats = QLabel("選択中: 0日 / 全0日")
        s3.addLayout(togg); s3.addWidget(self.lbl_date_stats)
        self.calendar_container = QWidget()
        self.calendar_outer_layout = QVBoxLayout(self.calendar_container)
        self.calendar_outer_layout.setContentsMargins(0, 4, 0, 4)
        self.calendar_outer_layout.setSpacing(0)
        self.calendar_months_wrap = QWidget()
        self.calendar_months_layout = QGridLayout(self.calendar_months_wrap)
        self.calendar_months_layout.setContentsMargins(0, 0, 0, 0)
        self.calendar_months_layout.setSpacing(15)
        self.calendar_outer_layout.addWidget(self.calendar_months_wrap)
        self.scr = QScrollArea(); self.scr.setWidgetResizable(True); self.scr.setWidget(self.calendar_container)
        self.scr.setMinimumHeight(340)
        s3.addWidget(self.scr, 1)
        left.addWidget(StepBox("STEP 2：対象日の選択（カレンダー）", s3w), 5)

        # STEP 3
        step3_frame = QFrame(); step3_frame.setObjectName("stepBox"); step3_frame.setMaximumHeight(60)
        step3_layout = QHBoxLayout(step3_frame); step3_layout.setContentsMargins(10, 8, 10, 8); step3_layout.setSpacing(8)
        step3_title = QLabel("STEP 3：実行"); step3_title.setObjectName("stepTitle")
        self.btn_run = QPushButton("集計スタート"); self.btn_run.clicked.connect(self.start_run)
        self.btn_open_csv = QPushButton("出力CSVを開く"); self.btn_open_csv.clicked.connect(self.open_output_csv); self.btn_open_csv.setEnabled(False)
        self.btn_open_folder = QPushButton("保存先フォルダを開く"); self.btn_open_folder.clicked.connect(self.open_output_folder); self.btn_open_folder.setEnabled(False)
        step3_layout.addWidget(step3_title)
        step3_layout.addStretch(1)
        step3_layout.addWidget(self.btn_run)
        step3_layout.addWidget(self.btn_open_csv)
        step3_layout.addWidget(self.btn_open_folder)
        left.addWidget(step3_frame)

        self.chart = RealtimeSlotChart(); left.addWidget(self.chart, 2)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(2000); self.log.setMinimumHeight(60); self.log.setMaximumHeight(70)
        left.addWidget(self.log, 1)

        right = QVBoxLayout(); body.addLayout(right, 2)
        panel = QFrame(); pv = QVBoxLayout(panel)
        self.lbl_status = QLabel("状態: IDLE")
        self.lbl_progress = QLabel("進捗ファイル: 0/0（0.0%）")
        self.lbl_elapsed = QLabel("経過 00:00:00"); self.lbl_elapsed.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        self.lbl_eta = QLabel("残り --:--:--"); self.lbl_eta.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        self.lbl_telemetry = QLabel("CYBER TELEMETRY\n対象CSV数: 0\n抽出日数: 0\n選択中日数: 0\n対象メッシュ数: 0\n対象2次メッシュ:\n-\nエラー数: 0")
        self.lbl_telemetry.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.lbl_telemetry.setWordWrap(True)
        self.sweep = SweepWidget()
        pv.addWidget(self.lbl_status); pv.addWidget(self.lbl_progress); pv.addWidget(self.lbl_elapsed); pv.addWidget(self.lbl_eta)
        pv.addWidget(self.lbl_telemetry); pv.addWidget(self.sweep, 1)
        right.addWidget(panel, 1)

        logo_path = Path(__file__).resolve().parent / "assets" / "logos" / UI_LOGO_FILENAME
        if logo_path.exists():
            self.logo = QLabel(self); self.logo.setPixmap(QPixmap(str(logo_path)).scaledToHeight(76, Qt.TransformationMode.SmoothTransformation)); self.logo.move(30, 26); self.logo.show()

        self.setStyleSheet(
            """
            QWidget{background:#040a08;color:#d8fff0;font-family:Meiryo UI;}
            QFrame#stepBox{border:2px solid #00ff99;border-radius:12px;background: rgba(0,255,153,16);}
            QLabel#stepTitle{color:#00ff99;font-weight:700;}
            QPushButton{background:#083424;border:1px solid #13d989;border-radius:10px;padding:6px 12px;}
            QPushButton:disabled{background:#0d1814;color:#6f887e;border-color:#365247;}
            QPlainTextEdit{background:#0b1412;border:1px solid #1f4a3d;border-radius:8px;}
            QScrollArea{border:1px solid #1f4a3d;}
            QLabel#title{color:#76ff8e;}
            """
        )

    def now_text(self):
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    def append_log(self, text: str):
        self.log.appendPlainText(f"{self.now_text()} {text}")

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "第1スクリーニング済みフォルダを選択")
        if not d:
            return
        self.input_folder = Path(d)
        self.lbl_folder.setText(self.input_folder.name)
        self.refresh_csv_and_dates()

    def on_recursive_changed(self):
        if self.input_folder:
            self.refresh_csv_and_dates()

    def _list_csv(self) -> list[Path]:
        if not self.input_folder:
            return []
        if self.chk_recursive.isChecked():
            return sorted(p for p in self.input_folder.rglob("*.csv") if p.is_file())
        return sorted(p for p in self.input_folder.glob("*.csv") if p.is_file())

    def _count_csv_only(self) -> int:
        if not self.input_folder:
            return 0
        if self.chk_recursive.isChecked():
            return sum(1 for p in self.input_folder.rglob("*.csv") if p.is_file())
        return sum(1 for p in self.input_folder.glob("*.csv") if p.is_file())

    def _scan_dates(self, files: list[Path], progress: QProgressDialog | None = None) -> tuple[list[date], list[str]]:
        out_dates: set[date] = set()
        out_meshes: set[str] = set()
        total = len(files)
        for i, fp in enumerate(files, start=1):
            try:
                with fp.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                    r = csv.reader(f)
                    first = next(r, None)
                    if first is None:
                        continue
                    dt_idx = 6
                    has_header = any(not re.fullmatch(r"[-+]?\d+(\.\d+)?", c.strip()) for c in first)
                    rows = r if has_header else [first]
                    for row in rows:
                        if dt_idx < len(row):
                            tok = row[dt_idx].strip()
                            if len(tok) >= 8 and tok[:8].isdigit():
                                try:
                                    out_dates.add(datetime.strptime(tok[:8], "%Y%m%d").date())
                                except ValueError:
                                    pass
                        if len(row) > 24:
                            mesh_token = row[24].strip()
                            if mesh_token and re.fullmatch(r"\d+", mesh_token):
                                out_meshes.add(mesh_token)
            except Exception:
                continue
            finally:
                if progress:
                    progress.setValue(i)
                    progress.setLabelText(f"CSVを読み込み中... {i:,} / {total:,}")
                    if i % 10 == 0 or i == total:
                        QApplication.processEvents()
        return sorted(out_dates), sorted(out_meshes)

    def _clear_date_selection(self):
        self.available_dates = []
        self.available_meshes = []
        self.selected_dates = set()
        self._rebuild_calendar()

    def _refresh_csv_count_only(self) -> bool:
        self.append_log("CSV件数を確認中...")
        self.total_files = self._count_csv_only()
        if self.total_files == 0:
            QMessageBox.warning(self, "警告", "CSVが0件です。")
            self.input_folder = None
            self.lbl_folder.setText("未選択")
            self._clear_date_selection()
            return False
        self.append_log(f"対象CSV数: {self.total_files:,}")
        return True

    def _load_dates_with_progress(self):
        self.csv_files = self._list_csv()
        self.total_files = len(self.csv_files)
        progress = QProgressDialog(f"CSVを読み込み中... 0 / {self.total_files:,}", None, 0, self.total_files, self)
        progress.setWindowTitle("日付読込み中")
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setValue(0)
        progress.show()
        self.run_state_text = "LOADING"
        self.lbl_status.setText("状態: LOADING")
        self.sweep.set_running(True)
        self.anim_timer.start(60)
        QApplication.processEvents()
        try:
            self.available_dates, self.available_meshes = self._scan_dates(self.csv_files, progress=progress)
        finally:
            self.anim_timer.stop()
            self.sweep.set_running(False)
            self.run_state_text = "IDLE"
            self.lbl_status.setText("状態: IDLE")
            progress.close()

        self.selected_dates = set(self.available_dates)
        self._rebuild_calendar()
        self.append_log(f"抽出日数: {len(self.available_dates):,}")
        self.append_log(f"抽出メッシュ数: {len(self.available_meshes):,}")
        self.append_log("抽出メッシュはCSVの25列目から取得（座標再計算なし）")

    def refresh_csv_and_dates(self, confirm: bool = True):
        if not self._refresh_csv_count_only():
            return
        if confirm:
            ret = QMessageBox.question(
                self,
                "確認",
                "読み込みを開始しますか？\n読込みは長時間を要する場合があります。\n"
                f"対象CSV数: {self.total_files:,}件",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Ok,
            )
            if ret != QMessageBox.StandardButton.Ok:
                self._clear_date_selection()
                self.append_log("日付読込みをキャンセルしました。")
                return
        self._load_dates_with_progress()

    def _rebuild_calendar(self):
        while self.calendar_months_layout.count():
            item = self.calendar_months_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.day_cells.clear()
        if not self.available_dates:
            self.calendar_months_layout.addWidget(QLabel("日付データなし"), 0, 0)
            self.lbl_date_stats.setText("選択中: 0日 / 全0日")
            return

        by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
        for d in self.available_dates:
            by_month[(d.year, d.month)].append(d)

        min_month_w = 280
        max_month_w = 320
        cols = 3
        for i, ym in enumerate(sorted(by_month.keys())):
            y, m = ym
            box = QFrame()
            box.setMinimumWidth(min_month_w)
            box.setMaximumWidth(max_month_w)
            box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
            lv = QVBoxLayout(box)
            lv.setContentsMargins(10, 10, 10, 12)
            lv.setSpacing(8)
            lv.setAlignment(Qt.AlignmentFlag.AlignTop)
            title_lbl = QLabel(f"{y}年{m}月")
            title_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lv.addWidget(title_lbl, 0, Qt.AlignmentFlag.AlignTop)
            grid = QGridLayout(); lv.addLayout(grid, 0)
            grid.setAlignment(Qt.AlignmentFlag.AlignTop)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(6)
            for c, wd in enumerate(["月", "火", "水", "木", "金", "土", "日"]):
                h = QLabel(wd)
                h.setAlignment(Qt.AlignmentFlag.AlignCenter)
                h.setMinimumHeight(28)
                grid.addWidget(h, 0, c)
            first_wd = date(y, m, 1).weekday()
            row = 1
            col = first_wd
            for d in sorted(by_month[ym]):
                b = QPushButton(str(d.day)); b.setCheckable(True); b.setChecked(True)
                b.setMinimumHeight(40)
                b.clicked.connect(lambda _=False, dd=d: self.toggle_day(dd))
                grid.addWidget(b, row, col)
                self.day_cells[d] = b
                col += 1
                if col >= 7:
                    col = 0; row += 1
            row = i // cols
            col = i % cols
            self.calendar_months_layout.addWidget(box, row, col, alignment=Qt.AlignmentFlag.AlignTop)
            self.calendar_months_layout.setRowStretch(row, 0)

        self.calendar_months_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scr.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._update_day_styles()

    def _update_day_styles(self):
        for d, b in self.day_cells.items():
            on = d in self.selected_dates
            b.setChecked(on)
            if on:
                b.setStyleSheet("background:#00aa66;border:1px solid #76ff8e;border-radius:7px;")
            else:
                b.setStyleSheet("background:#1a2320;border:1px solid #42544d;border-radius:7px;")
        self.lbl_date_stats.setText(f"選択中: {len(self.selected_dates)}日 / 全{len(self.available_dates)}日")

    def toggle_day(self, d: date):
        if d in self.selected_dates:
            self.selected_dates.remove(d)
        else:
            self.selected_dates.add(d)
        self._update_day_styles()

    def toggle_all_dates(self):
        if len(self.selected_dates) == len(self.available_dates):
            self.selected_dates.clear()
        else:
            self.selected_dates = set(self.available_dates)
        self._update_day_styles()

    def toggle_weekday(self, monday0: int):
        targets = [d for d in self.available_dates if d.weekday() == monday0]
        if not targets:
            return
        all_on = all(d in self.selected_dates for d in targets)
        if all_on:
            for d in targets:
                self.selected_dates.discard(d)
        else:
            for d in targets:
                self.selected_dates.add(d)
        self._update_day_styles()

    def _compact_dates(self, dates: list[date]) -> str:
        if not dates:
            return ""
        by_ym: dict[tuple[int, int], list[int]] = defaultdict(list)
        for d in sorted(dates):
            by_ym[(d.year, d.month)].append(d.day)
        parts = []
        prev_year = None
        for (y, m), ds in sorted(by_ym.items()):
            dpart = "+".join(str(x) for x in sorted(ds))
            if prev_year != y:
                parts.append(f"{y}_{m}_{dpart}")
            else:
                parts.append(f"{m}_{dpart}")
            prev_year = y
        return "/".join(parts)

    def _output_path(self) -> Path:
        assert self.input_folder is not None
        parent = self.input_folder.parent
        return parent / f"{self.input_folder.name}_時間帯存在トリップ.csv"

    def start_run(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        if not self.input_folder or self.total_files <= 0:
            QMessageBox.warning(self, "警告", "フォルダ未選択またはCSV 0件です。")
            return
        if not self.selected_dates:
            QMessageBox.warning(self, "警告", "対象日を1日以上選択してください。")
            return
        out = self._output_path()
        if out.exists():
            if QMessageBox.question(self, "上書き確認", "既に存在トリップ集計CSVが存在します。上書きしますか？") != QMessageBox.StandardButton.Yes:
                return
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            t = out.parent / ".writetest"; t.write_text("ok", encoding="utf-8"); t.unlink(missing_ok=True)
        except Exception:
            QMessageBox.critical(self, "エラー", "出力先へ書き込みできません。")
            return

        py = sys.executable
        script = Path(__file__).resolve().parent / "02_existence_trip_counter.py"
        date_list = [d.strftime("%Y-%m-%d") for d in sorted(self.selected_dates)]
        compact = self._compact_dates(sorted(self.selected_dates))
        args = [
            str(script), "--input", str(self.input_folder), "--meshes", "+".join(self.available_meshes),
            "--dates", json.dumps(date_list, ensure_ascii=False), "--dates-compact", compact, "--output", str(out)
        ]
        if self.chk_recursive.isChecked():
            args.append("--recursive")

        self.proc = QProcess(self)
        self.proc.setProgram(py)
        self.proc.setArguments(args)
        self.proc.setWorkingDirectory(str(Path(__file__).resolve().parent))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_proc_error)

        self.done_files = 0; self.error_count = 0; self.slot_counts = [0] * 48; self.chart.clear(); self.last_output_csv = out
        self._eta_done = 0; self._eta_total = self.total_files; self._reset_eta_estimator()
        self.finished_at = None
        self._frozen_elapsed_sec = 0.0
        self._run_completed = False
        self._run_result = "running"
        self.run_state_text = "RUNNING"
        self.lbl_status.setText("状態: RUNNING")
        self.lbl_eta.setText("残り --:--:--")
        self.started_at = time.time()
        self.sweep.set_running(True)
        self.anim_timer.start(60)
        self._set_inputs_enabled(False)
        self.append_log("集計開始")
        self.append_log("集計条件: 対象日一致のみ（メッシュ条件なし）")
        self.append_log("集計方法: 全CSV・全行の日時を確認し、対象日なら30分スロットへ累積")
        self.append_log(f"対象日数: {len(self.selected_dates)}日")
        self.append_log(f"サブフォルダ: {'はい' if self.chk_recursive.isChecked() else 'いいえ'}")
        self.proc.start()

    def _set_inputs_enabled(self, enabled: bool):
        self.btn_pick.setEnabled(enabled); self.chk_recursive.setEnabled(enabled)
        self.btn_run.setEnabled(enabled)

    def _on_stdout(self):
        if not self.proc:
            return
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            t = line.strip()
            if not t:
                continue
            m = RE_SLOT.match(t)
            if m:
                i, c = int(m.group(1)), int(m.group(2))
                if 0 <= i < 48:
                    self.slot_counts[i] = c
                    self.chart.set_slot(i, c)
                continue
            fd = RE_FILE_DONE.search(t)
            if fd:
                self.done_files = int(fd.group(1).replace(",", "")); self.total_files = max(1, int(fd.group(2).replace(",", "")))
                self._eta_done = self.done_files; self._eta_total = self.total_files
                self._update_progress_label()
                continue
            if t.startswith("現在ピーク:"):
                continue
            if "[ERROR]" in t:
                self.error_count += 1
                self.append_log(t)
                continue
            if t.startswith("[INFO]"):
                self.append_log(t.replace("[INFO] ", ""))
                continue
            self.append_log(t)

    def _on_stderr(self):
        if not self.proc:
            return
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            if line.strip():
                self.append_log(f"[STDERR] {line.strip()}")

    def _on_finished(self, code: int, _status):
        ok = code == 0
        if ok:
            self.run_state_text = "COMPLETED"
        elif self.run_state_text != "ERROR":
            self.run_state_text = "ERROR"
        self._freeze_runtime_ui(completed=ok, errored=not ok)
        self.lbl_status.setText("状態: COMPLETED" if ok else "状態: ERROR")
        self._set_inputs_enabled(True)
        self.btn_open_csv.setEnabled(ok and self.last_output_csv is not None and self.last_output_csv.exists())
        self.btn_open_folder.setEnabled(ok and self.last_output_csv is not None)
        if ok:
            self.append_log("🎉 おめでとうございます。存在トリップ集計完了です。\n再度対象日を変更して計算できます。（STEP2から）")
        self._write_batch_log_file()
        self.proc = None

    def _on_proc_error(self, _err):
        self.run_state_text = "ERROR"
        self._freeze_runtime_ui(completed=False, errored=True)

    def _freeze_runtime_ui(self, *, completed: bool = False, errored: bool = False):
        if self._run_completed:
            return
        self.finished_at = time.time()
        self._frozen_elapsed_sec = (self.finished_at - self.started_at) if self.started_at else 0.0
        self._run_completed = True
        self._run_result = "completed" if completed else ("error" if errored else "idle")

        self.anim_timer.stop()
        self.sweep.set_running(False)

        self.lbl_elapsed.setText(f"経過 {self._fmt_hms(self._frozen_elapsed_sec)}")
        if completed:
            self.lbl_eta.setText("残り 00:00:00")
            self.run_state_text = "COMPLETED"
            self.lbl_status.setText("状態: COMPLETED")
        elif errored:
            self.lbl_eta.setText("残り --:--:--")
            self.run_state_text = "ERROR"
            self.lbl_status.setText("状態: ERROR")
        else:
            self.lbl_eta.setText("残り --:--:--")
            self.run_state_text = "IDLE"
            self.lbl_status.setText("状態: IDLE")

    def _update_progress_label(self):
        pct = (self.done_files / self.total_files * 100) if self.total_files else 0
        self.lbl_progress.setText(f"進捗ファイル: {self.done_files:,}/{self.total_files:,}（{pct:.1f}%）")

    def _fmt_hms(self, sec: float) -> str:
        sec = int(max(0, sec) + 0.5)
        h = sec // 3600; m = (sec % 3600) // 60; s = sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _reset_eta_estimator(self):
        self._eta_last_t = None; self._eta_last_done_obs = None; self._eta_rate_ema = None
        self._eta_prev_remain = None; self._eta_last_calc_t = 0.0; self._eta_last_text = "残り --:--:--"
        self._eta_countdown_sec = None; self._eta_countdown_last_t = 0.0; self._eta_start_t = None; self._eta_start_done = None

    def _update_eta(self):
        now = time.time()
        if self._eta_countdown_sec is not None:
            if self._eta_countdown_last_t <= 0.0:
                self._eta_countdown_last_t = now
            dt_show = now - self._eta_countdown_last_t
            if dt_show >= 1.0:
                self._eta_countdown_sec = max(0.0, self._eta_countdown_sec - dt_show)
                self._eta_countdown_last_t = now
                self._eta_last_text = f"残り {self._fmt_hms(self._eta_countdown_sec)}"
                self.lbl_eta.setText(self._eta_last_text)
        if now - self._eta_last_calc_t < self.ETA_INTERVAL_SEC:
            return
        self._eta_last_calc_t = now

        done = int(self._eta_done or 0); total = int(self._eta_total or 0)
        if total <= 0 or done <= 0 or done >= total:
            self._eta_last_text = "残り --:--:--" if done < total else "残り 00:00:00"
            self.lbl_eta.setText(self._eta_last_text)
            self._eta_countdown_sec = None
            return
        if self._eta_last_t is None:
            self._eta_last_t = now; self._eta_last_done_obs = done; self._eta_start_t = now; self._eta_start_done = done
            return
        dt = max(1e-6, now - self._eta_last_t); dd = max(0, done - (self._eta_last_done_obs or 0))
        inst = dd / dt
        if inst > 0:
            alpha = 0.22
            self._eta_rate_ema = inst if self._eta_rate_ema is None else ((1 - alpha) * self._eta_rate_ema + alpha * inst)
        self._eta_last_t = now; self._eta_last_done_obs = done
        cum_dt = max(1e-6, now - (self._eta_start_t or now)); cum_dd = max(0, done - int(self._eta_start_done or 0))
        cum_rate = cum_dd / cum_dt if cum_dd > 0 else 0.0
        ema_rate = self._eta_rate_ema or 0.0
        rate = cum_rate * 0.82 + ema_rate * 0.18
        if rate <= 1e-6:
            return
        remain = (total - done) / rate
        elapsed = now - self.started_at if self.started_at else 0
        if self._eta_prev_remain is not None and elapsed > 10 and done >= 5:
            remain = min(remain, self._eta_prev_remain * 1.15)
        self._eta_prev_remain = remain
        self._eta_countdown_sec = float(remain); self._eta_countdown_last_t = now
        self._eta_last_text = f"残り {self._fmt_hms(remain)}"; self.lbl_eta.setText(self._eta_last_text)

    def _tick(self):
        is_running = (not self._run_completed) and self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning
        if is_running and self.started_at:
            elapsed = time.time() - self.started_at
        else:
            elapsed = self._frozen_elapsed_sec if self._run_completed else 0.0
        self.lbl_elapsed.setText(f"経過 {self._fmt_hms(elapsed)}")
        if is_running:
            self._update_eta()
        self._update_progress_label()
        mesh_n = len(self.available_meshes)
        meshes_text = ", ".join(self.available_meshes) if self.available_meshes else "-"
        if is_running:
            state_text = "RUNNING"
            self.run_state_text = "RUNNING"
        else:
            state_text = self.run_state_text
        self.lbl_telemetry.setText(
            f"CYBER TELEMETRY\n"
            f"対象CSV数: {self.total_files:,}\n"
            f"抽出日数: {len(self.available_dates):,}\n"
            f"選択中日数: {len(self.selected_dates):,}\n"
            f"対象メッシュ数: {mesh_n:,}\n"
            f"対象2次メッシュ（参考・集計には未使用）:\n{meshes_text}\n"
            f"進捗ファイル: {self.done_files:,}/{self.total_files:,}\n"
            f"エラー数: {self.error_count:,}\n"
            f"現在状態: {state_text}\n"
            f"経過時間: {self._fmt_hms(elapsed)}\n"
            f"残り時間: {self.lbl_eta.text().replace('残り ','')}"
        )

    def open_output_csv(self):
        if self.last_output_csv and self.last_output_csv.exists():
            if sys.platform.startswith("win"):
                os.startfile(str(self.last_output_csv))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.last_output_csv)])
            else:
                subprocess.Popen(["xdg-open", str(self.last_output_csv)])

    def open_output_folder(self):
        if self.last_output_csv:
            d = self.last_output_csv.parent
            if sys.platform.startswith("win"):
                os.startfile(str(d))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])

    def _write_batch_log_file(self):
        if not self.last_output_csv:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = self.last_output_csv.parent / f"02_batch_log_{stamp}.txt"
        total_sec = self._frozen_elapsed_sec if self._run_completed else (time.time() - self.started_at if self.started_at else 0)
        lines = [
            f"Input: {self.input_folder}",
            f"CSV数: {self.total_files:,}",
            f"抽出日数: {len(self.available_dates):,}",
            f"選択日数: {len(self.selected_dates):,}",
            f"対象2次メッシュ一覧: {','.join(self.available_meshes)}",
            f"Meshes: {','.join(self.available_meshes)}",
            f"Dates: {','.join(d.strftime('%Y-%m-%d') for d in sorted(self.selected_dates))}",
            f"開始: {datetime.fromtimestamp(self.started_at).strftime('%Y/%m/%d %H:%M:%S') if self.started_at else ''}",
            f"終了: {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}",
            f"総所要時間: {self._fmt_hms(total_sec)}",
            "",
            self.log.toPlainText(),
        ]
        out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow(); w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
