from __future__ import annotations

import importlib.util
import math
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QMargins, QPoint, QRect, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

MODULE_PATH = Path(__file__).with_name("01_split_by_opid_streaming.py")
spec = importlib.util.spec_from_file_location("split_mod", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("Cannot load splitter module")
split_mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = split_mod
spec.loader.exec_module(split_mod)
SplitConfig = split_mod.SplitConfig
run_split = split_mod.run_split

STAGES = ["SCAN", "EXTRACT", "SORT", "VERIFY"]


@dataclass
class ZipState:
    status: str = "待機"
    zip_pct: int = 0
    zip_new: int = 0
    zip_append: int = 0
    rows_in_zip: int = 0


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=10):
        super().__init__(parent)
        self.item_list = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

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
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        for item in self.item_list:
            next_x = x + item.sizeHint().width() + self.spacing()
            if next_x - self.spacing() > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + self.spacing()
                next_x = x + item.sizeHint().width() + self.spacing()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()


class SweepWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.angle = 0
        self.setMinimumHeight(140)

    def tick(self) -> None:
        self.angle = (self.angle + 7) % 360
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
        rad = self.angle * math.pi / 180
        x = int(c.x() + r * math.cos(rad))
        y = int(c.y() - r * math.sin(rad))
        p.drawLine(c.x(), c.y(), x, y)


class ZipCard(QFrame):
    def __init__(self, zip_name: str) -> None:
        super().__init__()
        self.zip_name = zip_name
        self.setObjectName("zipCard")
        self.setFixedSize(290, 165)
        v = QVBoxLayout(self)
        self.title = QLabel(zip_name if len(zip_name) <= 30 else f"...{zip_name[-27:]}")
        self.state = QLabel("状態: 待機")
        self.bar = QProgressBar(); self.bar.setRange(0, 100)
        self.rows = QLabel("読込行数: 0")
        self.newc = QLabel("新規CSV作成(まとまり): 0")
        self.app = QLabel("既存CSV追記(まとまり): 0")
        for w in [self.title, self.state, self.bar, self.rows, self.newc, self.app]:
            v.addWidget(w)
        self.apply("待機")

    def apply(self, status: str, pct: int = 0, rows: int = 0, newc: int = 0, app: int = 0):
        self.state.setText(f"状態: {status}")
        self.bar.setValue(max(0, min(100, pct)))
        self.rows.setText(f"読込行数: {rows:,}")
        self.newc.setText(f"新規CSV作成(まとまり): {newc:,}")
        self.app.setText(f"既存CSV追記(まとまり): {app:,}")
        if status == "処理中":
            self.setStyleSheet("QFrame#zipCard{border:2px solid #9cffbe;background:#0f1e17;} QLabel{color:#b5ffd0;}")
        elif status == "完了":
            self.setStyleSheet("QFrame#zipCard{border:2px solid #68d088;background:#0c1712;} QLabel{color:#a2f0be;}")
            self.state.setText("状態: 完了 ✓")
        elif status == "エラー":
            self.setStyleSheet("QFrame#zipCard{border:2px solid #d96f6f;background:#261010;} QLabel{color:#ffaaaa;}")
        else:
            self.setStyleSheet("QFrame#zipCard{border:1px solid #2a6b45;background:#0a120f;} QLabel{color:#79d58f;}")


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
            self.finished_ok.emit("CANCELLED" if self.cancel_event.is_set() else "COMPLETE")
        except Exception as exc:
            self.failed.emit(str(exc))

    def _on_progress(self, stage: str, done: int, total: int, extra: dict) -> None:
        self.progress.emit(stage, done, total, extra)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("01 第1スクリーニング HUD")
        self.resize(1600, 980)
        self.worker: SplitWorker | None = None
        self.started_at = 0.0
        self.rows_written = 0
        self.errors = 0
        self.log_lines: list[str] = []
        self.cards: dict[str, ZipCard] = {}
        self.current_zip = "-"
        self.current_sort_file = "-"
        self._build_ui()
        self._set_style()
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick_animation)
        self.anim_timer.start(120)

    def _build_ui(self) -> None:
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        about_box = QGroupBox("本ソフトについて")
        about_layout = QVBoxLayout(about_box)
        self.about_full_text = (
            "本ソフトは、ETC2.0プローブデータ（様式1-2出力）から指定した2次メッシュに該当するデータを抽出し、"
            "運行ID（OPID）ごとにCSVファイルへ分割したうえで、各CSVの内容を時系列順に並べ替えます。"
            "これにより必要な運行データのみを整理・抽出し、後続の分析を効率的に実施できます。"
        )
        self.about_text = QLabel()
        self.about_text.setWordWrap(False)
        self.about_text.setMinimumHeight(24)
        self.about_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.about_text.setToolTip(self.about_full_text)
        self._refresh_about_text()
        about_layout.addWidget(self.about_text)
        outer.addWidget(about_box)

        form_grid = QGridLayout()
        form_grid.setHorizontalSpacing(16)
        form_grid.setVerticalSpacing(4)
        self.input_dir = QLineEdit(); self.output_dir = QLineEdit(); self.term_name = QLineEdit("R7_2")
        self.zip_keys = QLineEdit("523357,523347,523450,523440")
        self.chunk_rows = QSpinBox(); self.chunk_rows.setRange(1000, 5_000_000); self.chunk_rows.setValue(200000)
        in_row = QHBoxLayout(); in_row.addWidget(self.input_dir); bi = QPushButton("..."); bi.clicked.connect(lambda: self._pick_dir(self.input_dir)); in_row.addWidget(bi)
        out_row = QHBoxLayout(); out_row.addWidget(self.output_dir); bo = QPushButton("..."); bo.clicked.connect(lambda: self._pick_dir(self.output_dir)); out_row.addWidget(bo)

        row = 0
        self._add_form_row(form_grid, row, "INPUT_DIR", self._wrap(in_row), "様式1-2出力の OUT1-2 フォルダを指定（中に日別ZIP / data.csv）。")
        row += 1
        self._add_form_row(form_grid, row, "OUTPUT_DIR", self._wrap(out_row), "第1スクリーニング保存先（後続分析で共通利用推奨）。")
        row += 1
        self._add_form_row(form_grid, row, "TERM", self.term_name, "出力ファイル名の先頭識別子（例 R7_2）。")
        row += 1
        self._add_form_row(form_grid, row, "ZIP_KEYS", self.zip_keys, "対象2次メッシュ番号（カンマ区切り）。")
        row += 1
        self._add_form_row(form_grid, row, "CHUNK_ROWS", self.chunk_rows, "並べ替え時に一度に読む行数（メモリ不足時は下げる）。")

        form_grid.setColumnStretch(0, 0)
        form_grid.setColumnStretch(1, 3)
        form_grid.setColumnStretch(2, 4)
        outer.addLayout(form_grid)

        btns = QHBoxLayout(); self.btn_run = QPushButton("RUN"); self.btn_cancel = QPushButton("CANCEL"); self.btn_open = QPushButton("OPEN OUTPUT")
        self.btn_run.clicked.connect(self.start_run); self.btn_cancel.clicked.connect(self.cancel_run); self.btn_open.clicked.connect(self.open_output)
        btns.addWidget(self.btn_run); btns.addWidget(self.btn_cancel); btns.addWidget(self.btn_open); btns.addStretch(1); outer.addLayout(btns)

        top = QHBoxLayout(); outer.addLayout(top, 3)

        self.map_frame = QFrame(); lmap = QVBoxLayout(self.map_frame)
        lmap.addWidget(QLabel("マインドマップ / 実行フロー"))
        self.map_lines = []
        lines = [
            "① 入力フォルダ → ZIPを列挙（SCAN）",
            "② ZIPを選択 → data.csvを1行ずつ読む（ストリーミング）",
            "③ 運行ID(OPID)ごとにCSVへ分割（新規/追記）",
            "④ 全ZIP完了 → OPID別CSVを時系列に並べ替え（SORT）",
            "⑤ 完了（VERIFY）",
        ]
        for t in lines:
            lb = QLabel(f"└─ {t}"); self.map_lines.append(lb); lmap.addWidget(lb)
        lmap.addStretch(1)
        top.addWidget(self.map_frame, 2)

        zip_panel = QFrame(); zip_layout = QVBoxLayout(zip_panel); zip_layout.addWidget(QLabel("入力ZIPアイコン一覧"))
        self.zip_scroll = QScrollArea(); self.zip_scroll.setWidgetResizable(True)
        self.zip_container = QWidget(); self.zip_flow = FlowLayout(self.zip_container, margin=4, spacing=8)
        self.zip_container.setLayout(self.zip_flow)
        self.zip_scroll.setWidget(self.zip_container)
        zip_layout.addWidget(self.zip_scroll)
        top.addWidget(zip_panel, 5)

        telem = QFrame(); tg = QVBoxLayout(telem); tg.addWidget(QLabel("CYBER TELEMETRY"))
        self.zip_progress = QProgressBar(); self.sort_progress = QProgressBar()
        self.tele = {
            "zip": QLabel("現在ZIP: -"), "rows": QLabel("累計行数: 0"), "rps": QLabel("rows/s: 0.0"),
            "zip_counts": QLabel("新規/追記: 0 / 0"), "sort_file": QLabel("SORT中: -"), "errors": QLabel("エラー数: 0"), "status": QLabel("状態: IDLE")
        }
        tg.addWidget(QLabel("全体ZIP進捗")); tg.addWidget(self.zip_progress)
        tg.addWidget(QLabel("SORT進捗")); tg.addWidget(self.sort_progress)
        for k in ["zip", "rows", "rps", "zip_counts", "sort_file", "errors", "status"]: tg.addWidget(self.tele[k])
        self.sweep = SweepWidget(); tg.addWidget(self.sweep)
        tg.addStretch(1)
        top.addWidget(telem, 3)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(100)
        outer.addWidget(self.log, 1)
        self._set_stage("IDLE")

    def _wrap(self, layout) -> QWidget:
        w = QWidget(); w.setLayout(layout); return w

    def _add_form_row(self, form: QGridLayout, row: int, label: str, field: QWidget, help_text: str) -> None:
        form.addWidget(QLabel(label), row, 0)
        form.addWidget(field, row, 1)
        help_label = QLabel(help_text)
        help_label.setWordWrap(False)
        help_label.setObjectName("fieldHelp")
        help_label.setStyleSheet("color:#6bbf8a;")
        help_label.setFont(QFont("Consolas", 9))
        form.addWidget(help_label, row, 2)

    def _set_style(self) -> None:
        self.setStyleSheet("""
            QWidget { background: #050908; color: #79d58f; }
            QLineEdit, QPlainTextEdit, QSpinBox, QProgressBar { background: #0a120f; border: 1px solid #1f3f2d; }
            QPushButton { background: #112116; border: 1px solid #2a6b45; padding: 6px 10px; }
            QPushButton:hover { background: #18321f; }
            QFrame { border: 1px solid #1c4f33; border-radius: 4px; }
            QGroupBox { border: 1px solid #1c4f33; border-radius: 4px; margin-top: 8px; padding-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
            QLabel#fieldHelp { color: #6bbf8a; }
        """)
        self.setFont(QFont("Consolas", 10))

    def _pick_dir(self, target: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select directory", target.text() or str(Path.cwd()))
        if d:
            target.setText(d)

    def _set_stage(self, stage: str) -> None:
        glow = {"SCAN": 0, "EXTRACT": 2, "SORT": 3, "VERIFY": 4}
        active = glow.get(stage, -1)
        for i, lb in enumerate(self.map_lines):
            lb.setStyleSheet("color:#a2f0be;font-weight:700;" if i <= active else "color:#2b6040;")
        self.tele["status"].setText(f"状態: {stage}")

    def _append_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{ts}] {msg}")
        self.log_lines = self.log_lines[-100:]
        self.log.setPlainText("\n".join(self.log_lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _tick_animation(self) -> None:
        self.sweep.tick()
        if self.started_at > 0:
            elapsed = time.time() - self.started_at
            rps = self.rows_written / elapsed if elapsed > 0 else 0.0
            self.tele["rps"].setText(f"rows/s: {rps:,.1f}")

    def _config(self) -> SplitConfig:
        return SplitConfig(
            input_dir=self.input_dir.text().strip(), output_dir=self.output_dir.text().strip(),
            term_name=self.term_name.text().strip(), inner_csv="data.csv",
            zip_digit_keys=[x.strip() for x in self.zip_keys.text().split(",") if x.strip()],
            encoding="utf-8", delim=",",
            do_final_sort=True, timestamp_col=6, chunk_rows=self.chunk_rows.value(),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_about_text()

    def _refresh_about_text(self) -> None:
        fm = QFontMetrics(self.about_text.font())
        self.about_text.setText(fm.elidedText(self.about_full_text, Qt.TextElideMode.ElideRight, self.about_text.width()))

    def _reset_zip_cards(self, zip_list: list[str]) -> None:
        while self.zip_flow.count():
            item = self.zip_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.cards.clear()
        for name in zip_list:
            card = ZipCard(name)
            self.cards[name] = card
            self.zip_flow.addWidget(card)

    def start_run(self) -> None:
        cfg = self._config()
        if not cfg.input_dir or not cfg.output_dir or not cfg.term_name or not cfg.zip_digit_keys:
            QMessageBox.warning(self, "Missing input", "必須項目を設定してください")
            return
        self.started_at = time.time(); self.rows_written = 0; self.errors = 0
        self.zip_progress.setValue(0); self.sort_progress.setValue(0)
        self._append_log("管制: ミッション開始。ZIP走査へ移行します。")
        self.worker = SplitWorker(cfg)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def cancel_run(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self._append_log("管制: 中断要求送信（CANCEL）")

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
        self._set_stage(stage)
        if stage == "SCAN":
            zips = extra.get("zip_list", [])
            self._reset_zip_cards(zips)
            self._append_log(f"ZIPを走査中…（{len(zips)}件）")
            return

        if stage == "EXTRACT":
            zip_name = extra.get("zip", "")
            zdone = int(extra.get("zips_done", done)); ztot = max(1, int(extra.get("zips_total", total)))
            self.zip_progress.setValue(int(zdone * 100 / ztot))
            self.current_zip = zip_name or self.current_zip
            self.tele["zip"].setText(f"現在ZIP: {self.current_zip}")
            self.rows_written = int(extra.get("rows_written", self.rows_written))
            self.tele["rows"].setText(f"累計行数: {self.rows_written:,}")
            zn = int(extra.get("zip_new", 0)); za = int(extra.get("zip_append", 0))
            self.tele["zip_counts"].setText(f"新規/追記: {zn:,} / {za:,}")
            if zip_name in self.cards:
                status = "処理中" if int(extra.get("zip_pct", 0)) < 100 else "完了"
                self.cards[zip_name].apply(status, int(extra.get("zip_pct", 0)), int(extra.get("rows_in_zip", 0)), zn, za)
            self._append_log(f"ZIP解析中：{zip_name}（進捗 {int(extra.get('zip_pct',0))}%）")

        if stage == "SORT":
            total_files = max(1, int(extra.get("total_files", total)))
            done_files = int(extra.get("done_files", done))
            self.sort_progress.setValue(int(done_files * 100 / total_files))
            self.current_sort_file = extra.get("current_file", "-")
            self.tele["sort_file"].setText(f"SORT中: {self.current_sort_file}")
            self._append_log(f"並べ替え中：{done_files}/{total_files}（現在：{self.current_sort_file}）")

        if stage == "VERIFY":
            status = extra.get("status", "DONE")
            self._append_log("完了：COMPLETE" if status == "DONE" else "中断：CANCELLED")
            for card in self.cards.values():
                if "処理中" in card.state.text() or "待機" in card.state.text():
                    card.apply("スキップ" if status == "CANCELLED" else "完了", card.bar.value(), 0, 0, 0)

    def on_finished(self, status: str) -> None:
        self._set_stage("VERIFY")
        self.tele["status"].setText(f"状態: {status}")

    def on_failed(self, message: str) -> None:
        self.errors += 1
        self.tele["errors"].setText(f"エラー数: {self.errors}")
        self._set_stage("ERROR")
        self._append_log(f"エラー発生: {message}")


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
