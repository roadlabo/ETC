import base64
import json
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QUrl, QUrlQuery, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

APP_TITLE = "11[UI] 交差点サンプラー"
FOLDER_CROSS = "11_交差点(Point)データ"
DUPLICATE_MSG = "その交差点名は既に存在します。別名で保存してください。"

COL_NAME = 0
COL_CSV = 1
COL_JPG = 2


class Bridge(QObject):
    saved = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window

    @pyqtSlot()
    def jsReady(self) -> None:
        return

    @pyqtSlot(str)
    def requestSave(self, json_str: str) -> None:
        try:
            payload = json.loads(json_str)
        except json.JSONDecodeError:
            self.error.emit("保存データの解析に失敗しました。")
            return

        base_name = str(payload.get("base_name", "")).strip()
        csv_text = payload.get("csv_text", "")
        jpg_data_url = payload.get("jpg_data_url", "")

        if not base_name:
            self.error.emit("出力ファイル名を入力してください。")
            return
        if not self.window.cross_dir:
            self.error.emit("先にプロジェクトを選択してください。")
            return

        csv_path = self.window.cross_dir / f"{base_name}.csv"
        jpg_path = self.window.cross_dir / f"{base_name}.jpg"
        if csv_path.exists() or jpg_path.exists():
            self.error.emit(DUPLICATE_MSG)
            return

        try:
            csv_path.write_text(str(csv_text), encoding="utf-8")
            image_b64 = str(jpg_data_url).split(",", 1)[1]
            jpg_path.write_bytes(base64.b64decode(image_b64))
        except Exception as exc:
            self.error.emit(f"保存に失敗しました: {exc}")
            return

        self.saved.emit(base_name)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 800)

        self.project_dir: Path | None = None
        self.cross_dir: Path | None = None
        self.html_path = Path(__file__).resolve().parent / "11_crossroad_sampler.html"

        self._build_ui()
        self._setup_web_channel()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)

        left = QVBoxLayout()
        layout.addLayout(left, stretch=1)

        self.btn_project = QPushButton("① プロジェクト選択")
        self.btn_project.clicked.connect(self.select_project)
        left.addWidget(self.btn_project)

        self.lbl_project = QLabel("Project: (未選択)")
        left.addWidget(self.lbl_project)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["交差点名", "CSV", "JPG"])
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_CSV, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_JPG, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        left.addWidget(self.table, stretch=1)

        row = QHBoxLayout()
        self.btn_rename = QPushButton("リネーム")
        self.btn_rename.clicked.connect(self.rename_selected)
        self.btn_delete = QPushButton("削除")
        self.btn_delete.clicked.connect(self.delete_selected)
        row.addWidget(self.btn_rename)
        row.addWidget(self.btn_delete)
        left.addLayout(row)

        right = QVBoxLayout()
        layout.addLayout(right, stretch=2)

        top = QHBoxLayout()
        right.addLayout(top)
        top.addWidget(QLabel("出力ファイル名"))

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例: crossroad001")
        top.addWidget(self.name_edit, stretch=1)

        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self.save_clicked)
        top.addWidget(self.btn_save)

        self.btn_clear = QPushButton("全消去")
        self.btn_clear.clicked.connect(self.clear_clicked)
        top.addWidget(self.btn_clear)

        self.lbl_guide = QLabel("左クリック：中心 / 方向追加　右クリック：やり直し")
        right.addWidget(self.lbl_guide)

        self.web = QWebEngineView()
        right.addWidget(self.web, stretch=1)

    def _setup_web_channel(self) -> None:
        self.channel = QWebChannel(self.web.page())
        self.bridge = Bridge(self)
        self.bridge.saved.connect(self.on_saved)
        self.bridge.error.connect(self.on_bridge_error)
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)

    def _show_error(self, msg: str) -> None:
        QMessageBox.warning(self, "注意", msg)

    def _set_yes_no(self, row: int, col: int, ok: bool) -> None:
        item = QTableWidgetItem("✔" if ok else "×")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, col, item)

    def select_project(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択", str(Path.cwd()))
        if not selected:
            return
        self.project_dir = Path(selected).resolve()
        self.cross_dir = self.project_dir / FOLDER_CROSS
        self.cross_dir.mkdir(parents=True, exist_ok=True)

        self.lbl_project.setText(f"Project: {self.project_dir}")
        self.load_html()
        self.scan_crossroads()

    def load_html(self) -> None:
        if not self.html_path.exists():
            self._show_error(f"HTMLファイルが見つかりません:\n{self.html_path}")
            return
        url = QUrl.fromLocalFile(str(self.html_path))
        query = QUrlQuery()
        query.addQueryItem("embed", "1")
        url.setQuery(query)
        self.web.setUrl(url)

    def scan_crossroads(self) -> None:
        self.table.setRowCount(0)
        if not self.cross_dir or not self.cross_dir.exists():
            return

        names = set()
        for p in self.cross_dir.glob("*.csv"):
            names.add(p.stem)
        for p in self.cross_dir.glob("*.jpg"):
            names.add(p.stem)

        for name in sorted(names):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, COL_NAME, QTableWidgetItem(name))
            self._set_yes_no(row, COL_CSV, (self.cross_dir / f"{name}.csv").exists())
            self._set_yes_no(row, COL_JPG, (self.cross_dir / f"{name}.jpg").exists())

    def selected_name(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, COL_NAME)
        return item.text().strip() if item else None

    def save_clicked(self) -> None:
        if not self.cross_dir:
            self._show_error("先にプロジェクトを選択してください。")
            return

        base_name = self.name_edit.text().strip()
        if not base_name:
            self._show_error("出力ファイル名を入力してください。")
            return

        csv_path = self.cross_dir / f"{base_name}.csv"
        jpg_path = self.cross_dir / f"{base_name}.jpg"
        if csv_path.exists() or jpg_path.exists():
            self._show_error(DUPLICATE_MSG)
            return

        js = f"beginSaveFromPy({json.dumps(base_name)})"
        self.web.page().runJavaScript(js)

    def on_saved(self, base_name: str) -> None:
        self.name_edit.clear()
        self.web.page().runJavaScript("clearAll()")
        self.scan_crossroads()

        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            if item and item.text() == base_name:
                self.table.selectRow(row)
                break

    def on_bridge_error(self, msg: str) -> None:
        self._show_error(msg)

    def clear_clicked(self) -> None:
        self.name_edit.clear()
        self.web.page().runJavaScript("clearAll()")

    def delete_selected(self) -> None:
        if not self.cross_dir:
            self._show_error("先にプロジェクトを選択してください。")
            return
        name = self.selected_name()
        if not name:
            self._show_error("削除対象を選択してください。")
            return

        reply = QMessageBox.question(self, "確認", f"{name} を削除しますか？")
        if reply != QMessageBox.StandardButton.Yes:
            return

        for ext in (".csv", ".jpg"):
            path = self.cross_dir / f"{name}{ext}"
            if path.exists():
                path.unlink()
        self.scan_crossroads()

    def rename_selected(self) -> None:
        if not self.cross_dir:
            self._show_error("先にプロジェクトを選択してください。")
            return
        old_name = self.selected_name()
        if not old_name:
            self._show_error("リネーム対象を選択してください。")
            return

        new_name, ok = QInputDialog.getText(self, "リネーム", "新しい交差点名")
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            self._show_error("新しい交差点名を入力してください。")
            return
        if new_name == old_name:
            return

        new_csv = self.cross_dir / f"{new_name}.csv"
        new_jpg = self.cross_dir / f"{new_name}.jpg"
        if new_csv.exists() or new_jpg.exists():
            self._show_error(DUPLICATE_MSG)
            return

        old_csv = self.cross_dir / f"{old_name}.csv"
        old_jpg = self.cross_dir / f"{old_name}.jpg"
        if old_csv.exists():
            old_csv.rename(new_csv)
        if old_jpg.exists():
            old_jpg.rename(new_jpg)

        self.scan_crossroads()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            if item and item.text() == new_name:
                self.table.selectRow(row)
                break


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
