from __future__ import annotations

import csv
import os
import re
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QProcess, QTimer, Qt
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
START_FULLSCREEN = True
RE_PROGRESS = re.compile(r"\[PROGRESS\]\s+done=(\d+)\s+total=(\d+)\s+file=(.+)")
RE_TOTAL = re.compile(r"\[TOTAL\]\s+total=(\d+)")
RE_HIT = re.compile(r"\[HIT\]\s+op_id=(\S+)\s+zone=(.+?)\s+hit_count=(\d+)")
RE_ZONE_COUNT = re.compile(r"\[INFO\]\s+有効ゾーン数:\s*(\d+)")


def _normalize_log_line(text: str) -> str:
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def format_seconds_hybrid(sec: int) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}時間{m}分{s}秒"
    if m > 0:
        return f"{m}分{s}秒"
    return f"{s}秒"


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
        self.resize(1600, 900)

        self.proc: QProcess | None = None
        self.input_folder: Path | None = None
        self.zoning_file: Path | None = None
        self.output_csv: Path | None = None
        self.total_files = 0
        self.done_files = 0
        self.hit_count = 0
        self.latest_hit_zone = "-"
        self.zone_count = "（読込後に表示）"
        self.current_file = "-"
        self.is_running = False
        self.run_started_at: float | None = None
        self.spinner_frames = ["◐", "◓", "◑", "◒"]
        self.spinner_index = 0
        self._last_log_line = ""
        self._stdout_buffer = ""
        self._hit_highlight_ticks = 0

        self._build_ui()
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_runtime_status)
        self.ui_timer.start(500)

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

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        left_panel = QWidget()
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

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
        s1.addLayout(r1)
        s1.addWidget(self.lbl_csv_count)
        s1.addWidget(self.lbl_csv_warn)
        box1 = StepBox("STEP1\n第1スクリーニングフォルダを選択\n運行ID別CSVが格納されたフォルダを指定してください。", s1w)

        s2w = QWidget(); s2 = QVBoxLayout(s2w); s2.setContentsMargins(0, 0, 0, 0); s2.setSpacing(8)
        r2 = QHBoxLayout()
        self.btn_pick_zoning = QPushButton("選択")
        self.btn_pick_zoning.clicked.connect(self.pick_zoning)
        self.lbl_zoning = QLabel("未選択")
        self.lbl_zoning.setWordWrap(True)
        r2.addWidget(self.btn_pick_zoning)
        r2.addWidget(self.lbl_zoning, 1)
        s2.addLayout(r2)

        self.zone_card = QFrame()
        self.zone_card.setObjectName("zoneCard")
        zone_l = QVBoxLayout(self.zone_card)
        zone_l.setContentsMargins(10, 8, 10, 8)
        self.lbl_zone_card_title = QLabel("ゾーニング情報")
        self.lbl_zone_card_title.setObjectName("zoneCardTitle")
        self.lbl_zone_count = QLabel(f"ゾーン数: {self.zone_count}")
        self.lbl_hit_count = QLabel("現在HITした運行ID数: 0")
        self.lbl_hit_count.setObjectName("hitCount")
        self.lbl_latest_zone = QLabel("最新HITゾーン: -")
        self.lbl_zoning_name = QLabel("ゾーニングファイル: 未選択")
        self.lbl_zoning_name.setWordWrap(True)
        for w in [self.lbl_zone_count, self.lbl_hit_count, self.lbl_latest_zone, self.lbl_zoning_name]:
            zone_l.addWidget(w)
        zone_l.insertWidget(0, self.lbl_zone_card_title)
        s2.addWidget(self.zone_card)

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
        for i in range(4):
            steps.setColumnStretch(i, 1)

        left_lay.addWidget(steps_host)

        info_card = QFrame()
        info_card.setObjectName("infoCard")
        info_l = QVBoxLayout(info_card)
        info_l.setContentsMargins(12, 10, 12, 10)
        self.lbl_progress = QLabel("進捗ファイル: 0/0（0.0%）")
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        info_l.addWidget(self.lbl_progress)
        info_l.addWidget(self.progress)
        left_lay.addWidget(info_card)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 10))
        self.log.setMaximumBlockCount(3000)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        left_lay.addWidget(self.log, 1)

        self.lbl_notice = QLabel(
            "この機能はあくまで推定拠点であり、実際の居住地・事業所所在地を直接示すものではありません。\n"
            "GPS誤差や夜間運用形態等により判定には限界があります。分析結果は補助的情報として慎重に利用してください。"
        )
        self.lbl_notice.setWordWrap(True)
        left_lay.addWidget(self.lbl_notice)

        right_panel = QFrame()
        right_panel.setObjectName("statusCard")
        right_panel.setMinimumWidth(260)
        right_l = QVBoxLayout(right_panel)
        right_l.setContentsMargins(12, 12, 12, 12)
        right_l.setSpacing(8)
        lbl_status_title = QLabel("実行ステータス")
        lbl_status_title.setObjectName("zoneCardTitle")
        right_l.addWidget(lbl_status_title)

        self.lbl_elapsed = QLabel("経過時間: 0秒")
        self.lbl_remaining = QLabel("残り時間: --")
        self.lbl_total = QLabel("対象CSV数: 0")
        self.lbl_done = QLabel("処理済CSV数: 0")
        self.lbl_spinner = QLabel("待機")
        self.lbl_spinner.setObjectName("spinner")
        self.lbl_current = QLabel("現在処理中: -")
        self.lbl_current.setWordWrap(True)

        for w in [self.lbl_elapsed, self.lbl_remaining, self.lbl_total, self.lbl_done, self.lbl_spinner, self.lbl_current]:
            right_l.addWidget(w)
        right_l.addStretch(1)

        body.addWidget(left_panel, 5)
        body.addWidget(right_panel, 1)

        self.setStyleSheet(
            """
            QWidget { background:#060b09; color:#8ee5ac; }
            QFrame#hero, QFrame#infoCard, QFrame#stepBox, QFrame#statusCard { background:#0b1511; border:1px solid #1f3f2d; border-radius:12px; }
            QFrame#zoneCard { background:#06130f; border:1px solid #2ed9b5; border-radius:10px; }
            QLabel#heroTitle { font-size:22px; font-weight:800; color:#dcffe9; }
            QLabel#heroDesc { color:#9cd9b5; }
            QLabel#stepTitle { font-weight:700; color:#7cffc6; }
            QLabel#warn { color:#ffd866; font-weight:700; }
            QLabel#zoneCardTitle { font-size:14px; font-weight:800; color:#6dffe3; }
            QLabel#hitCount { font-size:15px; font-weight:800; color:#c3ff6b; }
            QLabel#spinner { font-size:18px; font-weight:900; color:#6dffe3; }
            QPushButton {
                background:#0f2a1c; border:2px solid #00ff99; border-radius:10px;
                color:#eafff4; font-weight:700; padding:8px 12px;
            }
            QPushButton:hover { background:#18412d; }
            QPushButton:disabled { border-color:#2a6b45; color:#3d6a55; background:#0b1511; }
            QPlainTextEdit {
                background:#0a120f; border:1px solid #1f3f2d; border-radius:10px;
                color:#c6f8d8; padding:4px; line-height:1.15;
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
        line = _normalize_log_line(text)
        if not line or line == self._last_log_line:
            return
        self._last_log_line = line
        self.log.appendPlainText(line)

    def _count_zones_in_file(self, path: Path) -> int:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
        except Exception:
            return 0
        if not rows:
            return 0
        body = rows[1:] if rows and any(x for x in rows[0]) else rows
        return len([r for r in body if any(str(c).strip() for c in r)])

    def recount_target_csvs(self) -> int:
        if not self.input_folder:
            return 0
        gen = self.input_folder.rglob("*.csv") if self.chk_recursive.isChecked() else self.input_folder.glob("*.csv")
        excluded_names = {"zoning_data.csv"}
        count = 0
        for p in gen:
            if not p.is_file():
                continue
            if p.name in excluded_names or p.name.endswith("_拠点ゾーン.csv"):
                continue
            if self.output_csv and p == self.output_csv:
                continue
            count += 1
        return count

    def _update_run_button_state(self) -> None:
        running = self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning
        ready = self.input_folder is not None and self.zoning_file is not None and self.total_files > 0
        self.btn_run.setEnabled((not running) and ready)

    def _recalc_csv_count(self) -> None:
        self.total_files = self.recount_target_csvs()
        self.lbl_csv_count.setText(f"対象CSV数: {self.total_files:,}")
        self.lbl_total.setText(f"対象CSV数: {self.total_files:,}")
        self.lbl_csv_warn.setText("[WARN] 対象CSVが0件です" if self.total_files == 0 else "")
        self._update_run_button_state()

    def _update_progress_ui(self) -> None:
        total = max(self.total_files, 0)
        done = max(min(self.done_files, total if total > 0 else self.done_files), 0)
        pct = (done * 100.0 / total) if total > 0 else 0.0
        self.progress.setValue(int(pct))
        self.lbl_progress.setText(f"進捗ファイル: {done:,}/{total:,}（{pct:.1f}%）")
        self.lbl_total.setText(f"対象CSV数: {total:,}")
        self.lbl_done.setText(f"処理済CSV数: {done:,}")

    def _reset_zoning_card(self) -> None:
        self.hit_count = 0
        self.latest_hit_zone = "-"
        self.lbl_hit_count.setText("現在HITした運行ID数: 0")
        self.lbl_latest_zone.setText("最新HITゾーン: -")

    def pick_folder(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "第1スクリーニングフォルダを選択")
        if not p:
            return
        self.input_folder = Path(p)
        self.lbl_folder.setText(str(self.input_folder))
        self._update_output_path()
        self._recalc_csv_count()

    def pick_zoning(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "任意ゾーニングファイルを選択", "", "CSV (*.csv);;All Files (*)")
        if not p:
            return
        self.zoning_file = Path(p)
        self.lbl_zoning.setText(str(self.zoning_file))
        self.lbl_zoning_name.setText(f"ゾーニングファイル: {self.zoning_file.name}")
        c = self._count_zones_in_file(self.zoning_file)
        self.zone_count = f"{c:,}" if c > 0 else "（読込後に表示）"
        self.lbl_zone_count.setText(f"ゾーン数: {self.zone_count}")
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

        self._update_output_path()
        self.total_files = self.recount_target_csvs()
        if self.total_files <= 0:
            self.lbl_progress.setText("進捗ファイル: 0/0（0.0%） 対象CSVがありません")
            QMessageBox.warning(self, "入力不足", "対象CSVがありません。フォルダ設定を見直してください。")
            return

        self.done_files = 0
        self.current_file = "-"
        self.run_started_at = time.time()
        self.is_running = True
        self.spinner_index = 0
        self._stdout_buffer = ""
        self._last_log_line = ""
        self._reset_zoning_card()
        self._update_progress_ui()
        self.lbl_remaining.setText("残り時間: 算出中...")
        self.lbl_elapsed.setText("経過時間: 0秒")
        self.lbl_current.setText("現在処理中: -")
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

    def _process_log_line(self, line: str) -> None:
        mt = RE_TOTAL.search(line)
        if mt:
            self.total_files = int(mt.group(1))
            self._update_progress_ui()

        mp = RE_PROGRESS.search(line)
        if mp:
            self.done_files = int(mp.group(1))
            self.total_files = int(mp.group(2))
            self.current_file = _normalize_log_line(mp.group(3))
            self.lbl_current.setText(f"現在処理中: {self.current_file}")
            self._update_progress_ui()

        mh = RE_HIT.search(line)
        if mh:
            self.hit_count = int(mh.group(3))
            self.latest_hit_zone = _normalize_log_line(mh.group(2))
            self.lbl_hit_count.setText(f"現在HITした運行ID数: {self.hit_count:,}")
            self.lbl_latest_zone.setText(f"最新HITゾーン: {self.latest_hit_zone}")
            self._hit_highlight_ticks = 4

        mz = RE_ZONE_COUNT.search(line)
        if mz:
            self.zone_count = f"{int(mz.group(1)):,}"
            self.lbl_zone_count.setText(f"ゾーン数: {self.zone_count}")

        if line.startswith("[INFO] 現在処理中:"):
            self.current_file = line.split(":", 1)[-1].strip()
            self.lbl_current.setText(f"現在処理中: {self.current_file}")

    def on_output(self) -> None:
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="ignore")
        combined = self._stdout_buffer + data
        rows = combined.split("\n")
        self._stdout_buffer = rows.pop() if rows else ""
        for raw in rows:
            line = _normalize_log_line(raw)
            if not line:
                continue
            self._process_log_line(line)
            self.append_log(line)

    def update_runtime_status(self) -> None:
        if self.is_running:
            self.spinner_index = (self.spinner_index + 1) % len(self.spinner_frames)
            self.lbl_spinner.setText(f"稼働中 {self.spinner_frames[self.spinner_index]}")
            if self.run_started_at:
                elapsed = int(time.time() - self.run_started_at)
                self.lbl_elapsed.setText(f"経過時間: {format_seconds_hybrid(elapsed)}")
                if self.done_files > 0 and self.total_files > 0:
                    avg_sec_per_file = elapsed / self.done_files
                    remaining_files = max(self.total_files - self.done_files, 0)
                    eta_sec = int(avg_sec_per_file * remaining_files)
                    self.lbl_remaining.setText(f"残り時間: {format_seconds_hybrid(eta_sec)}")
                else:
                    self.lbl_remaining.setText("残り時間: 算出中...")
        else:
            self.lbl_spinner.setText("停止 ●")

        if self._hit_highlight_ticks > 0:
            self.lbl_hit_count.setStyleSheet("color:#e8ff75;")
            self._hit_highlight_ticks -= 1
        else:
            self.lbl_hit_count.setStyleSheet("")

    def on_finished(self, code: int, _status) -> None:
        if self._stdout_buffer:
            line = _normalize_log_line(self._stdout_buffer)
            if line:
                self._process_log_line(line)
                self.append_log(line)
            self._stdout_buffer = ""

        self._set_enabled(True)
        self.is_running = False
        ok = code == 0
        if ok:
            self.done_files = max(self.done_files, self.total_files)
            self._update_progress_ui()
            self.lbl_remaining.setText("残り時間: 完了")
            self.append_log("[INFO] 出力完了")
            if self.output_csv:
                self.append_log(f"[INFO] 出力ファイル: {self.output_csv}")
            self.btn_open.setEnabled(self.output_csv is not None and self.output_csv.exists())
            QMessageBox.information(self, "完了", "出力完了しました。")
        else:
            self.lbl_remaining.setText("残り時間: --")
            self.append_log("[ERROR] 処理が異常終了しました。ログを確認してください。")
            QMessageBox.warning(self, "エラー", "処理中にエラーが発生しました。ログを確認してください。")
        self._update_run_button_state()

    def open_output(self) -> None:
        if self.output_csv and self.output_csv.exists():
            os.startfile(str(self.output_csv))


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    if START_FULLSCREEN:
        w.showFullScreen()
    else:
        w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
