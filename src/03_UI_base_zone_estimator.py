from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtGui import QFont, QPixmap
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
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "03_運行ID別 推定拠点ゾーン対応表 作成"
RE_FILE_DONE = re.compile(r"進捗ファイル:\s*([0-9,]+)\s*/\s*([0-9,]+)")


class StepBox(QFrame):
    def __init__(self, title: str, content: QWidget):
        super().__init__()
        self.setObjectName("stepBox")
        self.setMinimumHeight(120)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lbl = QLabel(title)
        lbl.setWordWrap(True)
        lbl.setObjectName("stepTitle")
        lay.addWidget(lbl)
        lay.addWidget(content)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 820)

        self.proc: QProcess | None = None
        self.input_folder: Path | None = None
        self.zoning_file: Path | None = None
        self.output_csv: Path | None = None
        self.total_files = 0
        self.done_files = 0

        self._build_ui()

    def _build_ui(self) -> None:
        cw = QWidget()
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        head = QFrame()
        head.setObjectName("hero")
        head_l = QHBoxLayout(head)
        head_l.setContentsMargins(16, 14, 16, 14)
        head_l.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(6)
        title = QLabel(APP_TITLE)
        title.setObjectName("heroTitle")
        desc = QLabel(
            "第1スクリーニング後データから、夜間停留位置等を基に運行IDごとの推定拠点ゾーンを判定します。\n"
            "様式1-2固定列CSVを優先して読み込み、結果を拠点ゾーン対応表として出力します。"
        )
        desc.setWordWrap(True)
        desc.setObjectName("heroDesc")
        left.addWidget(title)
        left.addWidget(desc)
        head_l.addLayout(left, 1)

        logo = QLabel()
        logo.setFixedSize(180, 72)
        logo.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pix = QPixmap(str(Path(__file__).with_name("logo.png")))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(170, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        head_l.addWidget(logo)
        root.addWidget(head)

        steps_host = QWidget()
        steps = QGridLayout(steps_host)
        steps.setContentsMargins(0, 0, 0, 0)
        steps.setHorizontalSpacing(10)
        steps.setVerticalSpacing(10)

        s1w = QWidget(); s1 = QVBoxLayout(s1w); s1.setContentsMargins(0, 0, 0, 0)
        r1 = QHBoxLayout()
        self.btn_pick_folder = QPushButton("選択")
        self.btn_pick_folder.clicked.connect(self.pick_folder)
        self.lbl_folder = QLabel("未選択")
        self.lbl_folder.setWordWrap(True)
        self.chk_recursive = QCheckBox("サブフォルダも含める")
        self.chk_recursive.stateChanged.connect(self._recalc_csv_count)
        r1.addWidget(self.btn_pick_folder)
        r1.addWidget(self.lbl_folder, 1)
        r1.addWidget(self.chk_recursive)
        self.lbl_csv_count = QLabel("対象CSV数: 0")
        self.lbl_csv_warn = QLabel("")
        self.lbl_csv_warn.setObjectName("warn")
        s1.addLayout(r1); s1.addWidget(self.lbl_csv_count); s1.addWidget(self.lbl_csv_warn)
        box1 = StepBox("STEP1\n第1スクリーニングフォルダを選択\n運行ID別CSVが格納されたフォルダを指定してください。", s1w)

        s2w = QWidget(); s2 = QHBoxLayout(s2w); s2.setContentsMargins(0, 0, 0, 0)
        self.btn_pick_zoning = QPushButton("選択")
        self.btn_pick_zoning.clicked.connect(self.pick_zoning)
        self.lbl_zoning = QLabel("未選択")
        self.lbl_zoning.setWordWrap(True)
        s2.addWidget(self.btn_pick_zoning)
        s2.addWidget(self.lbl_zoning, 1)
        box2 = StepBox("STEP2\n任意ゾーニングファイルを選択\nzoning_data.csv 形式のゾーン定義ファイルを指定してください。", s2w)

        s3w = QWidget(); s3 = QVBoxLayout(s3w); s3.setContentsMargins(0, 0, 0, 0)
        self.lbl_output = QLabel("未確定")
        self.lbl_output.setWordWrap(True)
        s3.addWidget(self.lbl_output)
        box3 = StepBox("STEP3\n出力先を確認\n選択フォルダと同じ階層に『_拠点ゾーン.csv』を出力します。", s3w)

        s4w = QWidget(); s4 = QVBoxLayout(s4w); s4.setContentsMargins(0, 0, 0, 0)
        self.btn_run = QPushButton("推定拠点ゾーン対応表を作成")
        self.btn_run.clicked.connect(self.start_run)
        self.btn_run.setEnabled(False)
        self.btn_open = QPushButton("出力ファイルを開く")
        self.btn_open.clicked.connect(self.open_output)
        self.btn_open.setEnabled(False)
        s4.addWidget(self.btn_run)
        s4.addWidget(self.btn_open)
        box4 = StepBox("STEP4\n推定拠点ゾーン対応表を作成\n夜間停留位置等を基に、運行IDごとの推定拠点ゾーンを判定します。", s4w)

        for b in [box1, box2, box3, box4]:
            b.setMinimumWidth(220)
        steps.addWidget(box1, 0, 0)
        steps.addWidget(box2, 0, 1)
        steps.addWidget(box3, 0, 2)
        steps.addWidget(box4, 0, 3)
        steps.setColumnStretch(0, 2)
        steps.setColumnStretch(1, 2)
        steps.setColumnStretch(2, 1)
        steps.setColumnStretch(3, 1)
        root.addWidget(steps_host)

        info_card = QFrame()
        info_card.setObjectName("infoCard")
        info_l = QVBoxLayout(info_card)
        info_l.setContentsMargins(12, 10, 12, 10)
        self.lbl_progress = QLabel("進捗ファイル: 0/0（0.0%）")
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        info_l.addWidget(self.lbl_progress)
        info_l.addWidget(self.progress)
        root.addWidget(info_card)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 10))
        self.log.setMaximumBlockCount(3000)
        root.addWidget(self.log, 1)

        self.lbl_notice = QLabel(
            "この機能はあくまで推定拠点であり、実際の居住地・事業所所在地を直接示すものではありません。\n"
            "GPS誤差や夜間運用形態等により判定には限界があります。分析結果は補助的情報として慎重に利用してください。"
        )
        self.lbl_notice.setWordWrap(True)
        root.addWidget(self.lbl_notice)

        self.setStyleSheet(
            """
            QWidget { background:#060b09; color:#8ee5ac; }
            QFrame#hero, QFrame#infoCard, QFrame#stepBox { background:#0b1511; border:1px solid #1f3f2d; border-radius:12px; }
            QLabel#heroTitle { font-size:22px; font-weight:800; color:#dcffe9; }
            QLabel#heroDesc { color:#9cd9b5; }
            QLabel#stepTitle { font-weight:700; color:#7cffc6; }
            QLabel#warn { color:#ffd866; font-weight:700; }
            QPushButton {
                background:#0f2a1c; border:2px solid #00ff99; border-radius:10px;
                color:#eafff4; font-weight:700; padding:8px 12px;
            }
            QPushButton:hover { background:#18412d; }
            QPushButton:disabled { border-color:#2a6b45; color:#3d6a55; background:#0b1511; }
            QPlainTextEdit {
                background:#0a120f; border:1px solid #1f3f2d; border-radius:10px;
                color:#c6f8d8; padding:8px; line-height:1.35;
            }
            QProgressBar {
                background:#0a120f; border:1px solid #1f3f2d; border-radius:8px; text-align:center;
            }
            QProgressBar::chunk { background:#2ddf83; border-radius:8px; }
            QCheckBox { color:#7cffc6; spacing:8px; }
            QCheckBox::indicator {
                width:16px; height:16px; border:2px solid #00ff99; border-radius:4px; background:#0a120f;
            }
            QCheckBox::indicator:checked { background:#00ff99; }
            """
        )

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _update_run_button_state(self) -> None:
        running = self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning
        ready = self.input_folder is not None and self.zoning_file is not None and self.total_files > 0
        self.btn_run.setEnabled((not running) and ready)

    def _recalc_csv_count(self) -> None:
        if not self.input_folder:
            self.total_files = 0
            self.lbl_csv_count.setText("対象CSV数: 0")
            self.lbl_csv_warn.setText("")
            self._update_run_button_state()
            return

        if self.chk_recursive.isChecked():
            self.total_files = len(list(self.input_folder.rglob("*.csv")))
        else:
            self.total_files = len(list(self.input_folder.glob("*.csv")))

        self.lbl_csv_count.setText(f"対象CSV数: {self.total_files:,}")
        self.lbl_csv_warn.setText("[WARN] 対象CSVが0件です" if self.total_files == 0 else "")
        self._update_run_button_state()

    def pick_folder(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "第1スクリーニングフォルダを選択")
        if not p:
            return
        self.input_folder = Path(p)
        self.lbl_folder.setText(str(self.input_folder))
        self._recalc_csv_count()
        self._update_output_path()

    def pick_zoning(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "任意ゾーニングファイルを選択", "", "CSV (*.csv);;All Files (*)")
        if not p:
            return
        self.zoning_file = Path(p)
        self.lbl_zoning.setText(str(self.zoning_file))
        self._update_run_button_state()

    def _update_output_path(self) -> None:
        if self.input_folder:
            self.output_csv = self.input_folder.parent / f"{self.input_folder.name}_拠点ゾーン.csv"
            self.lbl_output.setText(str(self.output_csv))

    def start_run(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        if not self.input_folder or not self.zoning_file:
            QMessageBox.warning(self, "入力不足", "第1スクリーニングフォルダと任意ゾーニングファイルを選択してください。")
            return
        if self.total_files <= 0:
            QMessageBox.warning(self, "入力不足", "対象CSVが0件です。フォルダ設定を見直してください。")
            return
        self._update_output_path()

        self.done_files = 0
        self.progress.setValue(0)
        self.lbl_progress.setText("進捗ファイル: 0/0（0.0%）")
        self.log.clear()
        self.btn_open.setEnabled(False)

        script = Path(__file__).with_name("03_base_zone_estimator.py")
        args = [str(script), "--input", str(self.input_folder), "--zoning", str(self.zoning_file)]
        if self.output_csv:
            args += ["--output", str(self.output_csv)]
        if self.chk_recursive.isChecked():
            args.append("--recursive")

        self.proc = QProcess(self)
        root_dir = Path(__file__).resolve().parent.parent
        runtime_py = root_dir / "runtime" / "python" / "python.exe"
        python_exec = str(runtime_py) if runtime_py.exists() else sys.executable

        self.proc.setProgram(python_exec)
        self.proc.setArguments(args)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.proc.start()

        self._set_enabled(False)
        self.btn_run.setEnabled(False)
        self.append_log("[INFO] 処理開始")

    def _set_enabled(self, enabled: bool) -> None:
        for w in [self.btn_pick_folder, self.btn_pick_zoning, self.chk_recursive]:
            w.setEnabled(enabled)

    def on_output(self) -> None:
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="ignore")
        for raw in data.splitlines():
            line = raw.strip()
            if not line:
                continue
            m = RE_FILE_DONE.search(line)
            if m:
                done = int(m.group(1).replace(",", ""))
                total = int(m.group(2).replace(",", ""))
                self.done_files = done
                self.total_files = total
                pct = (done / total * 100.0) if total else 0.0
                self.lbl_progress.setText(f"進捗ファイル: {done}/{total}（{pct:.1f}%）")
                self.progress.setValue(int(pct))
            self.append_log(line)

    def on_finished(self, code: int, _status) -> None:
        self._set_enabled(True)
        ok = code == 0
        if ok:
            self.append_log("[INFO] 出力完了")
            if self.output_csv:
                self.append_log(f"[INFO] 出力ファイル: {self.output_csv}")
            self.btn_open.setEnabled(self.output_csv is not None and self.output_csv.exists())
            QMessageBox.information(self, "完了", "出力完了しました。")
        else:
            self.append_log("[ERROR] 処理が異常終了しました。ログを確認してください。")
            QMessageBox.warning(self, "エラー", "処理中にエラーが発生しました。ログを確認してください。")
        self._update_run_button_state()

    def open_output(self) -> None:
        if self.output_csv and self.output_csv.exists():
            os.startfile(str(self.output_csv))


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
