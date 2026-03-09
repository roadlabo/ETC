# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import folium
import numpy as np
import pandas as pd
from folium.plugins import HeatMap
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
TEMP_DIR = ROOT_DIR / "temp"
LOGO_DIR = ROOT_DIR / "assets" / "logos"
TEMP_HTML_PATH = TEMP_DIR / "41_od_heatmap_current.html"

REQUIRED_COLUMN_ALIASES: dict[str, list[str]] = {
    "origin_lon": ["起点経度", "o_lon", "origin_lon"],
    "origin_lat": ["起点緯度", "o_lat", "origin_lat"],
    "dest_lon": ["終点経度", "d_lon", "dest_lon"],
    "dest_lat": ["終点緯度", "d_lat", "dest_lat"],
}

DEFAULTS = {
    "radius": 20,
    "blur": 15,
    "min_opacity": 0.2,
    "max_zoom": 18,
    "weight_multiplier": 1.0,
    "map_zoom": 10,
}


class ODHeatmapViewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("41 OD Heatmap Viewer")
        self.resize(1600, 980)

        self.df_valid: pd.DataFrame | None = None
        self.center_lat: float = 35.681236
        self.center_lon: float = 139.767125
        self.current_zoom: int = DEFAULTS["map_zoom"]
        self.map_loaded: bool = False
        self.ui_render_deadline: float = 0.0

        self.build_ui()
        self.set_style()
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] TEMP_DIR ready: {TEMP_DIR}")

    def build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        base = QHBoxLayout(root)
        base.setContentsMargins(12, 12, 12, 12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        base.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setSpacing(10)

        input_group = self._make_group("入力")
        input_layout = QGridLayout(input_group)
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText("OD CSVファイルを選択")
        browse_btn = QPushButton("参照")
        load_btn = QPushButton("読込")
        browse_btn.clicked.connect(self.browse_csv)
        load_btn.clicked.connect(self.load_csv)
        self.rows_label = QLabel("読込件数: -")

        input_layout.addWidget(QLabel("CSVファイル"), 0, 0)
        input_layout.addWidget(self.csv_edit, 0, 1, 1, 2)
        input_layout.addWidget(browse_btn, 0, 3)
        input_layout.addWidget(load_btn, 0, 4)
        input_layout.addWidget(self.rows_label, 1, 0, 1, 5)

        mode_group = self._make_group("表示対象")
        mode_layout = QHBoxLayout(mode_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Origin", "Destination"])
        mode_layout.addWidget(QLabel("描画対象"))
        mode_layout.addWidget(self.mode_combo)

        setting_group = self._make_group("ヒートマップ設定")
        setting_layout = QGridLayout(setting_group)
        self.radius_spin = QSpinBox(); self.radius_spin.setRange(1, 100); self.radius_spin.setValue(DEFAULTS["radius"])
        self.blur_spin = QSpinBox(); self.blur_spin.setRange(1, 100); self.blur_spin.setValue(DEFAULTS["blur"])
        self.min_opacity_spin = QDoubleSpinBox(); self.min_opacity_spin.setRange(0.01, 1.0); self.min_opacity_spin.setSingleStep(0.01); self.min_opacity_spin.setValue(DEFAULTS["min_opacity"])
        self.max_zoom_spin = QSpinBox(); self.max_zoom_spin.setRange(1, 22); self.max_zoom_spin.setValue(DEFAULTS["max_zoom"])
        self.weight_mul_spin = QDoubleSpinBox(); self.weight_mul_spin.setRange(0.1, 99.9); self.weight_mul_spin.setSingleStep(0.1); self.weight_mul_spin.setValue(DEFAULTS["weight_multiplier"])

        setting_layout.addWidget(QLabel("Radius"), 0, 0); setting_layout.addWidget(self.radius_spin, 0, 1)
        setting_layout.addWidget(QLabel("Blur"), 1, 0); setting_layout.addWidget(self.blur_spin, 1, 1)
        setting_layout.addWidget(QLabel("Min Opacity"), 2, 0); setting_layout.addWidget(self.min_opacity_spin, 2, 1)
        setting_layout.addWidget(QLabel("Max Zoom"), 3, 0); setting_layout.addWidget(self.max_zoom_spin, 3, 1)
        setting_layout.addWidget(QLabel("Weight倍率"), 4, 0); setting_layout.addWidget(self.weight_mul_spin, 4, 1)

        action_group = self._make_group("操作")
        action_layout = QGridLayout(action_group)
        redraw_btn = QPushButton("再描画")
        save_png_btn = QPushButton("PNG保存")
        save_html_btn = QPushButton("HTML保存")
        reset_btn = QPushButton("初期値へ戻す")

        redraw_btn.clicked.connect(self.rerender_preserve_view)
        save_png_btn.clicked.connect(self.save_png)
        save_html_btn.clicked.connect(self.save_html)
        reset_btn.clicked.connect(self.reset_defaults)

        action_layout.addWidget(redraw_btn, 0, 0, 1, 2)
        action_layout.addWidget(save_png_btn, 1, 0, 1, 2)
        action_layout.addWidget(save_html_btn, 2, 0, 1, 2)
        action_layout.addWidget(reset_btn, 3, 0, 1, 2)

        guide_group = self._make_group("説明")
        guide_layout = QVBoxLayout(guide_group)
        guide_layout.addWidget(QLabel(
            "1) CSVを読み込み\n"
            "2) Origin / Destination を選択\n"
            "3) パラメータ調整後に再描画\n"
            "4) UI内表示 / 外部ブラウザ表示 を選択\n"
            "5) PNG / HTML で保存"
        ))

        for grp in [input_group, mode_group, setting_group, action_group, guide_group]:
            left_layout.addWidget(grp)
        left_layout.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        title = QLabel("41 OD Heatmap Viewer")
        title.setObjectName("panelTitle")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        view_mode_layout = QHBoxLayout()
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["UI内表示", "外部ブラウザ表示"])
        view_mode_layout.addWidget(QLabel("表示モード"))
        view_mode_layout.addWidget(self.view_mode_combo, 1)

        self.view_hint_label = QLabel("")
        self.web_view = QWebEngineView()
        self.web_view.loadFinished.connect(self._on_map_loaded)
        right_layout.addWidget(title)
        right_layout.addLayout(view_mode_layout)
        right_layout.addWidget(self.view_hint_label)
        right_layout.addWidget(self.web_view, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([430, 1170])

        status = QStatusBar()
        self.setStatusBar(status)
        self.statusBar().showMessage("CSVを選択して読み込んでください。")

    def set_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background-color: #0b1218; color: #d8faff; font-size: 13px; }
            QGroupBox {
                border: 1px solid #1f3d4a; border-radius: 12px; margin-top: 10px;
                background-color: #111c23; padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 6px;
                color: #58e5ff; font-weight: 600;
            }
            QLabel#panelTitle {
                background: #13242c; border: 1px solid #2d4b58; border-radius: 10px;
                padding: 10px; color: #6ff4ff;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #0f191f; border: 1px solid #355868; border-radius: 8px;
                padding: 6px; color: #e3fbff;
            }
            QPushButton {
                background-color: #123646; border: 1px solid #2f6e86; border-radius: 8px;
                padding: 8px 10px; color: #defbff; font-weight: 600;
            }
            QPushButton:hover { background-color: #1a4f64; }
            QPushButton:pressed { background-color: #10313f; }
            QStatusBar { border-top: 1px solid #1f3d4a; }
            """
        )

    def _make_group(self, title: str) -> QGroupBox:
        return QGroupBox(title)

    def _on_map_loaded(self, ok: bool) -> None:
        print(f"[INFO] map loaded: {ok}")
        if not ok:
            self.map_loaded = False
            self.view_hint_label.setText("UI内表示に失敗したため、外部ブラウザで開きます。")
            self.statusBar().showMessage("UI内表示失敗 → 外部ブラウザへフォールバック")
            QTimer.singleShot(100, self.open_in_external_browser)
            return

        QTimer.singleShot(1200, self._verify_leaflet_map_visible)

    def _verify_leaflet_map_visible(self) -> None:
        if self.view_mode_combo.currentText() != "UI内表示":
            return

        js = """
(function() {
  try {
    if (!window.__od_heatmap_map) return "no-map";
    var c = window.__od_heatmap_map.getCenter();
    var z = window.__od_heatmap_map.getZoom();
    var tiles = document.querySelectorAll('.leaflet-tile');
    return JSON.stringify({
      status: "ok",
      lat: c.lat,
      lng: c.lng,
      zoom: z,
      tileCount: tiles.length
    });
  } catch (e) {
    return "error:" + e;
  }
})();
"""

        def callback(result: object) -> None:
            tile_count = 0
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    tile_count = int(parsed.get("tileCount", 0))
                except Exception:
                    tile_count = 0

            if tile_count >= 1:
                self.map_loaded = True
                self.view_hint_label.setText("")
                self.statusBar().showMessage("UI内表示で描画完了")
                return

            self.map_loaded = False
            self.view_hint_label.setText("UI内表示が不安定なため、外部ブラウザで開きました。")
            self.statusBar().showMessage("UI表示不安定 → 外部ブラウザへフォールバック")
            self.open_in_external_browser()

        self.web_view.page().runJavaScript(js, callback)

    def open_in_external_browser(self) -> None:
        if not TEMP_HTML_PATH.exists():
            QMessageBox.warning(self, "外部ブラウザ表示失敗", "HTMLがまだ生成されていません。")
            return
        import webbrowser

        webbrowser.open(TEMP_HTML_PATH.resolve().as_uri())

    def browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CSV選択", "", "CSV Files (*.csv);;All Files (*)")
        if path:
            self.csv_edit.setText(path)

    def load_csv(self) -> None:
        csv_path = self.csv_edit.text().strip()
        if not csv_path:
            print("[WARN] CSV未指定")
            QMessageBox.warning(self, "CSV未指定", "CSVファイルを選択してください。")
            return

        print(f"[INFO] CSV読込開始: {csv_path}")
        try:
            df = self.read_csv_robust(csv_path)
            valid_df = self.validate_dataframe(df)
            if valid_df.empty:
                raise ValueError("有効な座標データがありません。")
        except Exception as e:
            print(f"[ERROR] CSV読込失敗: {e}")
            QMessageBox.critical(self, "CSV読込失敗", str(e))
            return

        self.df_valid = valid_df
        self.rows_label.setText(f"読込件数: {len(df):,} 行 / 有効件数: {len(valid_df):,}")

        self.center_lat, self.center_lon = self.estimate_weighted_center(valid_df)
        self.current_zoom = DEFAULTS["map_zoom"]

        print(f"[INFO] CSV読込成功: rows={len(df)}, valid={len(valid_df)}")
        print(f"[INFO] 有効点数(Origin)={valid_df[['origin_lat','origin_lon']].dropna().shape[0]}, (Destination)={valid_df[['dest_lat','dest_lon']].dropna().shape[0]}")
        self.render_map(self.center_lat, self.center_lon, self.current_zoom)

    def read_csv_robust(self, path: str) -> pd.DataFrame:
        candidates = [
            {"encoding": "utf-8-sig"},
            {"encoding": "utf-8"},
            {"encoding": "cp932"},
            {"encoding": "shift_jis"},
            {"encoding": "utf-16"},
            {"sep": None, "engine": "python", "encoding": "utf-8-sig"},
            {"sep": None, "engine": "python", "encoding": "cp932"},
        ]
        for kwargs in candidates:
            try:
                return pd.read_csv(path, **kwargs)
            except Exception:
                continue
        raise ValueError("CSVを読み込めませんでした（エンコーディングまたは区切りの問題の可能性）。")

    def validate_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for key, aliases in REQUIRED_COLUMN_ALIASES.items():
            col = next((a for a in aliases if a in df.columns), None)
            if col is None:
                missing.append("/".join(aliases))
            else:
                resolved[key] = col
        if missing:
            raise ValueError("必須列不足: " + ", ".join(missing))

        out = pd.DataFrame()
        out["origin_lat"] = df[resolved["origin_lat"]].map(self.to_float)
        out["origin_lon"] = df[resolved["origin_lon"]].map(self.to_float)
        out["dest_lat"] = df[resolved["dest_lat"]].map(self.to_float)
        out["dest_lon"] = df[resolved["dest_lon"]].map(self.to_float)

        out[["origin_lat", "origin_lon"]] = out.apply(
            lambda r: pd.Series(self.normalize_latlon(r["origin_lat"], r["origin_lon"])), axis=1
        )
        out[["dest_lat", "dest_lon"]] = out.apply(
            lambda r: pd.Series(self.normalize_latlon(r["dest_lat"], r["dest_lon"])), axis=1
        )

        out = out.dropna(subset=["origin_lat", "origin_lon", "dest_lat", "dest_lon"]).reset_index(drop=True)
        return out

    @staticmethod
    def to_float(value: object) -> float:
        try:
            return float(value)
        except Exception:
            try:
                return float(str(value).replace(",", "").replace(" ", ""))
            except Exception:
                return np.nan

    @staticmethod
    def normalize_latlon(lat: float, lon: float) -> tuple[float, float]:
        if pd.isna(lat) or pd.isna(lon):
            return np.nan, np.nan
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            if -90 <= lon <= 90 and -180 <= lat <= 180:
                lat, lon = lon, lat
            else:
                return np.nan, np.nan
        return float(lat), float(lon)

    def extract_points(self, mode: str) -> pd.DataFrame:
        if self.df_valid is None:
            return pd.DataFrame(columns=["lat", "lon"])
        if mode == "Origin":
            return self.df_valid[["origin_lat", "origin_lon"]].rename(columns={"origin_lat": "lat", "origin_lon": "lon"})
        return self.df_valid[["dest_lat", "dest_lon"]].rename(columns={"dest_lat": "lat", "dest_lon": "lon"})

    @staticmethod
    def aggregate_points(points: pd.DataFrame) -> pd.DataFrame:
        if points.empty:
            return pd.DataFrame(columns=["lat", "lon", "trip_count"])
        return points.groupby(["lat", "lon"], as_index=False).size().rename(columns={"size": "trip_count"})

    @staticmethod
    def estimate_weighted_center(df_valid: pd.DataFrame) -> tuple[float, float]:
        all_points = pd.concat(
            [
                df_valid[["origin_lat", "origin_lon"]].rename(columns={"origin_lat": "lat", "origin_lon": "lon"}),
                df_valid[["dest_lat", "dest_lon"]].rename(columns={"dest_lat": "lat", "dest_lon": "lon"}),
            ],
            ignore_index=True,
        )
        return float(all_points["lat"].mean()), float(all_points["lon"].mean())

    def map_settings(self) -> dict[str, float | int]:
        return {
            "radius": int(self.radius_spin.value()),
            "blur": int(self.blur_spin.value()),
            "min_opacity": float(self.min_opacity_spin.value()),
            "max_zoom": int(self.max_zoom_spin.value()),
            "weight_multiplier": float(self.weight_mul_spin.value()),
        }

    def build_map_html(self, mode: str, center_lat: float, center_lon: float, zoom: int) -> str:
        points = self.extract_points(mode)
        agg = self.aggregate_points(points)
        settings = self.map_settings()

        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=zoom,
            control_scale=True,
            tiles="https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
            attr="国土地理院",
        )

        if not agg.empty:
            heat_data = [
                [float(r.lat), float(r.lon), float(r.trip_count * settings["weight_multiplier"])]
                for r in agg.itertuples(index=False)
            ]
            HeatMap(
                heat_data,
                radius=settings["radius"],
                blur=settings["blur"],
                min_opacity=settings["min_opacity"],
                max_zoom=settings["max_zoom"],
            ).add_to(m)

        map_name = m.get_name()
        html = m.get_root().render()
        state_script = f"""
<script>
(function() {{
  window.__od_heatmap_map = {map_name};
  window.__od_heatmap_get_state = function() {{
    if (!window.__od_heatmap_map) return null;
    var c = window.__od_heatmap_map.getCenter();
    return JSON.stringify({{lat: c.lat, lng: c.lng, zoom: window.__od_heatmap_map.getZoom()}});
  }};
}})();
</script>
"""
        return html.replace("</body>", state_script + "\n</body>")

    def render_map(self, center_lat: float, center_lon: float, zoom: int) -> None:
        if self.df_valid is None:
            QMessageBox.warning(self, "再描画失敗", "CSVを先に読み込んでください。")
            return

        print("[INFO] 再描画開始")
        try:
            mode = self.mode_combo.currentText()
            html = self.build_map_html(mode, center_lat, center_lon, zoom)
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            TEMP_HTML_PATH.write_text(html, encoding="utf-8")
            self.center_lat, self.center_lon, self.current_zoom = center_lat, center_lon, zoom

            if self.view_mode_combo.currentText() == "外部ブラウザ表示":
                self.web_view.hide()
                self.view_hint_label.setText("外部ブラウザで表示しました。パラメータ調整後は再描画してください。")
                self.map_loaded = False
                self.open_in_external_browser()
                self.statusBar().showMessage("外部ブラウザで再描画完了")
                return

            self.web_view.show()
            self.view_hint_label.setText("UI内表示で描画中…")
            self.map_loaded = False
            self.ui_render_deadline = time.monotonic() + 4.0
            self.web_view.setUrl(QUrl.fromLocalFile(str(TEMP_HTML_PATH.resolve())))
            self.statusBar().showMessage(f"再描画完了: {mode} / zoom={zoom}")
            print(f"[INFO] 再描画完了: mode={mode}, center=({center_lat:.6f},{center_lon:.6f}), zoom={zoom}")
        except Exception as e:
            print(f"[ERROR] 再描画失敗: {e}")
            QMessageBox.critical(self, "再描画失敗", str(e))

    def rerender_preserve_view(self) -> None:
        if self.df_valid is None:
            QMessageBox.warning(self, "再描画失敗", "CSVを先に読み込んでください。")
            return

        if self.view_mode_combo.currentText() == "外部ブラウザ表示":
            self.render_map(self.center_lat, self.center_lon, self.current_zoom)
            return

        js = "window.__od_heatmap_get_state ? window.__od_heatmap_get_state() : null;"

        def callback(result: object) -> None:
            lat, lon, zoom = self.center_lat, self.center_lon, self.current_zoom
            try:
                if isinstance(result, str) and result:
                    state = json.loads(result)
                    lat = float(state.get("lat", lat))
                    lon = float(state.get("lng", lon))
                    zoom = int(state.get("zoom", zoom))
            except Exception:
                pass
            finally:
                self.render_map(lat, lon, zoom)

        self.web_view.page().runJavaScript(js, callback)

    def save_png(self) -> None:
        if self.df_valid is None:
            print("[WARN] PNG保存失敗: CSV未読込")
            QMessageBox.warning(self, "PNG保存失敗", "CSVを先に読み込んでください。")
            return
        if self.view_mode_combo.currentText() == "外部ブラウザ表示":
            QMessageBox.information(
                self,
                "PNG保存不可",
                "外部ブラウザ表示モードではUIからPNG保存できません。UI内表示に切り替えて再描画してください。",
            )
            return
        if not self.map_loaded:
            print("[WARN] PNG保存失敗: 地図未描画")
            QMessageBox.warning(self, "PNG保存失敗", "地図描画完了後に実行してください。")
            return

        path, _ = QFileDialog.getSaveFileName(self, "PNG保存", "od_heatmap.png", "PNG Files (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"

        pixmap = self.web_view.grab()
        if pixmap.isNull() or not pixmap.save(path, "PNG"):
            print("[ERROR] PNG保存失敗")
            QMessageBox.critical(self, "PNG保存失敗", "PNGファイルの保存に失敗しました。")
            return

        print(f"[INFO] PNG保存成功: {path}")
        self.statusBar().showMessage(f"PNG保存完了: {path}")

    def save_html(self) -> None:
        if self.df_valid is None:
            print("[WARN] HTML保存失敗: CSV未読込")
            QMessageBox.warning(self, "HTML保存失敗", "CSVを先に読み込んでください。")
            return
        if not TEMP_HTML_PATH.exists():
            print("[ERROR] HTML保存失敗: 一時HTMLなし")
            QMessageBox.critical(self, "HTML保存失敗", "一時HTMLが見つかりません。再描画してから保存してください。")
            return

        path, _ = QFileDialog.getSaveFileName(self, "HTML保存", "od_heatmap.html", "HTML Files (*.html)")
        if not path:
            return
        if not path.lower().endswith(".html"):
            path += ".html"

        try:
            shutil.copyfile(TEMP_HTML_PATH, path)
            print(f"[INFO] HTML保存成功: {path}")
            self.statusBar().showMessage(f"HTML保存完了: {path}")
        except Exception as e:
            print(f"[ERROR] HTML保存失敗: {e}")
            QMessageBox.critical(self, "HTML保存失敗", str(e))

    def reset_defaults(self) -> None:
        self.radius_spin.setValue(DEFAULTS["radius"])
        self.blur_spin.setValue(DEFAULTS["blur"])
        self.min_opacity_spin.setValue(DEFAULTS["min_opacity"])
        self.max_zoom_spin.setValue(DEFAULTS["max_zoom"])
        self.weight_mul_spin.setValue(DEFAULTS["weight_multiplier"])
        if self.df_valid is not None:
            self.rerender_preserve_view()
        self.statusBar().showMessage("初期値へ戻しました。")


def main() -> None:
    app = QApplication(sys.argv)
    w = ODHeatmapViewer()
    w.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
