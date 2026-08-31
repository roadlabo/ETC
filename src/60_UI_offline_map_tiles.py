from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QProcess, Qt, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
LOGO_FILENAME = "logo_60_offline_map_tiles.png"
GSI_TEST_URL = "https://cyberjapandata.gsi.go.jp/xyz/pale/9/451/198.png"
OFFLINE_MESSAGE = "現在、オフライン環境です。インターネット環境で立ち上げなおしてください。"


def can_connect_to_gsi(timeout: float = 3.0) -> bool:
    try:
        request = Request(GSI_TEST_URL, headers={"User-Agent": "ETC2-Analyzer/1.0"})
        with urlopen(request, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 200) < 400
    except (HTTPError, URLError, TimeoutError, OSError):
        return False
    except Exception:
        return False


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.process: QProcess | None = None
        self.setWindowTitle("60_オフライン地図作成")
        self.resize(980, 720)
        self._set_icon()
        self._build_ui()
        self._set_style()
        QTimer.singleShot(200, self.check_network_on_startup)

    def _set_icon(self) -> None:
        logo_path = SRC_DIR / "assets" / "logos" / LOGO_FILENAME
        if logo_path.exists():
            self.setWindowIcon(QIcon(str(logo_path)))

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        logo = QLabel()
        logo_path = SRC_DIR / "assets" / "logos" / LOGO_FILENAME
        if logo_path.exists():
            pix = QPixmap(str(logo_path)).scaledToHeight(88, Qt.TransformationMode.SmoothTransformation)
            logo.setPixmap(pix)
        title_box = QVBoxLayout()
        title = QLabel("スタンドアロン用地図データ作成")
        title.setObjectName("title")
        subtitle = QLabel("インターネットがない場所でも、指定した範囲だけ背景地図を表示できるようにします。")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addWidget(logo)
        header.addLayout(title_box, 1)
        outer.addLayout(header)

        note = QLabel(
            "通常、津山市街地用の地図データはすでに同梱済みです。この画面は、津山以外の地域や、"
            "別の調査範囲を追加したい場合に使います。地図を残したい範囲を示すGeoJSONファイルを選ぶと、"
            "国土地理院の淡色地図タイルを src/tiles/gsi_pale に保存します。"
        )
        note.setWordWrap(True)
        note.setObjectName("note")
        outer.addWidget(note)

        form = QFrame()
        form.setObjectName("panel")
        grid = QGridLayout(form)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self.boundary = QLineEdit()
        self.boundary.setPlaceholderText("例: D:\\data\\調査範囲.geojson")
        btn_boundary = QPushButton("選択")
        btn_boundary.clicked.connect(self.pick_boundary)
        btn_tsuyama = QPushButton("同梱の津山市街地範囲を使う")
        btn_tsuyama.clicked.connect(self.use_tsuyama_boundary)

        boundary_row = QHBoxLayout()
        boundary_row.addWidget(self.boundary, 1)
        boundary_row.addWidget(btn_boundary)
        boundary_row.addWidget(btn_tsuyama)

        self.min_zoom = QSpinBox()
        self.min_zoom.setRange(1, 20)
        self.min_zoom.setValue(9)
        self.max_zoom = QSpinBox()
        self.max_zoom.setRange(1, 20)
        self.max_zoom.setValue(18)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("最小"))
        zoom_row.addWidget(self.min_zoom)
        zoom_row.addSpacing(18)
        zoom_row.addWidget(QLabel("最大"))
        zoom_row.addWidget(self.max_zoom)
        zoom_row.addStretch(1)

        self.reuse = QLineEdit()
        self.reuse.setPlaceholderText("既存の z/x/y.png タイルがある場合だけ指定します。空欄でOKです。")
        btn_reuse = QPushButton("選択")
        btn_reuse.clicked.connect(self.pick_reuse)
        reuse_row = QHBoxLayout()
        reuse_row.addWidget(self.reuse, 1)
        reuse_row.addWidget(btn_reuse)

        self.output = QLabel(str(SRC_DIR / "tiles" / "gsi_pale"))
        self.output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._add_row(grid, 0, "地図を残したい範囲", self._wrap(boundary_row), "Polygon / MultiPolygon形式のGeoJSONを指定します。市町村界や調査区域の境界ファイルです。")
        self._add_row(grid, 1, "ズーム範囲", self._wrap(zoom_row), "標準は9から18です。広い範囲で18まで作ると容量が大きくなります。")
        self._add_row(grid, 2, "既存タイル再利用", self._wrap(reuse_row), "過去に作った地図タイルがある場合だけ指定します。同じタイルはダウンロードせず再利用します。")
        self._add_row(grid, 3, "保存先", self.output, "ここに入ったタイルが、オフライン時の背景地図として自動的に使われます。")
        outer.addWidget(form)

        actions = QHBoxLayout()
        self.run_btn = QPushButton("作成開始")
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
        self.log.setPlaceholderText("実行状況がここに表示されます。")
        outer.addWidget(self.log, 1)
        self.append_log("準備完了。津山市街地だけで使う場合は、追加作成は不要です。")

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
            QLineEdit, QSpinBox, QPlainTextEdit {
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

    def pick_boundary(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "範囲GeoJSONを選択", str(ROOT_DIR), "GeoJSON (*.geojson *.json);;All files (*.*)")
        if path:
            self.boundary.setText(path)

    def use_tsuyama_boundary(self) -> None:
        self.boundary.setText(str(SRC_DIR / "tsuyama_urban_area.geojson"))
        self.append_log("同梱の津山市街地範囲を選択しました。既存タイルの確認だけなら短時間で終わります。")

    def pick_reuse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "既存タイルのフォルダーを選択", str(ROOT_DIR))
        if path:
            self.reuse.setText(path)

    def open_output(self) -> None:
        out = SRC_DIR / "tiles" / "gsi_pale"
        out.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(out)  # type: ignore[attr-defined]

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def start_download(self) -> None:
        if not can_connect_to_gsi():
            self.run_btn.setEnabled(False)
            self.append_log(OFFLINE_MESSAGE)
            QMessageBox.warning(self, "オフライン環境", OFFLINE_MESSAGE)
            return

        boundary = Path(self.boundary.text().strip().strip('"'))
        if not boundary.is_file():
            QMessageBox.warning(self, "範囲ファイルがありません", "地図を残したい範囲のGeoJSONファイルを選択してください。")
            return
        if self.min_zoom.value() > self.max_zoom.value():
            QMessageBox.warning(self, "ズーム範囲を確認してください", "最小ズームは最大ズーム以下にしてください。")
            return
        reuse_text = self.reuse.text().strip().strip('"')
        if reuse_text and not Path(reuse_text).exists():
            QMessageBox.warning(self, "再利用元がありません", "既存タイル再利用フォルダーが見つかりません。空欄にするか、正しいフォルダーを選択してください。")
            return

        script = SRC_DIR / "download_offline_tiles.py"
        args = [
            str(script),
            str(boundary),
            "--output",
            str(SRC_DIR / "tiles" / "gsi_pale"),
            "--min-zoom",
            str(self.min_zoom.value()),
            "--max-zoom",
            str(self.max_zoom.value()),
        ]
        if reuse_text:
            args.extend(["--reuse", reuse_text])

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
        self.append_log(f"範囲: {boundary}")
        self.append_log(f"ズーム: {self.min_zoom.value()} - {self.max_zoom.value()}")
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
