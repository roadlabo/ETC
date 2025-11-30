"""
Polygon Builder Tool
--------------------
A Tkinter-based tool for creating and editing polygon annotations over a map image.

This script fulfills the requirements described in the specification for building and
editing polygons, supporting CSV input/output and interactive snapping/validation
behaviors.
"""

import csv
import math
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from PIL import Image, ImageTk

# Configuration
MAP_IMAGE_PATH = os.environ.get("MAP_IMAGE_PATH", "map.png")
MAP_MIN_LON = float(os.environ.get("MAP_MIN_LON", 133.95))
MAP_MAX_LON = float(os.environ.get("MAP_MAX_LON", 134.15))
MAP_MIN_LAT = float(os.environ.get("MAP_MIN_LAT", 35.0))
MAP_MAX_LAT = float(os.environ.get("MAP_MAX_LAT", 35.2))
IMAGE_WIDTH = int(os.environ.get("IMAGE_WIDTH", 1200))
IMAGE_HEIGHT = int(os.environ.get("IMAGE_HEIGHT", 800))
SNAP_THRESHOLD = int(os.environ.get("SNAP_THRESHOLD", 10))


@dataclass
class Polygon:
    name: str
    vertices: List[Tuple[float, float]]
    canvas_points: List[int] = field(default_factory=list)
    canvas_lines: List[int] = field(default_factory=list)
    label_id: Optional[int] = None

    def centroid(self) -> Tuple[float, float]:
        if not self.vertices:
            return 0.0, 0.0
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        return sum(xs) / len(xs), sum(ys) / len(ys)


class PolygonBuilder:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.polygons: List[Polygon] = []
        self.current_points: List[Tuple[float, float]] = []
        self.current_canvas_points: List[int] = []
        self.current_canvas_lines: List[int] = []
        self.preview_line: Optional[int] = None
        self.current_mode = tk.StringVar(value="新規作成")
        self.csv_path: Optional[str] = None

        self._init_mode_dialog()
        self._setup_main_window()
        self._load_map_image()
        if self.current_mode.get() == "既存データを編集" and self.csv_path:
            self._load_existing_csv(self.csv_path)

        self.root.deiconify()
        self.root.mainloop()

    def _init_mode_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("ポリゴン作成ツール - 起動")
        dialog.geometry("360x160")
        dialog.grab_set()

        tk.Label(dialog, text="作業モードを選択してください", font=("Arial", 12)).pack(pady=10)
        buttons = tk.Frame(dialog)
        buttons.pack(pady=10)

        def select_new() -> None:
            self.current_mode.set("新規作成")
            dialog.destroy()

        def select_existing() -> None:
            self.current_mode.set("既存データを編集")
            path = filedialog.askopenfilename(
                title="編集する polygon_data.csv を選択",
                filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            )
            if path:
                self.csv_path = path
                dialog.destroy()
            else:
                self.current_mode.set("新規作成")
                dialog.destroy()

        tk.Button(buttons, text="新規作成", width=15, command=select_new).grid(row=0, column=0, padx=10)
        tk.Button(buttons, text="既存データを編集", width=15, command=select_existing).grid(row=0, column=1, padx=10)

        dialog.wait_window()

    def _setup_main_window(self) -> None:
        self.root.title("ポリゴン作成ツール")
        self.root.geometry("1200x850")
        self.root.resizable(True, True)

        self.canvas = tk.Canvas(self.root, width=IMAGE_WIDTH, height=IMAGE_HEIGHT, bg="white")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.status = tk.Label(self.root, text="モード: {}".format(self.current_mode.get()), anchor="w")
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_motion)

    def _load_map_image(self) -> None:
        try:
            image = Image.open(MAP_IMAGE_PATH)
            self.image_width, self.image_height = image.size
            image = image.resize((IMAGE_WIDTH, IMAGE_HEIGHT))
            self.tk_image = ImageTk.PhotoImage(image)
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_image)
        except FileNotFoundError:
            messagebox.showwarning("画像が見つかりません", f"{MAP_IMAGE_PATH} が見つかりません。背景なしで続行します。")
            self.image_width, self.image_height = IMAGE_WIDTH, IMAGE_HEIGHT

    # Coordinate conversions
    def lonlat_to_px(self, lon: float, lat: float) -> Tuple[float, float]:
        x = (lon - MAP_MIN_LON) / (MAP_MAX_LON - MAP_MIN_LON) * IMAGE_WIDTH
        y = (MAP_MAX_LAT - lat) / (MAP_MAX_LAT - MAP_MIN_LAT) * IMAGE_HEIGHT
        return x, y

    def px_to_lonlat(self, px: float, py: float) -> Tuple[float, float]:
        lon = MAP_MIN_LON + (px / IMAGE_WIDTH) * (MAP_MAX_LON - MAP_MIN_LON)
        lat = MAP_MAX_LAT - (py / IMAGE_HEIGHT) * (MAP_MAX_LAT - MAP_MIN_LAT)
        return lon, lat

    def _load_existing_csv(self, path: str) -> None:
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 3 or len(row) % 2 == 0:
                        continue
                    name = row[0]
                    coords = list(map(float, row[1:]))
                    vertices = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
                    poly = Polygon(name=name, vertices=vertices)
                    self._draw_polygon(poly, color="black")
                    self.polygons.append(poly)
        except FileNotFoundError:
            messagebox.showerror("読み込み失敗", f"{path} が見つかりません。新規作成に切り替えます。")
            self.current_mode.set("新規作成")

    def _draw_polygon(self, poly: Polygon, color: str = "black") -> None:
        prev_pxpy: Optional[Tuple[float, float]] = None
        for lon, lat in poly.vertices:
            px, py = self.lonlat_to_px(lon, lat)
            point_id = self.canvas.create_oval(px - 3, py - 3, px + 3, py + 3, fill=color, outline=color)
            poly.canvas_points.append(point_id)
            if prev_pxpy:
                line_id = self.canvas.create_line(prev_pxpy[0], prev_pxpy[1], px, py, fill=color, width=2)
                poly.canvas_lines.append(line_id)
            prev_pxpy = (px, py)

        if len(poly.vertices) > 2:
            first_px, first_py = self.lonlat_to_px(*poly.vertices[0])
            line_id = self.canvas.create_line(prev_pxpy[0], prev_pxpy[1], first_px, first_py, fill=color, width=2)
            poly.canvas_lines.append(line_id)

        cx, cy = poly.centroid()
        label_px, label_py = self.lonlat_to_px(cx, cy)
        poly.label_id = self.canvas.create_text(label_px, label_py, text=poly.name, fill=color, font=("Arial", 12, "bold"))
        self.canvas.tag_bind(poly.label_id, "<Button-3>", lambda e, p=poly: self._on_label_right_click(e, p))

    def _on_left_click(self, event: tk.Event) -> None:
        lon, lat = self.px_to_lonlat(event.x, event.y)
        self._add_current_point(lon, lat, event.x, event.y)

    def _add_current_point(self, lon: float, lat: float, px: Optional[float] = None, py: Optional[float] = None) -> None:
        if px is None or py is None:
            px, py = self.lonlat_to_px(lon, lat)
        point_id = self.canvas.create_oval(px - 4, py - 4, px + 4, py + 4, fill="red", outline="red")
        self.current_points.append((lon, lat))
        self.current_canvas_points.append(point_id)
        if len(self.current_points) > 1:
            prev_px, prev_py = self.lonlat_to_px(*self.current_points[-2])
            line_id = self.canvas.create_line(prev_px, prev_py, px, py, fill="red", width=2)
            self.current_canvas_lines.append(line_id)
        self._update_status(event_px=px, event_py=py)

    def _on_motion(self, event: tk.Event) -> None:
        lon, lat = self.px_to_lonlat(event.x, event.y)
        self._update_status(lon=lon, lat=lat)
        if not self.current_points:
            return
        last_px, last_py = self.lonlat_to_px(*self.current_points[-1])
        if self.preview_line:
            self.canvas.delete(self.preview_line)
        self.preview_line = self.canvas.create_line(last_px, last_py, event.x, event.y, fill="red", dash=(4, 2))

    def _find_snap_point(self, px: float, py: float) -> Optional[Tuple[float, float, float, float]]:
        candidates: List[Tuple[float, float, float, float]] = []
        for poly in self.polygons:
            for lon, lat in poly.vertices:
                spx, spy = self.lonlat_to_px(lon, lat)
                dist = math.hypot(spx - px, spy - py)
                if dist <= SNAP_THRESHOLD:
                    candidates.append((dist, lon, lat, spx, spy))
        if candidates:
            candidates.sort(key=lambda c: c[0])
            _, lon, lat, spx, spy = candidates[0]
            return lon, lat, spx, spy
        if self.current_points:
            first_lon, first_lat = self.current_points[0]
            spx, spy = self.lonlat_to_px(first_lon, first_lat)
            if math.hypot(spx - px, spy - py) <= SNAP_THRESHOLD:
                return first_lon, first_lat, spx, spy
        return None

    def _on_right_click(self, event: tk.Event) -> None:
        if self._label_clicked(event):
            return
        snap = self._find_snap_point(event.x, event.y)
        if snap:
            lon, lat, spx, spy = snap
            if self.current_points and (lon, lat) == self.current_points[0] and len(self.current_points) >= 3:
                self._add_current_point(lon, lat, spx, spy)
                self._finalize_current_polygon()
                return
            self._add_current_point(lon, lat, spx, spy)
            return

        if len(self.current_points) >= 2:
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="一つ前の点を削除", command=self._remove_last_point)
            menu.add_command(label="キャンセル")
            menu.tk_popup(event.x_root, event.y_root)

    def _label_clicked(self, event: tk.Event) -> bool:
        items = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in items:
            for poly in self.polygons:
                if poly.label_id == item:
                    self._show_label_menu(event, poly)
                    return True
        return False

    def _show_label_menu(self, event: tk.Event, poly: Polygon) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="このポリゴンを削除", command=lambda p=poly: self._delete_polygon(p))
        menu.add_command(label="キャンセル")
        menu.tk_popup(event.x_root, event.y_root)

    def _remove_last_point(self) -> None:
        if not self.current_points:
            return
        self.canvas.delete(self.current_canvas_points.pop())
        if self.current_canvas_lines:
            self.canvas.delete(self.current_canvas_lines.pop())
        self.current_points.pop()
        if self.preview_line:
            self.canvas.delete(self.preview_line)
            self.preview_line = None

    def _finalize_current_polygon(self) -> None:
        if not self.current_points or len(self.current_points) < 3:
            return
        if self._has_self_intersection(self.current_points):
            messagebox.showerror("自己交差", "ポリゴンが自己交差しています。このポリゴンは削除されます。")
            self._clear_current_drawing()
            return
        name = simpledialog.askstring("ポリゴン名", "ポリゴン名を入力してください")
        if not name:
            self._clear_current_drawing()
            return

        for pid in self.current_canvas_points:
            self.canvas.itemconfig(pid, fill="black", outline="black")
        for lid in self.current_canvas_lines:
            self.canvas.itemconfig(lid, fill="black")
        if self.preview_line:
            self.canvas.delete(self.preview_line)
            self.preview_line = None

        poly = Polygon(name=name, vertices=list(self.current_points), canvas_points=list(self.current_canvas_points), canvas_lines=list(self.current_canvas_lines))
        cx, cy = poly.centroid()
        lpx, lpy = self.lonlat_to_px(cx, cy)
        poly.label_id = self.canvas.create_text(lpx, lpy, text=name, fill="black", font=("Arial", 12, "bold"))
        self.canvas.tag_bind(poly.label_id, "<Button-3>", lambda e, p=poly: self._on_label_right_click(e, p))
        self.polygons.append(poly)

        self.current_points.clear()
        self.current_canvas_points.clear()
        self.current_canvas_lines.clear()

        if not messagebox.askyesno("続行", "続けてポリゴンを作成しますか？"):
            self._save_polygons()

    def _on_label_right_click(self, event: tk.Event, poly: Polygon) -> None:
        self._show_label_menu(event, poly)

    def _delete_polygon(self, poly: Polygon) -> None:
        for pid in poly.canvas_points:
            self.canvas.delete(pid)
        for lid in poly.canvas_lines:
            self.canvas.delete(lid)
        if poly.label_id:
            self.canvas.delete(poly.label_id)
        self.polygons = [p for p in self.polygons if p is not poly]

    def _has_self_intersection(self, vertices: List[Tuple[float, float]]) -> bool:
        def segments_intersect(p1, p2, p3, p4) -> bool:
            def ccw(a, b, c) -> float:
                return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

            d1 = ccw(p1, p2, p3)
            d2 = ccw(p1, p2, p4)
            d3 = ccw(p3, p4, p1)
            d4 = ccw(p3, p4, p2)

            if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
                return True
            if d1 == 0 and self._on_segment(p1, p2, p3):
                return True
            if d2 == 0 and self._on_segment(p1, p2, p4):
                return True
            if d3 == 0 and self._on_segment(p3, p4, p1):
                return True
            if d4 == 0 and self._on_segment(p3, p4, p2):
                return True
            return False

        n = len(vertices)
        for i in range(n):
            for j in range(i + 1, n):
                if abs(i - j) <= 1 or (i == 0 and j == n - 1):
                    continue
                p1, p2 = vertices[i], vertices[(i + 1) % n]
                p3, p4 = vertices[j], vertices[(j + 1) % n]
                if segments_intersect(p1, p2, p3, p4):
                    return True
        return False

    @staticmethod
    def _on_segment(p1: Tuple[float, float], p2: Tuple[float, float], q: Tuple[float, float]) -> bool:
        return min(p1[0], p2[0]) <= q[0] <= max(p1[0], p2[0]) and min(p1[1], p2[1]) <= q[1] <= max(p1[1], p2[1])

    def _clear_current_drawing(self) -> None:
        for pid in self.current_canvas_points:
            self.canvas.delete(pid)
        for lid in self.current_canvas_lines:
            self.canvas.delete(lid)
        if self.preview_line:
            self.canvas.delete(self.preview_line)
            self.preview_line = None
        self.current_points.clear()
        self.current_canvas_points.clear()
        self.current_canvas_lines.clear()

    def _save_polygons(self) -> None:
        initialdir = os.path.dirname(self.csv_path) if self.csv_path else os.getcwd()
        path = filedialog.asksaveasfilename(
            initialfile=os.path.basename(self.csv_path) if self.csv_path else "polygon_data.csv",
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialdir=initialdir,
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for poly in self.polygons:
                row = [poly.name]
                for lon, lat in poly.vertices:
                    row.extend([lon, lat])
                writer.writerow(row)
        messagebox.showinfo("保存完了", f"ポリゴンデータを保存しました:\n{path}")
        self.root.quit()

    def _update_status(self, lon: Optional[float] = None, lat: Optional[float] = None, event_px: Optional[float] = None, event_py: Optional[float] = None) -> None:
        status_parts = [f"モード: {self.current_mode.get()}"]
        if lon is not None and lat is not None:
            status_parts.append(f"カーソル: lon={lon:.6f}, lat={lat:.6f}")
        if event_px is not None and event_py is not None:
            status_parts.append(f"ピクセル: x={event_px:.1f}, y={event_py:.1f}")
        self.status.config(text=" | ".join(status_parts))


def main() -> None:
    PolygonBuilder()


if __name__ == "__main__":
    main()
