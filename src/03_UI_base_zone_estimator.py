from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        lbl = QLabel(title)
        lbl.setObjectName("stepTitle")
        lay.addWidget(lbl)
        lay.addWidget(content)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 760)

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

        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size:20px;font-weight:700;")
        root.addWidget(title)

        # STEP1
        s1w = QWidget(); s1 = QVBoxLayout(s1w); s1.setContentsMargins(0, 0, 0, 0)
        r1 = QHBoxLayout()
        self.btn_pick_folder = QPushButton("第1スクリーニングフォルダを選択")
        self.btn_pick_folder.clicked.connect(self.pick_folder)
        self.lbl_folder = QLabel("未選択")
        self.chk_recursive = QCheckBox("サブフォルダも含める")
        r1.addWidget(self.btn_pick_folder)
        r1.addWidget(self.lbl_folder, 1)
        r1.addWidget(self.chk_recursive)
        self.lbl_csv_count = QLabel("対象CSV数: 0")
        s1.addLayout(r1); s1.addWidget(self.lbl_csv_count)
        root.addWidget(StepBox("STEP1：第1スクリーニングフォルダを選択\n運行ID別CSVが格納されたフォルダを指定してください。", s1w))

        # STEP2
        s2w = QWidget(); s2 = QHBoxLayout(s2w); s2.setContentsMargins(0, 0, 0, 0)
        self.btn_pick_zoning = QPushButton("任意ゾーニングファイルを選択")
        self.btn_pick_zoning.clicked.connect(self.pick_zoning)
        self.lbl_zoning = QLabel("未選択")
        s2.addWidget(self.btn_pick_zoning)
        s2.addWidget(self.lbl_zoning, 1)
        root.addWidget(StepBox("STEP2：任意ゾーニングファイルを選択\n推定拠点ゾーンの判定に使用するゾーン定義ファイルを指定してください。", s2w))

        # STEP3
        s3w = QWidget(); s3 = QVBoxLayout(s3w); s3.setContentsMargins(0, 0, 0, 0)
        self.lbl_output = QLabel("未確定")
        s3.addWidget(self.lbl_output)
        root.addWidget(StepBox("STEP3：出力ファイルを確認\n選択フォルダと同じ階層に「_拠点ゾーン.csv」を出力します。", s3w))

        # STEP4
        s4w = QWidget(); s4 = QHBoxLayout(s4w); s4.setContentsMargins(0, 0, 0, 0)
        self.btn_run = QPushButton("推定拠点ゾーン対応表を作成")
        self.btn_run.clicked.connect(self.start_run)
        self.btn_open = QPushButton("出力ファイルを開く")
        self.btn_open.clicked.connect(self.open_output)
        self.btn_open.setEnabled(False)
        s4.addWidget(self.btn_run)
        s4.addWidget(self.btn_open)
        s4.addStretch(1)
        root.addWidget(StepBox("STEP4：推定拠点ゾーン対応表を作成\n夜間停留位置等を基に、運行IDごとの推定拠点ゾーンを判定します。", s4w))

        self.lbl_progress = QLabel("進捗ファイル: 0/0（0.0%）")
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        root.addWidget(self.lbl_progress)
        root.addWidget(self.progress)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

        self.lbl_notice = QLabel(
            "この機能はあくまで推定拠点であり、実際の居住地・事業所所在地を直接示すものではありません。\n"
            "GPS誤差や夜間運用形態等により判定には限界があります。分析結果は補助的情報として慎重に利用してください。"
        )
        self.lbl_notice.setWordWrap(True)
        root.addWidget(self.lbl_notice)

        self.setStyleSheet(
            """
            QFrame#stepBox{border:1px solid #1f8f4f;border-radius:8px;background:#f6fffa;}
            QLabel#stepTitle{font-weight:700;color:#116d3c;}
            """
        )

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def pick_folder(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "第1スクリーニングフォルダを選択")
        if not p:
            return
        self.input_folder = Path(p)
        self.lbl_folder.setText(str(self.input_folder))
        self.total_files = len(list(self.input_folder.glob("*.csv")))
        if self.chk_recursive.isChecked():
            self.total_files = len(list(self.input_folder.rglob("*.csv")))
        self.lbl_csv_count.setText(f"対象CSV数: {self.total_files:,}")
        self._update_output_path()

    def pick_zoning(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "任意ゾーニングファイルを選択", "", "CSV (*.csv);;All Files (*)")
        if not p:
            return
        self.zoning_file = Path(p)
        self.lbl_zoning.setText(str(self.zoning_file))

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
        runtime_py = Path(__file__).parent / "runtime" / "python.exe"

        if runtime_py.exists():
            python_exec = str(runtime_py)
        else:
            python_exec = sys.executable

        self.proc.setProgram(python_exec)
        self.proc.setArguments(args)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.proc.start()

        self._set_enabled(False)
        self.append_log("[INFO] 処理開始")

    def _set_enabled(self, enabled: bool) -> None:
        for w in [self.btn_pick_folder, self.btn_pick_zoning, self.chk_recursive, self.btn_run]:
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
            QMessageBox.warning(self, "エラー", "処理中にエラーが発生しました。ログを確認してください。")

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
