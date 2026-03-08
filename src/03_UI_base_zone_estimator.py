from __future__ import annotations

import csv
import os
import re
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtGui import QFont, QPainter, QPen, QColor, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "03_運行ID別 推定拠点ゾーン対応表 作成"
RE_PROGRESS = re.compile(r"\[PROGRESS\]\s+done=(\d+)\s+total=(\d+)\s+file=(.+)")
RE_TOTAL = re.compile(r"\[TOTAL\]\s+total=(\d+)")
RE_HIT = re.compile(r"\[HIT\]\s+op_id=(\S+)\s+zone=(.+?)\s+hit_count=(\d+)")
RE_ZONE_COUNT = re.compile(r"\[INFO\]\s+有効ゾーン数:\s*(\d+)")


def _normalize_log_line(text: str) -> str:
    s = (text or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", s).strip()


def format_hhmmss(sec: int) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_zone_shapes(path: Path) -> dict[str, list[tuple[float, float]]]:
    rows: list[list[str]] = []
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
        except Exception:
            return {}
    if not rows:
        return {}
    header = rows[0]
    has_header = any(not re.fullmatch(r"[-+]?\d+(\.\d+)?", c.strip()) for c in header[1:])
    body = rows[1:] if has_header else rows
    zone_map: dict[str, list[tuple[float, float]]] = {}
    for row in body:
        if not row:
            continue
        name = row[0].strip()
        if not name:
            continue
        nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", ",".join(row[1:]))]
        points = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        if len(points) >= 3:
            zone_map[name] = points
    return zone_map


class RadarWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.angle = 0
        self.running = False
        self.setMinimumHeight(130)

    def set_running(self, running: bool) -> None:
        self.running = running

    def tick(self) -> None:
        if self.running:
            self.angle = (self.angle + 7) % 360
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#050a09"))
        c = self.rect().center()
        r = min(self.width(), self.height()) // 2 - 10
        p.setPen(QPen(QColor("#1f5a46"), 1))
        for k in (1.0, 0.66, 0.33):
            p.drawEllipse(c, int(r * k), int(r * k))
        p.setPen(QPen(QColor("#53ffd0"), 2))
        rad = self.angle * 3.14159 / 180.0
        x = int(c.x() + r * __import__("math").cos(rad))
        y = int(c.y() - r * __import__("math").sin(rad))
        p.drawLine(c.x(), c.y(), x, y)


class ZoneMapWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.zone_name = ""
        self.points: list[tuple[float, float]] = []
        self.setMinimumHeight(360)

    def set_zone(self, zone_name: str, points: list[tuple[float, float]]) -> None:
        self.zone_name = zone_name
        self.points = points
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ffffff"))
        if not self.points:
            p.setPen(QPen(QColor("#999999"), 1))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "ゾーンカードをクリックするとここに表示")
            return
        lons = [pt[0] for pt in self.points]
        lats = [pt[1] for pt in self.points]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        w = max(max_lon - min_lon, 1e-9)
        h = max(max_lat - min_lat, 1e-9)
        rw = self.width() * 0.8
        rh = self.height() * 0.75
        scale = min(rw / w, rh / h)
        cx = (min_lon + max_lon) / 2.0
        cy = (min_lat + max_lat) / 2.0
        poly = QPolygonF()
        for lon, lat in self.points:
            x = self.width() / 2 + (lon - cx) * scale
            y = self.height() / 2 - (lat - cy) * scale
            poly.append(__import__("PyQt6.QtCore").QtCore.QPointF(x, y))
        p.setPen(QPen(QColor("#00a3a3"), 3))
        p.setBrush(QColor(0, 170, 170, 60))
        p.drawPolygon(poly)
        p.setPen(QPen(QColor("#333333"), 1))
        p.drawText(16, 28, self.zone_name)


class ZoneCard(QPushButton):
    def __init__(self, zone_name: str):
        super().__init__()
        self.zone_name = zone_name
        self.count = 0
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(90)
        self.lbl = QLabel()
        self.lbl.setObjectName("zoneText")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(self.lbl)
        self.refresh()

    def set_count(self, value: int) -> None:
        self.count = value
        self.refresh()

    def refresh(self) -> None:
        self.lbl.setText(f"{self.zone_name}\nHIT運行ID数: {self.count:,}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1600, 920)

        self.proc: QProcess | None = None
        self.input_folder: Path | None = None
        self.zoning_file: Path | None = None
        self.output_csv: Path | None = None
        self.total_files = 0
        self.done_files = 0
        self.hit_count = 0
        self.current_file = "-"
        self.run_started_at: float | None = None
        self.zone_shapes: dict[str, list[tuple[float, float]]] = {}
        self.zone_cards: dict[str, ZoneCard] = {}
        self.zone_hit_counts: dict[str, int] = {}
        self._counted_ops: set[str] = set()
        self._last_log = ""
        self._stdout_buffer = ""

        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(500)

    def _build_ui(self) -> None:
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw); root.setContentsMargins(12, 12, 12, 12); root.setSpacing(10)

        top = QFrame(); top.setObjectName("card")
        top_l = QVBoxLayout(top); top_l.setContentsMargins(12, 10, 12, 10); top_l.setSpacing(8)
        top_l.addWidget(QLabel(APP_TITLE, objectName="title"))
        desc = QLabel("夜をまたぐ位置を最優先に、運行IDごとの推定拠点ゾーンを判定します。")
        desc.setWordWrap(True)
        top_l.addWidget(desc)

        steps = QHBoxLayout(); steps.setSpacing(8)
        top_l.addLayout(steps)
        texts = [
            "STEP1\n第1スクリーニングフォルダを選択\n運行ID別CSVが格納されたフォルダを指定してください。",
            "STEP2\n任意ゾーニングファイルを選択\nzoning_data.csv 形式のゾーン定義ファイルを指定してください。",
            "STEP3\n出力先を確認\n選択フォルダと同階層に「_拠点ゾーン.csv」を出力します。",
            "STEP4\n推定拠点ゾーン対応表を作成\n夜間の位置関係と夜をまたぐ位置を基に、運行IDごとの推定拠点ゾーンを判定します。",
        ]
        for i, t in enumerate(texts):
            box = QFrame(); box.setObjectName("step")
            bl = QVBoxLayout(box); bl.setContentsMargins(10, 8, 10, 8)
            lbl = QLabel(t); lbl.setWordWrap(True)
            bl.addWidget(lbl)
            if i == 0:
                r = QHBoxLayout()
                self.btn_pick_folder = QPushButton("選択"); self.btn_pick_folder.clicked.connect(self.pick_folder)
                self.lbl_folder = QLabel("未選択"); self.lbl_folder.setWordWrap(True)
                self.chk_recursive = QCheckBox("サブフォルダも含める"); self.chk_recursive.stateChanged.connect(self._recalc_csv_count)
                r.addWidget(self.btn_pick_folder); r.addWidget(self.lbl_folder, 1); r.addWidget(self.chk_recursive)
                bl.addLayout(r)
            elif i == 1:
                r = QHBoxLayout()
                self.btn_pick_zoning = QPushButton("選択"); self.btn_pick_zoning.clicked.connect(self.pick_zoning)
                self.lbl_zoning = QLabel("未選択"); self.lbl_zoning.setWordWrap(True)
                r.addWidget(self.btn_pick_zoning); r.addWidget(self.lbl_zoning, 1)
                bl.addLayout(r)
            elif i == 2:
                self.lbl_output = QLabel("未設定"); self.lbl_output.setWordWrap(True)
                bl.addWidget(self.lbl_output)
            else:
                r = QHBoxLayout()
                self.btn_run = QPushButton("実行"); self.btn_run.clicked.connect(self.start_run)
                self.btn_open = QPushButton("CSVを開く"); self.btn_open.clicked.connect(self.open_output); self.btn_open.setEnabled(False)
                r.addWidget(self.btn_run); r.addWidget(self.btn_open)
                bl.addLayout(r)
            steps.addWidget(box)
            steps.setStretch(i, 1)
        root.addWidget(top)

        middle = QHBoxLayout(); middle.setSpacing(10); root.addLayout(middle, 1)

        left_frame = QFrame(); left_frame.setObjectName("card")
        lf = QVBoxLayout(left_frame); lf.setContentsMargins(8, 8, 8, 8)
        lf.addWidget(QLabel("ゾーンカード一覧", objectName="panelTitle"))
        self.card_container = QWidget(); self.card_grid = QGridLayout(self.card_container); self.card_grid.setSpacing(8)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setWidget(self.card_container)
        lf.addWidget(self.scroll, 1)

        center_frame = QFrame(); center_frame.setObjectName("card")
        cf = QVBoxLayout(center_frame); cf.setContentsMargins(8, 8, 8, 8)
        cf.addWidget(QLabel("地図表示エリア", objectName="panelTitle"))
        self.map_widget = ZoneMapWidget()
        cf.addWidget(self.map_widget, 1)

        right_frame = QFrame(); right_frame.setObjectName("telemetry")
        rf = QVBoxLayout(right_frame); rf.setContentsMargins(10, 10, 10, 10); rf.setSpacing(6)
        rf.addWidget(QLabel("CYBER TELEMETRY", objectName="cyTitle"))
        self.lbl_zone_count = QLabel("ゾーン数: 0")
        self.lbl_status = QLabel("状態: IDLE")
        self.lbl_current = QLabel("現在: -"); self.lbl_current.setWordWrap(True)
        self.lbl_progress = QLabel("進捗ファイル: 0/0 (0.0%)")
        self.lbl_hit = QLabel("正常HIT: 0")
        self.lbl_elapsed = QLabel("経過 00:00:00", objectName="big")
        self.lbl_remaining = QLabel("残り --:--:--", objectName="big")
        for w in [self.lbl_zone_count, self.lbl_status, self.lbl_current, self.lbl_progress, self.lbl_hit, self.lbl_elapsed, self.lbl_remaining]:
            rf.addWidget(w)
        self.radar = RadarWidget(); rf.addWidget(self.radar)
        rf.addStretch(1)

        middle.addWidget(left_frame)
        middle.addWidget(center_frame)
        middle.addWidget(right_frame)
        middle.setStretch(0, 40)
        middle.setStretch(1, 30)
        middle.setStretch(2, 14)

        bottom = QFrame(); bottom.setObjectName("card")
        bf = QVBoxLayout(bottom); bf.setContentsMargins(8, 8, 8, 8)
        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(44)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        bf.addWidget(self.progress); bf.addWidget(self.log)
        root.addWidget(bottom)

        self.setStyleSheet(
            """
            QWidget { background:#060b09; color:#98f3c8; }
            QFrame#card, QFrame#telemetry, QFrame#step { background:#0d1714; border:1px solid #1f4a38; border-radius:10px; }
            QFrame#telemetry { border:1px solid #35ffd5; }
            QLabel#title { font-size:22px; font-weight:800; color:#e9fff4; }
            QLabel#panelTitle { font-size:14px; font-weight:800; color:#73ffe1; }
            QLabel#cyTitle { font-size:18px; font-weight:900; color:#73ffe1; }
            QLabel#big { font-size:20px; font-weight:800; color:#d4ff7d; }
            QPushButton { background:#123325; border:1px solid #00ff99; border-radius:8px; padding:6px 10px; color:#edfff6; }
            QPushButton:checked { background:#1f5f47; border:2px solid #72ffe2; }
            QPushButton:hover { background:#20543c; }
            QLabel#zoneText { font-size:15px; font-weight:700; color:#d8fff2; }
            QPlainTextEdit { background:#0a120f; border:1px solid #1f4a38; }
            """
        )

    def append_log_line(self, text: str) -> None:
        line = _normalize_log_line(text)
        if not line or line == self._last_log:
            return
        self._last_log = line
        self.log.setPlainText(line)

    def recount_target_csvs(self) -> int:
        if not self.input_folder:
            return 0
        gen = self.input_folder.rglob("*.csv") if self.chk_recursive.isChecked() else self.input_folder.glob("*.csv")
        return sum(1 for p in gen if p.is_file() and not p.name.endswith("_拠点ゾーン.csv") and p.name != "zoning_data.csv")

    def _recalc_csv_count(self) -> None:
        self.total_files = self.recount_target_csvs()
        self._refresh_progress()
        self._update_run_state()

    def _update_run_state(self) -> None:
        running = self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning
        self.btn_run.setEnabled((not running) and self.input_folder is not None and self.zoning_file is not None and self.total_files > 0)

    def _update_output_path(self) -> None:
        if self.input_folder:
            self.output_csv = self.input_folder.parent / f"{self.input_folder.name}_拠点ゾーン.csv"
            self.lbl_output.setText(str(self.output_csv))

    def pick_folder(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "第1スクリーニングフォルダを選択")
        if not p:
            return
        self.input_folder = Path(p)
        self.lbl_folder.setText(str(self.input_folder))
        self._update_output_path(); self._recalc_csv_count()

    def build_zone_cards(self) -> None:
        while self.card_grid.count():
            item = self.card_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.zone_cards.clear()
        self.zone_hit_counts = {name: 0 for name in self.zone_shapes.keys()}
        for idx, zone_name in enumerate(self.zone_shapes.keys()):
            card = ZoneCard(zone_name)
            card.clicked.connect(lambda _=False, n=zone_name: self.select_zone_card(n))
            self.zone_cards[zone_name] = card
            self.card_grid.addWidget(card, idx // 3, idx % 3)
        self.lbl_zone_count.setText(f"ゾーン数: {len(self.zone_shapes):,}")

    def select_zone_card(self, zone_name: str) -> None:
        for n, c in self.zone_cards.items():
            c.setChecked(n == zone_name)
        self.render_zone_on_map(zone_name)

    def render_zone_on_map(self, zone_name: str) -> None:
        self.map_widget.set_zone(zone_name, self.zone_shapes.get(zone_name, []))

    def update_zone_card(self, zone_name: str, count: int) -> None:
        self.zone_hit_counts[zone_name] = count
        if zone_name in self.zone_cards:
            self.zone_cards[zone_name].set_count(count)

    def pick_zoning(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "任意ゾーニングファイルを選択", "", "CSV (*.csv)")
        if not p:
            return
        self.zoning_file = Path(p)
        self.lbl_zoning.setText(str(self.zoning_file))
        self.zone_shapes = parse_zone_shapes(self.zoning_file)
        self.build_zone_cards()
        if self.zone_shapes:
            self.select_zone_card(next(iter(self.zone_shapes.keys())))
        self._update_run_state()

    def _refresh_progress(self) -> None:
        pct = (self.done_files / self.total_files * 100.0) if self.total_files else 0.0
        self.progress.setValue(int(pct))
        self.lbl_progress.setText(f"進捗ファイル: {self.done_files:,}/{self.total_files:,} ({pct:.1f}%)")

    def start_run(self) -> None:
        if not self.input_folder or not self.zoning_file:
            return
        self._update_output_path()
        self.total_files = self.recount_target_csvs()
        if self.total_files <= 0:
            QMessageBox.warning(self, "入力不足", "対象CSVがありません。")
            return
        self.done_files = 0
        self.hit_count = 0
        self._counted_ops.clear()
        for n in self.zone_hit_counts:
            self.update_zone_card(n, 0)
        self._refresh_progress()
        self.log.clear(); self._last_log = ""
        self.run_started_at = time.time()
        self.lbl_status.setText("状態: RUNNING")
        self.radar.set_running(True)
        self.btn_open.setEnabled(False)

        script = Path(__file__).with_name("03_base_zone_estimator.py")
        args = [str(script), "--input", str(self.input_folder), "--zoning", str(self.zoning_file)]
        if self.output_csv:
            args += ["--output", str(self.output_csv)]
        if self.chk_recursive.isChecked():
            args.append("--recursive")

        self.proc = QProcess(self)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(args)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.proc.start()
        self._update_run_state()
        self.append_log_line("[INFO] 開始")

    def _process_log_line(self, line: str) -> None:
        if m := RE_TOTAL.search(line):
            self.total_files = int(m.group(1))
            self._refresh_progress()
        if m := RE_PROGRESS.search(line):
            self.done_files = int(m.group(1)); self.total_files = int(m.group(2)); self.current_file = _normalize_log_line(m.group(3))
            self.lbl_current.setText(f"現在: {self.current_file}")
            self._refresh_progress()
        if m := RE_HIT.search(line):
            op_id = m.group(1)
            zone = _normalize_log_line(m.group(2))
            if op_id not in self._counted_ops:
                self._counted_ops.add(op_id)
                self.hit_count += 1
                self.update_zone_card(zone, self.zone_hit_counts.get(zone, 0) + 1)
            self.lbl_hit.setText(f"正常HIT: {self.hit_count:,}")
        if m := RE_ZONE_COUNT.search(line):
            self.lbl_zone_count.setText(f"ゾーン数: {int(m.group(1)):,}")

    def on_output(self) -> None:
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="ignore")
        rows = (self._stdout_buffer + data).split("\n")
        self._stdout_buffer = rows.pop() if rows else ""
        for raw in rows:
            line = _normalize_log_line(raw)
            if not line:
                continue
            if any(line.startswith(tag) for tag in ("[INFO]", "[TOTAL]", "[HIT]", "[PROGRESS]", "[WARN]", "[ERROR]")):
                self.append_log_line(line)
            self._process_log_line(line)

    def _tick(self) -> None:
        self.radar.tick()
        running = self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning
        if running and self.run_started_at:
            elapsed = int(time.time() - self.run_started_at)
            self.lbl_elapsed.setText(f"経過 {format_hhmmss(elapsed)}")
            if self.done_files > 0 and self.total_files > 0:
                eta = int((elapsed / self.done_files) * max(self.total_files - self.done_files, 0))
                self.lbl_remaining.setText(f"残り {format_hhmmss(eta)}")
            else:
                self.lbl_remaining.setText("残り --:--:--")

    def on_finished(self, code: int, _status) -> None:
        if self._stdout_buffer:
            line = _normalize_log_line(self._stdout_buffer)
            self._process_log_line(line)
            self.append_log_line(line)
        self._stdout_buffer = ""
        self.lbl_status.setText("状態: DONE" if code == 0 else "状態: ERROR")
        self.radar.set_running(False)
        self.done_files = max(self.done_files, self.total_files if code == 0 else self.done_files)
        self._refresh_progress()
        self.btn_open.setEnabled(code == 0 and self.output_csv is not None and self.output_csv.exists())
        self._update_run_state()

    def open_output(self) -> None:
        if self.output_csv and self.output_csv.exists():
            os.startfile(str(self.output_csv))


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
