# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

import folium
import numpy as np
import pandas as pd
from folium.plugins import HeatMap
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
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

REQUIRED_COLUMN_ALIASES: dict[str, list[str]] = {
    "origin_lon": ["起点経度", "o_lon", "origin_lon"],
    "origin_lat": ["起点緯度", "o_lat", "origin_lat"],
    "dest_lon": ["終点経度", "d_lon", "dest_lon"],
    "dest_lat": ["終点緯度", "d_lat", "dest_lat"],
}

DEFAULTS = {
    "radius": 16,
    "blur": 18,
    "min_opacity": 0.15,
    "max_zoom": 12,
    "weight_multiplier": 1.0,
    "map_zoom": 9,
}


class ODHeatmapViewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("41 OD Heatmap Viewer")
        self.resize(1600, 980)

        self.df_raw: pd.DataFrame | None = None
        self.df_valid: pd.DataFrame | None = None
        self.center_lat: float | None = None
        self.center_lon: float | None = None
        self.current_zoom: int = DEFAULTS["map_zoom"]
        self.current_html: str = ""
        self.current_mode: str = "Origin"
        self.temp_dir = Path(__file__).resolve().parent / "temp"
        self.temp_html_path = self.temp_dir / "41_od_heatmap_current.html"

        self.build_ui()
        self.set_style()

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

        input_group = self._make_group("入力設定")
        input_layout = QGridLayout(input_group)
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText("CSVファイルを選択")
        browse_btn = QPushButton("参照")
        load_btn = QPushButton("読込")
        browse_btn.clicked.connect(self.browse_csv)
        load_btn.clicked.connect(self.load_csv)
        self.result_label = QLabel("未読込")
        self.rows_label = QLabel("総行数: -")
        self.origin_count_label = QLabel("有効Origin件数: -")
        self.dest_count_label = QLabel("有効Destination件数: -")
        self.center_label = QLabel("推定中心座標: -")

        input_layout.addWidget(QLabel("CSV"), 0, 0)
        input_layout.addWidget(self.csv_edit, 0, 1, 1, 2)
        input_layout.addWidget(browse_btn, 0, 3)
        input_layout.addWidget(load_btn, 0, 4)
        input_layout.addWidget(self.result_label, 1, 0, 1, 5)
        input_layout.addWidget(self.rows_label, 2, 0, 1, 5)
        input_layout.addWidget(self.origin_count_label, 3, 0, 1, 5)
        input_layout.addWidget(self.dest_count_label, 4, 0, 1, 5)
        input_layout.addWidget(self.center_label, 5, 0, 1, 5)

        mode_group = self._make_group("表示対象")
        mode_layout = QHBoxLayout(mode_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Origin", "Destination"])
        mode_layout.addWidget(QLabel("表示モード"))
        mode_layout.addWidget(self.mode_combo)

        setting_group = self._make_group("ヒートマップ設定")
        setting_layout = QGridLayout(setting_group)
        self.radius_spin = QSpinBox(); self.radius_spin.setRange(1, 80); self.radius_spin.setValue(DEFAULTS["radius"])
        self.blur_spin = QSpinBox(); self.blur_spin.setRange(1, 80); self.blur_spin.setValue(DEFAULTS["blur"])
        self.min_opacity_spin = QDoubleSpinBox(); self.min_opacity_spin.setRange(0.01, 1.0); self.min_opacity_spin.setSingleStep(0.01); self.min_opacity_spin.setValue(DEFAULTS["min_opacity"])
        self.max_zoom_spin = QSpinBox(); self.max_zoom_spin.setRange(1, 22); self.max_zoom_spin.setValue(DEFAULTS["max_zoom"])
        self.weight_mul_spin = QDoubleSpinBox(); self.weight_mul_spin.setRange(0.1, 20.0); self.weight_mul_spin.setSingleStep(0.1); self.weight_mul_spin.setValue(DEFAULTS["weight_multiplier"])

        setting_layout.addWidget(QLabel("Radius"), 0, 0); setting_layout.addWidget(self.radius_spin, 0, 1)
        setting_layout.addWidget(QLabel("Blur"), 1, 0); setting_layout.addWidget(self.blur_spin, 1, 1)
        setting_layout.addWidget(QLabel("Min Opacity"), 2, 0); setting_layout.addWidget(self.min_opacity_spin, 2, 1)
        setting_layout.addWidget(QLabel("Max Zoom"), 3, 0); setting_layout.addWidget(self.max_zoom_spin, 3, 1)
        setting_layout.addWidget(QLabel("Weight倍率"), 4, 0); setting_layout.addWidget(self.weight_mul_spin, 4, 1)

        action_group = self._make_group("操作")
        action_layout = QGridLayout(action_group)
        redraw_btn = QPushButton("再描画")
        save_png_btn = QPushButton("現在表示を画像保存")
        save_html_btn = QPushButton("HTML保存")
        reset_btn = QPushButton("初期値へ戻す")

        redraw_btn.clicked.connect(self.capture_current_view_and_rerender)
        save_png_btn.clicked.connect(self.save_png)
        save_html_btn.clicked.connect(self.save_html)
        reset_btn.clicked.connect(self.reset_defaults)

        action_layout.addWidget(redraw_btn, 0, 0, 1, 2)
        action_layout.addWidget(save_png_btn, 1, 0, 1, 2)
        action_layout.addWidget(save_html_btn, 2, 0, 1, 2)
        action_layout.addWidget(reset_btn, 3, 0, 1, 2)

        guide_group = self._make_group("説明")
        guide_layout = QVBoxLayout(guide_group)
        guide_layout.addWidget(QLabel("・UI上でヒートマップ条件を調整できます。\n"
                                     "・再描画しても中心位置と縮尺を保持します。\n"
                                     "・保存画像は報告資料に利用できます。"))

        for grp in [input_group, mode_group, setting_group, action_group, guide_group]:
            left_layout.addWidget(grp)
        left_layout.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        title = QLabel("41 OD Heatmap Viewer")
        title.setObjectName("panelTitle")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        self.web_view = QWebEngineView()
        right_layout.addWidget(title)
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
            QMainWindow, QWidget { background-color: #0b1117; color: #d7f7ff; font-size: 13px; }
            QGroupBox {
                border: 1px solid #1f3641; border-radius: 12px; margin-top: 10px;
                background-color: #111a22; padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 6px;
                color: #55d6f6; font-weight: 600;
            }
            QLabel#panelTitle {
                background: #13212b; border: 1px solid #274353; border-radius: 10px;
                padding: 10px; color: #71f0ff;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #0f181f; border: 1px solid #33505f; border-radius: 8px;
                padding: 6px; color: #ddf9ff;
            }
            QPushButton {
                background-color: #123243; border: 1px solid #2f667d; border-radius: 8px;
                padding: 8px 10px; color: #d6f8ff; font-weight: 600;
            }
            QPushButton:hover { background-color: #1a4a62; }
            QPushButton:pressed { background-color: #0f2c3b; }
            QStatusBar { border-top: 1px solid #1f3641; }
            """
        )

    def _make_group(self, title: str) -> QGroupBox:
        grp = QGroupBox(title)
        grp.setObjectName("card")
        return grp

    def browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CSV選択", "", "CSV Files (*.csv);;All Files (*)")
        if path:
            self.csv_edit.setText(path)

    def load_csv(self) -> None:
        csv_path = self.csv_edit.text().strip()
        if not csv_path:
            QMessageBox.warning(self, "CSV未選択", "CSVファイルを選択してください。")
            return

        try:
            df = self.read_csv_robust(csv_path)
            if df is None or df.empty:
                raise ValueError("CSVが空、または読み込みできませんでした。")
            valid_df = self.validate_dataframe(df)
        except Exception as e:
            QMessageBox.critical(self, "CSV読込失敗", str(e))
            return

        if valid_df.empty:
            QMessageBox.warning(self, "有効座標なし", "有効な緯度経度データが見つかりません。")
            return

        self.df_raw = df
        self.df_valid = valid_df
        self.current_mode = self.mode_combo.currentText()

        lat_center, lon_center = self.estimate_weighted_center(valid_df)
        self.center_lat = lat_center
        self.center_lon = lon_center
        self.current_zoom = DEFAULTS["map_zoom"]

        self.result_label.setText("読込結果: 成功")
        self.rows_label.setText(f"総行数: {len(df):,}")
        self.origin_count_label.setText(f"有効Origin件数: {valid_df['origin_lat'].notna().sum():,}")
        self.dest_count_label.setText(f"有効Destination件数: {valid_df['dest_lat'].notna().sum():,}")
        self.center_label.setText(f"推定中心座標: ({lat_center:.6f}, {lon_center:.6f})")

        self.render_map(lat_center, lon_center, self.current_zoom)
        self.statusBar().showMessage(f"CSV読込完了: {Path(csv_path).name}")

    def read_csv_robust(self, path: str) -> pd.DataFrame | None:
        candidates = [
            {},
            {"encoding": "utf-8-sig"},
            {"encoding": "cp932"},
            {"sep": None, "engine": "python"},
            {"sep": None, "engine": "python", "encoding": "cp932"},
        ]
        for kwargs in candidates:
            try:
                return pd.read_csv(path, **kwargs)
            except Exception:
                continue
        return None

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
                return float(str(value).replace(",", ""))
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
        combined = pd.concat(
            [
                df_valid[["origin_lat", "origin_lon"]].rename(columns={"origin_lat": "lat", "origin_lon": "lon"}),
                df_valid[["dest_lat", "dest_lon"]].rename(columns={"dest_lat": "lat", "dest_lon": "lon"}),
            ],
            ignore_index=True,
        )
        return float(combined["lat"].mean()), float(combined["lon"].mean())

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

        m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, control_scale=True, tiles="CartoDB positron")

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
        else:
            folium.Marker([center_lat, center_lon], tooltip="有効点なし").add_to(m)

        map_name = m.get_name()
        html = m.get_root().render()
        state_script = f"""
<script>
window.getMapState = function() {{
  try {{
    var c = {map_name}.getCenter();
    return JSON.stringify({{lat: c.lat, lng: c.lng, zoom: {map_name}.getZoom()}});
  }} catch (e) {{
    return JSON.stringify({{lat: {center_lat}, lng: {center_lon}, zoom: {zoom}}});
  }}
}};
</script>
"""
        return html.replace("</body>", state_script + "\n</body>")

    def render_map(self, center_lat: float, center_lon: float, zoom: int) -> None:
        if self.df_valid is None:
            QMessageBox.warning(self, "地図再描画失敗", "CSVを先に読み込んでください。")
            return
        try:
            mode = self.mode_combo.currentText()
            self.current_mode = mode
            self.current_html = self.build_map_html(mode, center_lat, center_lon, zoom)
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            self.temp_html_path.write_text(self.current_html, encoding="utf-8")
            self.web_view.setUrl(QUrl.fromLocalFile(str(self.temp_html_path.resolve())))
            self.center_lat = center_lat
            self.center_lon = center_lon
            self.current_zoom = zoom
            self.statusBar().showMessage(f"再描画完了: {mode} / zoom {zoom}")
        except Exception as e:
            QMessageBox.critical(self, "地図再描画失敗", str(e))

    def capture_current_view_and_rerender(self) -> None:
        if self.df_valid is None:
            QMessageBox.warning(self, "地図再描画失敗", "CSVを先に読み込んでください。")
            return

        js = "window.getMapState ? window.getMapState() : null;"

        def callback(result: object) -> None:
            lat = self.center_lat if self.center_lat is not None else 35.681236
            lon = self.center_lon if self.center_lon is not None else 139.767125
            zoom = self.current_zoom
            if isinstance(result, str) and result:
                try:
                    state = json.loads(result)
                    lat = float(state.get("lat", lat))
                    lon = float(state.get("lng", lon))
                    zoom = int(state.get("zoom", zoom))
                except Exception:
                    pass
            self.render_map(lat, lon, zoom)

        self.web_view.page().runJavaScript(js, callback)

    def save_png(self) -> None:
        if self.df_valid is None:
            QMessageBox.warning(self, "PNG保存失敗", "CSVを先に読み込んでください。")
            return

        mode_name = self.mode_combo.currentText().lower()
        default_name = f"{mode_name}_heatmap.png"
        path, _ = QFileDialog.getSaveFileName(self, "PNG保存", default_name, "PNG Files (*.png)")
        if not path:
            return

        pixmap = self.web_view.grab()
        if pixmap.isNull():
            QMessageBox.critical(self, "PNG保存失敗", "画面のキャプチャに失敗しました。")
            return

        if not path.lower().endswith(".png"):
            path += ".png"
        if pixmap.save(path, "PNG"):
            self.statusBar().showMessage(f"PNG保存完了: {path}")
        else:
            QMessageBox.critical(self, "PNG保存失敗", "PNGファイルの保存に失敗しました。")

    def save_html(self) -> None:
        if self.df_valid is None:
            QMessageBox.warning(self, "HTML保存失敗", "CSVを先に読み込んでください。")
            return

        mode_name = self.mode_combo.currentText().lower()
        default_name = f"{mode_name}_heatmap.html"
        path, _ = QFileDialog.getSaveFileName(self, "HTML保存", default_name, "HTML Files (*.html)")
        if not path:
            return

        if self.center_lat is None or self.center_lon is None:
            QMessageBox.critical(self, "HTML保存失敗", "地図中心情報が未初期化です。")
            return

        try:
            html = self.build_map_html(self.mode_combo.currentText(), self.center_lat, self.center_lon, self.current_zoom)
            if not path.lower().endswith(".html"):
                path += ".html"
            Path(path).write_text(html, encoding="utf-8")
            self.statusBar().showMessage(f"HTML保存完了: {path}")
        except Exception as e:
            QMessageBox.critical(self, "HTML保存失敗", str(e))

    def reset_defaults(self) -> None:
        self.radius_spin.setValue(DEFAULTS["radius"])
        self.blur_spin.setValue(DEFAULTS["blur"])
        self.min_opacity_spin.setValue(DEFAULTS["min_opacity"])
        self.max_zoom_spin.setValue(DEFAULTS["max_zoom"])
        self.weight_mul_spin.setValue(DEFAULTS["weight_multiplier"])
        if self.df_valid is not None and self.center_lat is not None and self.center_lon is not None:
            self.render_map(self.center_lat, self.center_lon, self.current_zoom)
        self.statusBar().showMessage("表示設定を初期値へ戻しました。")


def main() -> None:
    app = QApplication(sys.argv)
    w = ODHeatmapViewer()
    w.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
