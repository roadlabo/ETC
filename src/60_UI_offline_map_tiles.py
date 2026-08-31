from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, QProcess, QTimer, QUrl, Qt, pyqtSlot
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

SRC_DIR = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent
TILE_DIR = SRC_DIR / "tiles" / "gsi_pale"
LOGO_FILENAME = "logo_60_offline_map_tiles.png"
GSI_TEST_URL = "https://cyberjapandata.gsi.go.jp/xyz/pale/9/451/198.png"
OFFLINE_MESSAGE = "現在、オフライン環境です。インターネット環境で立ち上げなおしてください。"
AREA_TOO_LARGE_MESSAGE = "データ量が大きくなります。もう一度狭い範囲で再指定してください。"
MAX_ESTIMATED_TILES = 50_000


def can_connect_to_gsi(timeout: float = 3.0) -> bool:
    try:
        request = Request(GSI_TEST_URL, headers={"User-Agent": "ETC2-Analyzer/1.0"})
        with urlopen(request, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 200) < 400
    except (HTTPError, URLError, TimeoutError, OSError):
        return False
    except Exception:
        return False


def rectangle_geojson(bounds: tuple[float, float, float, float]) -> dict:
    south, west, north, east = bounds
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "offline_tile_area"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [west, south],
                        [east, south],
                        [east, north],
                        [west, north],
                        [west, south],
                    ]],
                },
            }
        ],
    }


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = (1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n
    return x, y


def estimate_tile_count(bounds: tuple[float, float, float, float], min_zoom: int, max_zoom: int) -> int:
    south, west, north, east = bounds
    total = 0
    for zoom in range(min_zoom, max_zoom + 1):
        x1, y1 = lonlat_to_tile(west, north, zoom)
        x2, y2 = lonlat_to_tile(east, south, zoom)
        min_x, max_x = sorted((math.floor(x1), math.floor(x2)))
        min_y, max_y = sorted((math.floor(y1), math.floor(y2)))
        total += (max_x - min_x + 1) * (max_y - min_y + 1)
        if total > MAX_ESTIMATED_TILES:
            return total
    return total


class MapBridge(QObject):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window

    @pyqtSlot(float, float, float, float)
    def setBounds(self, south: float, west: float, north: float, east: float) -> None:
        self.window.set_map_bounds(south, west, north, east)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.process: QProcess | None = None
        self.map_bounds: tuple[float, float, float, float] | None = None
        self.setWindowTitle("60_オフライン地図作成")
        self.resize(1280, 860)
        self._set_icon()
        self._build_ui()
        self._set_style()
        self._load_map()
        QTimer.singleShot(200, self.check_network_on_startup)

    def _set_icon(self) -> None:
        logo_path = SRC_DIR / "assets" / "logos" / LOGO_FILENAME
        if logo_path.exists():
            self.setWindowIcon(QIcon(str(logo_path)))

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        logo = QLabel()
        logo_path = SRC_DIR / "assets" / "logos" / LOGO_FILENAME
        if logo_path.exists():
            pix = QPixmap(str(logo_path)).scaledToHeight(78, Qt.TransformationMode.SmoothTransformation)
            logo.setPixmap(pix)
        title_box = QVBoxLayout()
        title = QLabel("スタンドアロン用地図データ作成")
        title.setObjectName("title")
        subtitle = QLabel("地図を移動してから、範囲指定モードで保存したい範囲をドラッグしてください。")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addWidget(logo)
        header.addLayout(title_box, 1)
        outer.addLayout(header)

        note = QLabel(
            "津山市街地用の地図データは同梱済みです。津山以外の地域や別の調査範囲を追加したい場合は、"
            "下の地図を移動・拡大し、範囲指定モードで必要な範囲をドラッグしてから作成開始してください。"
            "保存先は固定で ETC\\src\\tiles\\gsi_pale です。"
        )
        note.setWordWrap(True)
        note.setObjectName("note")
        outer.addWidget(note)

        map_panel = QFrame()
        map_panel.setObjectName("panel")
        map_layout = QVBoxLayout(map_panel)
        map_layout.setContentsMargins(10, 10, 10, 10)
        map_help = QLabel("操作: 「地図を動かす」で位置調整、「範囲を指定」で地図上をドラッグ。赤枠の内側が保存対象です。")
        map_help.setObjectName("help")
        self.map_view = QWebEngineView()
        self.map_view.setMinimumHeight(390)
        self.bounds_label = QLabel("選択範囲: 未指定")
        self.bounds_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        map_layout.addWidget(map_help)
        map_layout.addWidget(self.map_view, 1)
        map_layout.addWidget(self.bounds_label)
        outer.addWidget(map_panel, 1)

        settings = QFrame()
        settings.setObjectName("panel")
        grid = QGridLayout(settings)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self.min_zoom = QSpinBox()
        self.min_zoom.setRange(1, 20)
        self.min_zoom.setValue(9)
        self.max_zoom = QSpinBox()
        self.max_zoom.setRange(1, 20)
        self.max_zoom.setValue(18)
        self.min_zoom.valueChanged.connect(self.refresh_bounds_label)
        self.max_zoom.valueChanged.connect(self.refresh_bounds_label)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("最小"))
        zoom_row.addWidget(self.min_zoom)
        zoom_row.addSpacing(18)
        zoom_row.addWidget(QLabel("最大"))
        zoom_row.addWidget(self.max_zoom)
        zoom_row.addStretch(1)

        output = QLabel(str(TILE_DIR))
        output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._add_row(grid, 0, "ズーム範囲", self._wrap(zoom_row), "標準は9から18です。範囲が広いほど時間と容量が大きくなります。")
        self._add_row(grid, 1, "保存先", output, "固定です。ETC2.0アナライザーの各地図画面は、オフライン時にこのフォルダーを自動で読みます。")
        outer.addWidget(settings)

        actions = QHBoxLayout()
        self.run_btn = QPushButton("指定した範囲で作成開始")
        self.run_btn.clicked.connect(self.start_download)
        self.cancel_btn = QPushButton("中止")
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.cancel_btn.setEnabled(False)
        self.open_btn = QPushButton("保存先を開く")
        self.open_btn.clicked.connect(self.open_output)
        actions.addWidget(self.run_btn)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.open_btn)
        actions.addStretch(1)
        outer.addLayout(actions)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        self.log.setPlaceholderText("実行状況がここに表示されます。")
        outer.addWidget(self.log)
        self.append_log("準備完了。津山市街地だけで使う場合は、追加作成は不要です。")

    def _load_map(self) -> None:
        channel = QWebChannel(self.map_view.page())
        self.bridge = MapBridge(self)
        channel.registerObject("bridge", self.bridge)
        self.map_view.page().setWebChannel(channel)
        settings = self.map_view.page().settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.map_view.setHtml(self._map_html(), QUrl.fromLocalFile(str(SRC_DIR) + os.sep))

    def _map_html(self) -> str:
        leaflet_css = (SRC_DIR / "leaflet" / "leaflet.css").as_uri()
        leaflet_js = (SRC_DIR / "leaflet" / "leaflet.js").as_uri()
        return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="{leaflet_css}">
  <script src="{leaflet_js}"></script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .leaflet-control-attribution {{ font-size: 11px; }}
    .area-help {{
      background: rgba(255,255,255,.94);
      border: 1px solid #d8d8d8;
      border-radius: 4px;
      padding: 8px 10px;
      font: 14px sans-serif;
      color: #172018;
      box-shadow: 0 1px 5px rgba(0,0,0,.18);
    }}
    .area-tools {{
      background: rgba(255,255,255,.96);
      border: 1px solid #d8d8d8;
      border-radius: 4px;
      box-shadow: 0 1px 5px rgba(0,0,0,.18);
      padding: 6px;
      display: flex;
      gap: 6px;
      font: 14px sans-serif;
    }}
    .area-tools button {{
      border: 1px solid #b9c5bb;
      background: #fff;
      border-radius: 4px;
      padding: 6px 10px;
      cursor: pointer;
    }}
    .area-tools button.active {{
      background: #245b37;
      border-color: #245b37;
      color: #fff;
    }}
    #map.selecting {{ cursor: crosshair; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    let bridge = null;
    new QWebChannel(qt.webChannelTransport, function(channel) {{
      bridge = channel.objects.bridge;
      updateRectangle();
    }});

    const map = L.map('map', {{ center: [35.069, 134.004], zoom: 12 }});
    L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/pale/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '地理院タイル',
      maxZoom: 18
    }}).addTo(map);

    const help = L.control({{ position: 'topright' }});
    help.onAdd = function() {{
      const div = L.DomUtil.create('div', 'area-help');
      div.innerHTML = '範囲指定モードでドラッグしてください';
      return div;
    }};
    help.addTo(map);

    const tools = L.control({{ position: 'topleft' }});
    tools.onAdd = function() {{
      const div = L.DomUtil.create('div', 'area-tools');
      div.innerHTML = '<button id="panMode" class="active" type="button">地図を動かす</button><button id="selectMode" type="button">範囲を指定</button>';
      L.DomEvent.disableClickPropagation(div);
      return div;
    }};
    tools.addTo(map);

    let rectangle = null;
    let selectMode = false;
    let dragStart = null;
    let isDrawing = false;

    function setMode(mode) {{
      selectMode = mode === 'select';
      document.getElementById('panMode').classList.toggle('active', !selectMode);
      document.getElementById('selectMode').classList.toggle('active', selectMode);
      document.getElementById('map').classList.toggle('selecting', selectMode);
      if (selectMode) {{
        map.dragging.disable();
      }} else {{
        map.dragging.enable();
      }}
    }}

    document.getElementById('panMode').onclick = function() {{ setMode('pan'); }};
    document.getElementById('selectMode').onclick = function() {{ setMode('select'); }};

    function reportBounds(b) {{
      if (!bridge || !b) return;
      const sw = b.getSouthWest();
      const ne = b.getNorthEast();
      bridge.setBounds(sw.lat, sw.lng, ne.lat, ne.lng);
    }}

    function drawRectangle(b) {{
      if (!rectangle) {{
        rectangle = L.rectangle(b, {{
          color: '#d83b3b',
          weight: 3,
          fillColor: '#d83b3b',
          fillOpacity: 0.08,
          interactive: false
        }}).addTo(map);
      }} else {{
        rectangle.setBounds(b);
      }}
      reportBounds(b);
    }}

    map.on('mousedown', function(e) {{
      if (!selectMode) return;
      isDrawing = true;
      dragStart = e.latlng;
      drawRectangle(L.latLngBounds(dragStart, dragStart));
      L.DomEvent.preventDefault(e);
    }});

    map.on('mousemove', function(e) {{
      if (!selectMode || !isDrawing || !dragStart) return;
      drawRectangle(L.latLngBounds(dragStart, e.latlng));
    }});

    map.on('mouseup', function(e) {{
      if (!selectMode || !isDrawing || !dragStart) return;
      isDrawing = false;
      drawRectangle(L.latLngBounds(dragStart, e.latlng));
      dragStart = null;
    }});

    map.on('mouseout', function(e) {{
      if (!selectMode || !isDrawing || !dragStart) return;
      isDrawing = false;
      dragStart = null;
      if (rectangle) reportBounds(rectangle.getBounds());
    }});
  </script>
</body>
</html>
"""

    def check_network_on_startup(self) -> None:
        self.append_log("インターネット接続を確認しています。")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        online = can_connect_to_gsi()
        QApplication.restoreOverrideCursor()
        if online:
            self.append_log("インターネット接続を確認しました。")
            return
        self.run_btn.setEnabled(False)
        self.append_log(OFFLINE_MESSAGE)
        QMessageBox.warning(self, "オフライン環境", OFFLINE_MESSAGE)

    def set_map_bounds(self, south: float, west: float, north: float, east: float) -> None:
        self.map_bounds = (south, west, north, east)
        self.refresh_bounds_label()

    def refresh_bounds_label(self) -> None:
        if not self.map_bounds:
            self.bounds_label.setText("選択範囲: 未指定")
            return
        south, west, north, east = self.map_bounds
        tile_count = estimate_tile_count(self.map_bounds, self.min_zoom.value(), self.max_zoom.value())
        self.bounds_label.setText(
            f"選択範囲: 南 {south:.6f} / 西 {west:.6f} / 北 {north:.6f} / 東 {east:.6f}"
            f" / 概算 {tile_count:,} タイル"
        )

    def _wrap(self, layout: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _add_row(self, grid: QGridLayout, row: int, label: str, field: QWidget, help_text: str) -> None:
        label_widget = QLabel(label)
        label_widget.setObjectName("fieldLabel")
        help_widget = QLabel(help_text)
        help_widget.setWordWrap(True)
        help_widget.setObjectName("help")
        grid.addWidget(label_widget, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(field, row, 1)
        grid.addWidget(help_widget, row, 2)

    def _set_style(self) -> None:
        self.setFont(QFont("Meiryo UI", 10))
        self.setStyleSheet(
            """
            QWidget { background: #f7f8f5; color: #18201b; }
            QLabel#title { font-size: 24px; font-weight: 700; }
            QLabel#subtitle { color: #4c5b51; font-size: 12px; }
            QLabel#note {
                background: #eaf1e8;
                border: 1px solid #c8d8c4;
                border-radius: 6px;
                padding: 12px;
                color: #213327;
            }
            QFrame#panel {
                background: #ffffff;
                border: 1px solid #d7ded6;
                border-radius: 6px;
            }
            QLabel#fieldLabel { font-weight: 700; }
            QLabel#help { color: #57665d; }
            QSpinBox, QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #bfc9c0;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton {
                background: #245b37;
                color: white;
                border: 1px solid #1c462b;
                border-radius: 4px;
                padding: 7px 12px;
                font-weight: 700;
            }
            QPushButton:hover { background: #2f7146; }
            QPushButton:disabled { background: #aeb8b0; border-color: #aeb8b0; }
            """
        )

    def open_output(self) -> None:
        TILE_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(TILE_DIR)  # type: ignore[attr-defined]

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def selected_boundary_path(self) -> Path | None:
        if not self.map_bounds:
            QMessageBox.warning(self, "範囲を確認してください", "地図の読み込みが終わるまで待ってから、もう一度実行してください。")
            return None
        boundary_dir = ROOT_DIR / "logs"
        boundary_dir.mkdir(parents=True, exist_ok=True)
        path = boundary_dir / "60_offline_map_selected_area.geojson"
        path.write_text(json.dumps(rectangle_geojson(self.map_bounds), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def start_download(self) -> None:
        if not can_connect_to_gsi():
            self.run_btn.setEnabled(False)
            self.append_log(OFFLINE_MESSAGE)
            QMessageBox.warning(self, "オフライン環境", OFFLINE_MESSAGE)
            return
        if self.min_zoom.value() > self.max_zoom.value():
            QMessageBox.warning(self, "ズーム範囲を確認してください", "最小ズームは最大ズーム以下にしてください。")
            return
        if self.map_bounds and estimate_tile_count(self.map_bounds, self.min_zoom.value(), self.max_zoom.value()) > MAX_ESTIMATED_TILES:
            self.append_log(AREA_TOO_LARGE_MESSAGE)
            QMessageBox.warning(self, "範囲が広すぎます", AREA_TOO_LARGE_MESSAGE)
            return
        boundary = self.selected_boundary_path()
        if boundary is None:
            return

        script = SRC_DIR / "download_offline_tiles.py"
        args = [
            str(script),
            str(boundary),
            "--output",
            str(TILE_DIR),
            "--min-zoom",
            str(self.min_zoom.value()),
            "--max-zoom",
            str(self.max_zoom.value()),
        ]
        python = ROOT_DIR / "runtime" / "python" / "python.exe"
        program = str(python if python.exists() else Path(sys.executable))

        self.process = QProcess(self)
        self.process.setProgram(program)
        self.process.setArguments(args)
        self.process.setWorkingDirectory(str(ROOT_DIR))
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.log.clear()
        self.append_log("地図タイルの作成を開始します。国土地理院の淡色地図を取得します。")
        self.append_log(f"赤枠の範囲: {boundary}")
        self.append_log(f"ズーム: {self.min_zoom.value()} - {self.max_zoom.value()}")
        self.append_log(f"保存先: {TILE_DIR}")
        self.append_log("広い範囲では時間と容量が大きくなります。途中で止める場合は「中止」を押してください。")
        self.process.start()

    def read_stdout(self) -> None:
        if self.process:
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
            if text:
                self.append_log(text)

    def read_stderr(self) -> None:
        if self.process:
            text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip()
            if text:
                self.append_log(text)

    def finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if exit_code == 0:
            self.append_log("完了しました。オフライン時は、保存済みタイルがある範囲だけ背景地図が表示されます。")
            QMessageBox.information(self, "完了", "スタンドアロン用地図データの作成が完了しました。")
        else:
            self.append_log(f"エラーで終了しました。終了コード: {exit_code}")
            QMessageBox.warning(self, "エラー", "地図データ作成中にエラーが発生しました。画面下部の実行状況を確認してください。")
        self.process = None

    def process_error(self, error: QProcess.ProcessError) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.append_log(f"プロセスを開始できませんでした: {error.name}")

    def cancel_download(self) -> None:
        if self.process:
            self.process.kill()
            self.append_log("中止しました。")


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
