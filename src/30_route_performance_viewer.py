from __future__ import annotations

import faulthandler
import importlib.util
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QObject, Qt, QThread, QUrl, pyqtSignal, qInstallMessageHandler
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover
    QWebEngineSettings = None
    QWebEngineView = None

PERF_PATH = SRC_DIR / "30_route_performance.py"
if not PERF_PATH.exists():
    PERF_PATH = SRC_DIR / "unreleased" / "30_route_performance.py"
spec = importlib.util.spec_from_file_location("route_performance30", PERF_PATH)
perf = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = perf
spec.loader.exec_module(perf)

APP_ROOT = SRC_DIR.parent
LOG_DIR = APP_ROOT / "logs"
RUNTIME_LOG = LOG_DIR / "30_route_performance_viewer_runtime.log"
_LOG_HANDLE = None


def append_runtime_log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with RUNTIME_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {message}\n")


def install_runtime_logging() -> None:
    global _LOG_HANDLE
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_HANDLE = RUNTIME_LOG.open("a", encoding="utf-8", buffering=1)
    _LOG_HANDLE.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] viewer start\n")
    faulthandler.enable(_LOG_HANDLE, all_threads=True)

    def excepthook(exc_type, exc, tb) -> None:
        append_runtime_log("UNHANDLED PYTHON EXCEPTION\n" + "".join(traceback.format_exception(exc_type, exc, tb)))

    def qt_message_handler(mode, context, message) -> None:
        append_runtime_log(f"QT MESSAGE {mode}: {message}")

    sys.excepthook = excepthook
    qInstallMessageHandler(qt_message_handler)


class ViewerBuildWorker(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, output_dir: str) -> None:
        super().__init__()
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            append_runtime_log(f"build viewer start: {self.output_dir}")
            viewer = Path(perf.build_viewer_from_output(self.output_dir)).resolve()
        except Exception as exc:
            append_runtime_log("build viewer failed\n" + traceback.format_exc())
            self.failed.emit(str(exc))
            return
        append_runtime_log(f"build viewer done: {viewer}")
        self.finished.emit(str(viewer))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("30 Route Performance Viewer")
        self.resize(1180, 780)
        self.viewer_path: Path | None = None
        self.build_thread: QThread | None = None
        self.build_worker: ViewerBuildWorker | None = None
        self.loading_dialog: QProgressDialog | None = None
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
            if QWebEngineSettings is not None:
                settings = self.web.settings()
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            self.web.loadFinished.connect(self.web_load_finished)
            if hasattr(self.web, "renderProcessTerminated"):
                self.web.renderProcessTerminated.connect(self.web_render_process_terminated)
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
        if self.build_thread is not None and self.build_thread.isRunning():
            self.status.setText("ビューアーデータを読み込み中です。完了までお待ちください。")
            return
        output_dir = self.output_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "未選択", "30_route_performance 出力フォルダを選択してください。")
            return
        self.status.setText("ビューアーデータを読み込み中です。しばらくお待ちください。")
        self.show_loading_dialog("ビューアーデータを読み込み中です。\nJSONを集約し、地図表示用HTMLを生成しています。")
        self.build_thread = QThread()
        self.build_worker = ViewerBuildWorker(output_dir)
        self.build_worker.moveToThread(self.build_thread)
        self.build_thread.started.connect(self.build_worker.run)
        self.build_worker.finished.connect(self.viewer_rebuild_done)
        self.build_worker.failed.connect(self.viewer_rebuild_failed)
        self.build_worker.finished.connect(self.build_thread.quit)
        self.build_worker.failed.connect(self.build_thread.quit)
        self.build_thread.finished.connect(self.build_worker.deleteLater)
        self.build_thread.finished.connect(self.build_thread.deleteLater)
        self.build_thread.finished.connect(self.viewer_rebuild_thread_finished)
        self.build_thread.start()

    def show_loading_dialog(self, message: str) -> None:
        self.close_loading_dialog()
        dialog = QProgressDialog(message, None, 0, 0, self)
        dialog.setWindowTitle("データ読み込み中")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.setCancelButton(None)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.show()
        QApplication.processEvents()
        self.loading_dialog = dialog

    def close_loading_dialog(self) -> None:
        if self.loading_dialog is not None:
            self.loading_dialog.close()
            self.loading_dialog.deleteLater()
            self.loading_dialog = None

    def viewer_rebuild_done(self, viewer: str) -> None:
        self.viewer_path = Path(viewer)
        self.status.setText(f"ビューアーを再生成しました: {self.viewer_path}")
        if self.web is not None:
            append_runtime_log(f"web load start: {self.viewer_path}")
            self.web.load(QUrl.fromLocalFile(str(self.viewer_path)))
        else:
            self.close_loading_dialog()

    def viewer_rebuild_failed(self, message: str) -> None:
        self.close_loading_dialog()
        QMessageBox.critical(self, "ビューアー再生成失敗", message)

    def viewer_rebuild_thread_finished(self) -> None:
        self.build_thread = None
        self.build_worker = None

    def web_load_finished(self, ok: bool) -> None:
        self.close_loading_dialog()
        append_runtime_log(f"web load finished: ok={ok} path={self.viewer_path}")
        if not ok:
            self.status.setText("ビューアーHTMLの読み込みに失敗しました。外部ブラウザで確認してください。")
            return
        if self.viewer_path is not None:
            self.status.setText(f"ビューアーを表示しました: {self.viewer_path}")

    def web_render_process_terminated(self, *args) -> None:
        self.close_loading_dialog()
        append_runtime_log(f"WEBENGINE RENDER PROCESS TERMINATED: {args}")
        self.status.setText("ビューアー表示エンジンが停止しました。外部ブラウザで開いて確認してください。")

    def open_external(self) -> None:
        if self.viewer_path is None:
            self.rebuild_viewer()
        if self.viewer_path is None:
            return
        subprocess.Popen([sys.executable, "-m", "webbrowser", str(self.viewer_path)])


def main() -> None:
    install_runtime_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
