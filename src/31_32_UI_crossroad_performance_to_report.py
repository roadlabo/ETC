import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

APP_TITLE = "31+32 交差点performance→report（一括実行）"

FOLDER_CROSS = "11_交差点(Point)データ"
FOLDER_S2 = "20_第２スクリーニング"
FOLDER_31OUT = "31_交差点パフォーマンス"
FOLDER_32OUT = "32_交差点レポート"
WEEKDAYS = ["ALL", "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 780)

        self.project_dir: Path | None = None
        self.proc: QProcess | None = None
        self.queue: list[str] = []
        self.current_name: str | None = None
        self.current_step = ""

        self._build_ui()
        self._log("[INFO] ①プロジェクト選択 → ②曜日選択 → 31→32 一括実行")

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        top = QHBoxLayout()
        v.addLayout(top)

        self.btn_project = QPushButton("① プロジェクト選択")
        self.btn_project.clicked.connect(self.select_project)

        self.cmb_weekday = QComboBox()
        self.cmb_weekday.addItems(WEEKDAYS)
        self.cmb_weekday.setCurrentText("ALL")

        weekday_container = QHBoxLayout()
        weekday_label = QLabel("② 曜日選択")
        weekday_container.addWidget(weekday_label)
        weekday_container.addWidget(self.cmb_weekday)

        weekday_widget = QWidget()
        weekday_widget.setLayout(weekday_container)

        self.btn_run = QPushButton("31→32 一括実行")
        self.btn_run.clicked.connect(self.start_batch)

        arrow1 = QLabel(" → ")
        arrow2 = QLabel(" → ")
        arrow1.setStyleSheet("font-size: 18px; font-weight: bold;")
        arrow2.setStyleSheet("font-size: 18px; font-weight: bold;")

        top.addWidget(self.btn_project)
        top.addWidget(arrow1)
        top.addWidget(weekday_widget)
        top.addWidget(arrow2)
        top.addWidget(self.btn_run)
        top.addStretch(1)

        self.lbl_project = QLabel("Project: (未選択)")
        v.addWidget(self.lbl_project)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "実行",
                "交差点名",
                "cross.csv",
                "cross.jpg",
                "第2スクリーニング(フォルダ)",
                "第2スクリーニング(CSVあり)",
                "31出力(performance.csv)",
                "32出力(report.xlsx)",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, stretch=3)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("background-color: black; color: #00ff66;")
        self.log.setFont(QFont("Consolas", 10))
        v.addWidget(self.log, stretch=2)

    def _log(self, s: str) -> None:
        self.log.appendPlainText(s)

    def _set_run_controls_enabled(self, enabled: bool) -> None:
        self.btn_project.setEnabled(enabled)
        self.btn_run.setEnabled(enabled)
        self.cmb_weekday.setEnabled(enabled)

    def select_project(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択", str(Path.cwd()))
        if not d:
            return
        self.project_dir = Path(d).resolve()
        self.lbl_project.setText(f"Project: {self.project_dir}")
        self._log(f"[INFO] project set: {self.project_dir}")
        self.scan_crossroads()

    def scan_crossroads(self) -> None:
        self.table.setRowCount(0)
        if not self.project_dir:
            self._log("[WARN] project not selected.")
            return

        cross_dir = self.project_dir / FOLDER_CROSS
        s2_dir = self.project_dir / FOLDER_S2
        out31_dir = self.project_dir / FOLDER_31OUT
        out32_dir = self.project_dir / FOLDER_32OUT
        out31_dir.mkdir(parents=True, exist_ok=True)
        out32_dir.mkdir(parents=True, exist_ok=True)

        if not cross_dir.exists():
            QMessageBox.critical(self, "エラー", f"交差点フォルダが見つかりません:\n{cross_dir}")
            return

        csvs = sorted(cross_dir.glob("*.csv"))
        if not csvs:
            QMessageBox.warning(self, "注意", f"交差点CSVが見つかりません:\n{cross_dir}")
            return

        for csv_path in csvs:
            name = csv_path.stem
            jpg_path = cross_dir / f"{name}.jpg"
            s2_cross_dir = s2_dir / name
            s2_has_csv = s2_cross_dir.exists() and any(s2_cross_dir.glob("*.csv"))
            out31 = out31_dir / f"{name}_performance.csv"
            out32 = out32_dir / f"{name}_report.xlsx"

            has_csv = csv_path.exists()
            has_jpg = jpg_path.exists()
            has_s2_dir = s2_cross_dir.exists()
            has_s2_csv = s2_has_csv
            has31 = out31.exists()
            has32 = out32.exists()

            default_run = has_csv and has_jpg and has_s2_dir and has_s2_csv

            r = self.table.rowCount()
            self.table.insertRow(r)

            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if default_run else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(name))
            self.table.setItem(r, 2, QTableWidgetItem("✔" if has_csv else "×"))
            self.table.setItem(r, 3, QTableWidgetItem("✔" if has_jpg else "×"))
            self.table.setItem(r, 4, QTableWidgetItem("✔" if has_s2_dir else "×"))
            self.table.setItem(r, 5, QTableWidgetItem("✔" if has_s2_csv else "×"))
            self.table.setItem(r, 6, QTableWidgetItem("✔" if has31 else "×"))
            self.table.setItem(r, 7, QTableWidgetItem("✔" if has32 else "×"))

        self._log(f"[INFO] scanned: {len(csvs)} crossroads")

    def _collect_targets(self) -> list[str]:
        targets: list[str] = []
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, 0)
            name_item = self.table.item(r, 1)
            if chk and name_item and chk.checkState() == Qt.CheckState.Checked:
                targets.append(name_item.text())
        return targets

    def start_batch(self) -> None:
        if not self.project_dir:
            QMessageBox.warning(self, "未設定", "①プロジェクトフォルダを選択してください。")
            return

        targets = self._collect_targets()
        if not targets:
            QMessageBox.information(self, "対象なし", "実行対象の交差点が選択されていません。")
            return

        self.queue = targets
        self._set_run_controls_enabled(False)

        self._log("")
        self._log("[INFO] =======================================")
        self._log(f"[INFO] start: targets={len(targets)}, weekday={self.cmb_weekday.currentText()}")
        self._log("[INFO] =======================================")

        QMessageBox.information(self, "実行開始", f"{len(targets)}交差点の処理を開始します。")
        self._start_next_crossroad()

    def _start_next_crossroad(self) -> None:
        if not self.queue:
            self._log("[INFO] 全件処理完了")
            self.scan_crossroads()
            self._set_run_controls_enabled(True)
            return

        self.current_name = self.queue.pop(0)
        self.current_step = "31"
        self._log(f"[START] {self.current_name}")
        self._start_step31(self.current_name)

    def _start_step31(self, name: str) -> None:
        script31 = Path(__file__).resolve().parent / "31_crossroad_trip_performance.py"
        if not script31.exists():
            self._log(f"[ERROR] 31 script not found: {script31}")
            self._start_next_crossroad()
            return

        args = [str(script31), "--project", str(self.project_dir), "--weekday", self.cmb_weekday.currentText(), "--targets", name]
        self._launch_process(args)

    def _start_step32(self, name: str) -> None:
        script32 = Path(__file__).resolve().parent / "32_crossroad_report.py"
        if not script32.exists():
            self._log(f"[ERROR] 32 script not found: {script32}")
            self._start_next_crossroad()
            return
        args = [str(script32), "--project", str(self.project_dir), "--targets", name]
        self._launch_process(args)

    def _launch_process(self, args: list[str]) -> None:
        if self.proc:
            self.proc.kill()
            self.proc = None

        self.proc = QProcess(self)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(args)
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.start()

    def _on_stdout(self) -> None:
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            for line in data.splitlines():
                self._log(line)

    def _on_stderr(self) -> None:
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace")
        if data:
            for line in data.splitlines():
                self._log(line)

    def _on_finished(self, code: int, _status) -> None:
        if self.current_name is None:
            self._start_next_crossroad()
            return

        if code != 0:
            self._log(f"[ERROR] {self.current_step} failed: {self.current_name} (code={code})")
            self._log(f"[DONE] {self.current_name}")
            self._start_next_crossroad()
            return

        if self.current_step == "31":
            self.current_step = "32"
            self._start_step32(self.current_name)
            return

        self._log(f"[DONE] {self.current_name}")
        self._start_next_crossroad()



def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
