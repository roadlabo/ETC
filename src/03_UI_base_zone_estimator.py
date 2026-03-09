from __future__ import annotations

import csv
import math
import os
import re
import sys
import time
from pathlib import Path
import logging
from html import escape

from PyQt6.QtCore import QPointF, QProcess, QProcessEnvironment, QTimer, Qt, QUrl
from PyQt6.QtGui import QPainter, QPen, QColor, QPolygonF
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
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

WEBENGINE_AVAILABLE = False
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except Exception:
    QWebEngineView = None

APP_TITLE = "03_運行ID別 推定拠点ゾーン対応表 作成"
RE_PROGRESS = re.compile(r"\[PROGRESS\]\s+done=(\d+)\s+total=(\d+)(?:\s+file=(.+))?")
RE_TOTAL = re.compile(r"\[TOTAL\]\s+total=(\d+)")
RE_HIT = re.compile(r"\[HIT\]\s+op_id=(\S+)\s+zone=(.+?)\s+hit_count=(\d+)")
RE_HIT_AUX = re.compile(r"\[HIT_AUX\]\s+op_id=(\S+)\s+zone=(.+?)\s+aux_count=(\d+)")
RE_ZONE_COUNT = re.compile(r"\[INFO\]\s+有効ゾーン数:\s*(\d+)")
AUX_ZONE_NAMES = ("北方面", "南方面", "東方面", "西方面")


def _normalize_log_line(text: str) -> str:
    s = (text or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", s).strip()


def format_hhmmss(sec: int) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def should_count_as_input_csv(filename: str) -> bool:
    n = (filename or "").strip()
    lower = n.lower()
    if not lower.endswith(".csv"):
        return False
    if n.startswith(".") or n.startswith("~$"):
        return False
    if lower == "zoning_data.csv" or n.endswith("_拠点ゾーン.csv"):
        return False
    return True


def fast_count_csv_files(folder: str, include_subfolders: bool) -> int:
    base = Path(folder)
    if not base.is_dir():
        return 0
    count = 0
    stack = [str(base)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for ent in it:
                    if ent.is_file() and should_count_as_input_csv(ent.name):
                        count += 1
                    elif include_subfolders and ent.is_dir(follow_symlinks=False) and not ent.name.startswith("."):
                        stack.append(ent.path)
        except Exception:
            continue
    return count


def build_processing_file_list(folder: str, include_subfolders: bool) -> list[Path]:
    base = Path(folder)
    if not base.is_dir():
        return []
    files: list[Path] = []
    stack = [str(base)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for ent in it:
                    if ent.is_file() and should_count_as_input_csv(ent.name):
                        files.append(Path(ent.path))
                    elif include_subfolders and ent.is_dir(follow_symlinks=False) and not ent.name.startswith("."):
                        stack.append(ent.path)
        except Exception:
            continue
    return sorted(files)


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
    zone_map: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        if not row or len(row) < 7:
            continue
        name = (row[0] or "").replace("\ufeff", "").strip()
        if not name:
            continue
        vals: list[float] = []
        for cell in row[1:]:
            try:
                vals.append(float(str(cell).strip()))
            except Exception:
                continue
        points = [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]
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
        self.main_zone_name = ""
        self.main_polygon_points: list[tuple[float, float]] = []
        self.all_zone_polygons: list[list[tuple[float, float]]] = []
        self.aux_sector_polygon: list[tuple[float, float]] = []
        self.center_point: tuple[float, float] | None = None
        self.render_mode = "normal"
        self.message = "ゾーンカードをクリックするとここに表示"
        self.setMinimumHeight(360)

    def set_normal_zone(self, zone_name: str, points: list[tuple[float, float]], message: str = "") -> None:
        self.main_zone_name = zone_name
        self.main_polygon_points = points
        self.all_zone_polygons = []
        self.aux_sector_polygon = []
        self.center_point = None
        self.render_mode = "normal"
        self.message = message
        self.update()

    def set_aux_direction_zone(
        self,
        zone_name: str,
        sector_points: list[tuple[float, float]],
        all_zone_polygons: list[list[tuple[float, float]]],
        center_point: tuple[float, float],
        message: str = "",
    ) -> None:
        self.main_zone_name = zone_name
        self.main_polygon_points = []
        self.all_zone_polygons = all_zone_polygons
        self.aux_sector_polygon = sector_points
        self.center_point = center_point
        self.render_mode = "aux"
        self.message = message
        self.update()

    @staticmethod
    def _bounds(
        polygons: list[list[tuple[float, float]]],
        points: list[tuple[float, float]] | None = None,
    ) -> tuple[float, float, float, float] | None:
        all_points = [pt for poly in polygons for pt in poly] + list(points or [])
        if not all_points:
            return None
        lons = [pt[0] for pt in all_points]
        lats = [pt[1] for pt in all_points]
        return min(lons), max(lons), min(lats), max(lats)

    def _map_point(self, lon: float, lat: float, cx: float, cy: float, scale: float) -> QPointF:
        x = self.width() / 2 + (lon - cx) * scale
        y = self.height() / 2 - (lat - cy) * scale
        return QPointF(x, y)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ffffff"))
        if self.render_mode == "normal" and not self.main_polygon_points:
            p.setPen(QPen(QColor("#999999"), 1))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return

        if self.render_mode == "aux" and (not self.aux_sector_polygon or not self.all_zone_polygons):
            p.setPen(QPen(QColor("#999999"), 1))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return

        polygons = [self.main_polygon_points] if self.render_mode == "normal" else [self.aux_sector_polygon] + self.all_zone_polygons
        center_points = [self.center_point] if self.center_point else []
        bounds = self._bounds(polygons, points=[pt for pt in center_points if pt])
        if bounds is None:
            p.setPen(QPen(QColor("#999999"), 1))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return

        min_lon, max_lon, min_lat, max_lat = bounds
        w = max(max_lon - min_lon, 1e-9)
        h = max(max_lat - min_lat, 1e-9)
        margin = 0.1
        draw_w = max(self.width() * (1.0 - margin * 2.0), 1.0)
        draw_h = max(self.height() * (1.0 - margin * 2.0), 1.0)
        scale = min(draw_w / w, draw_h / h)
        cx = (min_lon + max_lon) / 2.0
        cy = (min_lat + max_lat) / 2.0

        if self.render_mode == "normal":
            poly = QPolygonF()
            for lon, lat in self.main_polygon_points:
                poly.append(self._map_point(lon, lat, cx, cy, scale))
            p.setPen(QPen(QColor("#00a3a3"), 4))
            p.setBrush(QColor(0, 170, 170, 80))
            p.drawPolygon(poly)
        else:
            for zone_poly in self.all_zone_polygons:
                poly = QPolygonF()
                for lon, lat in zone_poly:
                    poly.append(self._map_point(lon, lat, cx, cy, scale))
                p.setPen(QPen(QColor("#cc1f1f"), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolygon(poly)

            sector_poly = QPolygonF()
            for lon, lat in self.aux_sector_polygon:
                sector_poly.append(self._map_point(lon, lat, cx, cy, scale))
            p.setPen(QPen(QColor("#1d4ed8"), 3))
            p.setBrush(QColor(29, 78, 216, 90))
            p.drawPolygon(sector_poly)

            if self.center_point:
                cp = self._map_point(self.center_point[0], self.center_point[1], cx, cy, scale)
                p.setPen(QPen(QColor("#0f172a"), 1))
                p.setBrush(QColor("#1d4ed8"))
                p.drawEllipse(cp, 4, 4)
                p.drawText(int(cp.x()) + 8, int(cp.y()) - 8, "中心")

            p.setPen(QPen(QColor("#333333"), 1))
            p.setBrush(QColor(255, 255, 255, 220))
            p.drawRoundedRect(self.width() - 240, 10, 225, 54, 4, 4)
            p.setPen(QPen(QColor("#cc1f1f"), 1))
            p.drawText(self.width() - 230, 30, "赤線: 指定ゾーニング")
            p.setPen(QPen(QColor("#1d4ed8"), 1))
            p.drawText(self.width() - 230, 50, "青塗: 方面補助分類")

        p.setPen(QPen(QColor("#333333"), 1))
        font = p.font()
        font.setPointSize(16)
        font.setBold(True)
        p.setFont(font)
        title = self.main_zone_name
        if self.render_mode == "aux":
            title = f"{self.main_zone_name}（補助分類エリア）"
        p.drawText(self.rect().adjusted(0, 8, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, title)


class ZoneCard(QPushButton):
    def __init__(self, zone_name: str):
        super().__init__()
        self.zone_name = zone_name
        self.count = 0
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(68)
        self.lbl_title = QLabel(self.zone_name)
        self.lbl_title.setObjectName("zoneTitle")
        self.lbl_count = QLabel()
        self.lbl_count.setObjectName("zoneText")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(1)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_count)
        self.refresh()

    def set_count(self, value: int) -> None:
        self.count = value
        self.refresh()

    def refresh(self) -> None:
        self.lbl_count.setText(f"HIT運行ID数: {self.count:,}")


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
        self.zone_shape_aliases: dict[str, str] = {}
        self.zone_cards: dict[str, ZoneCard] = {}
        self.zone_hit_counts: dict[str, int] = {}
        self._counted_ops: set[str] = set()
        self._last_log = ""
        self._stdout_buffer = ""
        self._last_hit_milestone = 0
        self._last_progress_milestone = 0
        self.is_running = False
        self.selected_zone_name = ""
        self._debug_logger = logging.getLogger("zone_estimator_ui")

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
        self.map_holder = QWidget()
        self.map_stack = QStackedLayout(self.map_holder)
        self.map_stack.setContentsMargins(0, 0, 0, 0)
        self.map_widget = ZoneMapWidget()
        self.map_stack.addWidget(self.map_widget)
        self.web_map = None
        if WEBENGINE_AVAILABLE:
            self.web_map = QWebEngineView(self.map_holder)
            self.map_stack.addWidget(self.web_map)
        self.map_stack.setCurrentWidget(self.map_widget)
        cf.addWidget(self.map_holder, 1)

        right_frame = QFrame(); right_frame.setObjectName("telemetry")
        rf = QVBoxLayout(right_frame); rf.setContentsMargins(10, 10, 10, 10); rf.setSpacing(6)
        rf.addWidget(QLabel("CYBER TELEMETRY", objectName="cyTitle"))
        self.lbl_zone_count = QLabel("ゾーン数: 0")
        self.lbl_status = QLabel("状態: IDLE")
        self.lbl_progress = QLabel("進捗ファイル: 0/0 (0.0%)")
        self.lbl_hit = QLabel("正常HIT: 0")
        self.lbl_map_mode = QLabel("地図表示: SIMPLE")
        self.lbl_elapsed = QLabel("経過 00:00:00", objectName="big")
        self.lbl_remaining = QLabel("残り --:--:--", objectName="big")
        for w in [self.lbl_zone_count, self.lbl_status, self.lbl_progress, self.lbl_hit, self.lbl_map_mode, self.lbl_elapsed, self.lbl_remaining]:
            rf.addWidget(w)
        self.radar = RadarWidget(); rf.addWidget(self.radar)

        logic = QFrame(); logic.setObjectName("logicCard")
        ll = QVBoxLayout(logic); ll.setContentsMargins(8, 8, 8, 8); ll.setSpacing(4)
        ll.addWidget(QLabel("JUDGEMENT LOGIC", objectName="panelTitle"))
        logic_text = (
            "【判定コンセプト】\n"
            "このソフトは、運行IDごとの軌跡から「拠点らしい場所」を推定します。\n"
            "重視するのは、夜にトリップが終わり、翌朝に同じ場所から再び動き始める「夜をまたぐ位置」です。\n\n"
            "【判定手順】\n"
            "① 日ごとの最終点と翌日の最初の点を取り出します。\n"
            "② 夜側（20:00以降）と朝側（5:00～10:00）の組で、距離が近いものを夜越し地点候補とします。\n"
            "③ その代表点をゾーンに当てはめ、同じゾーンが複数回出れば、そのゾーンを拠点と判定します。\n"
            "④ 夜越し地点が見つからない場合は、CSV内で最も深夜3:00に近い点を使ってゾーン判定します。\n"
            "⑤ それでもゾーンに入らない場合は判定不可とします。\n\n"
            "【補助分類】\n"
            "通常ゾーンに入らない場合は、全体位置関係から東西南北の補助分類を行います。"
        )
        self.logic_scroll = QScrollArea()
        self.logic_scroll.setWidgetResizable(True)
        self.logic_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.logic_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.logic_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        logic_inner = QWidget()
        logic_inner_layout = QVBoxLayout(logic_inner)
        logic_inner_layout.setContentsMargins(4, 4, 4, 4)
        logic_inner_layout.setSpacing(0)

        self.logic_label = QLabel(logic_text)
        self.logic_label.setWordWrap(True)
        self.logic_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.logic_label.setStyleSheet("font-size:11px;color:#baf7de; line-height:1.35;")
        logic_inner_layout.addWidget(self.logic_label)
        logic_inner_layout.addStretch(1)

        self.logic_scroll.setWidget(logic_inner)
        ll.addWidget(self.logic_scroll, 1)
        rf.addWidget(logic, 1)
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
            QFrame#card, QFrame#telemetry, QFrame#step, QFrame#logicCard { background:#0d1714; border:1px solid #1f4a38; border-radius:10px; }
            QFrame#telemetry { border:1px solid #35ffd5; }
            QLabel#title { font-size:22px; font-weight:800; color:#e9fff4; }
            QLabel#panelTitle { font-size:14px; font-weight:800; color:#73ffe1; }
            QLabel#cyTitle { font-size:18px; font-weight:900; color:#73ffe1; }
            QLabel#big { font-size:20px; font-weight:800; color:#d4ff7d; }
            QPushButton { background:#123325; border:1px solid #00ff99; border-radius:8px; padding:6px 10px; color:#edfff6; }
            QPushButton:checked { background:#1f5f47; border:2px solid #72ffe2; }
            QPushButton:hover { background:#20543c; }
            QPushButton#zoneCard { text-align:left; }
            QPushButton#zoneCard[selected="true"] { background:#2f6b58; border:2px solid #95ffe9; }
            QPushButton#zoneCard[selected="false"] { background:#123325; border:1px solid #00ff99; }
            QLabel#zoneText { font-size:15px; font-weight:700; color:#d8fff2; }
            QLabel#zoneTitle { font-size:16px; font-weight:900; color:#e8fff6; }
            QPushButton#zoneCard[selected="true"] QLabel#zoneTitle { color:#fff58a; }
            QPlainTextEdit { background:#0a120f; border:1px solid #1f4a38; }
            QScrollArea { background:#0d1714; }
            QScrollBar:vertical { background:#08110d; width:8px; margin:0; }
            QScrollBar::handle:vertical { background:#2fa182; border-radius:4px; min-height:24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            """
        )

    def append_log_line(self, text: str) -> None:
        line = _normalize_log_line(text)
        if not line or line == self._last_log:
            return
        self._last_log = line
        self.log.setPlainText(line)

    def append_uiinfo(self, text: str) -> None:
        self.append_log_line(f"[UIINFO] {text}")

    def append_uiwarn(self, text: str) -> None:
        self.append_log_line(f"[UIWARN] {text}")

    def append_uierror(self, text: str) -> None:
        self.append_log_line(f"[UIERROR] {text}")

    def recount_target_csvs(self) -> int:
        return 0 if not self.input_folder else fast_count_csv_files(str(self.input_folder), self.chk_recursive.isChecked())

    def _recalc_csv_count(self) -> None:
        self.total_files = self.recount_target_csvs()
        self._refresh_progress()
        self._update_run_state()

    def _update_run_state(self) -> None:
        running = self.is_running
        self.btn_run.setEnabled((not running) and self.input_folder is not None and self.zoning_file is not None and self.total_files > 0)
        self.set_step_controls_enabled(not running)
        self.set_zone_cards_enabled(True)
        if not running:
            self.btn_open.setEnabled(self.output_csv is not None and self.output_csv.exists())

    def set_step_controls_enabled(self, enabled: bool) -> None:
        for w in [
            self.btn_pick_folder,
            self.chk_recursive,
            self.btn_pick_zoning,
            self.btn_run,
            self.btn_open,
        ]:
            w.setEnabled(enabled)
        if enabled:
            self.btn_run.setEnabled(self.input_folder is not None and self.zoning_file is not None and self.total_files > 0)
            self.btn_open.setEnabled(self.output_csv is not None and self.output_csv.exists())

    def set_zone_cards_enabled(self, enabled: bool) -> None:
        for c in self.zone_cards.values():
            c.setEnabled(enabled)

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
        self._update_output_path()
        self.total_files = self.recount_target_csvs()
        self.append_uiinfo(f"対象CSV数: {self.total_files:,}")
        self._refresh_progress()
        self._update_run_state()

    def build_zone_cards(self) -> None:
        while self.card_grid.count():
            item = self.card_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        ordered = list(self.zone_shapes.keys()) + list(AUX_ZONE_NAMES)
        self.zone_cards.clear()
        self.zone_hit_counts = {name: 0 for name in ordered}
        for idx, zone_name in enumerate(ordered):
            card = ZoneCard(zone_name)
            card.setObjectName("zoneCard")
            card.clicked.connect(lambda _=False, n=zone_name: self.on_zone_card_clicked(n))
            self.zone_cards[zone_name] = card
            self.card_grid.addWidget(card, idx // 3, idx % 3)
        self.highlight_selected_card(self.selected_zone_name)
        self.lbl_zone_count.setText(f"ゾーン数: {len(self.zone_shapes):,}")

    def highlight_selected_card(self, zone_name: str) -> None:
        for n, c in self.zone_cards.items():
            selected = n == zone_name
            c.setChecked(selected)
            c.setProperty("selected", "true" if selected else "false")
            c.lbl_title.setProperty("selected", "true" if selected else "false")
            c.style().unpolish(c)
            c.style().polish(c)
            c.lbl_title.style().unpolish(c.lbl_title)
            c.lbl_title.style().polish(c.lbl_title)

    def on_zone_card_clicked(self, zone_name: str) -> None:
        self._debug_logger.debug("[DEBUG] card clicked: %s", zone_name)
        self.selected_zone_name = zone_name
        self.highlight_selected_card(zone_name)
        self.render_zone_on_map(zone_name)

    def _build_zone_aliases(self) -> None:
        self.zone_shape_aliases = {}
        for name in self.zone_shapes:
            key = self._normalize_zone_key(name)
            if key:
                self.zone_shape_aliases[key] = name

    @staticmethod
    def _normalize_zone_key(name: str) -> str:
        return re.sub(r"\s+", "", (name or "").strip())

    def _resolve_zone_points(self, zone_name: str) -> list[tuple[float, float]]:
        if zone_name in self.zone_shapes:
            return self.zone_shapes[zone_name]
        alias_name = self.zone_shape_aliases.get(self._normalize_zone_key(zone_name), "")
        return self.zone_shapes.get(alias_name, []) if alias_name else []

    def is_aux_direction_zone(self, zone_name: str) -> bool:
        return zone_name in AUX_ZONE_NAMES

    def get_global_zone_bounds(self) -> tuple[float, float, float, float] | None:
        points = [pt for poly in self.zone_shapes.values() for pt in poly]
        if not points:
            return None
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        return min_lon, max_lon, min_lat, max_lat

    @staticmethod
    def build_sector_polygon(
        center_lon: float,
        center_lat: float,
        radius: float,
        start_deg: float,
        end_deg: float,
        step_deg: float = 5.0,
    ) -> list[tuple[float, float]]:
        pts = [(center_lon, center_lat)]
        s = float(start_deg)
        e = float(end_deg)
        if e < s:
            e += 360.0
        ang = s
        while ang <= e + 1e-9:
            rad = math.radians(ang)
            lon = center_lon + radius * math.cos(rad)
            lat = center_lat + radius * math.sin(rad)
            pts.append((lon, lat))
            ang += step_deg
        rad = math.radians(e)
        pts.append((center_lon + radius * math.cos(rad), center_lat + radius * math.sin(rad)))
        pts.append((center_lon, center_lat))
        return pts

    def build_aux_sector(self, zone_name: str) -> tuple[list[tuple[float, float]], tuple[float, float]]:
        bounds = self.get_global_zone_bounds()
        if not bounds:
            return [], (0.0, 0.0)
        min_lon, max_lon, min_lat, max_lat = bounds
        center_lon = (min_lon + max_lon) / 2.0
        center_lat = (min_lat + max_lat) / 2.0
        radius = max(max_lon - min_lon, max_lat - min_lat) * 0.9
        angle_map = {
            "東方面": (-45.0, 45.0),
            "北方面": (45.0, 135.0),
            "西方面": (135.0, 225.0),
            "南方面": (-135.0, -45.0),
        }
        if zone_name not in angle_map:
            return [], (center_lon, center_lat)
        start_deg, end_deg = angle_map[zone_name]
        sector = self.build_sector_polygon(center_lon, center_lat, radius, start_deg, end_deg, step_deg=5.0)
        return sector, (center_lon, center_lat)

    def render_zone_on_map(self, zone_name: str) -> None:
        if self.is_aux_direction_zone(zone_name):
            self.render_aux_direction_map(zone_name)
            return

        points = self._resolve_zone_points(zone_name)
        self._debug_logger.debug("[DEBUG] polygon points: %d", len(points))
        if len(points) < 3:
            msg = "ゾーン定義が見つかりません"
            self.map_widget.set_normal_zone(zone_name, [], msg)
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")
            self.map_widget.update()
            return

        valid_points: list[tuple[float, float]] = []
        for lon, lat in points:
            if not (isinstance(lon, (int, float)) and isinstance(lat, (int, float))):
                continue
            valid_points.append((float(lon), float(lat)))

        if len(valid_points) < 3:
            self.map_widget.set_normal_zone(zone_name, [], "ゾーンポリゴンを表示できません")
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")
            self.map_widget.update()
            return

        self.map_widget.set_normal_zone(zone_name, valid_points)
        self.map_widget.update()
        if WEBENGINE_AVAILABLE and self.web_map is not None:
            self._render_web_map(zone_name, valid_points)
        else:
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")

    def render_aux_direction_map(self, zone_name: str) -> None:
        all_zone_polygons = [poly for poly in self.zone_shapes.values() if len(poly) >= 3]
        if not all_zone_polygons:
            self.map_widget.set_aux_direction_zone(zone_name, [], [], (0.0, 0.0), "ゾーンポリゴンを表示できません")
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")
            return

        sector_points, center_point = self.build_aux_sector(zone_name)
        if len(sector_points) < 3:
            self.map_widget.set_aux_direction_zone(zone_name, [], all_zone_polygons, center_point, "方面エリアを表示できません")
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")
            return

        self.map_widget.set_aux_direction_zone(zone_name, sector_points, all_zone_polygons, center_point)
        if WEBENGINE_AVAILABLE and self.web_map is not None:
            self._render_web_map_aux(zone_name, sector_points, all_zone_polygons, center_point)
        else:
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")

    def _render_web_map(self, zone_name: str, points: list[tuple[float, float]]) -> None:
        if self.web_map is None or not points:
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")
            return
        try:
            lat_center = sum(lat for _, lat in points) / len(points)
            lon_center = sum(lon for lon, _ in points) / len(points)
            coords = ",\n".join(f"[{lat}, {lon}]" for lon, lat in points)
            safe_zone_name = escape(zone_name)
            html = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html, body, #map {{ height:100%; margin:0; background:#fff; }}
.ttl {{
  position:absolute; z-index:1000; left:8px; bottom:8px;
  background:rgba(255,255,255,0.92);
  padding:6px 10px; border-radius:6px; font-weight:700;
  box-shadow: 0 1px 4px rgba(0,0,0,0.25);
}}
</style>
</head>
<body>
<div class="ttl">{safe_zone_name}</div>
<div id="map"></div>
<script>
const map = L.map('map').setView([{lat_center}, {lon_center}], 13);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap'
}}).addTo(map);

const pts = [{coords}];
const poly = L.polygon(pts, {{
  color: '#00a3a3',
  weight: 3,
  fillOpacity: 0.25
}}).addTo(map);

map.fitBounds(poly.getBounds(), {{ padding: [20, 20] }});
</script>
</body>
</html>
"""
            self.web_map.setHtml(html, QUrl("https://unpkg.com/"))
            self.map_stack.setCurrentWidget(self.web_map)
            self.lbl_map_mode.setText("地図表示: WEB")
        except Exception:
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")
            self.append_uiwarn("ベースマップ取得に失敗したため簡易地図表示に切替")

    def _render_web_map_aux(
        self,
        zone_name: str,
        sector_points: list[tuple[float, float]],
        all_zone_polygons: list[list[tuple[float, float]]],
        center_point: tuple[float, float],
    ) -> None:
        if self.web_map is None or not sector_points or not all_zone_polygons:
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")
            return
        try:
            safe_zone_name = escape(f"{zone_name}（補助分類エリア）")
            zone_coords = []
            for zone_poly in all_zone_polygons:
                coord = ", ".join(f"[{lat}, {lon}]" for lon, lat in zone_poly)
                zone_coords.append(f"[{coord}]")
            all_zone_js = ",\n".join(zone_coords)
            sector_js = ",\n".join(f"[{lat}, {lon}]" for lon, lat in sector_points)
            html = f"""
<!doctype html>
<html>
<head>
<meta charset=\"utf-8\"/>
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>
<link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\"/>
<script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
<style>
html, body, #map {{ height:100%; margin:0; background:#fff; }}
.ttl {{
  position:absolute; z-index:1000; left:8px; bottom:8px;
  background:rgba(255,255,255,0.92);
  padding:6px 10px; border-radius:6px; font-weight:700;
  box-shadow: 0 1px 4px rgba(0,0,0,0.25);
}}
.lg {{
  position:absolute; z-index:1000; right:8px; top:8px;
  background:rgba(255,255,255,0.92); padding:6px 10px; border-radius:6px; font-size:12px;
}}
</style>
</head>
<body>
<div class=\"ttl\">{safe_zone_name}</div>
<div class=\"lg\">赤線: 指定ゾーニング<br/>青塗: 指定ゾーン外の方面補助分類</div>
<div id=\"map\"></div>
<script>
const map = L.map('map').setView([{center_point[1]}, {center_point[0]}], 12);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap'
}}).addTo(map);

const zones = [{all_zone_js}];
const group = L.featureGroup().addTo(map);
const sector = [{sector_js}];
const sectorWithHoles = [sector, ...zones];
L.polygon(sectorWithHoles, {{
  color: '#1d4ed8',
  weight: 3,
  fillColor: '#1d4ed8',
  fillOpacity: 0.35,
  interactive: false
}}).addTo(group);

for (const z of zones) {{
  L.polygon(z, {{
    color: '#cc1f1f',
    weight: 2,
    fill: false
  }}).addTo(group);
}}

L.circleMarker([{center_point[1]}, {center_point[0]}], {{ color:'#1d4ed8', fillColor:'#1d4ed8', radius:5 }}).addTo(group).bindTooltip('中心', {{permanent:true, direction:'right'}});

map.fitBounds(group.getBounds(), {{ padding: [20, 20] }});
</script>
</body>
</html>
"""
            self.web_map.setHtml(html, QUrl("https://unpkg.com/"))
            self.map_stack.setCurrentWidget(self.web_map)
            self.lbl_map_mode.setText("地図表示: WEB")
        except Exception:
            self.map_stack.setCurrentWidget(self.map_widget)
            self.lbl_map_mode.setText("地図表示: SIMPLE")
            self.append_uiwarn("ベースマップ取得に失敗したため簡易地図表示に切替")

    def increment_zone_hit_count(self, zone_name: str) -> None:
        resolved_name = zone_name
        if resolved_name not in self.zone_cards:
            resolved_name = self.zone_shape_aliases.get(self._normalize_zone_key(zone_name), zone_name)
        next_count = self.zone_hit_counts.get(resolved_name, 0) + 1
        self.update_zone_card(resolved_name, next_count)

    def update_zone_card(self, zone_name: str, count: int) -> None:
        resolved_name = zone_name
        if resolved_name not in self.zone_cards:
            resolved_name = self.zone_shape_aliases.get(self._normalize_zone_key(zone_name), zone_name)
        self.zone_hit_counts[resolved_name] = count
        if resolved_name in self.zone_cards:
            card = self.zone_cards[resolved_name]
            card.set_count(count)
            card.update()

    def pick_zoning(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "任意ゾーニングファイルを選択", "", "CSV (*.csv)")
        if not p:
            return
        self.zoning_file = Path(p)
        self.lbl_zoning.setText(str(self.zoning_file))
        self.zone_shapes = parse_zone_shapes(self.zoning_file)
        self._build_zone_aliases()
        self.build_zone_cards()
        if self.zone_shapes:
            self.on_zone_card_clicked(next(iter(self.zone_shapes.keys())))
        else:
            self.map_widget.set_normal_zone("", [], "ゾーンポリゴンを表示できません")
        self._update_run_state()

    def _refresh_progress(self) -> None:
        pct = (self.done_files / self.total_files * 100.0) if self.total_files else 0.0
        self.progress.setValue(int(pct))
        self.lbl_progress.setText(f"進捗ファイル: {self.done_files:,}/{self.total_files:,} ({pct:.1f}%)")

    def start_run(self) -> None:
        if not self.input_folder or not self.zoning_file:
            return
        self._update_output_path()
        self.total_files = len(build_processing_file_list(str(self.input_folder), self.chk_recursive.isChecked()))
        if self.total_files <= 0:
            QMessageBox.warning(self, "入力不足", "対象CSVがありません。")
            return
        self.done_files = 0
        self.hit_count = 0
        self._counted_ops.clear()
        self._last_hit_milestone = 0
        self._last_progress_milestone = 0
        for n in self.zone_hit_counts:
            self.update_zone_card(n, 0)
        self._refresh_progress()
        self.log.clear(); self._last_log = ""
        self.run_started_at = time.time()
        self.is_running = True
        self.lbl_status.setText("状態: RUNNING")
        self.radar.set_running(True)
        self.btn_open.setEnabled(False)
        self.set_step_controls_enabled(False)
        self.set_zone_cards_enabled(True)

        script = Path(__file__).with_name("03_base_zone_estimator.py")
        args = [str(script), "--input", str(self.input_folder), "--zoning", str(self.zoning_file)]
        if self.output_csv:
            args += ["--output", str(self.output_csv)]
        if self.chk_recursive.isChecked():
            args.append("--recursive")

        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        self.proc.setProcessEnvironment(env)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(["-X", "utf8"] + args)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.proc.start()
        self._update_run_state()
        self.append_uiinfo(f"解析開始 / 対象CSV数={self.total_files:,} / ゾーン数={len(self.zone_shapes):,}")

    def _process_log_line(self, line: str) -> None:
        if m := RE_TOTAL.search(line):
            self.total_files = int(m.group(1))
            self._refresh_progress()
        if m := RE_PROGRESS.search(line):
            self.done_files = int(m.group(1)); self.total_files = int(m.group(2))
            if m.group(3):
                self.current_file = _normalize_log_line(m.group(3))
            self._refresh_progress()
            if self.total_files > 0:
                milestone = (int((self.done_files / self.total_files) * 100) // 10) * 10
                if milestone >= 10 and milestone > self._last_progress_milestone:
                    self._last_progress_milestone = milestone
                    self.append_uiinfo(f"進捗 {milestone}%")
        if m := RE_HIT.search(line):
            op_id, zone = m.group(1), _normalize_log_line(m.group(2))
            if op_id not in self._counted_ops:
                self._counted_ops.add(op_id)
                self.hit_count += 1
                self.increment_zone_hit_count(zone)
            self.lbl_hit.setText(f"正常HIT: {self.hit_count:,}")
            milestone = (self.hit_count // 100) * 100
            if milestone >= 100 and milestone > self._last_hit_milestone:
                self._last_hit_milestone = milestone
                self.append_uiinfo(f"HIT累積={milestone:,}")
        if m := RE_HIT_AUX.search(line):
            op_id, zone = m.group(1), _normalize_log_line(m.group(2))
            if op_id not in self._counted_ops:
                self._counted_ops.add(op_id)
                self.hit_count += 1
                self.increment_zone_hit_count(zone)
            self.lbl_hit.setText(f"正常HIT: {self.hit_count:,}")
        if m := RE_ZONE_COUNT.search(line):
            self.lbl_zone_count.setText(f"ゾーン数: {int(m.group(1)):,}")

    def on_output(self) -> None:
        if not self.proc:
            return
        raw = bytes(self.proc.readAllStandardOutput())
        data = raw.decode("utf-8", errors="replace")
        rows = (self._stdout_buffer + data).split("\n")
        self._stdout_buffer = rows.pop() if rows else ""
        for raw in rows:
            line = _normalize_log_line(raw)
            if not line:
                continue
            if line.startswith("[UIINFO]") or line.startswith("[UIWARN]") or line.startswith("[UIERROR]"):
                self.append_log_line(line)
            elif line.startswith("[ERROR]"):
                self.append_uierror(line[7:].strip())
            self._process_log_line(line)

    def _tick(self) -> None:
        self.radar.tick()
        running = self.is_running
        if running and self.run_started_at:
            elapsed = int(time.time() - self.run_started_at)
            self.lbl_elapsed.setText(f"経過 {format_hhmmss(elapsed)}")
            if self.done_files > 0 and self.total_files > 0:
                eta = int((elapsed / self.done_files) * max(self.total_files - self.done_files, 0))
                self.lbl_remaining.setText(f"残り {format_hhmmss(eta)}")
            else:
                self.lbl_remaining.setText("残り --:--:--")

    def on_finished(self, code: int, _status) -> None:
        self.is_running = False
        if self._stdout_buffer:
            line = _normalize_log_line(self._stdout_buffer)
            self._process_log_line(line)
        self._stdout_buffer = ""
        self.lbl_status.setText("状態: DONE" if code == 0 else "状態: ERROR")
        self.radar.set_running(False)
        self.done_files = max(self.done_files, self.total_files if code == 0 else self.done_files)
        self._refresh_progress()
        self.btn_open.setEnabled(code == 0 and self.output_csv is not None and self.output_csv.exists())
        self.set_step_controls_enabled(True)
        self.set_zone_cards_enabled(True)
        self._update_run_state()
        if code == 0:
            self.append_uiinfo(f"解析完了 / 正常HIT={self.hit_count:,}")
        else:
            self.append_uierror(f"解析失敗 / code={code}")

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
