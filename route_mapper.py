"""Route Mapper Desktop Application

This script provides a Tkinter-based desktop GUI for browsing and visualising
route CSV files. It scans the configured directory for CSV files that match the
provided filename pattern and renders the selected file inside an embedded
Matplotlib figure. Switching between files reuses a single window and redraws
the existing axes according to the rendering rules described below.

Usage:
    python route_mapper.py

Configuration constants near the top of this file define how files are
discovered and which columns represent longitude, latitude, and flag values.
Adjust INPUT_DIR, PATTERN, and column indices as needed to fit your data.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INPUT_DIR = r"D:\01仕事\05 ETC2.0分析\生データ\out(1st)"
PATTERN = "R7_2_*.csv"  # glob on filename
ENCODING = "utf-8"  # or "cp932"
DELIM = ","  # CSV delimiter

# 1-based column indices in the input CSV
LON_COL_1B = 2
LAT_COL_1B = 3
FLAG_COL_1B = 13

# Marker sizes
SIZE_START = 90
SIZE_END = 90
SIZE_OTHER = 20

# Line width and alpha
LINE_WIDTH = 1.2
LINE_ALPHA = 0.9

# Performance
MAX_POINTS_FOR_ANTIALIAS = 100_000

WINDOW_TITLE = "Route Mapper"
WINDOW_SIZE = "1280x800"

# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def discover_csv_files(directory: Path, pattern: str) -> List[Path]:
    """Return a sorted list of CSV paths matching pattern in directory."""
    if not directory.exists():
        return []
    return sorted(directory.glob(pattern))


def load_route_data(csv_path: Path) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Load longitude, latitude, and flag arrays from the CSV file.

    Returns None if reading fails or no valid points are available.
    """

    usecols = [LON_COL_1B - 1, LAT_COL_1B - 1, FLAG_COL_1B - 1]
    try:
        df = pd.read_csv(
            csv_path,
            header=None,
            usecols=usecols,
            encoding=ENCODING,
            sep=DELIM,
            dtype=str,
            engine="c",
        )
    except Exception as exc:  # pragma: no cover - GUI feedback instead
        messagebox.showerror("読み込みエラー", f"CSVの読み込みに失敗しました:\n{csv_path}\n\n{exc}")
        return None

    df.columns = ["lon", "lat", "flag"]
    df[["lon", "lat"]] = df[["lon", "lat"]].apply(pd.to_numeric, errors="coerce")
    df["flag"] = pd.to_numeric(df["flag"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["lon", "lat", "flag"])

    if df.empty:
        messagebox.showinfo("情報", "有効な位置情報がありませんでした。")
        return None

    lons = df["lon"].to_numpy(dtype=float, copy=False)
    lats = df["lat"].to_numpy(dtype=float, copy=False)
    flags = df["flag"].to_numpy(dtype=np.int64, copy=False)
    return lons, lats, flags


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------


npt = np.ndarray


class RouteMapperApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_SIZE)

        self.files = discover_csv_files(Path(INPUT_DIR), PATTERN)
        self.current_index: Optional[int] = None

        self.status_var = tk.StringVar(value="ファイルを選択してください。")

        self._build_layout()
        self._populate_listbox()

        if self.files:
            self.select_index(0)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Left pane with listbox and buttons
        left_frame = tk.Frame(self.root, padx=10, pady=10)
        left_frame.grid(row=0, column=0, sticky="nsew")
        left_frame.rowconfigure(1, weight=1)

        label = tk.Label(left_frame, text="CSVファイル")
        label.grid(row=0, column=0, columnspan=2, pady=(0, 5))

        self.listbox = tk.Listbox(left_frame, exportselection=False, activestyle="dotbox")
        self.listbox.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

        scrollbar = tk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.grid(row=1, column=2, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        up_button = tk.Button(left_frame, text="▲ 上へ", command=self.move_up)
        up_button.grid(row=2, column=0, sticky="ew", pady=(5, 0))

        down_button = tk.Button(left_frame, text="▼ 下へ", command=self.move_down)
        down_button.grid(row=2, column=1, sticky="ew", pady=(5, 0))

        # Right pane with matplotlib figure
        right_frame = tk.Frame(self.root)
        right_frame.grid(row=0, column=1, sticky="nsew")
        right_frame.rowconfigure(0, weight=1)
        right_frame.columnconfigure(0, weight=1)

        self.figure = Figure(figsize=(6, 4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_title("ルート図")
        self.ax.set_xlabel("Longitude")
        self.ax.set_ylabel("Latitude")

        self.canvas = FigureCanvasTkAgg(self.figure, master=right_frame)
        canvas_widget = self.canvas.get_tk_widget()
        canvas_widget.grid(row=0, column=0, sticky="nsew")

        # Status bar
        status_label = tk.Label(self.root, textvariable=self.status_var, anchor="w", relief=tk.SUNKEN)
        status_label.grid(row=1, column=0, columnspan=2, sticky="ew")

        # Keyboard bindings
        self.root.bind("<Up>", lambda event: self.move_up())
        self.root.bind("<Down>", lambda event: self.move_down())

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _populate_listbox(self) -> None:
        self.listbox.delete(0, tk.END)
        for path in self.files:
            self.listbox.insert(tk.END, path.name)
        if not self.files:
            self.status_var.set("CSVファイルが見つかりませんでした。")

    def _on_listbox_select(self, _event: tk.Event) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]
        if index != self.current_index:
            self.select_index(index)

    def move_up(self) -> None:
        if not self.files:
            return
        if self.current_index is None:
            target = 0
        else:
            target = max(self.current_index - 1, 0)
        self.select_index(target)

    def move_down(self) -> None:
        if not self.files:
            return
        if self.current_index is None:
            target = 0
        else:
            target = min(self.current_index + 1, len(self.files) - 1)
        self.select_index(target)

    def select_index(self, index: int) -> None:
        if not self.files:
            return
        index = max(0, min(index, len(self.files) - 1))
        self.current_index = index
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)
        self.render_current()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render_current(self) -> None:
        if self.current_index is None or not self.files:
            return

        csv_path = self.files[self.current_index]
        self.status_var.set(f"読み込み中... {csv_path.name}")
        self.root.update_idletasks()

        result = load_route_data(csv_path)
        if result is None:
            # Keep previous figure intact
            if self.current_index is not None:
                self.status_var.set(f"読み込み失敗: {csv_path.name}")
            return

        lons, lats, flags = result
        point_count = len(lons)
        self.status_var.set(f"{csv_path.name} - {point_count} 点")

        self.ax.clear()

        use_antialias = point_count <= MAX_POINTS_FOR_ANTIALIAS

        # Scatter groups
        is_start = flags == 0
        is_end = flags == 1
        is_other = ~is_start & ~is_end

        if is_start.any():
            self.ax.scatter(
                lons[is_start],
                lats[is_start],
                s=SIZE_START,
                facecolors="white",
                edgecolors="red",
                linewidths=1.5,
                alpha=1.0,
                antialiased=use_antialias,
                zorder=3,
            )

        if is_end.any():
            self.ax.scatter(
                lons[is_end],
                lats[is_end],
                s=SIZE_END,
                facecolors="blue",
                edgecolors="blue",
                linewidths=0.0,
                alpha=1.0,
                antialiased=use_antialias,
                zorder=3,
            )

        if is_other.any():
            self.ax.scatter(
                lons[is_other],
                lats[is_other],
                s=SIZE_OTHER,
                facecolors="black",
                edgecolors="black",
                linewidths=0.0,
                alpha=1.0,
                antialiased=use_antialias,
                zorder=2,
            )

        segments = self._build_segments(lons, lats, flags)
        if segments:
            collection = LineCollection(
                segments,
                colors="black",
                linewidths=LINE_WIDTH,
                alpha=LINE_ALPHA,
                antialiased=use_antialias,
            )
            self.ax.add_collection(collection)

        self.ax.set_aspect("equal", adjustable="datalim")
        self.ax.autoscale(enable=True, tight=True)
        self.ax.set_title(csv_path.name)
        self.ax.grid(False)

        self.canvas.draw_idle()

    @staticmethod
    def _build_segments(lons: npt, lats: npt, flags: npt) -> List[list[tuple[float, float]]]:
        segments: List[list[tuple[float, float]]] = []
        if len(lons) < 2:
            return segments
        for idx in range(len(lons) - 1):
            if flags[idx] == 1:
                continue
            if flags[idx + 1] == 0:
                continue
            segments.append([(lons[idx], lats[idx]), (lons[idx + 1], lats[idx + 1])])
        return segments

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    app = RouteMapperApp()
    app.run()


if __name__ == "__main__":
    main()
