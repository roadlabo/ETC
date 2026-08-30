from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

MODULE_PATH = SRC_DIR / "15_area_screening.py"
spec = importlib.util.spec_from_file_location("area15", MODULE_PATH)
area15 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = area15
spec.loader.exec_module(area15)

APP_TITLE = "15_エリア第1.5スクリーニング"
STATE_PATH = Path.home() / ".etc_area15_screening_ui.json"


class ScreeningWorker(QThread):
    progress = pyqtSignal(str, int, int, dict)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            result = area15.run_screening(self.config, progress_cb=self.progress.emit, cancel_flag=self.cancel_event)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 820)
        self.worker: ScreeningWorker | None = None
        self.started_at = 0.0
        self._build_ui()
        self._set_style()
        self._load_state()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        about = QLabel(
            "第1スクリーニング出力の各トリップから、指定した分析区域を通行する部分だけを切り出し、1サブトリップ1CSVで保存します。"
            "後続の第2スクリーニング、OD前処理、交差点・ルート分析へ渡すための前処理です。"
        )
        about.setWordWrap(True)
        main.addWidget(about)

        form_box = QGroupBox("入力と出力")
        form = QGridLayout(form_box)
        self.input_path = QLineEdit()
        self.area_path = QLineEdit()
        self.output_dir = QLineEdit()
        self.chk_recursive = QCheckBox("サブフォルダも含める")
        self._path_row(form, 0, "第1スクリーニングCSV/フォルダ", self.input_path, self._pick_input)
        self._path_row(form, 1, "エリアGeoJSON", self.area_path, self._pick_area)
        self._path_row(form, 2, "出力フォルダ", self.output_dir, self._pick_output)
        form.addWidget(self.chk_recursive, 3, 1)
        main.addWidget(form_box)

        settings = QGroupBox("解析設定")
        grid = QGridLayout(settings)
        self.spin_boundary = self._double_spin(0, 100, 5, " m")
        self.spin_min_dist = self._double_spin(0, 1000, 10, " m")
        self.spin_min_sec = self._double_spin(0, 3600, 5, " 秒")
        self.spin_merge = self._double_spin(0, 3600, 10, " 秒")
        rows = [
            ("境界付近許容距離", self.spin_boundary),
            ("短距離除外しきい値", self.spin_min_dist),
            ("短時間除外しきい値", self.spin_min_sec),
            ("短時間再流入統合", self.spin_merge),
        ]
        for row, (label, widget) in enumerate(rows):
            grid.addWidget(QLabel(label), row // 3, (row % 3) * 2)
            grid.addWidget(widget, row // 3, (row % 3) * 2 + 1)
        main.addWidget(settings)

        buttons = QHBoxLayout()
        self.btn_run = QPushButton("解析開始")
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_open = QPushButton("出力フォルダを開く")
        self.btn_run.clicked.connect(self.start_run)
        self.btn_cancel.clicked.connect(self.cancel_run)
        self.btn_open.clicked.connect(self.open_output)
        buttons.addWidget(self.btn_run)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_open)
        buttons.addStretch(1)
        main.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel("待機中")
        self.time_label = QLabel("経過 00:00:00 / 残り --:--:--")
        self.time_label.setFont(QFont("Consolas", 16, QFont.Weight.Bold))
        main.addWidget(self.progress)
        main.addWidget(self.status)
        main.addWidget(self.time_label)

        self.summary = QLabel("サブトリップ: 0 / 除外: 0")
        main.addWidget(self.summary)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        main.addWidget(self.log, 1)

    def _path_row(self, form: QGridLayout, row: int, label: str, edit: QLineEdit, slot) -> None:
        button = QPushButton("選択")
        button.clicked.connect(slot)
        form.addWidget(QLabel(label), row, 0)
        form.addWidget(edit, row, 1)
        form.addWidget(button, row, 2)

    def _double_spin(self, low: float, high: float, value: float, suffix: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(1)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    def _set_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background:#050908; color:#d6ffe8; font-family:"Meiryo UI","Segoe UI"; font-size:12px; }
            QGroupBox { border:1px solid #1c4f33; border-radius:6px; margin-top:8px; padding-top:12px; }
            QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
            QLineEdit, QPlainTextEdit, QDoubleSpinBox, QProgressBar { background:#0a120f; border:1px solid #1f3f2d; border-radius:6px; padding:4px; }
            QPushButton { background:#0a1b14; border:1px solid #2ef29a; border-radius:8px; padding:7px 12px; font-weight:700; }
            QPushButton:hover { background:#103322; }
            QPushButton:disabled { color:#597262; border-color:#224432; }
            QProgressBar::chunk { background:#00ff99; border-radius:4px; }
            """
        )

    def _pick_input(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "第1スクリーニングフォルダを選択")
        if folder:
            self.input_path.setText(folder)

    def _pick_area(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "GeoJSONを選択", "", "GeoJSON (*.geojson *.json);;All files (*.*)")
        if path:
            self.area_path.setText(path)

    def _pick_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "出力フォルダを選択")
        if folder:
            self.output_dir.setText(folder)

    def _config(self):
        return area15.ScreeningConfig(
            input_path=Path(self.input_path.text().strip()),
            area_geojson=Path(self.area_path.text().strip()),
            output_dir=Path(self.output_dir.text().strip()),
            recursive=self.chk_recursive.isChecked(),
            boundary_tolerance_m=self.spin_boundary.value(),
            min_subtrip_distance_m=self.spin_min_dist.value(),
            min_subtrip_duration_sec=self.spin_min_sec.value(),
            merge_gap_sec=self.spin_merge.value(),
        )

    def _validate(self) -> str:
        cfg = self._config()
        if not cfg.input_path.exists():
            return "第1スクリーニングCSV/フォルダが見つかりません。"
        if not cfg.area_geojson.is_file():
            return "エリアGeoJSONが見つかりません。"
        if cfg.input_path.is_dir() and not any(cfg.input_path.rglob("*.csv") if cfg.recursive else cfg.input_path.glob("*.csv")):
            return "入力フォルダにCSVがありません。"
        try:
            area15.load_area_definition(cfg.area_geojson)
        except Exception as exc:
            return f"GeoJSONを確認してください: {exc}"
        return ""

    def start_run(self) -> None:
        error = self._validate()
        if error:
            QMessageBox.warning(self, "入力確認", error)
            return
        self._save_state()
        self.started_at = time.time()
        self.progress.setValue(0)
        self.status.setText("解析準備中")
        self.log.clear()
        self.append_log("解析を開始しました。")
        self.worker = ScreeningWorker(self._config())
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()
        self.btn_run.setEnabled(False)

    def cancel_run(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.append_log("キャンセル要求を送信しました。")

    def on_progress(self, stage: str, done: int, total: int, extra: dict) -> None:
        pct = int(done * 100 / total) if total else 0
        self.progress.setValue(max(0, min(100, pct)))
        self.status.setText(f"{stage}: {done}/{total} {extra.get('file', '')}")
        self.summary.setText(f"サブトリップ: {extra.get('subtrips', 0)} / 除外: {extra.get('excluded', 0)}")
        if stage in {"PROCESS", "DONE"}:
            self.append_log(self.status.text())

    def on_finished(self, result: dict) -> None:
        self.btn_run.setEnabled(True)
        self.progress.setValue(100)
        stats = result.get("stats", {})
        self.status.setText("完了")
        self.summary.setText(f"サブトリップ: {stats.get('subtrips', 0)} / 除外: {stats.get('excluded', 0)}")
        self.append_log(f"完了: {result.get('output_dir')}")
        QMessageBox.information(self, "完了", "第1.5スクリーニングが完了しました。")

    def on_failed(self, message: str) -> None:
        self.btn_run.setEnabled(True)
        self.status.setText("エラー")
        self.append_log(f"エラー: {message}")
        QMessageBox.critical(self, "エラー", message)

    def _tick(self) -> None:
        if not (self.worker and self.worker.isRunning() and self.started_at):
            return
        elapsed = time.time() - self.started_at
        pct = self.progress.value()
        remain = elapsed * (100 - pct) / pct if pct else None
        self.time_label.setText(f"経過 {self._hms(elapsed)} / 残り {self._hms(remain) if remain is not None else '--:--:--'}")

    def _hms(self, seconds: float) -> str:
        total = max(0, int(seconds))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def append_log(self, message: str) -> None:
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")

    def open_output(self) -> None:
        path = self.output_dir.text().strip()
        if path and Path(path).exists():
            os.startfile(path)  # type: ignore[attr-defined]

    def _save_state(self) -> None:
        payload = {
            "input_path": self.input_path.text(),
            "area_path": self.area_path.text(),
            "output_dir": self.output_dir.text(),
            "recursive": self.chk_recursive.isChecked(),
            "boundary": self.spin_boundary.value(),
            "min_dist": self.spin_min_dist.value(),
            "min_sec": self.spin_min_sec.value(),
            "merge": self.spin_merge.value(),
        }
        STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_state(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        self.input_path.setText(payload.get("input_path", ""))
        self.area_path.setText(payload.get("area_path", ""))
        self.output_dir.setText(payload.get("output_dir", ""))
        self.chk_recursive.setChecked(bool(payload.get("recursive", False)))
        self.spin_boundary.setValue(float(payload.get("boundary", 5)))
        self.spin_min_dist.setValue(float(payload.get("min_dist", 10)))
        self.spin_min_sec.setValue(float(payload.get("min_sec", 5)))
        self.spin_merge.setValue(float(payload.get("merge", 10)))


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
