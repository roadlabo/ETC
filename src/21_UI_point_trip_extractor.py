import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

from PyQt6.QtCore import Qt, QProcess
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPlainTextEdit, QMessageBox
)

APP_TITLE = "21[UI] Point Trip Extractor（第2スクリーニング）"

FOLDER_CROSS = "11_交差点(Point)データ"
FOLDER_OUT = "20_第２スクリーニング"


def resolve_project_paths(project_dir: Path) -> tuple[Path, Path]:
    return project_dir / FOLDER_CROSS, project_dir / FOLDER_OUT


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1100, 760)

        self.project_dir: Path | None = None
        self.input_dir: Path | None = None

        self.proc: QProcess | None = None
        self.log_lines: list[str] = []
        self.batch_started_at: datetime | None = None
        self.batch_ended_at: datetime | None = None
        self.batch_start_perf: float | None = None

        self._build_ui()
        self._log("[INFO] ①②を選択 → 実行、の流れです。")

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # Top controls
        top = QHBoxLayout()
        v.addLayout(top)

        btn_project = QPushButton("① プロジェクト選択")
        btn_input = QPushButton("② 第1スクリーニング選択")
        self.btn_run = QPushButton("第2スクリーニング開始")

        btn_project.clicked.connect(self.select_project)
        btn_input.clicked.connect(self.select_input)
        self.btn_run.clicked.connect(self.run_screening)

        arrow1 = QLabel(" → ")
        arrow2 = QLabel(" → ")

        arrow1.setStyleSheet("font-size: 18px; font-weight: bold;")
        arrow2.setStyleSheet("font-size: 18px; font-weight: bold;")

        top.addWidget(btn_project)
        top.addWidget(arrow1)
        top.addWidget(btn_input)
        top.addWidget(arrow2)
        top.addWidget(self.btn_run)
        top.addStretch(1)

        self.lbl_project = QLabel("Project: (未選択)")
        self.lbl_input = QLabel("Input(第1): (未選択)")
        v.addWidget(self.lbl_project)
        v.addWidget(self.lbl_input)

        # Table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["実行", "交差点名", "CSV", "JPG", "出力フォルダ"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, stretch=3)

        # Log
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("background-color: black; color: #00ff66;")
        self.log.setFont(QFont("Consolas", 10))
        v.addWidget(self.log, stretch=2)

    def _log(self, s: str):
        self.log.appendPlainText(s)
        self.log_lines.append(s)

    def select_project(self):
        d = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択", str(Path.cwd()))
        if not d:
            return
        self.project_dir = Path(d).resolve()
        self.lbl_project.setText(f"Project: {self.project_dir}")
        self._log(f"[INFO] project set: {self.project_dir}")

        # 自動でスキャン
        self.scan_crossroads()

    def select_input(self):
        d = QFileDialog.getExistingDirectory(self, "第1スクリーニングデータフォルダを選択", str(Path.cwd()))
        if not d:
            return
        self.input_dir = Path(d).resolve()
        self.lbl_input.setText(f"Input(第1): {self.input_dir}")
        self._log(f"[INFO] input set: {self.input_dir}")

    def scan_crossroads(self):
        self.table.setRowCount(0)

        if not self.project_dir:
            self._log("[WARN] project not selected.")
            return

        cross_dir, out_dir = resolve_project_paths(self.project_dir)

        if not cross_dir.exists():
            QMessageBox.critical(self, "エラー", f"交差点フォルダが見つかりません:\n{cross_dir}")
            return

        csvs = sorted(cross_dir.glob("*.csv"))
        if not csvs:
            QMessageBox.warning(self, "注意", f"交差点CSVが見つかりません:\n{cross_dir}")
            return

        # 表を作る
        for csv_path in csvs:
            name = csv_path.stem
            jpg_path = cross_dir / f"{name}.jpg"
            out_path = out_dir / name

            has_csv = True
            has_jpg = jpg_path.exists()
            has_out = out_path.exists()

            # デフォルトチェック：CSVあり AND JPGあり AND 出力なし
            default_run = (has_csv and has_jpg and (not has_out))

            r = self.table.rowCount()
            self.table.insertRow(r)

            # 実行チェック（ユーザー操作可）
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if default_run else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, chk)

            self.table.setItem(r, 1, QTableWidgetItem(name))
            self.table.setItem(r, 2, QTableWidgetItem("✔"))
            self.table.setItem(r, 3, QTableWidgetItem("✔" if has_jpg else "×"))
            self.table.setItem(r, 4, QTableWidgetItem("✔" if has_out else "×"))

        self._log(f"[INFO] scanned: {len(csvs)} crossroads")
        self._log(f"[INFO] cross_dir: {cross_dir}")
        self._log(f"[INFO] out_dir  : {out_dir}")

    def _collect_targets(self) -> list[str]:
        targets: list[str] = []
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, 0)
            name = self.table.item(r, 1).text()
            if chk.checkState() == Qt.CheckState.Checked:
                targets.append(name)
        return targets

    def _table_dump_lines(self) -> list[str]:
        lines: list[str] = []

        headers = []
        for c in range(self.table.columnCount()):
            header_item = self.table.horizontalHeaderItem(c)
            headers.append(header_item.text() if header_item else "")
        lines.append("\t".join(headers))

        for r in range(self.table.rowCount()):
            row_values: list[str] = []
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if c == 0:
                    run_flag = (
                        "1"
                        if item and item.checkState() == Qt.CheckState.Checked
                        else "0"
                    )
                    row_values.append(f"RUN={run_flag}")
                else:
                    row_values.append(item.text() if item else "")
            lines.append("\t".join(row_values))

        return lines

    def _write_batch_log_file(self, total_sec: float) -> None:
        if not self.project_dir:
            return

        _cross_dir, out_dir = resolve_project_paths(self.project_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = out_dir / f"21_batch_log_{stamp}.txt"

        started_at = self.batch_started_at.isoformat(sep=" ", timespec="seconds") if self.batch_started_at else ""
        ended_at = self.batch_ended_at.isoformat(sep=" ", timespec="seconds") if self.batch_ended_at else ""

        lines: list[str] = [
            f"Project: {self.project_dir}",
        ]

        if self.input_dir:
            lines.append(f"Input: {self.input_dir}")

        lines.extend(
            [
                f"開始: {started_at}",
                f"終了: {ended_at}",
                f"総所要時間(秒): {total_sec:.3f}",
                "",
                "[UI表]",
                *self._table_dump_lines(),
                "",
                "[実行ログ]",
                *self.log_lines,
            ]
        )

        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._log(f"[INFO] batch log saved: {log_path}")

    def run_screening(self):
        if not self.project_dir:
            QMessageBox.warning(self, "未設定", "①プロジェクトフォルダを選択してください。")
            return
        if not self.input_dir:
            QMessageBox.warning(self, "未設定", "②第1スクリーニングデータフォルダを選択してください。")
            return

        targets = self._collect_targets()
        if not targets:
            QMessageBox.information(self, "対象なし", "実行対象の交差点が選択されていません。")
            return

        # 21本体は同じ src 配下にある前提
        ui_py = Path(__file__).resolve()
        script21 = ui_py.parent / "21_point_trip_extractor.py"
        if not script21.exists():
            QMessageBox.critical(self, "エラー", f"本体スクリプトが見つかりません:\n{script21}")
            return

        # プロセス起動（batがembedded pythonで起動するので sys.executable は embedded python になる）
        if self.proc:
            self.proc.kill()
            self.proc = None

        self.log_lines = []
        self.batch_started_at = datetime.now()
        self.batch_start_perf = perf_counter()
        self.batch_ended_at = None

        self.btn_run.setEnabled(False)

        self._log("")
        self._log("[INFO] =======================================")
        self._log(f"[INFO] start: targets={len(targets)}")
        self._log("[INFO] =======================================")

        self.proc = QProcess(self)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(
            [str(script21), "--project", str(self.project_dir), "--input", str(self.input_dir), "--targets", *targets]
        )

        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)

        self.proc.start()

    def _on_stdout(self):
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            for line in data.splitlines():
                self._log(line)

    def _on_stderr(self):
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace")
        if data:
            for line in data.splitlines():
                self._log(line)

    def _on_finished(self, code: int, _status):
        self._log(f"[INFO] process finished: code={code}")
        self.btn_run.setEnabled(True)
        self.batch_ended_at = datetime.now()
        total_sec = perf_counter() - self.batch_start_perf if self.batch_start_perf else 0.0
        self._write_batch_log_file(total_sec)
        # 出力フォルダの状態を反映
        self.scan_crossroads()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
