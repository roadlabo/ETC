from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QRect, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except Exception:
    WEBENGINE_AVAILABLE = False

APP_TITLE = "43_ピーク30分帯内OD抽出（30分帯内の最初の点をO、最後の点をDとして任意ゾーニングへ割当）"
UI_LOGO_FILENAME = "logo_43_UI_peak30min_od.png"
DEFAULT_CENTER_LON = 133.93
DEFAULT_CENTER_LAT = 35.07
DEFAULT_CENTER_NAME = "津山市中心点（既定値）"

RE_FILE_DONE = re.compile(r"進捗ファイル:\s*([0-9,]+)\s*/\s*([0-9,]+)")
RE_ODCOUNT = re.compile(r"^ODCOUNT:(.*?):(.*?):(\d+)\s*$")
RE_DIRCOUNT = re.compile(r"^DIRCOUNT:(EAST|WEST|NORTH|SOUTH):(\d+)\s*$")
RE_SAME = re.compile(r"SAME_ZONE_RATIO:\s*([0-9.]+)")


class StepBox(QFrame):
    def __init__(self, title: str, content: QWidget):
        super().__init__()
        self.setObjectName("stepBox")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        t = QLabel(title)
        t.setObjectName("stepTitle")
        lay.addWidget(t)
        lay.addWidget(content)


class SweepWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.angle = 0
        self.setMinimumHeight(140)

    def tick(self):
        self.angle = (self.angle + 7) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#050b09"))
        c = self.rect().center()
        r = min(self.width(), self.height()) // 2 - 8
        p.setPen(QPen(QColor("#1b4f2f")))
        p.drawEllipse(c, r, r)
        p.drawEllipse(c, int(r * 0.66), int(r * 0.66))
        p.drawEllipse(c, int(r * 0.33), int(r * 0.33))
        p.setPen(QPen(QColor("#56d27f"), 2))
        rad = self.angle * math.pi / 180
        p.drawLine(c.x(), c.y(), int(c.x() + r * math.cos(rad)), int(c.y() - r * math.sin(rad)))


class RealtimeODChart(QWidget):
    def __init__(self):
        super().__init__()
        self.od_counts: dict[tuple[str, str], int] = {}
        self.same_ratio = 0.0
        self.setMinimumHeight(260)

    def set_od_count(self, oz: str, dz: str, c: int):
        self.od_counts[(oz, dz)] = c
        self.update()

    def set_same_ratio(self, r: float):
        self.same_ratio = r
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#09120f"))
        items = sorted(self.od_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
        chart = r.adjusted(14, 30, -14, -24)
        if not items:
            p.setPen(QColor("#9ef4ff"))
            p.drawText(chart, Qt.AlignmentFlag.AlignCenter, "ODランキング待機中")
            return
        maxv = max(v for _, v in items)
        bar_h = max(14, chart.height() // (len(items) + 1))
        peak_pair, peak_cnt = items[0]
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
        p.setPen(QColor("#76ff8e"))
        p.drawText(r.adjusted(14, r.height() - 22, -14, -4), Qt.AlignmentFlag.AlignRight, f"現在最大OD: {peak_pair[0]}→{peak_pair[1]} {peak_cnt}")


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
        self.btn_ok = QPushButton("この点を採用")
        self.btn_ok.clicked.connect(self.accept)
        b2 = QPushButton("キャンセル")
        b2.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(self.btn_ok)
        row.addWidget(b2)
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


class MainWindow(QMainWindow):
    ETA_INTERVAL_SEC = 10.0

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1700, 980)
        self.showMaximized()

        self.proc: QProcess | None = None
        self.input_folder: Path | None = None
        self.zoning_file: Path | None = None
        self.csv_files: list[Path] = []
        self.total_files = self.done_files = self.error_count = 0
        self.zone_count = 0
        self.started_at = 0.0
        self.last_output_matrix: Path | None = None
        self.last_output_detail: Path | None = None
        self.last_output_summary: Path | None = None
        self.od_counts: dict[tuple[str, str], int] = {}
        self.same_zone_ratio = 0.0
        self.dir_counts = {"EAST": 0, "WEST": 0, "NORTH": 0, "SOUTH": 0}

        self.center_lon = DEFAULT_CENTER_LON
        self.center_lat = DEFAULT_CENTER_LAT
        self.center_name = DEFAULT_CENTER_NAME

        self._eta_done = 0; self._eta_total = 0; self._eta_last_calc_t = 0.0
        self._eta_last_text = "残り --:--:--"; self._eta_countdown_sec = None; self._eta_countdown_last_t = 0.0
        self._eta_last_t = None; self._eta_last_done_obs = None; self._eta_rate_ema = None; self._eta_prev_remain = None
        self._eta_start_t = None; self._eta_start_done = None

        self._build_ui()
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self.anim = QTimer(self); self.anim.timeout.connect(self.sweep.tick); self.anim.start(60)

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw); root.setContentsMargins(12, 12, 12, 10); root.setSpacing(8)
        ttl = QLabel(APP_TITLE); ttl.setObjectName("title"); ttl.setFont(QFont("Meiryo UI", 14, QFont.Weight.Bold)); root.addWidget(ttl)

        body = QHBoxLayout(); root.addLayout(body, 1)
        left = QVBoxLayout(); left.setSpacing(8); body.addLayout(left, 5)

        # STEP1
        s1w = QWidget(); s1 = QVBoxLayout(s1w); s1.setContentsMargins(0, 0, 0, 0)
        row1 = QHBoxLayout()
        self.btn_pick = QPushButton("第1スクリーニングフォルダ選択"); self.btn_pick.clicked.connect(self.pick_folder)
        self.lbl_folder = QLabel("未選択")
        self.chk_recursive = QCheckBox("サブフォルダも含める"); self.chk_recursive.stateChanged.connect(self.refresh_csv)
        row1.addWidget(self.btn_pick); row1.addWidget(self.lbl_folder, 1); row1.addWidget(self.chk_recursive)
        self.lbl_csv = QLabel("対象CSV数: 0")
        s1.addLayout(row1); s1.addWidget(self.lbl_csv)
        left.addWidget(StepBox("STEP 1：第1スクリーニングフォルダの選択", s1w))

        # STEP2
        s2w = QWidget(); s2 = QFormLayout(s2w)
        self.cmb_slot = QComboBox(); self.cmb_slot.addItems(self._slot_labels())
        zrow = QHBoxLayout()
        self.btn_zone = QPushButton("ゾーニングCSV選択"); self.btn_zone.clicked.connect(self.pick_zoning)
        self.lbl_zone = QLabel("未選択")
        zrow.addWidget(self.btn_zone); zrow.addWidget(self.lbl_zone, 1)
        w = QWidget(); w.setLayout(zrow)

        crow = QHBoxLayout()
        self.lbl_center_name = QLabel(self.center_name)
        self.lbl_center_lon = QLabel(f"lon: {self.center_lon:.6f}")
        self.lbl_center_lat = QLabel(f"lat: {self.center_lat:.6f}")
        self.btn_pick_center = QPushButton("地図で選択"); self.btn_pick_center.clicked.connect(self.pick_center_map)
        self.btn_center_default = QPushButton("既定値に戻す"); self.btn_center_default.clicked.connect(self.reset_center)
        crow.addWidget(self.lbl_center_name); crow.addWidget(self.lbl_center_lon); crow.addWidget(self.lbl_center_lat)
        crow.addStretch(1); crow.addWidget(self.btn_pick_center); crow.addWidget(self.btn_center_default)
        cw2 = QWidget(); cw2.setLayout(crow)

        s2.addRow("30分帯", self.cmb_slot)
        s2.addRow("ゾーニング", w)
        s2.addRow("方向判定中心点", cw2)
        left.addWidget(StepBox("STEP 2：30分帯とゾーニングファイルの指定", s2w))

        # STEP3
        s3w = QWidget(); s3 = QVBoxLayout(s3w); s3.setContentsMargins(0, 0, 0, 0)
        self.lbl_conditions = QLabel("対象CSV数: 0\n指定30分帯: --\nゾーン数: 0")
        self.lbl_out_desc = QLabel("ポリゴン外の点は、設定した中心点との位置関係に基づき東西南北ゾーンへ自動分類します。\n座標欠損のみ MISSING とします。")
        self.lbl_out_desc.setWordWrap(True)
        self.chk_single_info = QCheckBox("30分帯内に1点しかない場合は同一点をO/Dとみなす（固定）")
        self.chk_single_info.setChecked(True); self.chk_single_info.setEnabled(False)
        s3.addWidget(self.lbl_conditions); s3.addWidget(self.lbl_out_desc); s3.addWidget(self.chk_single_info)
        left.addWidget(StepBox("STEP 3：OD抽出条件の確認", s3w))

        # STEP4
        s4w = QWidget(); s4 = QHBoxLayout(s4w); s4.setContentsMargins(0, 0, 0, 0)
        self.btn_run = QPushButton("OD抽出スタート"); self.btn_run.clicked.connect(self.start_run)
        self.btn_open_matrix = QPushButton("出力CSVを開く"); self.btn_open_matrix.clicked.connect(lambda: self._open(self.last_output_matrix)); self.btn_open_matrix.setEnabled(False)
        self.btn_open_detail = QPushButton("明細CSVを開く"); self.btn_open_detail.clicked.connect(lambda: self._open(self.last_output_detail)); self.btn_open_detail.setEnabled(False)
        self.btn_open_folder = QPushButton("保存先フォルダを開く"); self.btn_open_folder.clicked.connect(self.open_folder); self.btn_open_folder.setEnabled(False)
        s4.addWidget(self.btn_run); s4.addWidget(self.btn_open_matrix); s4.addWidget(self.btn_open_detail); s4.addWidget(self.btn_open_folder); s4.addStretch(1)
        left.addWidget(StepBox("STEP 4：実行", s4w))

        self.chart = RealtimeODChart(); left.addWidget(self.chart)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); left.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(3000); self.log.setMinimumHeight(170)
        left.addWidget(self.log)

        right = QVBoxLayout(); body.addLayout(right, 2)
        panel = QFrame(); pv = QVBoxLayout(panel)
        self.lbl_status = QLabel("状態: IDLE")
        self.lbl_progress = QLabel("進捗ファイル: 0/0（0.0%）")
        self.lbl_elapsed = QLabel("経過 00:00:00"); self.lbl_elapsed.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        self.lbl_eta = QLabel("残り --:--:--"); self.lbl_eta.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        self.lbl_tel = QLabel("CYBER TELEMETRY")
        self.lbl_tel.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sweep = SweepWidget()
        pv.addWidget(self.lbl_status); pv.addWidget(self.lbl_progress); pv.addWidget(self.lbl_elapsed); pv.addWidget(self.lbl_eta)
        pv.addWidget(self.lbl_tel); pv.addWidget(self.sweep, 1)
        right.addWidget(panel, 1)

        logo_path = Path(__file__).resolve().parent / "assets" / "logos" / UI_LOGO_FILENAME
        if logo_path.exists():
            self.logo = QLabel(self)
            self.logo.setPixmap(QPixmap(str(logo_path)).scaledToHeight(76, Qt.TransformationMode.SmoothTransformation))
            self.logo.move(30, 26)
            self.logo.show()

        self.setStyleSheet("""
            QWidget{background:#040a08;color:#d8fff0;font-family:Meiryo UI;}
            QFrame#stepBox{border:2px solid #00ff99;border-radius:12px;background: rgba(0,255,153,16);}
            QLabel#stepTitle{color:#00ff99;font-weight:700;}
            QPushButton{background:#083424;border:1px solid #13d989;border-radius:10px;padding:6px 12px;}
            QPushButton:disabled{background:#0d1814;color:#6f887e;border-color:#365247;}
            QComboBox,QPlainTextEdit,QProgressBar{background:#0b1412;border:1px solid #1f4a3d;border-radius:8px;}
            QLabel#title{color:#76ff8e;}
        """)

    def _slot_labels(self) -> list[str]:
        out = []
        for i in range(48):
            s = i * 30
            e = s + 29
            sh, sm = divmod(s, 60)
            eh, em = divmod(e, 60)
            out.append(f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}")
        return out

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
        self.refresh_csv()

    def _list_csv(self) -> list[Path]:
        if not self.input_folder:
            return []
        gen = self.input_folder.rglob("*.csv") if self.chk_recursive.isChecked() else self.input_folder.glob("*.csv")
        return sorted(p for p in gen if p.is_file())

    def refresh_csv(self):
        if not self.input_folder:
            return
        self.csv_files = self._list_csv()
        self.total_files = len(self.csv_files)
        self.lbl_csv.setText(f"対象CSV数: {self.total_files:,}")
        if self.total_files == 0:
            QMessageBox.warning(self, "警告", "CSVが0件です。")
            self.input_folder = None
            self.lbl_folder.setText("未選択")
        self._update_conditions()

    def pick_zoning(self):
        f, _ = QFileDialog.getOpenFileName(self, "ゾーニングCSVを選択", "", "CSV (*.csv);;All (*.*)")
        if not f:
            return
        self.zoning_file = Path(f)
        self.lbl_zone.setText(self.zoning_file.name)
        self.zone_count = self._count_zone_rows(self.zoning_file)
        self.append_log(f"[INFO] ゾーン数: {self.zone_count}")
        self._update_conditions()

    def _count_zone_rows(self, p: Path) -> int:
        for enc in ("utf-8-sig", "utf-8", "cp932"):
            try:
                with p.open("r", encoding=enc, errors="ignore") as f:
                    return max(sum(1 for line in f if line.strip()) - 1, 0)
            except Exception:
                continue
        return 0

    def pick_center_map(self):
        d = MapPickDialog(self.center_lon, self.center_lat, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.center_lon = d.selected_lon
            self.center_lat = d.selected_lat
            self.center_name = "ユーザー指定点"
            self._refresh_center_labels()

    def reset_center(self):
        self.center_lon = DEFAULT_CENTER_LON
        self.center_lat = DEFAULT_CENTER_LAT
        self.center_name = DEFAULT_CENTER_NAME
        self._refresh_center_labels()

    def _refresh_center_labels(self):
        self.lbl_center_name.setText(self.center_name)
        self.lbl_center_lon.setText(f"lon: {self.center_lon:.6f}")
        self.lbl_center_lat.setText(f"lat: {self.center_lat:.6f}")
        self._update_conditions()

    def _update_conditions(self):
        slot = self.cmb_slot.currentText()
        self.lbl_conditions.setText(
            f"対象CSV数: {self.total_files:,}\n指定30分帯: {slot}\nゾーン数: {self.zone_count:,}\n"
            f"中心点: {self.center_name} ({self.center_lon:.6f},{self.center_lat:.6f})"
        )

    def _validate(self) -> bool:
        if not self.input_folder or not self.input_folder.exists():
            QMessageBox.warning(self, "警告", "第1スクリーニング済みフォルダを選択してください。")
            return False
        if self.total_files <= 0:
            QMessageBox.warning(self, "警告", "CSVが0件です。")
            return False
        if not self.zoning_file or not self.zoning_file.exists():
            QMessageBox.warning(self, "警告", "ゾーニングCSVを選択してください。")
            return False
        if not (-180 <= self.center_lon <= 180 and -90 <= self.center_lat <= 90):
            QMessageBox.warning(self, "警告", "中心点lon/latの範囲が不正です。")
            return False
        return True

    def start_run(self):
        if not self._validate():
            return
        parent = self.input_folder.parent
        base = self.input_folder.name
        out_matrix = parent / f"{base}_43_peak30min_od_matrix.csv"
        out_detail = parent / f"{base}_43_peak30min_od_detail.csv"
        out_summary = parent / f"{base}_43_peak30min_od_summary.csv"
        if any(p.exists() for p in (out_matrix, out_detail, out_summary)):
            r = QMessageBox.question(self, "確認", "既存の出力ファイルがあります。上書きしますか？")
            if r != QMessageBox.StandardButton.Yes:
                return

        py = sys.executable
        script = Path(__file__).resolve().parent / "43_peak30min_od.py"
        args = [
            str(script), "--input", str(self.input_folder), "--zoning", str(self.zoning_file),
            "--slot-index", str(self.cmb_slot.currentIndex()),
            "--output-matrix", str(out_matrix), "--output-detail", str(out_detail), "--output-summary", str(out_summary),
            "--center-lon", str(self.center_lon), "--center-lat", str(self.center_lat), "--center-name", self.center_name,
        ]
        if self.chk_recursive.isChecked():
            args.append("--recursive")

        self.proc = QProcess(self)
        self.proc.setProgram(py)
        self.proc.setArguments(args)
        self.proc.setWorkingDirectory(str(Path(__file__).resolve().parent))
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)

        self.done_files = 0; self.error_count = 0; self.od_counts = {}; self.chart.od_counts = {}
        self.same_zone_ratio = 0.0; self.dir_counts = {"EAST": 0, "WEST": 0, "NORTH": 0, "SOUTH": 0}
        self.last_output_matrix, self.last_output_detail, self.last_output_summary = out_matrix, out_detail, out_summary
        self.started_at = time.time()
        self.lbl_status.setText("状態: RUNNING")
        self._set_inputs_enabled(False)
        self._eta_done = 0; self._eta_total = self.total_files; self._reset_eta_estimator()
        self.append_log(f"[INFO] 対象CSV数: {self.total_files}")
        self.append_log(f"[INFO] 指定30分帯: {self.cmb_slot.currentText()}")
        self.append_log(f"[INFO] 方向判定中心点: {self.center_name} lon={self.center_lon:.6f} lat={self.center_lat:.6f}")
        self.proc.start()

    def _set_inputs_enabled(self, b: bool):
        for w in [self.btn_pick, self.chk_recursive, self.btn_zone, self.cmb_slot, self.btn_pick_center, self.btn_center_default, self.btn_run]:
            w.setEnabled(b)

    def _on_stdout(self):
        if not self.proc:
            return
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            t = line.strip()
            if not t:
                continue
            m = RE_ODCOUNT.match(t)
            if m:
                oz, dz, c = m.group(1), m.group(2), int(m.group(3))
                self.od_counts[(oz, dz)] = c
                self.chart.set_od_count(oz, dz, c)
                self.append_log(t)
                continue
            m = RE_DIRCOUNT.match(t)
            if m:
                self.dir_counts[m.group(1)] = int(m.group(2))
                continue
            m = RE_SAME.search(t)
            if m:
                self.same_zone_ratio = float(m.group(1))
                self.chart.set_same_ratio(self.same_zone_ratio)
            fd = RE_FILE_DONE.search(t)
            if fd:
                self.done_files = int(fd.group(1).replace(",", ""))
                self.total_files = max(1, int(fd.group(2).replace(",", "")))
                self._eta_done = self.done_files; self._eta_total = self.total_files
            if "[ERROR]" in t:
                self.error_count += 1
            self.append_log(t)

    def _on_stderr(self):
        if not self.proc:
            return
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="ignore")
        for l in text.splitlines():
            if l.strip():
                self.append_log(f"[STDERR] {l.strip()}")

    def _on_finished(self, code: int, _status):
        ok = code == 0
        self.lbl_status.setText("状態: DONE" if ok else "状態: ERROR")
        self._set_inputs_enabled(True)
        self.btn_open_matrix.setEnabled(ok and self.last_output_matrix and self.last_output_matrix.exists())
        self.btn_open_detail.setEnabled(ok and self.last_output_detail and self.last_output_detail.exists())
        self.btn_open_folder.setEnabled(ok)
        if ok:
            self.lbl_eta.setText("残り 00:00:00")
            if self.same_zone_ratio >= 50:
                QMessageBox.information(self, "注意", "同一ゾーンOD比率が高めです。SUMO投入前にゾーン設定をご確認ください。")
        self._write_batch_log_file()

    def _update_progress_label(self):
        pct = (self.done_files / self.total_files * 100) if self.total_files else 0
        self.lbl_progress.setText(f"進捗ファイル: {self.done_files:,}/{self.total_files:,}（{pct:.1f}%）")
        self.progress.setValue(int(pct * 10))

    def _fmt_hms(self, sec: float) -> str:
        sec = int(max(0, sec) + 0.5)
        return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"

    def _reset_eta_estimator(self):
        self._eta_last_t = None; self._eta_last_done_obs = None; self._eta_rate_ema = None
        self._eta_prev_remain = None; self._eta_last_calc_t = 0.0; self._eta_countdown_sec = None
        self._eta_countdown_last_t = 0.0; self._eta_start_t = None; self._eta_start_done = None

    def _update_eta(self):
        now = time.time()
        if self._eta_countdown_sec is not None:
            if self._eta_countdown_last_t <= 0.0:
                self._eta_countdown_last_t = now
            dt = now - self._eta_countdown_last_t
            if dt >= 1.0:
                self._eta_countdown_sec = max(0.0, self._eta_countdown_sec - dt)
                self._eta_countdown_last_t = now
                self.lbl_eta.setText(f"残り {self._fmt_hms(self._eta_countdown_sec)}")
        if now - self._eta_last_calc_t < self.ETA_INTERVAL_SEC:
            return
        self._eta_last_calc_t = now
        done, total = int(self._eta_done or 0), int(self._eta_total or 0)
        if total <= 0 or done <= 0 or done >= total:
            self.lbl_eta.setText("残り --:--:--" if done < total else "残り 00:00:00")
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
        rate = (cum_dd / cum_dt) * 0.82 + (self._eta_rate_ema or 0) * 0.18
        if rate <= 1e-6:
            return
        remain = (total - done) / rate
        elapsed = now - self.started_at if self.started_at else 0
        if self._eta_prev_remain is not None and elapsed > 10 and done >= 5:
            remain = min(remain, self._eta_prev_remain * 1.15)
        self._eta_prev_remain = remain
        self._eta_countdown_sec = float(remain); self._eta_countdown_last_t = now
        self.lbl_eta.setText(f"残り {self._fmt_hms(remain)}")

    def _tick(self):
        elapsed = time.time() - self.started_at if self.started_at else 0
        self.lbl_elapsed.setText(f"経過 {self._fmt_hms(elapsed)}")
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self._update_eta()
        self._update_progress_label()
        self.lbl_tel.setText(
            "CYBER TELEMETRY\n"
            f"対象CSV数: {self.total_files:,}\n"
            f"ゾーン数: {self.zone_count:,}\n"
            f"進捗ファイル: {self.done_files:,}/{self.total_files:,}\n"
            f"エラー数: {self.error_count:,}\n"
            f"現在状態: {'RUNNING' if self.proc and self.proc.state()!=QProcess.ProcessState.NotRunning else 'IDLE'}\n"
            f"経過時間: {self._fmt_hms(elapsed)}\n"
            f"残り時間: {self.lbl_eta.text().replace('残り ','')}\n"
            f"同一ゾーンOD比率: {self.same_zone_ratio:.1f}%\n"
            f"東方面件数: {self.dir_counts['EAST']:,}\n"
            f"西方面件数: {self.dir_counts['WEST']:,}\n"
            f"北方面件数: {self.dir_counts['NORTH']:,}\n"
            f"南方面件数: {self.dir_counts['SOUTH']:,}\n"
            f"方向判定中心点: {self.center_name}\n"
            f"center lon: {self.center_lon:.6f}\n"
            f"center lat: {self.center_lat:.6f}"
        )

    def _open(self, p: Path | None):
        if not p or not p.exists():
            return
        if sys.platform.startswith("win"):
            os.startfile(str(p))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])

    def open_folder(self):
        if self.last_output_matrix:
            self._open(self.last_output_matrix.parent)

    def _write_batch_log_file(self):
        if not self.last_output_matrix:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = self.last_output_matrix.parent / f"43_batch_log_{stamp}.txt"
        sec = time.time() - self.started_at if self.started_at else 0
        lines = [
            f"Input: {self.input_folder}",
            f"Zoning: {self.zoning_file}",
            f"Slot: {self.cmb_slot.currentText()} ({self.cmb_slot.currentIndex()})",
            f"Center: {self.center_name} lon={self.center_lon:.6f} lat={self.center_lat:.6f}",
            f"開始: {datetime.fromtimestamp(self.started_at).strftime('%Y/%m/%d %H:%M:%S') if self.started_at else ''}",
            f"終了: {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}",
            f"総所要時間: {self._fmt_hms(sec)}",
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
