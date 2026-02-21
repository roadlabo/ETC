from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

MODULE_PATH = Path(__file__).with_name("01_split_by_opid_streaming.py")
spec = importlib.util.spec_from_file_location("split_mod", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("Cannot load splitter module")
split_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split_mod)
SplitConfig = split_mod.SplitConfig
run_split = split_mod.run_split

STAGES = ["SCAN", "EXTRACT", "SORT", "VERIFY"]


class SweepWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.angle = 0
        self.setMinimumHeight(120)

    def tick(self) -> None:
        self.angle = (self.angle + 8) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#050b09"))
        pen = QPen(QColor("#1b4f2f"))
        p.setPen(pen)
        r = min(self.width(), self.height()) // 2 - 8
        c = self.rect().center()
        p.drawEllipse(c, r, r)
        p.drawEllipse(c, int(r * 0.66), int(r * 0.66))
        p.drawEllipse(c, int(r * 0.33), int(r * 0.33))
        sweep_pen = QPen(QColor("#56d27f"), 2)
        p.setPen(sweep_pen)
        rad = self.angle * 3.14159 / 180
        x = int(c.x() + r * __import__("math").cos(rad))
        y = int(c.y() - r * __import__("math").sin(rad))
        p.drawLine(c.x(), c.y(), x, y)


class SplitWorker(QThread):
    progress = pyqtSignal(str, int, int, dict)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, config: SplitConfig) -> None:
        super().__init__()
        self.config = config
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            run_split(self.config, progress_cb=self._on_progress, cancel_flag=self.cancel_event)
            if self.cancel_event.is_set():
                self.finished_ok.emit("CANCELLED")
            else:
                self.finished_ok.emit("COMPLETE")
        except Exception as exc:
            self.failed.emit(str(exc))

    def _on_progress(self, stage: str, done: int, total: int, extra: dict) -> None:
        self.progress.emit(stage, done, total, extra)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("01 第1スクリーニング HUD")
        self.resize(1200, 760)
        self.worker: SplitWorker | None = None
        self.started_at = 0.0
        self.rows_written = 0
        self.errors = 0
        self.log_lines: list[str] = []
        self.last_stage = "IDLE"
        self._build_ui()
        self._set_style()
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick_animation)
        self.anim_timer.start(120)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        form = QFormLayout()
        self.input_dir = QLineEdit()
        self.output_dir = QLineEdit()
        self.term_name = QLineEdit("R7_2")
        self.inner_csv = QLineEdit("data.csv")
        self.zip_keys = QLineEdit("523357,523347,523450,523440")
        self.encoding = QLineEdit("utf-8")
        self.delim = QLineEdit(",")
        self.do_sort = QCheckBox("DO_FINAL_SORT")
        self.do_sort.setChecked(True)
        self.timestamp_col = QSpinBox(); self.timestamp_col.setRange(0, 100); self.timestamp_col.setValue(6)
        self.chunk_rows = QSpinBox(); self.chunk_rows.setRange(1000, 5_000_000); self.chunk_rows.setValue(200000)

        in_row = QHBoxLayout(); in_row.addWidget(self.input_dir); btn_in = QPushButton("..."); btn_in.clicked.connect(lambda: self._pick_dir(self.input_dir)); in_row.addWidget(btn_in)
        out_row = QHBoxLayout(); out_row.addWidget(self.output_dir); btn_out = QPushButton("..."); btn_out.clicked.connect(lambda: self._pick_dir(self.output_dir)); out_row.addWidget(btn_out)
        form.addRow("INPUT_DIR", self._wrap(in_row))
        form.addRow("OUTPUT_DIR", self._wrap(out_row))
        form.addRow("TERM_NAME", self.term_name)
        form.addRow("INNER_CSV", self.inner_csv)
        form.addRow("ZIP_DIGIT_KEYS", self.zip_keys)
        form.addRow("ENCODING", self.encoding)
        form.addRow("DELIM", self.delim)
        form.addRow("DO_FINAL_SORT", self.do_sort)
        form.addRow("TIMESTAMP_COL", self.timestamp_col)
        form.addRow("CHUNK_ROWS", self.chunk_rows)
        layout.addLayout(form)

        btns = QHBoxLayout()
        self.btn_run = QPushButton("RUN")
        self.btn_cancel = QPushButton("CANCEL")
        self.btn_open = QPushButton("OPEN OUTPUT")
        self.btn_run.clicked.connect(self.start_run)
        self.btn_cancel.clicked.connect(self.cancel_run)
        self.btn_open.clicked.connect(self.open_output)
        btns.addWidget(self.btn_run); btns.addWidget(self.btn_cancel); btns.addWidget(self.btn_open); btns.addStretch(1)
        layout.addLayout(btns)

        mid = QHBoxLayout()
        stage_frame = QFrame(); stage_layout = QVBoxLayout(stage_frame)
        stage_layout.addWidget(QLabel("STAGE PANEL"))
        self.stage_lamps: dict[str, QLabel] = {}
        for st in STAGES + ["ERROR"]:
            lbl = QLabel(f"● {st}")
            self.stage_lamps[st] = lbl
            stage_layout.addWidget(lbl)
        mid.addWidget(stage_frame, 1)

        telem = QFrame(); tg = QGridLayout(telem)
        tg.addWidget(QLabel("LIVE TELEMETRY"), 0, 0, 1, 2)
        self.tele = {k: QLabel("0") for k in ["elapsed", "zips", "rows", "out_files", "rows_per_sec", "errors", "status"]}
        for i, k in enumerate(self.tele, start=1):
            tg.addWidget(QLabel(k), i, 0); tg.addWidget(self.tele[k], i, 1)
        self.sweep = SweepWidget()
        tg.addWidget(self.sweep, len(self.tele) + 1, 0, 1, 2)
        mid.addWidget(telem, 2)
        layout.addLayout(mid)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(2000)
        layout.addWidget(self.log)
        self._set_stage("IDLE")

    def _wrap(self, layout) -> QWidget:
        w = QWidget(); w.setLayout(layout); return w

    def _set_style(self) -> None:
        self.setStyleSheet("""
            QWidget { background: #050908; color: #79d58f; }
            QLineEdit, QPlainTextEdit, QSpinBox { background: #0a120f; border: 1px solid #1f3f2d; }
            QPushButton { background: #112116; border: 1px solid #2a6b45; padding: 6px 10px; }
            QPushButton:hover { background: #18321f; }
            QCheckBox { spacing: 8px; }
        """)
        mono = QFont("Consolas", 10)
        self.setFont(mono)

    def _pick_dir(self, target: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select directory", target.text() or str(Path.cwd()))
        if d:
            target.setText(d)

    def _set_stage(self, stage: str) -> None:
        active = {"SCAN": 1, "EXTRACT": 2, "SORT": 3, "VERIFY": 4}.get(stage, 0)
        for idx, st in enumerate(STAGES, start=1):
            self.stage_lamps[st].setStyleSheet(f"color: {'#9cffbe' if idx <= active else '#2b6040'};")
        self.stage_lamps["ERROR"].setStyleSheet("color: #d66;" if stage == "ERROR" else "color: #5a2a2a;")
        self.tele["status"].setText(stage)

    def _append_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{ts}] {msg}")
        self.log_lines = self.log_lines[-50:]
        self.log.setPlainText("\n".join(self.log_lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _tick_animation(self) -> None:
        self.sweep.tick()
        if self.started_at > 0:
            elapsed = time.time() - self.started_at
            self.tele["elapsed"].setText(f"{elapsed:,.1f}s")
            rps = self.rows_written / elapsed if elapsed > 0 else 0
            self.tele["rows_per_sec"].setText(f"{rps:,.1f}")

    def _config(self) -> SplitConfig:
        return SplitConfig(
            input_dir=self.input_dir.text().strip(),
            output_dir=self.output_dir.text().strip(),
            term_name=self.term_name.text().strip(),
            inner_csv=self.inner_csv.text().strip() or "data.csv",
            zip_digit_keys=[x.strip() for x in self.zip_keys.text().split(",") if x.strip()],
            encoding=self.encoding.text().strip() or "utf-8",
            delim=self.delim.text() or ",",
            do_final_sort=self.do_sort.isChecked(),
            timestamp_col=self.timestamp_col.value(),
            chunk_rows=self.chunk_rows.value(),
        )

    def start_run(self) -> None:
        cfg = self._config()
        if not cfg.input_dir or not cfg.output_dir or not cfg.term_name or not cfg.zip_digit_keys:
            QMessageBox.warning(self, "Missing input", "必須項目を設定してください")
            return
        self.started_at = time.time()
        self.rows_written = 0
        self.errors = 0
        self._set_stage("SCAN")
        self._append_log("MISSION START")
        self.worker = SplitWorker(cfg)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def cancel_run(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self._append_log("CANCEL SIGNAL SENT")

    def open_output(self) -> None:
        out = self.output_dir.text().strip()
        if not out:
            return
        if sys.platform.startswith("win"):
            os.startfile(out)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{out}"')
        else:
            os.system(f'xdg-open "{out}"')

    def on_progress(self, stage: str, done: int, total: int, extra: dict) -> None:
        self.last_stage = stage
        self._set_stage(stage)
        self.tele["zips"].setText(f"{extra.get('zips_done', done)}/{extra.get('zips_total', total)}")
        self.rows_written = int(extra.get("rows_written", self.rows_written))
        self.tele["rows"].setText(str(self.rows_written))
        self.tele["out_files"].setText(str(extra.get("out_files", self.tele["out_files"].text())))
        if "zip" in extra:
            self._append_log(f"{stage} {done}/{total} {extra['zip']}")
        elif "current_file" in extra:
            self._append_log(f"{stage} {done}/{total} {extra['current_file']}")
        elif stage == "VERIFY":
            self._append_log(f"VERIFY {extra.get('status', '')}")

    def on_finished(self, status: str) -> None:
        self._set_stage("VERIFY")
        self.tele["status"].setText(status)
        self._append_log(status)

    def on_failed(self, message: str) -> None:
        self.errors += 1
        self.tele["errors"].setText(str(self.errors))
        self._set_stage("ERROR")
        self._append_log(f"ERROR {message}")


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
