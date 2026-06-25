from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover
    QWebEngineView = None

PERF_PATH = SRC_DIR / "30_route_performance.py"
if not PERF_PATH.exists():
    PERF_PATH = SRC_DIR / "unreleased" / "30_route_performance.py"
spec = importlib.util.spec_from_file_location("route_performance30", PERF_PATH)
perf = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = perf
spec.loader.exec_module(perf)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("30 Route Performance Viewer")
        self.resize(1180, 780)
        self.viewer_path: Path | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        title = QLabel("30 Route Performance Viewer")
        title.setObjectName("title")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("30 を実行済みのプロジェクト内にある 30_route_performance 出力フォルダを選択")
        choose = QPushButton("フォルダ変更")
        choose.clicked.connect(self.choose_output_dir)
        rebuild = QPushButton("ビューアー再生成")
        rebuild.clicked.connect(self.rebuild_viewer)
        open_external = QPushButton("外部ブラウザで開く")
        open_external.clicked.connect(self.open_external)
        row.addWidget(self.output_edit, 1)
        row.addWidget(choose)
        row.addWidget(rebuild)
        row.addWidget(open_external)
        layout.addLayout(row)

        self.status = QLabel("30 を一度実行した後の 30_route_performance 出力フォルダを選択してください。")
        layout.addWidget(self.status)

        if QWebEngineView is not None:
            self.web = QWebEngineView()
            layout.addWidget(self.web, 1)
        else:
            self.web = None
            layout.addWidget(QLabel("PyQt6-WebEngine が無い場合は外部ブラウザで確認してください。"), 1)

        self.setStyleSheet(
            """
            QWidget { background:#0b0f14; color:#e6f1ff; font-family:"Segoe UI","Meiryo UI"; font-size:12px; }
            QLabel#title { color:#00ff99; font-size:22px; font-weight:800; }
            QLineEdit { background:#020617; color:#e6f1ff; border:1px solid #334155; border-radius:6px; padding:7px; }
            QPushButton { border:1px solid #00ff99; border-radius:7px; padding:7px 10px; background:#10231a; color:#e6f1ff; font-weight:700; }
            QPushButton:hover { background:#16432b; }
            """
        )

    def choose_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "30_route_performance 出力フォルダを選択")
        if not path:
            return
        self.output_edit.setText(path)
        self.rebuild_viewer()

    def rebuild_viewer(self) -> None:
        output_dir = self.output_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "未選択", "30_route_performance 出力フォルダを選択してください。")
            return
        try:
            self.viewer_path = Path(perf.build_viewer_from_output(output_dir)).resolve()
        except Exception as exc:
            QMessageBox.critical(self, "ビューアー再生成失敗", str(exc))
            return
        self.status.setText(f"ビューアーを再生成しました: {self.viewer_path}")
        if self.web is not None:
            self.web.load(QUrl.fromLocalFile(str(self.viewer_path)))

    def open_external(self) -> None:
        if self.viewer_path is None:
            self.rebuild_viewer()
        if self.viewer_path is None:
            return
        subprocess.Popen([sys.executable, "-m", "webbrowser", str(self.viewer_path)])


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
