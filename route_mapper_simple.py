"""Simple standalone route mapper using Tkinter and Folium.

This script scans a target directory for CSV files and lets the user
select one via a minimal Tkinter listbox UI. When a file is selected,
its route is rendered to a Folium map (OpenStreetMap background) and
saved as ``map.html`` in the same directory. The HTML file is opened in
the default web browser and refreshed on each selection.

Usage:
    python route_mapper_simple.py [pattern]

* A folder selection dialog will prompt for the CSV directory.
* ``pattern`` defaults to ``*.csv``.

Dependencies: pandas, folium
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pandas as pd
import folium
import tkinter as tk
from tkinter import Tk, filedialog, messagebox

# CSV column indices (0-based)
LON_COL = 15   # 16列目（経度）
LAT_COL = 14   # 15列目（緯度）
FLAG_COL = 12  # 13列目（フラグ）

# Delimiter for CSV files
DELIM = ","

# Geographic filter for Japan
MIN_LON, MAX_LON = 120.0, 150.0
MIN_LAT, MAX_LAT = 20.0, 50.0

# Marker appearance
START_MARKER = dict(color="red", fill_color="white", fill_opacity=1.0, radius=9, weight=3)
END_MARKER = dict(color="blue", fill_color="blue", fill_opacity=1.0, radius=9, weight=2)
PASS_MARKER = dict(color="black", fill_color="black", fill_opacity=1.0, radius=4, weight=1)
LINE_STYLE = dict(color="black", weight=2, opacity=1.0)


def discover_csv_files(directory: Path, pattern: str) -> List[Path]:
    """Return a sorted list of CSV files matching ``pattern`` inside ``directory``."""

    if not directory.exists():
        return []
    return sorted(path for path in directory.glob(pattern) if path.is_file())


def read_route_data(csv_path: Path) -> pd.DataFrame:
    """Read lon/lat/flag columns from the given CSV path."""

    df = pd.read_csv(
        csv_path,
        header=None,
        usecols=[LON_COL, LAT_COL, FLAG_COL],
        dtype=float,
        engine="c",
        sep=DELIM,
    )

    # Ensure column order matches the original indices before naming
    df = df[[LON_COL, LAT_COL, FLAG_COL]].copy()
    df.columns = ["lon", "lat", "flag"]

    print("[DEBUG]", csv_path.name)
    print("lon/lat head:", df["lon"].head().tolist(), df["lat"].head().tolist())
    print("lon range:", df["lon"].min(), "→", df["lon"].max())
    print("lat range:", df["lat"].min(), "→", df["lat"].max())

    # Swap lon/lat automatically if they appear reversed
    if (
        df["lon"].between(20, 50).mean() > 0.8
        and df["lat"].between(120, 150).mean() > 0.8
    ):
        df[["lon", "lat"]] = df[["lat", "lon"]]

    df = df.dropna(subset=["lon", "lat", "flag"])
    # Filter to Japan bounds
    df = df[(df["lon"].between(MIN_LON, MAX_LON)) & (df["lat"].between(MIN_LAT, MAX_LAT))]
    df["flag"] = df["flag"].astype(int)
    return df


def chunk_route_points(points: Iterable[Tuple[float, float, int]]) -> Iterable[List[Tuple[float, float]]]:
    """Yield contiguous point sequences respecting start/end flag rules."""

    segment: List[Tuple[float, float]] = []
    prev_flag: Optional[int] = None

    for lon, lat, flag in points:
        current_point = (lat, lon)
        if not segment:
            segment.append(current_point)
        else:
            if prev_flag == 1 or flag == 0:
                if len(segment) >= 2:
                    yield segment
                segment = [current_point]
            else:
                segment.append(current_point)
        prev_flag = flag

    if len(segment) >= 2:
        yield segment


class RouteMapperApp:
    def __init__(self, directory: Path, pattern: str = "*.csv") -> None:
        self.directory = directory
        self.pattern = pattern
        self.root = tk.Tk()
        self.root.title("Route Mapper")
        self.root.resizable(False, False)

        self.listbox = tk.Listbox(self.root, width=50, height=20, exportselection=False)
        self.listbox.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(10, 0), pady=10)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        scrollbar = tk.Scrollbar(self.root, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.grid(row=0, column=1, rowspan=3, sticky="ns", pady=10)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        self.up_button = tk.Button(self.root, text="▲", width=4, command=self.move_up)
        self.up_button.grid(row=0, column=2, sticky="ew", padx=10, pady=(10, 0))

        self.down_button = tk.Button(self.root, text="▼", width=4, command=self.move_down)
        self.down_button.grid(row=1, column=2, sticky="ew", padx=10)

        self.refresh_button = tk.Button(self.root, text="🔄", width=4, command=self.refresh_files)
        self.refresh_button.grid(row=2, column=2, sticky="ew", padx=10, pady=(0, 10))

        self.status_var = tk.StringVar(value="Select a CSV file.")
        status_label = tk.Label(self.root, textvariable=self.status_var, anchor="w")
        status_label.grid(row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 10))

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.files: List[Path] = []
        self.map_path = self.directory / "map.html"
        self.refresh_files()

    # ------------------------------------------------------------------
    # File list management
    # ------------------------------------------------------------------
    def refresh_files(self) -> None:
        self.files = discover_csv_files(self.directory, self.pattern)
        self.listbox.delete(0, tk.END)
        for file in self.files:
            self.listbox.insert(tk.END, file.name)

        if not self.files:
            self.status_var.set("No CSV files found.")
        else:
            self.status_var.set("Select a CSV file.")
            self.listbox.selection_clear(0, tk.END)
            self.listbox.activate(0)
            self.listbox.selection_set(0)
            self.on_select()

    def move_up(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]
        new_index = max(0, index - 1)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(new_index)
        self.listbox.activate(new_index)
        self.on_select()

    def move_down(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]
        new_index = min(len(self.files) - 1, index + 1)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(new_index)
        self.listbox.activate(new_index)
        self.on_select()

    # ------------------------------------------------------------------
    # Map rendering
    # ------------------------------------------------------------------
    def on_select(self, _event: Optional[tk.Event] = None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]
        csv_path = self.files[index]
        try:
            df = read_route_data(csv_path)
        except Exception as exc:  # GUI feedback only
            messagebox.showerror("Read error", f"Failed to load CSV:\n{csv_path}\n\n{exc}")
            return

        if df.empty:
            messagebox.showinfo("Info", "No valid points inside Japan were found in this file.")
            self.status_var.set(f"{csv_path.name}: no valid points")
            return

        self.status_var.set(f"Rendering {csv_path.name} ({len(df)} points)")
        self.render_map(csv_path, df)

    def render_map(self, csv_path: Path, df: pd.DataFrame) -> None:
        start_location = [df.iloc[0]["lat"], df.iloc[0]["lon"]]
        fmap = folium.Map(location=start_location, zoom_start=12, tiles="OpenStreetMap")

        for lon, lat, flag in df[["lon", "lat", "flag"]].itertuples(index=False, name=None):
            if flag == 0:
                style = dict(START_MARKER)
            elif flag == 1:
                style = dict(END_MARKER)
            else:
                style = dict(PASS_MARKER)
            folium.CircleMarker(location=(lat, lon), **style).add_to(fmap)

        for segment in chunk_route_points(df[["lon", "lat", "flag"]].itertuples(index=False, name=None)):
            folium.PolyLine(segment, **LINE_STYLE).add_to(fmap)

        fmap.save(self.map_path)
        self.status_var.set(f"Saved map for {csv_path.name} -> {self.map_path.name}")
        self.open_map()

    def open_map(self) -> None:
        try:
            webbrowser.open(self.map_path.as_uri(), new=0, autoraise=True)
        except Exception:
            messagebox.showwarning("Browser", "Could not open map in web browser.")

    # ------------------------------------------------------------------
    # Tk mainloop
    # ------------------------------------------------------------------
    def run(self) -> None:
        self.root.mainloop()


def main(argv: Sequence[str]) -> None:
    pattern = argv[1] if len(argv) > 1 else "*.csv"

    root = Tk()
    root.withdraw()
    selected = filedialog.askdirectory(
        title="CSVフォルダを選択してください",
        initialdir=r"D:\01仕事\05 ETC2.0分析\生データ",
    )
    root.destroy()

    if not selected:
        print("キャンセルされました。処理を終了します。")
        sys.exit()

    directory = Path(selected)
    print(f"選択されたフォルダ: {directory}")
    directory = directory.resolve()

    if not directory.exists():
        messagebox.showerror("Directory not found", f"Directory does not exist:\n{directory}")
        return

    app = RouteMapperApp(directory=directory, pattern=pattern)
    app.run()


if __name__ == "__main__":
    main(sys.argv)
