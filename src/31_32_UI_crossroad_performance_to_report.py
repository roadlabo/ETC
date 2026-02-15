import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_LOGGING_RULES", "qt.text.font.db=false")

from PyQt6.QtCore import Qt, QProcess
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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
WEEKDAY_KANJI = ["月", "火", "水", "木", "金", "土", "日"]
WEEKDAY_KANJI_TO_ABBR = {
    "月": "MON",
    "火": "TUE",
    "水": "WED",
    "木": "THU",
    "金": "FRI",
    "土": "SAT",
    "日": "SUN",
}

COL_RUN = 0
COL_NAME = 1
COL_CROSS_CSV = 2
COL_CROSS_JPG = 3
COL_S2_DIR = 4
COL_S2_CSV = 5
COL_OUT31 = 6
COL_OUT32 = 7
COL_STATUS = 8
COL_DONE_FILES = 9
COL_TOTAL_FILES = 10
COL_WEEKDAY = 11
COL_SPLIT = 12
COL_TARGET = 13
COL_OK = 14
COL_UNK = 15
COL_NOTPASS = 16

CENTER_ALIGN_COLS = {
    COL_RUN,
    COL_CROSS_CSV,
    COL_CROSS_JPG,
    COL_S2_DIR,
    COL_S2_CSV,
    COL_OUT31,
    COL_OUT32,
}
RIGHT_ALIGN_COLS = {
    COL_DONE_FILES,
    COL_TOTAL_FILES,
    COL_WEEKDAY,
    COL_SPLIT,
    COL_TARGET,
    COL_OK,
    COL_UNK,
    COL_NOTPASS,
}

RE_PROGRESS = re.compile(r"進捗:\s*(\d+)\s*/\s*(\d+)")
RE_STATS = re.compile(
    r"曜日後:\s*(\d+).*?"
    r"行数:\s*(\d+).*?"
    r"成功:\s*(\d+).*?"
    r"不明:\s*(\d+).*?"
    r"不通過:\s*(\d+)"
)
RE_DONE = re.compile(
    r"完了:\s*ファイル=(\d+).*?"
    r"曜日後=(\d+).*?"
    r"行数=(\d+).*?"
    r"成功=(\d+).*?"
    r"不明=(\d+).*?"
    r"不通過=(\d+)"
)


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
        self._weekday_updating = False
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._last_log_line: str | None = None
        self._recent_process_lines: list[str] = []

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

        weekday_container = QHBoxLayout()
        weekday_container.setContentsMargins(0, 0, 0, 0)
        weekday_container.setSpacing(10)
        weekday_label = QLabel("② 曜日選択")
        weekday_container.addWidget(weekday_label)

        self.chk_all = QCheckBox("ALL")
        self.chk_all.stateChanged.connect(self._on_all_weekday_changed)
        weekday_container.addWidget(self.chk_all)

        self.weekday_checks: dict[str, QCheckBox] = {}
        for wd in WEEKDAY_KANJI:
            chk = QCheckBox(wd)
            chk.stateChanged.connect(self._on_single_weekday_changed)
            self.weekday_checks[wd] = chk
            weekday_container.addWidget(chk)

        self._set_weekdays_from_all(True)

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
        self.lbl_summary = QLabel("")
        v.addWidget(self.lbl_summary)

        self.table = QTableWidget(0, 17)
        self.table.setColumnCount(17)
        self.table.setHorizontalHeaderLabels(
            [
                "✅ALL",
                "交差点名",
                "cross.csv",
                "cross.jpg",
                "第2スクリーニング\n（フォルダ）",
                "第2スクリーニング\n（CSV）",
                "出力\n(performance.csv)",
                "出力\n(report)",
                "状態",
                "分析済ファイル",
                "対象ファイル",
                "曜日フィルター後",
                "トリップ分割数",
                "対象トリップ数",
                "枝判定成功",
                "枝不明",
                "交差点不通過",
            ]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStretchLastSection(False)
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, stretch=3)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("background-color: black; color: #00ff66;")
        self.log.setFont(QFont("Consolas", 10))
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setMaximumBlockCount(5000)

        self.lbl_progress = QLabel("")
        self.lbl_progress.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_progress.setStyleSheet("font-family: Consolas, 'Yu Gothic UI', monospace;")
        v.addWidget(self.lbl_progress)
        v.addWidget(self.log, stretch=2)

    def _log(self, s: str) -> None:
        if s == "" and self._last_log_line == "":
            return
        self.log.appendPlainText(s)
        self._last_log_line = s

    def _decode_qbytearray(self, ba) -> str:
        raw = bytes(ba)
        if not raw:
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp932", errors="replace")

    def _is_qt_font_warning(self, line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("qt.text.font.db:")

    def _handle_stream_line(self, line: str, from_carriage_return: bool, _is_err: bool) -> None:
        text = line.strip()
        if not text or self._is_qt_font_warning(text):
            return

        if "完了:" in text and "ファイル=" in text:
            self._apply_done_summary(text)

        if from_carriage_return or text.startswith("進捗:") or "進捗:" in text:
            self.lbl_progress.setText(text)
            self._update_table_progress(text)
            return

        self._recent_process_lines.append(text)
        if len(self._recent_process_lines) > 200:
            self._recent_process_lines = self._recent_process_lines[-200:]

        self._log(text)

    def _append_stream_chunk(self, chunk: str, is_err: bool) -> None:
        if not chunk:
            return

        if is_err:
            buf = self._stderr_buf + chunk
            self._maybe_update_realtime_from_buffer(buf)
        else:
            buf = self._stdout_buf + chunk
            self._maybe_update_realtime_from_buffer(buf)

        start = 0
        idx = 0
        while idx < len(buf):
            ch = buf[idx]
            if ch in ("\r", "\n"):
                line = buf[start:idx]
                prev_is_cr = idx > 0 and buf[idx - 1] == "\r"
                from_carriage_return = ch == "\r" or prev_is_cr
                self._handle_stream_line(line, from_carriage_return, is_err)
                start = idx + 1
            idx += 1

        if is_err:
            self._stderr_buf = buf[start:]
            self._maybe_update_realtime_from_buffer(self._stderr_buf)
        else:
            self._stdout_buf = buf[start:]
            self._maybe_update_realtime_from_buffer(self._stdout_buf)

    def _flush_process_buffers(self) -> None:
        if self._stdout_buf:
            self._handle_stream_line(self._stdout_buf, False, False)
            self._stdout_buf = ""
        if self._stderr_buf:
            self._handle_stream_line(self._stderr_buf, False, True)
            self._stderr_buf = ""

    def _set_run_controls_enabled(self, enabled: bool) -> None:
        self.btn_project.setEnabled(enabled)
        self.btn_run.setEnabled(enabled)
        self.chk_all.setEnabled(enabled)
        for chk in self.weekday_checks.values():
            chk.setEnabled(enabled)

    def _set_weekdays_from_all(self, checked: bool) -> None:
        self._weekday_updating = True
        target_state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.chk_all.setCheckState(target_state)
        for chk in self.weekday_checks.values():
            chk.setCheckState(target_state)
        self._weekday_updating = False

    def _norm_checkstate(self, state) -> Qt.CheckState:
        # state は int の場合も Qt.CheckState の場合もあるため両対応
        try:
            return Qt.CheckState(state)
        except TypeError:
            # ここに来るのは enum 型など。比較可能なのでそのまま返す
            return state

    def _on_all_weekday_changed(self, state) -> None:
        if self._weekday_updating:
            return
        self._weekday_updating = True
        st = state if isinstance(state, Qt.CheckState) else Qt.CheckState(state)
        target_state = Qt.CheckState.Checked if st == Qt.CheckState.Checked else Qt.CheckState.Unchecked
        for chk in self.weekday_checks.values():
            chk.setCheckState(target_state)
        self._weekday_updating = False

    def _on_single_weekday_changed(self, _state) -> None:
        if self._weekday_updating:
            return
        self._weekday_updating = True
        all_checked = all(chk.isChecked() for chk in self.weekday_checks.values())
        self.chk_all.setCheckState(Qt.CheckState.Checked if all_checked else Qt.CheckState.Unchecked)
        self._weekday_updating = False

    def _column_alignment(self, column: int) -> Qt.AlignmentFlag:
        if column in CENTER_ALIGN_COLS:
            return Qt.AlignmentFlag.AlignCenter
        if column in RIGHT_ALIGN_COLS:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    def _set_text_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(self._column_alignment(column))
        self.table.setItem(row, column, item)

    def _set_run_item(self, row: int, checked: bool) -> None:
        item = QTableWidgetItem("")
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        item.setTextAlignment(self._column_alignment(COL_RUN))
        self.table.setItem(row, COL_RUN, item)

    def _on_header_clicked(self, index: int) -> None:
        if index != COL_RUN:
            return

        all_checked = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_RUN)
            if not item or item.checkState() != Qt.CheckState.Checked:
                all_checked = False
                break

        new_state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_RUN)
            if item:
                item.setCheckState(new_state)

    def _selected_weekdays_for_cli(self) -> list[str]:
        if self.chk_all.isChecked():
            return []
        selected = [WEEKDAY_KANJI_TO_ABBR[wd] for wd, chk in self.weekday_checks.items() if chk.isChecked()]
        return selected

    def _selected_weekdays_for_log(self) -> str:
        selected = self._selected_weekdays_for_cli()
        return " ".join(selected) if selected else "(none)"

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

        sum_s2_csv = 0
        for csv_path in csvs:
            name = csv_path.stem
            jpg_path = cross_dir / f"{name}.jpg"
            s2_cross_dir = s2_dir / name
            n_csv = len(list(s2_cross_dir.glob("*.csv"))) if s2_cross_dir.exists() else 0
            sum_s2_csv += n_csv
            s2_has_csv = n_csv > 0
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

            self._set_run_item(r, default_run)
            self._set_text_item(r, COL_NAME, name)
            self._set_text_item(r, COL_CROSS_CSV, "✔" if has_csv else "×")
            self._set_text_item(r, COL_CROSS_JPG, "✔" if has_jpg else "×")
            self._set_text_item(r, COL_S2_DIR, "✔" if has_s2_dir else "×")
            self._set_text_item(r, COL_S2_CSV, "✔" if has_s2_csv else "×")
            self._set_text_item(r, COL_OUT31, "✔" if has31 else "×")
            self._set_text_item(r, COL_OUT32, "✔" if has32 else "×")
            self._set_text_item(r, COL_STATUS, "")
            self._set_text_item(r, COL_DONE_FILES, "0")
            self._set_text_item(r, COL_TOTAL_FILES, str(n_csv))
            self._set_text_item(r, COL_WEEKDAY, "0")
            self._set_text_item(r, COL_SPLIT, "0")
            self._set_text_item(r, COL_TARGET, "0")
            self._set_text_item(r, COL_OK, "0")
            self._set_text_item(r, COL_UNK, "0")
            self._set_text_item(r, COL_NOTPASS, "0")

            info = {
                "cross_csv": str(csv_path),
                "cross_jpg": str(jpg_path),
                "s2_dir": str(s2_cross_dir),
                "out31": str(out31),
                "out32": str(out32),
            }
            name_item = self.table.item(r, COL_NAME)
            if name_item:
                name_item.setData(Qt.ItemDataRole.UserRole, info)

        self.lbl_summary.setText(f"Crossroads: {len(csvs)} / S2 CSV total: {sum_s2_csv}")
        self._log(f"[INFO] scanned: {len(csvs)} crossroads")
        self._log(f"[INFO] s2 total csv files: {sum_s2_csv}")
        self._log(f"[INFO] s2 avg per cross: {sum_s2_csv / len(csvs):.1f}")

    def _collect_targets(self) -> list[str]:
        targets: list[str] = []
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, COL_RUN)
            name_item = self.table.item(r, COL_NAME)
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
        self._log(f"[INFO] weekdays: {self._selected_weekdays_for_log()}")
        self._log(f"[INFO] start: targets={len(targets)}")
        self._log("[INFO] =======================================")

        QMessageBox.information(self, "実行開始", f"{len(targets)}交差点の処理を開始します。")
        self._start_next_crossroad()

    def _start_next_crossroad(self) -> None:
        if not self.queue:
            self._log("[INFO] 全件処理完了")
            self.current_name = None
            self.current_step = ""
            self.lbl_progress.setText("")
            self._set_run_controls_enabled(True)
            return

        self.current_name = self.queue.pop(0)
        self.current_step = "31"
        self._log(f"[START] {self.current_name}")

        row = self._row_index_by_name(self.current_name)
        if row >= 0:
            name_item = self.table.item(row, COL_NAME)
            info = name_item.data(Qt.ItemDataRole.UserRole) if name_item else {}
            info = info or {}
            cross_csv = info.get("cross_csv", "")
            s2_dir = info.get("s2_dir", "")
            # --- [INFO] 表示（ユーザー向けに分かりやすい1行） ---
            cross_file = Path(cross_csv).name if cross_csv else ""
            map_img = self.current_name
            if not map_img.lower().endswith(".jpg"):
                map_img = f"{map_img}.jpg"

            n_csv = 0
            try:
                s2 = Path(s2_dir)
                n_csv = len(list(s2.glob("*.csv"))) if s2.exists() else 0
            except Exception:
                n_csv = 0

            log_line = (
                f"[INFO] 地図画像：{map_img}"
                f"｜交差点ファイル：{cross_file}"
                f"｜第2スクリーニング後CSVフォルダ：{s2_dir}"
                f"｜第2スクリーニングファイル数：{n_csv:,}"
            )
            self._log(log_line)
            # --- end ---

        self._start_step31(self.current_name)

    def _start_step31(self, name: str) -> None:
        script31 = Path(__file__).resolve().parent / "31_crossroad_trip_performance.py"
        if not script31.exists():
            self._log(f"[ERROR] 31 script not found: {script31}")
            self._start_next_crossroad()
            return

        args = [
            str(script31),
            "--project",
            str(self.project_dir),
            "--targets",
            name,
            "--progress-step",
            "1",
        ]
        selected_weekdays = self._selected_weekdays_for_cli()
        if selected_weekdays:
            args.extend(["--weekdays", *selected_weekdays])
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
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._recent_process_lines = []
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(["-u", *args])
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.start()

    def _on_stdout(self) -> None:
        if not self.proc:
            return
        chunk = self._decode_qbytearray(self.proc.readAllStandardOutput())
        self._append_stream_chunk(chunk, is_err=False)

    def _on_stderr(self) -> None:
        if not self.proc:
            return
        chunk = self._decode_qbytearray(self.proc.readAllStandardError())
        self._append_stream_chunk(chunk, is_err=True)

    def _on_finished(self, code: int, _status) -> None:
        self._flush_process_buffers()
        self.lbl_progress.setText("")
        if self.current_name is None:
            self._start_next_crossroad()
            return

        if code != 0:
            reason = self._extract_last_error_line()
            status = f"{self.current_step} failed (code={code})"
            if reason:
                status = f"{status} / {reason}"
            self._set_status_for_current_row(status)
            self._start_next_crossroad()
            return

        if self.current_step == "31":
            self._set_status_for_current_row("31 OK")
            self.current_step = "32"
            self._start_step32(self.current_name)
            return

        self._set_status_for_current_row("完了")
        self._log(f"[DONE] {self.current_name}")
        self._start_next_crossroad()

    def _find_row_by_name(self, name: str) -> int | None:
        row = self._row_index_by_name(name)
        return row if row >= 0 else None

    def _row_index_by_name(self, name: str) -> int:
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, COL_NAME)
            if name_item and name_item.text() == name:
                return r
        return -1

    def _set_status_for_current_row(self, status: str) -> None:
        if self.current_name is None:
            return
        row = self._find_row_by_name(self.current_name)
        if row is None:
            return
        self._set_text_item(row, COL_STATUS, status)

    def _extract_last_error_line(self) -> str:
        for line in reversed(self._recent_process_lines):
            if "[ERROR]" in line:
                return line
        return ""

    def _update_table_progress(self, text: str) -> None:
        if not self.current_name:
            return
        row = self._row_index_by_name(self.current_name)
        if row < 0:
            return

        progress_match = RE_PROGRESS.search(text)
        if progress_match:
            done = int(progress_match.group(1))
            total = int(progress_match.group(2))
            self._set_text_item(row, COL_DONE_FILES, str(done))
            self._set_text_item(row, COL_TOTAL_FILES, str(total))

        stats_match = RE_STATS.search(text)
        if stats_match:
            weekday = int(stats_match.group(1))
            rows = int(stats_match.group(2))
            ok = int(stats_match.group(3))
            unk = int(stats_match.group(4))
            notpass = int(stats_match.group(5))
            target = rows + notpass
            split = rows + notpass - weekday

            if ok + unk != rows:
                self._log(
                    f"[WARN] rows mismatch: ok({ok}) + unk({unk}) != rows({rows}) "
                    f"for {self.current_name}"
                )

            self._set_text_item(row, COL_WEEKDAY, str(weekday))
            self._set_text_item(row, COL_SPLIT, str(split))
            self._set_text_item(row, COL_TARGET, str(target))
            self._set_text_item(row, COL_OK, str(ok))
            self._set_text_item(row, COL_UNK, str(unk))
            self._set_text_item(row, COL_NOTPASS, str(notpass))

    def _maybe_update_realtime_from_buffer(self, buf: str) -> None:
        idx = buf.rfind("進捗:")
        if idx < 0:
            return
        tail = buf[idx:]
        if not tail.strip():
            return
        if not RE_PROGRESS.search(tail) and not RE_STATS.search(tail):
            return
        text = tail.strip()
        self.lbl_progress.setText(text)
        self._update_table_progress(text)

    def _apply_done_summary(self, text: str) -> None:
        if not self.current_name:
            return
        row = self._row_index_by_name(self.current_name)
        if row < 0:
            return

        match = RE_DONE.search(text)
        if not match:
            return

        total_files = int(match.group(1))
        weekday = int(match.group(2))
        rows = int(match.group(3))
        ok = int(match.group(4))
        unk = int(match.group(5))
        notpass = int(match.group(6))
        target = rows + notpass
        split = rows + notpass - weekday

        if ok + unk != rows:
            self._log(
                f"[WARN] rows mismatch(done): ok({ok}) + unk({unk}) != rows({rows}) "
                f"for {self.current_name}"
            )

        self._set_text_item(row, COL_DONE_FILES, str(total_files))
        self._set_text_item(row, COL_TOTAL_FILES, str(total_files))
        self._set_text_item(row, COL_WEEKDAY, str(weekday))
        self._set_text_item(row, COL_SPLIT, str(split))
        self._set_text_item(row, COL_TARGET, str(target))
        self._set_text_item(row, COL_OK, str(ok))
        self._set_text_item(row, COL_UNK, str(unk))
        self._set_text_item(row, COL_NOTPASS, str(notpass))



def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
