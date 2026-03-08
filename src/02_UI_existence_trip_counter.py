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

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QRect, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
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

APP_TITLE = "02_時間帯存在トリップ集計＋ピーク30分OD抽出"
UI_LOGO_FILENAME = "logo_02_UI_existence_trip_counter.png"

RE_FILE_DONE = re.compile(r"進捗ファイル:\s*([0-9,]+)\s*/\s*([0-9,]+)")
RE_SLOT = re.compile(r"^SLOTCOUNT:(\d+):(\d+)\s*$")
RE_ODCOUNT = re.compile(r"^ODCOUNT:(.*?):(.*?):(\d+)\s*$")
RE_DIRCOUNT = re.compile(r"^DIRCOUNT:(EAST|WEST|NORTH|SOUTH):(\d+)\s*$")
RE_SAME = re.compile(r"SAME_ZONE_RATIO:\s*([0-9.]+)")

DEFAULT_CENTER_LON = 134.003809
DEFAULT_CENTER_LAT = 35.064685
DEFAULT_CENTER_NAME = "津山城（既定値）"

try:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except Exception:
    WEBENGINE_AVAILABLE = False


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
        self.setMinimumHeight(130)

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
        chart = r.adjusted(46, 16, -10, -58)
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
        axis_font.setPointSize(max(7, axis_font.pointSize() - 2))
        p.setFont(axis_font)
        axis_labels = [
            (0, "0:00"),
            (4, "2:00"),
            (8, "4:00"),
            (12, "6:00"),
            (16, "8:00"),
            (20, "10:00"),
            (24, "12:00"),
            (28, "14:00"),
            (32, "16:00"),
            (36, "18:00"),
            (40, "20:00"),
            (44, "22:00"),
            (48, "24:00"),
        ]
        for idx, txt in axis_labels:
            x = chart.left() + int(idx * chart.width() / 48)
            p.drawLine(x, chart.bottom(), x, chart.bottom() + 4)
            text_w = 50 if idx in (0, 48) else 44
            p.drawText(x - text_w // 2, chart.bottom() + 8, text_w, 18, Qt.AlignmentFlag.AlignCenter, txt)

        p.drawText(r.adjusted(6, 1, -8, -4), Qt.AlignmentFlag.AlignLeft, "縦軸: 時間帯別レコード数（日平均）")
        p.drawText(r.adjusted(10, r.height() - 24, -10, -4), Qt.AlignmentFlag.AlignCenter, "時間帯（30分スロット）")

        info_rect = r.adjusted(int(r.width() * 0.48), 4, -8, -6)
        am_text = "午前ピーク: --:-- / 0"
        if am_peak_i is not None:
            hh, mm = divmod(am_peak_i * 30, 60)
            am_text = f"午前ピーク: {hh:02d}:{mm:02d}-{hh:02d}:{mm + 29:02d} / {self.slot_counts[am_peak_i]:,}"
        pm_text = "午後ピーク: --:-- / 0"
        if pm_peak_i is not None:
            hh, mm = divmod(pm_peak_i * 30, 60)
            pm_text = f"午後ピーク: {hh:02d}:{mm:02d}-{hh:02d}:{mm + 29:02d} / {self.slot_counts[pm_peak_i]:,}"
        p.setPen(QPen(QColor("#b8ffd6")))
        p.drawText(info_rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, f"{am_text}\n{pm_text}")


class RealtimeODChart(QWidget):
    def __init__(self):
        super().__init__()
        self.od_counts: dict[tuple[str, str], int] = {}
        self.same_ratio = 0.0
        self.setMinimumHeight(180)

    def set_od_count(self, oz: str, dz: str, c: int):
        self.od_counts[(oz, dz)] = c
        self.update()

    def set_same_ratio(self, r: float):
        self.same_ratio = r
        self.update()

    def clear(self):
        self.od_counts = {}
        self.same_ratio = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#09120f"))
        items = sorted(self.od_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]
        chart = r.adjusted(14, 24, -14, -12)
        if not items:
            p.setPen(QColor("#9ef4ff"))
            p.drawText(chart, Qt.AlignmentFlag.AlignCenter, "ODランキング待機中")
            return
        maxv = max(v for _, v in items)
        bar_h = max(14, chart.height() // (len(items) + 1))
        for i, ((oz, dz), cnt) in enumerate(items):
            y = chart.top() + i * bar_h
            w = int((cnt / maxv) * (chart.width() - 180))
            col = QColor("#76ff8e") if i == 0 else QColor("#11b3ff")
            p.fillRect(chart.left() + 170, y + 2, w, bar_h - 4, col)
            p.setPen(QColor("#d8fff0"))
            label = f"{oz}→{dz}"
            if len(label) > 20:
                label = label[:19] + "…"
            p.drawText(chart.left(), y, 165, bar_h, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, label)
            p.drawText(chart.left() + 174 + w, y, 80, bar_h, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(cnt))
        p.setPen(QColor("#9ef4ff"))
        p.drawText(r.adjusted(14, 4, -14, -4), Qt.AlignmentFlag.AlignLeft, "OD上位ペアランキング")
        p.drawText(r.adjusted(14, 4, -14, -4), Qt.AlignmentFlag.AlignRight, f"同一ゾーンOD比率: {self.same_ratio:.1f}%")


class MapBridge(QObject):
    picked = pyqtSignal(float, float)

    @pyqtSlot(float, float)
    def setPoint(self, lon: float, lat: float):
        self.picked.emit(lon, lat)


class MapPickDialog(QDialog):
    def __init__(self, lon: float, lat: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("方向判定中心点を地図で選択")
        self.resize(900, 620)
        self.selected_lon = lon
        self.selected_lat = lat

        v = QVBoxLayout(self)
        self.lbl = QLabel(f"lon={lon:.6f} lat={lat:.6f}")
        v.addWidget(self.lbl)

        if WEBENGINE_AVAILABLE:
            self.web = QWebEngineView(self)
            v.addWidget(self.web, 1)
            self.bridge = MapBridge()
            self.bridge.picked.connect(self._on_picked)
            channel = QWebChannel(self.web.page())
            channel.registerObject("bridge", self.bridge)
            self.web.page().setWebChannel(channel)
            self.web.setHtml(self._html(lon, lat))
        else:
            v.addWidget(QLabel("QWebEngineが無効なため地図表示できません。既定値利用か手入力が必要です。"), 1)

        row = QHBoxLayout()
        ok_btn = QPushButton("この点を採用")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(ok_btn)
        row.addWidget(cancel_btn)
        v.addLayout(row)

    def _on_picked(self, lon: float, lat: float):
        self.selected_lon = lon
        self.selected_lat = lat
        self.lbl.setText(f"lon={lon:.6f} lat={lat:.6f}")

    def _html(self, lon: float, lat: float) -> str:
        return f"""
<!doctype html><html><head>
<meta charset='utf-8'/><meta name='viewport' content='width=device-width,initial-scale=1'/>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<script src='qrc:///qtwebchannel/qwebchannel.js'></script>
<style>html,body,#map{{height:100%;margin:0;background:#000}}</style></head>
<body><div id='map'></div><script>
let map=L.map('map').setView([{lat},{lon}],11);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19}}).addTo(map);
let marker=L.marker([{lat},{lon}]).addTo(map);
let bridge=null; new QWebChannel(qt.webChannelTransport,function(ch){{bridge=ch.objects.bridge;}});
map.on('click', function(e){{marker.setLatLng(e.latlng); if(bridge) bridge.setPoint(e.latlng.lng, e.latlng.lat);}});
</script></body></html>
"""


@dataclass
class DayCell:
    d: date
    btn: QPushButton


class MainWindow(QMainWindow):
    ETA_INTERVAL_SEC = 10.0

    def __init__(self):
        super().__init__()
        self.logo = None
        self.setWindowTitle(APP_TITLE)
        self.resize(1700, 980)

        self.proc_count: QProcess | None = None
        self.proc_od: QProcess | None = None
        self.input_folder: Path | None = None
        self.zoning_file: Path | None = None
        self.csv_files: list[Path] = []
        self.available_dates: list[date] = []
        self.available_meshes: list[str] = []
        self.selected_dates: set[date] = set()
        self.day_cells: dict[date, QPushButton] = {}
        self.slot_counts = [0] * 48
        self.total_files = 0
        self.done_files_count = 0
        self.done_files_od = 0
        self.error_count = 0
        self.od_error_count = 0
        self.zone_count = 0
        self.is_count_running = False
        self.is_od_running = False
        self.count_state_text = "IDLE"
        self.od_state_text = "IDLE"
        self.count_started_at = 0.0
        self.od_started_at = 0.0
        self.last_output_csv: Path | None = None
        self.last_output_matrix: Path | None = None
        self.last_output_detail: Path | None = None
        self.last_output_summary: Path | None = None

        self.center_lon = DEFAULT_CENTER_LON
        self.center_lat = DEFAULT_CENTER_LAT
        self.center_name = DEFAULT_CENTER_NAME

        self.od_counts: dict[tuple[str, str], int] = {}
        self.same_zone_ratio = 0.0
        self.same_zone_count = 0
        self.od_total_trips = 0
        self.dir_counts = {"EAST": 0, "WEST": 0, "NORTH": 0, "SOUTH": 0}

        self.recommended_slot_index = 0
        self.recommended_slot_avg = 0
        self.app_state = "IDLE"

        self._build_ui()
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self.anim_timer = QTimer(self); self.anim_timer.timeout.connect(self.sweep.tick)
        QTimer.singleShot(0, self._post_init_layout)

    def _post_init_layout(self):
        self.showMaximized()
        self._adjust_layout_for_window()
        self.updateGeometry()
        self.repaint()

    def _slot_labels(self) -> list[str]:
        labels = []
        for i in range(48):
            s = i * 30
            e = s + 29
            sh, sm = divmod(s, 60)
            eh, em = divmod(e, 60)
            labels.append(f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}")
        return labels

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw); root.setContentsMargins(12, 12, 12, 10); root.setSpacing(8)

        title = QLabel(APP_TITLE); title.setObjectName("title"); title.setFont(QFont("Meiryo UI", 14, QFont.Weight.Bold)); root.addWidget(title)

        body = QHBoxLayout(); root.addLayout(body, 1)
        left = QVBoxLayout(); left.setSpacing(8); body.addLayout(left, 5)

        step1_frame = QFrame(); step1_frame.setObjectName("stepBox"); step1_frame.setMaximumHeight(60)
        step1_layout = QHBoxLayout(step1_frame); step1_layout.setContentsMargins(10, 8, 10, 8); step1_layout.setSpacing(8)
        step1_title = QLabel("STEP 1：第1スクリーニングフォルダの選択"); step1_title.setObjectName("stepTitle")
        self.btn_pick = QPushButton("第1スクリーニングフォルダ選択"); self.btn_pick.clicked.connect(self.pick_folder)
        self.lbl_folder = QLabel("未選択")
        self.chk_recursive = QCheckBox("サブフォルダも含める"); self.chk_recursive.stateChanged.connect(self.on_recursive_changed)
        step1_layout.addWidget(step1_title); step1_layout.addStretch(1); step1_layout.addWidget(self.btn_pick); step1_layout.addWidget(self.lbl_folder, 1); step1_layout.addWidget(self.chk_recursive)
        left.addWidget(step1_frame)

        s2w = QWidget(); s2 = QVBoxLayout(s2w); s2.setContentsMargins(0, 0, 0, 0)
        togg = QHBoxLayout(); self.btn_all = QPushButton("ALL"); self.btn_all.clicked.connect(self.toggle_all_dates); togg.addWidget(self.btn_all)
        self.wday_buttons = []
        for i, wd in enumerate(["月", "火", "水", "木", "金", "土", "日"]):
            b = QPushButton(wd); b.clicked.connect(lambda _=False, x=i: self.toggle_weekday(x)); self.wday_buttons.append(b); togg.addWidget(b)
        togg.addStretch(1)
        self.lbl_date_stats = QLabel("選択中: 0日 / 全0日")
        s2.addLayout(togg); s2.addWidget(self.lbl_date_stats)
        self.calendar_container = QWidget(); self.calendar_outer_layout = QVBoxLayout(self.calendar_container)
        self.calendar_outer_layout.setContentsMargins(0, 4, 0, 4); self.calendar_outer_layout.setSpacing(0)
        self.calendar_months_wrap = QWidget(); self.calendar_months_layout = QGridLayout(self.calendar_months_wrap)
        self.calendar_months_layout.setContentsMargins(0, 0, 0, 0); self.calendar_months_layout.setSpacing(15)
        self.calendar_months_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.calendar_outer_layout.addWidget(self.calendar_months_wrap)
        self.scr = QScrollArea(); self.scr.setWidgetResizable(True); self.scr.setWidget(self.calendar_container); self.scr.setMinimumHeight(300)
        s2.addWidget(self.scr, 1)
        left.addWidget(StepBox("STEP 2：対象日の選択（カレンダー）", s2w), 4)

        step3_frame = QFrame(); step3_frame.setObjectName("stepBox"); step3_frame.setMaximumHeight(64)
        step3_layout = QHBoxLayout(step3_frame); step3_layout.setContentsMargins(10, 8, 10, 8); step3_layout.setSpacing(8)
        step3_title = QLabel("STEP 3：時間帯存在トリップ集計"); step3_title.setObjectName("stepTitle")
        self.btn_run = QPushButton("集計スタート"); self.btn_run.clicked.connect(self.start_run)
        self.btn_open_csv = QPushButton("出力CSVを開く"); self.btn_open_csv.clicked.connect(self.open_output_csv); self.btn_open_csv.setEnabled(False)
        self.btn_open_folder = QPushButton("保存先フォルダを開く（集計）"); self.btn_open_folder.clicked.connect(self.open_output_folder); self.btn_open_folder.setEnabled(False)
        step3_layout.addWidget(step3_title); step3_layout.addStretch(1); step3_layout.addWidget(self.btn_run); step3_layout.addWidget(self.btn_open_csv); step3_layout.addWidget(self.btn_open_folder)
        left.addWidget(step3_frame)

        s5w = QWidget(); s5 = QVBoxLayout(s5w); s5.setContentsMargins(0, 0, 0, 0); s5.setSpacing(6)

        row1 = QHBoxLayout(); row1.setSpacing(6)
        self.cmb_slot = QComboBox(); self.cmb_slot.addItems(self._slot_labels()); self.cmb_slot.setCurrentIndex(0)
        self.lbl_recommended = QLabel("推奨30分帯: 00:00-00:29（日平均 0）")
        lbl_slot = QLabel("30分帯")
        lbl_slot.setWordWrap(False)
        self.lbl_recommended.setWordWrap(False)
        row1.addWidget(lbl_slot)
        row1.addWidget(self.cmb_slot, 1)
        row1.addWidget(self.lbl_recommended, 2)
        s5.addLayout(row1)

        row2 = QHBoxLayout(); row2.setSpacing(6)
        self.btn_zone = QPushButton("ゾーニングCSV選択"); self.btn_zone.clicked.connect(self.pick_zoning)
        self.lbl_zone = QLabel("未選択"); self.lbl_zone.setMinimumWidth(220); self.lbl_zone.setWordWrap(False)
        self.lbl_center_name = QLabel(self.center_name); self.lbl_center_name.setMinimumWidth(190); self.lbl_center_name.setWordWrap(False)
        self.btn_pick_center = QPushButton("地図で選択"); self.btn_pick_center.clicked.connect(self.pick_center_map)
        self.btn_center_default = QPushButton("既定値に戻す"); self.btn_center_default.clicked.connect(self.reset_center)
        row2.addWidget(self.btn_zone)
        row2.addWidget(self.lbl_zone, 1)
        row2.addWidget(self.lbl_center_name)
        row2.addWidget(self.btn_pick_center)
        row2.addWidget(self.btn_center_default)
        s5.addLayout(row2)

        bline = QHBoxLayout(); bline.setSpacing(6)
        self.btn_run_od = QPushButton("OD抽出スタート"); self.btn_run_od.clicked.connect(self.start_od_run)
        self.btn_open_matrix = QPushButton("出力CSVを開く"); self.btn_open_matrix.clicked.connect(lambda: self._open(self.last_output_matrix)); self.btn_open_matrix.setEnabled(False)
        self.btn_open_detail = QPushButton("明細CSVを開く"); self.btn_open_detail.clicked.connect(lambda: self._open(self.last_output_detail)); self.btn_open_detail.setEnabled(False)
        self.btn_open_od_folder = QPushButton("保存先フォルダを開く（OD）"); self.btn_open_od_folder.clicked.connect(self.open_od_folder); self.btn_open_od_folder.setEnabled(False)
        bline.addWidget(self.btn_run_od); bline.addWidget(self.btn_open_matrix); bline.addWidget(self.btn_open_detail); bline.addWidget(self.btn_open_od_folder); bline.addStretch(1)
        s5.addLayout(bline)

        self.lbl_od_logic = QLabel(
            "OD抽出ロジック：STEP2で選択した対象日のトリップのうち、指定した30分帯に存在するトリップを抽出します。\n"
            "その30分帯内で最初に観測された位置をO、最後に観測された位置をDとして任意ゾーンへ割り当て、ゾーン単位OD表を作成します。\n"
            "表示単位はトリップ/ピーク1時間（30分値を1時間換算）です。SUMO等へ投入する際は実測交通量に基づく拡大推計を行ってください。"
        )
        self.lbl_od_logic.setWordWrap(True)
        self.lbl_od_logic.setStyleSheet("color:#b8ffd6;")
        s5.addWidget(self.lbl_od_logic)
        step4_box = StepBox("STEP 4：ピーク30分OD抽出", s5w)
        step4_box.setMinimumHeight(155)
        left.addWidget(step4_box, 1)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(3000); self.log.setMinimumHeight(70)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._last_log_line = ""
        left.addWidget(self.log, 1)

        left.setStretch(0, 0)
        left.setStretch(1, 6)
        left.setStretch(2, 0)
        left.setStretch(3, 0)
        left.setStretch(4, 2)

        right = QVBoxLayout(); body.addLayout(right, 2)
        panel = QFrame(); pv = QVBoxLayout(panel)
        self.lbl_status = QLabel("集計状態: IDLE / OD状態: IDLE")
        self.lbl_progress = QLabel("集計進捗: 0/0（0.0%）\nOD進捗: 0/0（0.0%）")
        self.lbl_elapsed = QLabel("経過 00:00:00"); self.lbl_elapsed.setFont(QFont("Consolas", 16, QFont.Weight.Bold))
        self.lbl_eta = QLabel("残り --:--:--"); self.lbl_eta.setFont(QFont("Consolas", 16, QFont.Weight.Bold))
        self.lbl_telemetry = QLabel("CYBER TELEMETRY"); self.lbl_telemetry.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.lbl_telemetry.setWordWrap(True)
        self.chart = RealtimeSlotChart()
        self.sweep = SweepWidget()
        pv.addWidget(self.lbl_status); pv.addWidget(self.lbl_progress); pv.addWidget(self.lbl_elapsed); pv.addWidget(self.lbl_eta); pv.addWidget(self.lbl_telemetry); pv.addWidget(self.chart, 1); pv.addWidget(self.sweep, 1)
        right.addWidget(panel, 1)
        self._update_center_labels()

        logo_path = Path(__file__).resolve().parent / "assets" / "logos" / UI_LOGO_FILENAME
        if logo_path.exists():
            self.logo = QLabel(self)
            self.logo.setPixmap(
                QPixmap(str(logo_path)).scaled(
                    240,
                    120,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.logo.move(30, 26)
            self.logo.show()
        self._apply_styles()
        self._rebuild_calendar()

    def _apply_styles(self):
        self.setStyleSheet("""
        QWidget { background:#07110d; color:#c9ffe8; font-family:'Meiryo UI'; }
        QLabel#title { color:#a9ffd2; }
        QFrame#stepBox { border:1px solid #21543a; border-radius:8px; background:#091713; }
        QLabel#stepTitle { color:#7fffc0; font-weight:700; }
        QPushButton { background:#143326; border:1px solid #2b8f66; border-radius:6px; padding:4px 10px; }
        QPushButton:disabled { background:#1b2a24; color:#6c897d; border-color:#395648; }
        QPlainTextEdit, QScrollArea { border:1px solid #21543a; background:#060f0c; }
        """)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.logo is not None:
            self.logo.move(self.width() - self.logo.width() - 18, self.height() - self.logo.height() - 18)
        self._adjust_layout_for_window()

    def _adjust_layout_for_window(self):
        h = max(700, self.height())
        self.scr.setMinimumHeight(max(220, int(h * 0.24)))
        self.chart.setMinimumHeight(max(110, int(h * 0.13)))
        self.sweep.setMinimumHeight(max(115, int(h * 0.14)))
        self.log.setMinimumHeight(max(80, int(h * 0.11)))

    def now_text(self):
        return datetime.now().strftime("%H:%M:%S")

    def _normalize_log_line(self, text: str) -> str:
        if text is None:
            return ""
        s = str(text)
        s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
        return re.sub(r"\s+", " ", s).strip()

    def _should_show_in_log(self, line: str) -> bool:
        if not line:
            return False
        if line.startswith("進捗ファイル:"):
            return False
        if "[PROGRESS]" in line:
            return False
        return True

    def append_log(self, text: str):
        line = self._normalize_log_line(text)
        if not line or not self._should_show_in_log(line):
            return
        stamped = f"[{self.now_text()}] {line}"
        if stamped == self._last_log_line:
            return
        self._last_log_line = stamped
        self.log.appendPlainText(stamped)

    def _refresh_progress_text(self):
        count_pct = (self.done_files_count / self.total_files * 100) if self.total_files else 0
        od_pct = (self.done_files_od / self.total_files * 100) if self.total_files else 0
        self.lbl_progress.setText(
            f"集計進捗: {self.done_files_count:,}/{self.total_files:,}（{count_pct:.1f}%）\n"
            f"OD進捗: {self.done_files_od:,}/{self.total_files:,}（{od_pct:.1f}%）"
        )

    def _refresh_status_text(self):
        self.lbl_status.setText(f"集計状態: {self.count_state_text}\nOD状態: {self.od_state_text}")

    def _refresh_telemetry_text(self):
        meshes_text = ", ".join(self.available_meshes) if self.available_meshes else "-"
        self.lbl_telemetry.setText(
            f"CYBER TELEMETRY\n"
            f"対象CSV数: {self.total_files:,}\n"
            f"抽出日数: {len(self.available_dates):,}\n"
            f"選択中日数: {len(self.selected_dates):,}\n"
            f"対象メッシュ数: {len(self.available_meshes):,}\n"
            f"対象2次メッシュ（参考・集計には未使用）:\n{meshes_text}\n"
            f"集計進捗ファイル: {self.done_files_count:,}/{self.total_files:,}\n"
            f"集計エラー数: {self.error_count:,}\n"
            f"選択30分帯: {self.cmb_slot.currentText()}\n"
            f"ゾーニングCSV名: {(self.zoning_file.name if self.zoning_file else '-')}\n"
            f"ゾーン数: {self.zone_count:,}\n"
            f"中心点名: {self.center_name}\n"
            f"center lon: {self.center_lon:.6f}\n"
            f"center lat: {self.center_lat:.6f}\n"
            f"OD総抽出トリップ数: {self.od_total_trips:,}\n"
            f"同一ゾーンOD比率: {self.same_zone_ratio:.1f}%\n"
            f"東方面件数: {self.dir_counts['EAST']:,}\n"
            f"西方面件数: {self.dir_counts['WEST']:,}\n"
            f"北方面件数: {self.dir_counts['NORTH']:,}\n"
            f"南方面件数: {self.dir_counts['SOUTH']:,}\n"
            f"ODエラー数: {self.od_error_count:,}"
        )

    def _apply_progress_update(self, done: int, total: int, *, is_od: bool):
        if total > 0:
            self.total_files = total
        if is_od:
            self.done_files_od = done
        else:
            self.done_files_count = done
        self._refresh_progress_text()
        self._refresh_telemetry_text()

    def _list_csv(self) -> list[Path]:
        if not self.input_folder:
            return []
        gen = self.input_folder.rglob("*.csv") if self.chk_recursive.isChecked() else self.input_folder.glob("*.csv")
        return sorted(p for p in gen if p.is_file())

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "第1スクリーニングフォルダ選択")
        if not d:
            return
        self.input_folder = Path(d)
        self.lbl_folder.setText(str(self.input_folder))
        self.refresh_csv_and_dates(confirm=True)

    def on_recursive_changed(self):
        if self.input_folder:
            self.refresh_csv_and_dates(confirm=True)

    def _scan_dates(self, files: list[Path], progress: QProgressDialog | None = None) -> tuple[list[date], list[str]]:
        out_dates: set[date] = set()
        out_meshes: set[str] = set()
        total = len(files)

        for i, fp in enumerate(files, start=1):
            try:
                with fp.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                    r = csv.reader(f)
                    for row in r:
                        if len(row) > 6:
                            tok = row[6].strip()
                            if len(tok) >= 8 and tok[:8].isdigit():
                                try:
                                    out_dates.add(datetime.strptime(tok[:8], "%Y%m%d").date())
                                except ValueError:
                                    pass
                        if len(row) > 24:
                            mesh = row[24].strip()
                            if mesh and re.fullmatch(r"\d+", mesh):
                                out_meshes.add(mesh)
            except Exception:
                continue
            finally:
                if progress:
                    progress.setValue(i)
                    progress.setLabelText(f"CSVを読み込み中... {i:,} / {total:,}")
                    if i % 10 == 0 or i == total:
                        QApplication.processEvents()

        return sorted(out_dates), sorted(out_meshes)

    def refresh_csv_and_dates(self, confirm: bool = True):
        if self.input_folder is None:
            return
        self.append_log("[LOAD] CSV件数確認開始")
        self._set_app_state("LOADING_COUNT")
        self._refresh_status_text()
        count_progress = QProgressDialog("対象CSV件数を確認しています…", None, 0, 0, self)
        count_progress.setWindowTitle("CSV件数確認中")
        count_progress.setMinimumDuration(0)
        count_progress.setCancelButton(None)
        count_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        count_progress.show()
        QApplication.processEvents()

        self.csv_files = self._list_csv(); self.total_files = len(self.csv_files)
        count_progress.close()
        self._set_app_state("IDLE")
        self._refresh_progress_text()
        self._refresh_telemetry_text()
        self.append_log(f"[LOAD] 対象CSV数: {self.total_files}")

        if self.total_files == 0:
            self.available_dates = []; self.available_meshes = []; self.selected_dates = set(); self._rebuild_calendar(); self._set_app_state("IDLE"); return
        if confirm:
            msg = QMessageBox(self)
            msg.setWindowTitle("確認")
            msg.setIcon(QMessageBox.Icon.Question)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            msg.setText(
                f"対象CSV数: {self.total_files:,}件\n\n"
                "これから対象日付を読み取るため、全CSVを走査します。\n"
                "大量ファイルのため時間を要する場合があります。\n"
                "開始しますか？"
            )
            if msg.exec() != QMessageBox.StandardButton.Ok:
                self._set_app_state("IDLE")
                self.append_log("[LOAD] 日付走査をキャンセルしました")
                return
        self._load_dates_with_progress()

    def _load_dates_with_progress(self):
        self.append_log("[LOAD] 日付走査開始")
        self._set_app_state("LOADING_DATES")
        self._refresh_status_text()
        pr = QProgressDialog("CSVを読み込み中... 0 / 0", "", 0, self.total_files, self)
        pr.setWindowTitle("日付読込み中")
        pr.setCancelButton(None)
        pr.setWindowModality(Qt.WindowModality.WindowModal)
        pr.show()
        QApplication.processEvents()
        ds, meshes = self._scan_dates(self.csv_files, pr)
        pr.close()
        self.available_dates = ds
        self.available_meshes = meshes
        self.append_log(f"抽出日数: {len(self.available_dates):,}")
        if self.available_dates:
            self.append_log(f"最小日付: {self.available_dates[0]}")
            self.append_log(f"最大日付: {self.available_dates[-1]}")
        else:
            self.append_log("日付抽出結果: 0件")
        self.selected_dates = set(self.available_dates)
        self._rebuild_calendar()
        self.scr.ensureVisible(0, 0)
        self.calendar_container.adjustSize()
        self._set_app_state("IDLE")
        self._refresh_telemetry_text()

    def _rebuild_calendar(self):
        while self.calendar_months_layout.count():
            it = self.calendar_months_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.day_cells.clear()
        self.calendar_months_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        if not self.available_dates:
            self.calendar_months_layout.addWidget(QLabel("日付データなし"), 0, 0); self.lbl_date_stats.setText("選択中: 0日 / 全0日"); return
        by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
        for d in self.available_dates: by_month[(d.year, d.month)].append(d)
        cols = 3
        for i, ym in enumerate(sorted(by_month.keys())):
            y, m = ym
            box = QFrame(); box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed); lv = QVBoxLayout(box); lv.setContentsMargins(6, 6, 6, 6); lv.setSpacing(4)
            lv.setAlignment(Qt.AlignmentFlag.AlignTop)
            lv.addWidget(QLabel(f"{y}年{m}月"))
            grid = QGridLayout(); grid.setContentsMargins(0, 0, 0, 0); grid.setHorizontalSpacing(3); grid.setVerticalSpacing(3); grid.setAlignment(Qt.AlignmentFlag.AlignTop); lv.addLayout(grid)
            for c in range(7):
                grid.setColumnStretch(c, 1)
            for c, wd in enumerate(["月", "火", "水", "木", "金", "土", "日"]):
                h = QLabel(wd); h.setAlignment(Qt.AlignmentFlag.AlignCenter); h.setMinimumWidth(28); grid.addWidget(h, 0, c)
            row = 1; col = date(y, m, 1).weekday()
            for d in sorted(by_month[ym]):
                b = QPushButton(str(d.day)); b.setCheckable(True); b.setChecked(True); b.clicked.connect(lambda _=False, dd=d: self.toggle_day(dd))
                b.setMinimumHeight(24); b.setMinimumWidth(28)
                grid.addWidget(b, row, col); self.day_cells[d] = b; col += 1
                if col >= 7: col = 0; row += 1
            self.calendar_months_layout.addWidget(box, i // cols, i % cols)
        self.calendar_months_wrap.adjustSize()
        self.calendar_container.adjustSize()
        self._update_day_styles()

    def _update_day_styles(self):
        for d, b in self.day_cells.items():
            on = d in self.selected_dates; b.setChecked(on)
            b.setStyleSheet(
                "background:#00aa66;color:#ffffff;border:1px solid #76ff8e;border-radius:7px;"
                if on
                else "background:#16211d;color:#b8ffd6;border:1px solid #42544d;border-radius:7px;"
            )
        self.lbl_date_stats.setText(f"選択中: {len(self.selected_dates)}日 / 全{len(self.available_dates)}日")

    def toggle_day(self, d: date):
        if d in self.selected_dates: self.selected_dates.remove(d)
        else: self.selected_dates.add(d)
        self._update_day_styles()

    def toggle_all_dates(self):
        self.selected_dates = set() if len(self.selected_dates) == len(self.available_dates) else set(self.available_dates)
        self._update_day_styles()

    def toggle_weekday(self, monday0: int):
        targets = [d for d in self.available_dates if d.weekday() == monday0]
        if not targets: return
        all_on = all(d in self.selected_dates for d in targets)
        for d in targets:
            (self.selected_dates.discard if all_on else self.selected_dates.add)(d)
        self._update_day_styles()

    def _compact_dates(self, dates: list[date]) -> str:
        if not dates: return ""
        by_ym: dict[tuple[int, int], list[int]] = defaultdict(list)
        for d in sorted(dates): by_ym[(d.year, d.month)].append(d.day)
        parts = []; prev_year = None
        for (y, m), ds in sorted(by_ym.items()):
            dpart = "+".join(str(x) for x in sorted(ds)); parts.append(f"{y}_{m}_{dpart}" if prev_year != y else f"{m}_{dpart}"); prev_year = y
        return "/".join(parts)

    def _output_path(self) -> Path:
        assert self.input_folder is not None
        return self.input_folder.parent / f"{self.input_folder.name}_時間帯存在トリップ.csv"

    def _od_output_paths(self) -> tuple[Path, Path, Path]:
        assert self.input_folder is not None
        base = self.input_folder.name; parent = self.input_folder.parent
        return (
            parent / f"{base}_43_peak30min_od_matrix.csv",
            parent / f"{base}_43_peak30min_od_detail.csv",
            parent / f"{base}_43_peak30min_od_summary.csv",
        )

    def _set_radar_active(self, active: bool):
        self.sweep.set_running(active)
        if active:
            if not self.anim_timer.isActive():
                self.anim_timer.start(60)
        else:
            self.anim_timer.stop()

    def _set_app_state(self, state: str):
        self.app_state = state
        self._set_radar_active(state in {"LOADING", "LOADING_COUNT", "LOADING_DATES", "RUNNING", "OD_RUNNING"})

    def _set_inputs_enabled(self, enabled: bool):
        widgets = [self.btn_pick, self.chk_recursive, self.btn_run, self.btn_run_od, self.btn_zone, self.cmb_slot, self.btn_pick_center, self.btn_center_default, self.btn_all]
        for w in widgets: w.setEnabled(enabled)
        for b in self.wday_buttons: b.setEnabled(enabled)
        for b in self.day_cells.values(): b.setEnabled(enabled)

    def start_run(self):
        if self.is_count_running or self.is_od_running: return
        if not self.input_folder or self.total_files <= 0:
            QMessageBox.warning(self, "警告", "フォルダ未選択またはCSV 0件です。")
            return
        if not self.selected_dates:
            QMessageBox.warning(self, "警告", "対象日を1日以上選択してください。")
            return
        out = self._output_path()
        py = sys.executable; script = Path(__file__).resolve().parent / "02_existence_trip_counter.py"
        date_list = [d.strftime("%Y-%m-%d") for d in sorted(self.selected_dates)]
        args = [str(script), "--input", str(self.input_folder), "--meshes", "+".join(self.available_meshes), "--dates", json.dumps(date_list, ensure_ascii=False), "--dates-compact", self._compact_dates(sorted(self.selected_dates)), "--output", str(out)]
        if self.chk_recursive.isChecked(): args.append("--recursive")

        self.proc_count = QProcess(self)
        self.proc_count.setProgram(py); self.proc_count.setArguments(args); self.proc_count.setWorkingDirectory(str(Path(__file__).resolve().parent))
        env = QProcessEnvironment.systemEnvironment(); env.insert("PYTHONIOENCODING", "utf-8"); self.proc_count.setProcessEnvironment(env)
        self.proc_count.readyReadStandardOutput.connect(self._on_count_stdout); self.proc_count.readyReadStandardError.connect(self._on_count_stderr)
        self.proc_count.finished.connect(self._on_count_finished)

        self.last_output_csv = out; self.done_files_count = 0; self.error_count = 0; self.slot_counts = [0] * 48; self.chart.clear()
        self.count_started_at = time.time(); self.is_count_running = True; self.count_state_text = "RUNNING"; self._set_app_state("RUNNING"); self._set_inputs_enabled(False)
        self._refresh_progress_text(); self._refresh_status_text(); self._refresh_telemetry_text()
        self.append_log("[COUNT] 集計開始")
        self.proc_count.start()

    def _select_recommended_slot(self):
        if not self.slot_counts:
            self.recommended_slot_index = 0; self.recommended_slot_avg = 0
        else:
            mx = max(self.slot_counts)
            self.recommended_slot_index = self.slot_counts.index(mx) if mx > 0 else 0
            self.recommended_slot_avg = mx
        self.cmb_slot.setCurrentIndex(self.recommended_slot_index)
        self.lbl_recommended.setText(f"推奨30分帯: {self.cmb_slot.currentText()}（日平均 {self.recommended_slot_avg:,}）")

    def _on_count_stdout(self):
        if not self.proc_count: return
        text = bytes(self.proc_count.readAllStandardOutput()).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            t = line.strip()
            if not t: continue
            m = RE_SLOT.match(t)
            if m:
                i, c = int(m.group(1)), int(m.group(2));
                if 0 <= i < 48: self.slot_counts[i] = c; self.chart.set_slot(i, c)
                continue
            fd = RE_FILE_DONE.search(t)
            if fd:
                self._apply_progress_update(int(fd.group(1).replace(",", "")), max(1, int(fd.group(2).replace(",", ""))), is_od=False)
                continue
            if "[ERROR]" in t: self.error_count += 1
            if t.startswith("現在ピーク:"): continue
            self._refresh_telemetry_text()
            self.append_log(f"[COUNT] {t.replace('[INFO] ', '')}")

    def _on_count_stderr(self):
        if not self.proc_count: return
        text = bytes(self.proc_count.readAllStandardError()).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            if line.strip(): self.append_log(f"[COUNT][STDERR] {line}")

    def _on_count_finished(self, code: int, _status):
        ok = code == 0
        self.is_count_running = False; self.count_state_text = "COMPLETED" if ok else "ERROR"; self._set_app_state("COMPLETED" if ok else "ERROR"); self._set_inputs_enabled(True)
        self._refresh_status_text(); self._refresh_telemetry_text()
        self.btn_open_csv.setEnabled(ok and self.last_output_csv and self.last_output_csv.exists()); self.btn_open_folder.setEnabled(ok and self.last_output_csv is not None)
        if ok:
            self._select_recommended_slot()
            self.append_log("[COUNT] 🎉 おめでとうございます。存在トリップ集計完了です。")
        self.proc_count = None

    def pick_zoning(self):
        p, _ = QFileDialog.getOpenFileName(self, "ゾーニングCSV選択", str(self.input_folder.parent if self.input_folder else Path.home()), "CSV (*.csv)")
        if not p: return
        self.zoning_file = Path(p); self.lbl_zone.setText(self.zoning_file.name)
        self.zone_count = self._count_zones(self.zoning_file)
        self._refresh_telemetry_text()

    def _count_zones(self, path: Path) -> int:
        for enc in ("utf-8-sig", "utf-8", "cp932"):
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    rows = list(csv.reader(f))
                return max(0, len(rows) - 1)
            except Exception:
                continue
        return 0

    def pick_center_map(self):
        dlg = MapPickDialog(self.center_lon, self.center_lat, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.center_lon = dlg.selected_lon; self.center_lat = dlg.selected_lat
            self.center_name = "地図選択中心点"
            self._update_center_labels()

    def reset_center(self):
        self.center_lon = DEFAULT_CENTER_LON; self.center_lat = DEFAULT_CENTER_LAT; self.center_name = DEFAULT_CENTER_NAME
        self._update_center_labels()

    def _update_center_labels(self):
        self.lbl_center_name.setText(f"{self.center_name} ({self.center_lon:.6f}, {self.center_lat:.6f})")
        self._refresh_telemetry_text()

    def _validate_od(self) -> bool:
        if self.is_count_running:
            QMessageBox.warning(self, "警告", "集計実行中はOD抽出できません。")
            return False
        if not self.input_folder or self.total_files <= 0:
            QMessageBox.warning(self, "警告", "第1スクリーニングフォルダ未選択またはCSV 0件です。")
            return False
        if not self.selected_dates:
            QMessageBox.warning(self, "警告", "対象日を1日以上選択してください。")
            return False
        if not self.zoning_file or not self.zoning_file.exists():
            QMessageBox.warning(self, "警告", "ゾーニングCSVを選択してください。")
            return False
        if not (-180 <= self.center_lon <= 180 and -90 <= self.center_lat <= 90):
            QMessageBox.warning(self, "警告", "中心点lon/latの範囲が不正です。")
            return False
        if self.cmb_slot.currentIndex() < 0:
            QMessageBox.warning(self, "警告", "30分帯を選択してください。")
            return False
        return True

    def start_od_run(self):
        if self.is_count_running or self.is_od_running: return
        if not self._validate_od(): return
        out_m, out_d, out_s = self._od_output_paths()
        py = sys.executable; script = Path(__file__).resolve().parent / "43_peak30min_od.py"
        date_list = [d.strftime("%Y-%m-%d") for d in sorted(self.selected_dates)]
        args = [str(script), "--input", str(self.input_folder), "--zoning", str(self.zoning_file), "--slot-index", str(self.cmb_slot.currentIndex()), "--output-matrix", str(out_m), "--output-detail", str(out_d), "--output-summary", str(out_s), "--center-lon", str(self.center_lon), "--center-lat", str(self.center_lat), "--center-name", self.center_name, "--dates", json.dumps(date_list, ensure_ascii=False), "--dates-compact", self._compact_dates(sorted(self.selected_dates))]
        if self.chk_recursive.isChecked(): args.append("--recursive")

        self.proc_od = QProcess(self)
        self.proc_od.setProgram(py); self.proc_od.setArguments(args); self.proc_od.setWorkingDirectory(str(Path(__file__).resolve().parent))
        env = QProcessEnvironment.systemEnvironment(); env.insert("PYTHONIOENCODING", "utf-8"); self.proc_od.setProcessEnvironment(env)
        self.proc_od.readyReadStandardOutput.connect(self._on_od_stdout); self.proc_od.readyReadStandardError.connect(self._on_od_stderr)
        self.proc_od.finished.connect(self._on_od_finished)

        self.last_output_matrix, self.last_output_detail, self.last_output_summary = out_m, out_d, out_s
        self.done_files_od = 0; self.od_error_count = 0; self.same_zone_ratio = 0.0; self.same_zone_count = 0; self.od_total_trips = 0
        self.dir_counts = {"EAST": 0, "WEST": 0, "NORTH": 0, "SOUTH": 0}; self.od_counts = {}
        self.od_started_at = time.time(); self.is_od_running = True; self.od_state_text = "RUNNING"; self._set_app_state("OD_RUNNING"); self._set_inputs_enabled(False)
        self._refresh_progress_text(); self._refresh_status_text(); self._refresh_telemetry_text()
        self.append_log(f"[OD] 指定30分帯: {self.cmb_slot.currentText()}")
        self.proc_od.start()

    def _on_od_stdout(self):
        if not self.proc_od: return
        text = bytes(self.proc_od.readAllStandardOutput()).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            t = line.strip()
            if not t: continue
            m = RE_ODCOUNT.match(t)
            if m:
                oz, dz, c = m.group(1), m.group(2), int(m.group(3)); self.od_counts[(oz, dz)] = c
            m = RE_DIRCOUNT.match(t)
            if m: self.dir_counts[m.group(1)] = int(m.group(2))
            m = RE_SAME.search(t)
            if m: self.same_zone_ratio = float(m.group(1))
            fd = RE_FILE_DONE.search(t)
            if fd:
                self._apply_progress_update(int(fd.group(1).replace(",", "")), max(1, int(fd.group(2).replace(",", ""))), is_od=True)
                continue
            if "[ERROR]" in t: self.od_error_count += 1
            self._refresh_telemetry_text()
            self.append_log(f"[OD] {t}")

    def _on_od_stderr(self):
        if not self.proc_od: return
        text = bytes(self.proc_od.readAllStandardError()).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            if line.strip(): self.append_log(f"[OD][STDERR] {line}")

    def _parse_summary(self):
        if not self.last_output_summary or not self.last_output_summary.exists(): return
        for enc in ("utf-8-sig", "utf-8", "cp932"):
            try:
                with self.last_output_summary.open("r", encoding=enc, newline="") as f:
                    rows = list(csv.DictReader(f))
                if rows:
                    r = rows[0]
                    self.od_total_trips = int(float(r.get("total_trips_in_slot") or 0))
                    self.same_zone_count = int(float(r.get("same_zone_od_count") or 0))
                break
            except Exception:
                continue

    def _on_od_finished(self, code: int, _status):
        ok = code == 0
        self.is_od_running = False; self.od_state_text = "COMPLETED" if ok else "ERROR"; self._set_app_state("COMPLETED" if ok else "ERROR"); self._set_inputs_enabled(True)
        self._refresh_status_text(); self._refresh_telemetry_text()
        self.btn_open_matrix.setEnabled(ok and self.last_output_matrix and self.last_output_matrix.exists())
        self.btn_open_detail.setEnabled(ok and self.last_output_detail and self.last_output_detail.exists())
        self.btn_open_od_folder.setEnabled(ok and self.last_output_matrix is not None)
        if ok:
            self._parse_summary()
            self._refresh_telemetry_text()
            self.append_log(f"[OD] 完了 指定30分帯={self.cmb_slot.currentText()} 対象日数={len(self.selected_dates)} ゾーン数={self.zone_count} center={self.center_name}({self.center_lon:.6f},{self.center_lat:.6f}) 総抽出トリップ数={self.od_total_trips} 同一ゾーンOD比率={self.same_zone_ratio:.1f}% エラー数={self.od_error_count}")
            self.append_log(f"[OD] 出力CSV: {self.last_output_matrix}")
            self.append_log(f"[OD] 出力CSV: {self.last_output_detail}")
            self.append_log(f"[OD] 出力CSV: {self.last_output_summary}")
        self.proc_od = None

    def _fmt_hms(self, sec: float) -> str:
        sec = int(max(0, sec) + 0.5); return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"

    def _eta_text(self, done: int, total: int, started: float, running: bool) -> str:
        if not running or done <= 0 or total <= 0 or done >= total: return "--:--:--" if running else "00:00:00"
        elapsed = max(1.0, time.time() - started)
        rate = done / elapsed
        if rate <= 0: return "--:--:--"
        return self._fmt_hms((total - done) / rate)

    def _tick(self):
        active_elapsed = 0.0
        if self.is_count_running: active_elapsed = time.time() - self.count_started_at
        elif self.is_od_running: active_elapsed = time.time() - self.od_started_at
        self.lbl_elapsed.setText(f"経過 {self._fmt_hms(active_elapsed)}")
        eta = self._eta_text(self.done_files_count, self.total_files, self.count_started_at, self.is_count_running) if self.is_count_running else self._eta_text(self.done_files_od, self.total_files, self.od_started_at, self.is_od_running)
        if not self.is_count_running and not self.is_od_running and self.count_state_text == "IDLE" and self.od_state_text == "IDLE":
            eta = "--:--:--"
        self.lbl_eta.setText(f"残り {eta}")

    def _open(self, p: Path | None):
        if not p or not p.exists(): return
        if sys.platform.startswith("win"): os.startfile(str(p))
        elif sys.platform == "darwin": subprocess.Popen(["open", str(p)])
        else: subprocess.Popen(["xdg-open", str(p)])

    def open_output_csv(self):
        self._open(self.last_output_csv)

    def open_output_folder(self):
        if self.last_output_csv: self._open(self.last_output_csv.parent)

    def open_od_folder(self):
        if self.last_output_matrix: self._open(self.last_output_matrix.parent)

def main() -> int:
    app = QApplication(sys.argv)
    try:
        w = MainWindow(); w.show()
        return app.exec()
    except Exception as e:
        import traceback

        traceback.print_exc()
        QMessageBox.critical(None, "起動エラー", f"起動中にエラーが発生しました。\n\n{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
