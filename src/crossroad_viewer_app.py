import sys
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Column indices for performance data
COL_DATE = 3
COL_IN_BRANCH = 9
COL_OUT_BRANCH = 10
COL_SPEED = 13

# Column indices for crossroad definition data
COL_BRANCH_NO = 3
COL_DIR_DEG = 5


class ScaledPixmapLabel(QLabel):
    def __init__(self, pixmap: QPixmap | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = pixmap
        self.setAlignment(Qt.AlignCenter)
        if pixmap:
            self.setScaledContents(False)

    def setPixmap(self, pixmap: QPixmap) -> None:  # type: ignore[override]
        self._pixmap = pixmap
        super().setPixmap(pixmap)
        self._update_scaled_pixmap()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self) -> None:
        if not self._pixmap:
            return
        scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        super().setPixmap(scaled)


class MatplotlibCanvas(FigureCanvas):
    def __init__(self, parent: QWidget | None = None) -> None:
        self.fig = Figure(figsize=(5, 4))
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)

    def clear(self) -> None:
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)


class CrossroadViewer(QMainWindow):
    def __init__(self, crossroad_csv: Path, crossroad_jpg: Path, performance_csv: Path) -> None:
        super().__init__()
        self.setWindowTitle("Crossroad Performance Viewer")
        self.crossroad_path = crossroad_csv
        self.image_path = crossroad_jpg
        self.performance_path = performance_csv

        self.performance_df = pd.DataFrame()
        self.crossroad_df = pd.DataFrame()
        self.clean_df = pd.DataFrame()
        self.grouped_df = pd.DataFrame()
        self.unique_dates: list[datetime.date] = []

        self._setup_ui()
        self._load_and_prepare()

    def _setup_ui(self) -> None:
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        main_layout = QVBoxLayout(main_widget)
        header_layout = QGridLayout()

        self.crossroad_label = QLabel("Crossroad file: -")
        self.performance_label = QLabel("Performance file: -")
        self.total_days_label = QLabel("総日数: -")
        self.total_records_label = QLabel("総レコード数: -")

        header_layout.addWidget(self.crossroad_label, 0, 0)
        header_layout.addWidget(self.performance_label, 0, 1)
        header_layout.addWidget(self.total_days_label, 1, 0)
        header_layout.addWidget(self.total_records_label, 1, 1)

        main_layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, stretch=1)

        # Left splitter with image and calendar
        left_splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(left_splitter)

        self.image_label = ScaledPixmapLabel()
        self.image_label.setMinimumHeight(200)
        left_splitter.addWidget(self.image_label)

        self.calendar = QCalendarWidget()
        left_splitter.addWidget(self.calendar)

        # Right splitter with table and histogram
        right_splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(right_splitter)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "流入枝番",
            "流出枝番",
            "総台数",
            "日あたり台数",
            "平均速度(km/h)",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellClicked.connect(self._on_row_clicked)
        right_splitter.addWidget(self.table)

        self.canvas = MatplotlibCanvas()
        right_splitter.addWidget(self.canvas)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

    def _load_and_prepare(self) -> None:
        try:
            self.performance_df = pd.read_csv(self.performance_path, encoding="shift_jis")
        except Exception as exc:
            self._show_error(f"交差点パフォーマンスデータの読み込みに失敗しました: {exc}")
            return

        self.crossroad_df = self._load_crossroad_definition()
        if self.crossroad_df is None:
            return

        success = self._prepare_data()
        if not success:
            return

        self._load_image()
        self._populate_header()
        self._populate_table()
        self._highlight_calendar()

    def _load_crossroad_definition(self) -> pd.DataFrame | None:
        encodings = ["shift_jis", "cp932", "utf-8"]
        for enc in encodings:
            try:
                return pd.read_csv(self.crossroad_path, encoding=enc)
            except Exception:
                continue
        self._show_error("交差点定義ファイルの読み込みに失敗しました。")
        return None

    def _prepare_data(self) -> bool:
        try:
            date_series = self.performance_df.iloc[:, COL_DATE].astype(str).apply(self._parse_date)
            in_branch = pd.to_numeric(self.performance_df.iloc[:, COL_IN_BRANCH], errors="coerce")
            out_branch = pd.to_numeric(self.performance_df.iloc[:, COL_OUT_BRANCH], errors="coerce")
            speed = pd.to_numeric(self.performance_df.iloc[:, COL_SPEED], errors="coerce")

            data = pd.DataFrame({
                "date": date_series,
                "in_b": in_branch,
                "out_b": out_branch,
                "spd": speed,
            })
            data = data.dropna()

            data["in_b"] = data["in_b"].astype(int)
            data["out_b"] = data["out_b"].astype(int)

            self.clean_df = data
            self.unique_dates = sorted({d for d in data["date"]})

            total_days = len(self.unique_dates)
            grouped = data.groupby(["in_b", "out_b"]).agg(
                総台数=("spd", "size"),
                平均速度=("spd", "mean"),
            )
            if total_days > 0:
                grouped["日あたり台数"] = grouped["総台数"] / total_days
            else:
                grouped["日あたり台数"] = 0

            grouped = grouped.reset_index()
            grouped = grouped.sort_values(
                by=["総台数", "in_b", "out_b"],
                ascending=[False, True, True],
            )
            self.grouped_df = grouped
            return True
        except Exception as exc:
            self._show_error(f"データ処理に失敗しました: {exc}")
            return False

    def _populate_header(self) -> None:
        self.crossroad_label.setText(f"Crossroad file: {self.crossroad_path.name}")
        self.performance_label.setText(f"Performance file: {self.performance_path.name}")
        self.total_days_label.setText(f"総日数: {len(self.unique_dates)}")
        self.total_records_label.setText(f"総レコード数: {len(self.clean_df)}")

    def _populate_table(self) -> None:
        df = self.grouped_df
        self.table.setRowCount(len(df))
        for row, (_, rec) in enumerate(df.iterrows()):
            self.table.setItem(row, 0, QTableWidgetItem(str(rec["in_b"])))
            self.table.setItem(row, 1, QTableWidgetItem(str(rec["out_b"])))
            self.table.setItem(row, 2, QTableWidgetItem(str(rec["総台数"])))
            self.table.setItem(row, 3, QTableWidgetItem(f"{rec['日あたり台数']:.2f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{rec['平均速度']:.2f}"))
        self.table.resizeColumnsToContents()

    def _highlight_calendar(self) -> None:
        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor("pink"))
        for day in self.unique_dates:
            qdate = QDate.fromString(day.strftime("%Y-%m-%d"), "yyyy-MM-dd")
            if qdate.isValid():
                self.calendar.setDateTextFormat(qdate, highlight_format)

    def _on_row_clicked(self, row: int, column: int) -> None:  # noqa: ARG002
        try:
            in_b_item = self.table.item(row, 0)
            out_b_item = self.table.item(row, 1)
            if not in_b_item or not out_b_item:
                return
            in_b = int(in_b_item.text())
            out_b = int(out_b_item.text())
            self._draw_histogram(in_b, out_b)
        except Exception as exc:
            self._show_error(f"ヒストグラム描画に失敗しました: {exc}")

    def _draw_histogram(self, in_b: int, out_b: int) -> None:
        subset = self.clean_df[(self.clean_df["in_b"] == in_b) & (self.clean_df["out_b"] == out_b)]
        if subset.empty:
            self.canvas.clear()
            self.canvas.ax.text(0.5, 0.5, "データなし", ha="center", va="center")
            self.canvas.draw()
            return

        speeds = subset["spd"].tolist()
        count = len(speeds)
        avg_speed = subset["spd"].mean()

        self.canvas.clear()
        self.canvas.ax.hist(speeds, bins=20, color="skyblue", edgecolor="black")
        self.canvas.ax.set_title(f"{in_b}→{out_b} / 台数:{count} / 平均速度:{avg_speed:.1f} km/h")
        self.canvas.ax.set_xlabel("速度(km/h)")
        self.canvas.ax.set_ylabel("頻度")
        self.canvas.fig.tight_layout()
        self.canvas.draw()

    def _parse_date(self, value: str):
        text = str(value).strip()
        if not text:
            return None
        digits = re.sub(r"\D", "", text)
        if len(digits) != 8:
            return None
        try:
            return datetime.strptime(digits, "%Y%m%d").date()
        except ValueError:
            return None

    def _load_image(self) -> None:
        if not self.image_path.exists():
            return
        pixmap = QPixmap(str(self.image_path))
        if pixmap.isNull():
            return
        self.image_label.setPixmap(pixmap)

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Error", message)


def pick_three_files() -> tuple[Path, Path, Path] | None:
    while True:
        csv_path, _ = QFileDialog.getOpenFileName(
            None, "交差点ファイル（*.csv）を選択", "", "CSV (*.csv)"
        )
        if not csv_path:
            return None

        img_path, _ = QFileDialog.getOpenFileName(
            None,
            "交差点画像（*.jpg）を選択",
            str(Path(csv_path).parent),
            "Images (*.jpg *.jpeg *.png *.bmp)",
        )
        if not img_path:
            return None

        perf_path, _ = QFileDialog.getOpenFileName(
            None,
            "交差点パフォーマンス（*_performance.csv）を選択",
            str(Path(csv_path).parent),
            "CSV (*.csv)",
        )
        if not perf_path:
            return None

        csv_p = Path(csv_path)
        img_p = Path(img_path)
        perf_p = Path(perf_path)

        base = csv_p.stem
        ok = (img_p.stem == base) and (perf_p.stem == f"{base}_performance")
        if ok:
            return csv_p, img_p, perf_p

        QMessageBox.warning(
            None,
            "ファイル名が一致しません",
            f"選択ルール：\n"
            f"- {base}.csv\n"
            f"- {base}.jpg\n"
            f"- {base}_performance.csv\n\n"
            f"もう一度選び直してください。",
        )


def main() -> None:
    app = QApplication(sys.argv)
    picked = pick_three_files()
    if picked is None:
        sys.exit(0)
    crossroad_csv, crossroad_jpg, performance_csv = picked
    viewer = CrossroadViewer(crossroad_csv, crossroad_jpg, performance_csv)
    viewer.resize(1200, 800)
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
