import json
import re
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QObject, QPropertyAnimation, Qt, QTimer, QUrl, QUrlQuery, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QDoubleSpinBox, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from common.news.news_dialog import show_news_dialogs
from common.news.news_fetcher import news_debug
from common.ui.logo_link import ClickableLogoLabel

APP_TITLE = "10 ルートファイル作成ツール"
FOLDER_ROUTE = "10_ルート(Route)データ"
DUPLICATE_MSG = "そのルート名は既に存在します。別名で保存してください。"
COL_NAME, COL_CSV, COL_PITCH, COL_POINTS = range(4)
CORNER_LOGO_HEIGHT = 86
CORNER_LOGO_RESERVED_WIDTH = 200

CYBER_QSS = """
QMainWindow, QWidget { background:#0b0f14; color:#e6f1ff; font-family:"Segoe UI","Meiryo UI","Consolas"; font-size:12px; }
QPushButton { background:rgba(10,18,26,.92); border:1px solid rgba(0,255,136,.55); border-radius:10px; padding:7px 12px; color:#e6f1ff; font-weight:700; }
QPushButton:hover { border:1px solid rgba(0,255,136,.95); background:rgba(0,255,136,.08); }
QLineEdit, QDoubleSpinBox { background:rgba(5,10,16,.9); border:1px solid rgba(42,115,255,.55); border-radius:10px; padding:7px 10px; }
QTableWidget { background:rgba(5,10,16,.85); border:1px solid rgba(42,115,255,.35); border-radius:12px; gridline-color:rgba(42,115,255,.18); }
QHeaderView::section { background:rgba(10,18,26,.95); color:#00ff88; border:0; padding:8px 10px; font-weight:900; }
QTableWidget::item:selected { background:rgba(0,255,136,.18); color:#e6f1ff; }
QSplitter::handle { background:rgba(42,115,255,.20); }
QFrame#StepCard { background:rgba(5,10,16,.88); border:2px solid rgba(0,255,136,.55); border-radius:14px; }
QLabel#StepTitle { color:#00ff88; font-weight:900; font-size:13px; }
QLabel#StepBody { color:rgba(230,241,255,.92); font-weight:600; font-size:12px; }
"""


def sanitize_base_name(name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name.strip()).strip(" ._")
    return safe or "route"


def pitch_label(pitch_m: float) -> str:
    return f"{int(round(pitch_m))}m" if abs(pitch_m - round(pitch_m)) < 1e-6 else f"{pitch_m:g}m".replace(".", "p")


def base_name_with_pitch(route_name: str, pitch_m: float) -> str:
    base = re.sub(r'_\d+(?:p\d+)?m$', "", sanitize_base_name(route_name))
    return f"{base}_{pitch_label(pitch_m)}"


class Bridge(QObject):
    saved = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window

    @pyqtSlot()
    def jsReady(self) -> None:
        self.window.refresh_saved_route_overlay()

    @pyqtSlot(str)
    def requestSave(self, json_str: str) -> None:
        try:
            payload = json.loads(json_str)
            route_name = str(payload.get("route_name", "")).strip()
            pitch_m = float(payload.get("pitch_m", self.window.pitch_spin.value()))
            overwrite = bool(payload.get("overwrite", False))
            csv_text = str(payload.get("csv_text", ""))
        except Exception:
            self.error.emit("保存データの解析に失敗しました。")
            return
        if not route_name:
            self.error.emit("ルート名を入力してください。")
            return
        if not self.window.route_dir:
            self.error.emit("先にプロジェクトを選択してください。")
            return
        base_name = base_name_with_pitch(route_name, pitch_m)
        csv_path = self.window.route_dir / f"{base_name}.csv"
        if (not overwrite) and csv_path.exists():
            self.error.emit(DUPLICATE_MSG)
            return
        try:
            with csv_path.open("w", encoding="utf-8", newline="\n") as f:
                f.write(csv_text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n")
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
        self.route_dir: Path | None = None
        self.editing_name: str | None = None
        self.html_path = Path(__file__).resolve().parent / "10_route_sampler.html"
        self._pix_small = None
        self._logo_phase = ""
        self._corner_logo_visible = False
        self.splash = None
        self.corner_logo = None
        self._build_ui()
        QTimer.singleShot(0, self._init_logo_overlay)
        self._setup_web_channel()

    def _resolve_logo_path(self) -> Path | None:
        base = Path(__file__).resolve().parent
        p = base / "logo.png"
        return p if p.exists() else None

    def _init_logo_overlay(self) -> None:
        logo_path = self._resolve_logo_path()
        if not logo_path:
            return
        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return
        self._pix_small = pixmap.scaledToHeight(CORNER_LOGO_HEIGHT, Qt.TransformationMode.SmoothTransformation)
        if getattr(self, "logo_slot", None):
            self.logo_slot.setFixedSize(
                max(CORNER_LOGO_RESERVED_WIDTH, self._pix_small.width()),
                CORNER_LOGO_HEIGHT,
            )
        if self.corner_logo is None:
            self.corner_logo = ClickableLogoLabel(self)
            self.corner_logo.setStyleSheet("background:transparent;")
            self.corner_logo.hide()
        self.corner_logo.setPixmap(self._pix_small)
        self.corner_logo.setFixedSize(self._pix_small.size())
        self._place_corner_logo()
        big = pixmap.scaledToHeight(320, Qt.TransformationMode.SmoothTransformation)
        self.splash = QLabel(self)
        self.splash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash.setStyleSheet("background:transparent;")
        self.splash.setPixmap(big)
        self.splash.adjustSize()
        self.splash.move((self.width() - self.splash.width()) // 2, (self.height() - self.splash.height()) // 2)
        self._logo_phase = "center"
        self.splash.show()
        effect = QGraphicsOpacityEffect(self.splash)
        self.splash.setGraphicsEffect(effect)
        fade_in = QPropertyAnimation(effect, b"opacity", self)
        fade_in.setDuration(500); fade_in.setStartValue(0); fade_in.setEndValue(1)

        def start_fade_out():
            fade_out = QPropertyAnimation(effect, b"opacity", self)
            fade_out.setDuration(500); fade_out.setStartValue(1); fade_out.setEndValue(0)
            fade_out.finished.connect(self._show_corner_logo)
            fade_out.start()
        fade_in.finished.connect(lambda: QTimer.singleShot(3000, start_fade_out))
        fade_in.start()

    def _show_corner_logo(self) -> None:
        if self.splash:
            self.splash.deleteLater()
            self.splash = None
        if not self._pix_small or self.corner_logo is None:
            return
        self._place_corner_logo()
        self.corner_logo.raise_()
        self.corner_logo.show()
        self._logo_phase = "corner"
        self._corner_logo_visible = True

    def _place_corner_logo(self) -> None:
        if not self.corner_logo or not getattr(self, "logo_slot", None):
            return
        top_left = self.logo_slot.mapTo(self, self.logo_slot.rect().topLeft())
        x = top_left.x() + max(0, self.logo_slot.width() - self.corner_logo.width())
        y = top_left.y()
        self.corner_logo.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_corner_logo()

    def _make_step_card(self, title: str, body: QWidget) -> QFrame:
        card = QFrame(); card.setObjectName("StepCard")
        v = QVBoxLayout(card); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(6)
        ttl = QLabel(title); ttl.setObjectName("StepTitle")
        v.addWidget(ttl); v.addWidget(body)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return card

    def _build_ui(self) -> None:
        root = QWidget(); self.setCentralWidget(root)
        main = QHBoxLayout(root); main.setContentsMargins(8, 8, 8, 8); main.setSpacing(8)
        left = QWidget(); left.setMinimumWidth(420); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(6)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ルート名", "CSV", "ピッチ", "点数"])
        self.table.horizontalHeader().setMinimumSectionSize(36)
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (COL_CSV, COL_PITCH, COL_POINTS):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_CSV, 50)
        self.table.setColumnWidth(COL_PITCH, 56)
        self.table.setColumnWidth(COL_POINTS, 58)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        lv.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        for text, slot in (("詳細表示", self.detail_selected), ("編集", self.edit_selected), ("リネーム", self.rename_selected), ("削除", self.delete_selected)):
            b = QPushButton(text); b.clicked.connect(slot); buttons.addWidget(b)
        lv.addLayout(buttons)

        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(6)
        self.btn_project = QPushButton("選択"); self.btn_project.clicked.connect(self.select_project)
        self.project_path_edit = QLineEdit(); self.project_path_edit.setReadOnly(True); self.project_path_edit.setPlaceholderText("フォルダが選択されていません")
        self.project_path_edit.setFixedWidth(180)
        self.btn_clear = QPushButton("クリア"); self.btn_clear.clicked.connect(self.clear_clicked)
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText("例：01津山駅ルート")
        self.name_edit.setFixedWidth(222)
        self.pitch_spin = QDoubleSpinBox(); self.pitch_spin.setRange(1.0, 500.0); self.pitch_spin.setDecimals(1); self.pitch_spin.setSingleStep(5.0); self.pitch_spin.setValue(20.0); self.pitch_spin.setSuffix(" m"); self.pitch_spin.valueChanged.connect(self.update_pitch_label)
        self.pitch_hint = QLabel("→ _20m"); self.pitch_hint.setObjectName("StepBody")
        self.btn_save = QPushButton("保存"); self.btn_save.clicked.connect(self.save_clicked)

        steps = QWidget(); sl = QHBoxLayout(steps); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(10)
        body1 = QWidget(); l1 = QHBoxLayout(body1); l1.setContentsMargins(0, 0, 0, 0); l1.addWidget(self.btn_project); l1.addWidget(self.project_path_edit)
        body2 = QWidget(); l2 = QHBoxLayout(body2); l2.setContentsMargins(0, 0, 0, 0); label2 = QLabel("左クリック=ルート点追加\n右クリック=直前点を戻す"); label2.setObjectName("StepBody"); l2.addWidget(label2, 1); l2.addWidget(self.btn_clear)
        body3 = QWidget(); l3 = QHBoxLayout(body3); l3.setContentsMargins(0, 0, 0, 0); l3.setSpacing(8); l3.addWidget(QLabel("ルート名")); l3.addWidget(self.name_edit); l3.addWidget(QLabel("ピッチ")); l3.addWidget(self.pitch_spin); l3.addWidget(self.pitch_hint); l3.addWidget(self.btn_save)
        body4 = QWidget(); l4 = QHBoxLayout(body4); l4.setContentsMargins(0, 0, 0, 0); label4 = QLabel("保存後は自動クリア。\n次のルートを続けて作れます。"); label4.setObjectName("StepBody"); l4.addWidget(label4, 1)
        sl.addWidget(self._make_step_card("STEP1  プロジェクトフォルダ選択", body1), 10)
        sl.addWidget(self._make_step_card("STEP2  地図でルート指定", body2), 9)
        sl.addWidget(self._make_step_card("STEP3  ルートファイルの保存", body3), 34)
        sl.addWidget(self._make_step_card("STEP4  一括で次のルートへ", body4), 6)
        self.logo_slot = QWidget()
        self.logo_slot.setFixedSize(CORNER_LOGO_RESERVED_WIDTH, CORNER_LOGO_HEIGHT)
        self.logo_slot.setStyleSheet("background:transparent;")
        self.logo_slot_layout = QHBoxLayout(self.logo_slot)
        self.logo_slot_layout.setContentsMargins(0, 0, 0, 0)
        header = QWidget(); self.header_layout = QHBoxLayout(header); self.header_layout.setContentsMargins(0, 0, 0, 0); self.header_layout.addWidget(steps, 1); self.header_layout.addWidget(self.logo_slot, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        rv.addWidget(header); rv.addSpacing(10)
        self.web = QWebEngineView(); s = self.web.settings(); s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True); s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        rv.addWidget(self.web, 1)
        splitter = QSplitter(Qt.Orientation.Horizontal); splitter.addWidget(left); splitter.addWidget(right); splitter.setStretchFactor(0, 0); splitter.setStretchFactor(1, 1); splitter.setSizes([430, 1490])
        main.addWidget(splitter)

    def _setup_web_channel(self) -> None:
        self.channel = QWebChannel(self.web.page())
        self.bridge = Bridge(self); self.bridge.saved.connect(self.on_saved); self.bridge.error.connect(self._show_error)
        self.channel.registerObject("bridge", self.bridge); self.web.page().setWebChannel(self.channel)

    def _show_error(self, msg: str) -> None:
        QMessageBox.warning(self, "注意", msg)

    def select_project(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "プロジェクトフォルダを選択", str(Path.cwd()))
        if not selected:
            return
        self.project_dir = Path(selected).resolve(); self.route_dir = self.project_dir / FOLDER_ROUTE; self.route_dir.mkdir(parents=True, exist_ok=True)
        self.project_path_edit.setText(str(self.project_dir)); self.load_html(); self.scan_routes()

    def load_html(self) -> None:
        url = QUrl.fromLocalFile(str(self.html_path)); q = QUrlQuery(); q.addQueryItem("embed", "1"); url.setQuery(q); self.web.setUrl(url)

    def _pitch_from_name(self, name: str) -> str:
        m = re.search(r'_(\d+(?:p\d+)?)m$', name)
        return (m.group(1).replace("p", ".") + "m") if m else ""

    def _count_rows(self, path: Path) -> int | None:
        for enc in ("utf-8-sig", "cp932", "utf-8"):
            try:
                with path.open("r", encoding=enc) as f:
                    return sum(1 for line in f if line.strip())
            except Exception:
                pass
        return None

    def _read_route_points(self, path: Path) -> list[dict[str, float]]:
        for enc in ("utf-8-sig", "cp932", "utf-8"):
            try:
                points: list[dict[str, float]] = []
                for line in path.read_text(encoding=enc).splitlines():
                    if not line.strip():
                        continue
                    cols = line.split(",")
                    if len(cols) <= 15:
                        continue
                    try:
                        lon = float(cols[14]); lat = float(cols[15])
                    except ValueError:
                        continue
                    points.append({"lat": lat, "lon": lon})
                return points
            except Exception:
                pass
        return []

    def refresh_saved_route_overlay(self) -> None:
        if not self.route_dir:
            return
        routes = []
        for csv_path in sorted(self.route_dir.glob("*.csv")):
            points = self._read_route_points(csv_path)
            if len(points) >= 2:
                routes.append({"name": csv_path.stem, "points": points})
        self.web.page().runJavaScript(f"setSavedRoutes({json.dumps(routes, ensure_ascii=False)})")

    def scan_routes(self) -> None:
        self.table.setRowCount(0)
        if not self.route_dir:
            return
        for csv_path in sorted(self.route_dir.glob("*.csv")):
            row = self.table.rowCount(); self.table.insertRow(row); name = csv_path.stem
            values = [name, "✔", self._pitch_from_name(name) or "-", str(self._count_rows(csv_path) or "-")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value); item.setTextAlignment(Qt.AlignmentFlag.AlignCenter if col else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter); self.table.setItem(row, col, item)
        self.refresh_saved_route_overlay()

    def selected_name(self) -> str | None:
        item = self.table.item(self.table.currentRow(), COL_NAME) if self.table.currentRow() >= 0 else None
        return item.text().strip() if item else None

    def update_pitch_label(self) -> None:
        self.pitch_hint.setText(f"→ _{pitch_label(self.pitch_spin.value())}")

    def save_clicked(self) -> None:
        if not self.route_dir:
            self._show_error("先にプロジェクトを選択してください。"); return
        route_name = self.name_edit.text().strip()
        if not route_name:
            self._show_error("ルート名を入力してください。"); return
        pitch_m = float(self.pitch_spin.value()); base_name = base_name_with_pitch(route_name, pitch_m); overwrite = self.editing_name == base_name
        if (not overwrite) and (self.route_dir / f"{base_name}.csv").exists():
            self._show_error(DUPLICATE_MSG); return
        self.web.page().runJavaScript(f"beginSaveFromPy({json.dumps(route_name)}, {pitch_m}, {str(overwrite).lower()})")

    def on_saved(self, base_name: str) -> None:
        self.editing_name = None; self.name_edit.clear(); self.web.page().runJavaScript("setInteractionMode(true); clearAll()"); self.scan_routes()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            if item and item.text() == base_name:
                self.table.selectRow(row); break

    def clear_clicked(self) -> None:
        self.editing_name = None; self.name_edit.clear(); self.web.page().runJavaScript("setInteractionMode(true); clearAll()")

    def _load_selected_route(self, *, editable: bool) -> None:
        if not self.route_dir:
            self._show_error("先にプロジェクトを選択してください。"); return
        name = self.selected_name()
        if not name:
            self._show_error("対象ルートを選択してください。"); return
        path = self.route_dir / f"{name}.csv"
        if not path.exists():
            self._show_error("CSVファイルが見つかりません。"); return
        csv_text = None
        for enc in ("utf-8-sig", "cp932", "utf-8"):
            try:
                csv_text = path.read_text(encoding=enc); break
            except Exception:
                pass
        if csv_text is None:
            self._show_error("CSVの読み込みに失敗しました。"); return
        self.editing_name = name if editable else None
        self.name_edit.setText(re.sub(r'_\d+(?:p\d+)?m$', "", name) if editable else "")
        pitch = self._pitch_from_name(name)
        if pitch:
            self.pitch_spin.setValue(float(pitch.rstrip("m")))
        self.web.page().runJavaScript(f"loadFromCsvText({json.dumps(csv_text)}, {str(editable).lower()})")

    def detail_selected(self) -> None:
        self._load_selected_route(editable=False)

    def edit_selected(self) -> None:
        self._load_selected_route(editable=True)

    def delete_selected(self) -> None:
        if not self.route_dir:
            self._show_error("先にプロジェクトを選択してください。"); return
        name = self.selected_name()
        if not name:
            self._show_error("削除対象を選択してください。"); return
        if QMessageBox.question(self, "確認", f"{name} を削除しますか？") == QMessageBox.StandardButton.Yes:
            path = self.route_dir / f"{name}.csv"
            if path.exists():
                path.unlink()
            self.scan_routes()

    def rename_selected(self) -> None:
        if not self.route_dir:
            self._show_error("先にプロジェクトを選択してください。"); return
        old_name = self.selected_name()
        if not old_name:
            self._show_error("リネーム対象を選択してください。"); return
        old_pitch_text = self._pitch_from_name(old_name)
        old_pitch = float(old_pitch_text.rstrip("m")) if old_pitch_text else float(self.pitch_spin.value())
        new_route, ok = QInputDialog.getText(self, "リネーム", "新しいルート名（既存ピッチ suffix は保持）", text=re.sub(r'_\d+(?:p\d+)?m$', "", old_name))
        if not ok:
            return
        new_name = base_name_with_pitch(new_route, old_pitch)
        if new_name == old_name:
            return
        new_csv = self.route_dir / f"{new_name}.csv"
        if new_csv.exists():
            self._show_error(DUPLICATE_MSG); return
        old_csv = self.route_dir / f"{old_name}.csv"
        if old_csv.exists():
            old_csv.rename(new_csv)
        self.scan_routes()


def main() -> None:
    app = QApplication(sys.argv)
    if "--skip-news-check" not in sys.argv:
        try:
            show_news_dialogs()
        except Exception as e:
            news_debug(f"お知らせ表示をスキップしました: {e!r}")
    app.setStyleSheet(CYBER_QSS)
    win = MainWindow(); win.showMaximized(); sys.exit(app.exec())


if __name__ == "__main__":
    main()
