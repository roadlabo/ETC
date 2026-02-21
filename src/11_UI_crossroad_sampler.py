import base64
import json
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QPropertyAnimation, Qt, QTimer, QUrl, QUrlQuery, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtGui import QPixmap
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
    QGraphicsOpacityEffect,
    QSplitter,
    QSpacerItem,
    QSizePolicy,
)

APP_TITLE = "11 交差点ファイル作成ツール"
FOLDER_CROSS = "11_交差点(Point)データ"
DUPLICATE_MSG = "その交差点名は既に存在します。別名で保存してください。"

COL_NAME = 0
COL_CSV = 1
COL_JPG = 2

GREEN_LABEL_STYLE = """
QLabel {
    color: #00d26a;
    font-weight: bold;
    font-size: 13px;
}
"""


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
        overwrite = bool(payload.get("overwrite", False))
        csv_text = payload.get("csv_text", "")
        jpg_data_url = payload.get("jpg_data_url", "")

        if not base_name:
            self.error.emit("交差点名を入力してください。")
            return
        if not self.window.cross_dir:
            self.error.emit("先にプロジェクトを選択してください。")
            return

        csv_path = self.window.cross_dir / f"{base_name}.csv"
        jpg_path = self.window.cross_dir / f"{base_name}.jpg"
        if (not overwrite) and (csv_path.exists() or jpg_path.exists()):
            self.error.emit(DUPLICATE_MSG)
            return

        try:
            csv_norm = str(csv_text).replace("\r\n", "\n").replace("\r", "\n")
            csv_norm = csv_norm.rstrip("\n") + "\n"
            with open(csv_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(csv_norm)
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
        self.editing_name: str | None = None
        self.html_path = Path(__file__).resolve().parent / "11_crossroad_sampler.html"

        self._build_ui()
        self.splash = None
        self._corner_logo_visible = False
        self._pix_small = None
        self._logo_phase = ""
        self.LOGO_CORNER_PAD = 8
        self.LOGO_CORNER_DX = -10
        self.LOGO_CORNER_DY = -4
        QTimer.singleShot(0, self._init_logo_overlay)
        self._setup_web_channel()

    def _init_logo_overlay(self) -> None:
        logo_path = Path(__file__).resolve().parent / "logo.png"
        if not logo_path.exists():
            return

        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return

        pix_big = pixmap.scaledToHeight(320, Qt.TransformationMode.SmoothTransformation)
        self._pix_small = pixmap.scaledToHeight(110, Qt.TransformationMode.SmoothTransformation)

        self.splash = QLabel(self)
        self.splash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash.setStyleSheet("background: transparent;")
        self.splash.setPixmap(pix_big)
        self.splash.adjustSize()

        x, y = self._logo_center_pos(self.splash.width(), self.splash.height())
        self.splash.move(x, y)
        self._logo_phase = "center"
        self.splash.show()

        effect = QGraphicsOpacityEffect(self.splash)
        self.splash.setGraphicsEffect(effect)

        fade_in = QPropertyAnimation(effect, b"opacity", self)
        fade_in.setDuration(500)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)

        def start_fade_out():
            fade_out = QPropertyAnimation(effect, b"opacity", self)
            fade_out.setDuration(500)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)

            def show_corner_logo():
                self.splash.deleteLater()
                self._show_corner_logo()

            fade_out.finished.connect(show_corner_logo)
            fade_out.start()

        fade_in.finished.connect(lambda: QTimer.singleShot(3000, start_fade_out))
        fade_in.start()

    def _show_corner_logo(self) -> None:
        if not self._pix_small:
            return

        self.splash = QLabel(self)
        self.splash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash.setStyleSheet("background: transparent;")
        self.splash.setPixmap(self._pix_small)
        self.splash.adjustSize()

        x, y = self._logo_corner_pos(self.splash.width(), self.splash.height())
        self.splash.move(x, y)
        self.splash.show()

        self._corner_logo_visible = True
        self._logo_phase = "corner"

    def _logo_center_pos(self, w: int, h: int):
        r = self.rect()
        x = (r.width() - w) // 2
        y = (r.height() - h) // 2
        return x, y

    def _logo_corner_pos(self, w: int, h: int):
        r = self.rect()
        pad = getattr(self, "LOGO_CORNER_PAD", 8)
        dx = getattr(self, "LOGO_CORNER_DX", -10)
        dy = getattr(self, "LOGO_CORNER_DY", -4)
        x = r.width() - w - pad + dx
        y = pad + dy
        return x, y

    def resizeEvent(self, event):
        super().resizeEvent(event)

        if hasattr(self, "_refresh_about_text"):
            try:
                self._refresh_about_text()
            except Exception:
                pass

        splash = getattr(self, "splash", None)
        phase = getattr(self, "_logo_phase", "")

        # 中央表示中
        if splash and phase == "center":
            x, y = self._logo_center_pos(splash.width(), splash.height())
            splash.move(x, y)

        # 右上常駐中
        if splash and phase == "corner" and getattr(self, "_corner_logo_visible", False):
            x, y = self._logo_corner_pos(splash.width(), splash.height())
            splash.move(x, y)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main_hbox = QHBoxLayout(root)

        left_panel = QWidget()
        left_vbox = QVBoxLayout(left_panel)
        left_vbox.setContentsMargins(0, 0, 0, 0)
        left_vbox.setSpacing(6)

        self.btn_project = QPushButton("選択")
        self.btn_project.clicked.connect(self.select_project)

        self.project_path_edit = QLineEdit()
        self.project_path_edit.setReadOnly(True)
        self.project_path_edit.setPlaceholderText("フォルダが選択されていません")

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["交差点名", "CSV", "JPG"])
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_CSV, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_JPG, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        left_vbox.addWidget(self.table, stretch=1)

        row = QHBoxLayout()
        self.btn_edit = QPushButton("編集")
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_rename = QPushButton("リネーム")
        self.btn_rename.clicked.connect(self.rename_selected)
        self.btn_delete = QPushButton("削除")
        self.btn_delete.clicked.connect(self.delete_selected)
        row.addWidget(self.btn_edit)
        row.addWidget(self.btn_rename)
        row.addWidget(self.btn_delete)
        left_vbox.addLayout(row, stretch=0)

        right_panel = QWidget()
        right_vbox = QVBoxLayout(right_panel)
        right_vbox.setContentsMargins(0, 0, 0, 0)
        right_vbox.setSpacing(6)

        header_panel = QWidget()
        header_vbox = QVBoxLayout(header_panel)
        header_vbox.setContentsMargins(0, 0, 0, 0)
        header_vbox.setSpacing(6)

        # --- ①プロジェクトフォルダ選択（横並び・改行なし） ---
        row_project = QHBoxLayout()
        lbl_project_title = QLabel("①プロジェクトフォルダ選択")
        lbl_project_title.setStyleSheet(GREEN_LABEL_STYLE)
        row_project.addWidget(lbl_project_title)

        row_project.addWidget(self.btn_project)
        row_project.addWidget(self.project_path_edit, 1)
        row_project.addStretch(0)
        header_vbox.addLayout(row_project)

        row_map = QHBoxLayout()
        self.lbl_guide = QLabel("②地図上を左クリック：中心点指定・方向追加　右クリック：方向指定やり直し　中心点の指定から全てやり直しは右のクリアボタン　=>")
        self.lbl_guide.setStyleSheet(GREEN_LABEL_STYLE)
        row_map.addWidget(self.lbl_guide)
        row_map.addStretch(1)

        self.btn_clear = QPushButton("クリア")
        self.btn_clear.clicked.connect(self.clear_clicked)
        row_map.addWidget(self.btn_clear)
        header_vbox.addLayout(row_map)

        row_name = QHBoxLayout()

        lbl_name = QLabel("③交差点名")
        lbl_name.setStyleSheet(GREEN_LABEL_STYLE)
        row_name.addWidget(lbl_name)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例：01○○交差点")
        row_name.addWidget(self.name_edit, 1)

        self.btn_save = QPushButton("交差点ファイル保存")
        self.btn_save.clicked.connect(self.save_clicked)
        row_name.addWidget(self.btn_save)

        row_name.addStretch(0)
        header_vbox.addLayout(row_name)

        lbl_next = QLabel("④次の交差点を作成してください。または、左の一覧をクリックして、編集・リネーム・削除を行って下さい。")
        lbl_next.setStyleSheet(GREEN_LABEL_STYLE)
        lbl_next.setWordWrap(True)
        header_vbox.addWidget(lbl_next)

        header_container = QWidget()
        header_hbox = QHBoxLayout(header_container)
        header_hbox.setContentsMargins(0, 0, 0, 0)
        header_hbox.addWidget(header_panel, 1)
        header_hbox.addSpacerItem(QSpacerItem(320, 1, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum))

        right_vbox.addWidget(header_container, stretch=0)

        self.web = QWebEngineView()
        # --- allow file:// HTML to load https resources (Leaflet/OSM tiles) ---
        s = self.web.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        right_vbox.addWidget(self.web, stretch=1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 1400])
        main_hbox.addWidget(splitter)

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

        self.project_path_edit.setText(str(self.project_dir))
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
            self._show_error("交差点名を入力してください。")
            return

        overwrite = self.editing_name is not None and base_name == self.editing_name

        csv_path = self.cross_dir / f"{base_name}.csv"
        jpg_path = self.cross_dir / f"{base_name}.jpg"
        if (not overwrite) and (csv_path.exists() or jpg_path.exists()):
            self._show_error(DUPLICATE_MSG)
            return

        js = f"beginSaveFromPy({json.dumps(base_name)}, {str(overwrite).lower()})"
        self.web.page().runJavaScript(js)

    def on_saved(self, base_name: str) -> None:
        self.editing_name = None
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
        self.editing_name = None
        self.name_edit.clear()
        self.web.page().runJavaScript("clearAll()")

    def edit_selected(self) -> None:
        if not self.cross_dir:
            self._show_error("先にプロジェクトを選択してください。")
            return

        name = self.selected_name()
        if not name:
            self._show_error("編集対象を選択してください。")
            return

        csv_path = self.cross_dir / f"{name}.csv"
        if not csv_path.exists():
            self._show_error("CSVファイルが見つかりません。")
            return

        try:
            csv_text = csv_path.read_text(encoding="utf-8")
        except Exception as exc:
            self._show_error(f"CSVの読み込みに失敗しました: {exc}")
            return

        self.editing_name = name
        self.name_edit.setText(name)
        js = f"loadFromCsvText({json.dumps(csv_text)})"
        self.web.page().runJavaScript(js)

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

        new_name, ok = QInputDialog.getText(self, "リネーム", "新しい交差点名", text=old_name)
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
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
