"""Simple standalone route mapper using Tkinter and Folium.

This script scans a target directory for CSV files and lets the user
select one via a Tkinter listbox UI. When a file is selected, its route
is rendered to a Folium map (OpenStreetMap background) and saved as
``map.html`` beside this script. The browser tab is opened only once on
the first render to avoid duplicate tabs.

Usage:
    python route_mapper_simple.py [pattern]

* A folder selection dialog will prompt for the CSV directory.
* ``pattern`` defaults to ``*.csv``.

Dependencies: pandas, folium
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import folium
import pandas as pd
import tkinter as tk
import webbrowser
from tkinter import Tk, filedialog, messagebox

BROWSER_OPENED = False

# CSV column indices (0-based)
LON_COL = 15    # 16列目（経度）
LAT_COL = 14    # 15列目（緯度）
FLAG_COL = 12   # 13列目（フラグ）
TYPE_COL = 4    # 種別
USE_COL = 5     # 用途
TIME_COL = 6    # GPS時刻
SPEED_COL = 18  # 速度

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


# Mapping tables for info panel
TYPE_MAP = {0: "軽二輪", 1: "大型", 2: "普通", 3: "小型", 4: "軽自動車"}
USE_MAP = {0: "未使用", 1: "乗用", 2: "貨物", 3: "特殊", 4: "乗合"}


def parse_gps_time(val: object) -> Optional[datetime]:
    """Parse GPS timestamp strings to :class:`datetime` objects."""

    s = str(val).strip()
    if not s or not s.isdigit():
        return None
    try:
        if len(s) >= 14:
            return datetime.strptime(s[:14], "%Y%m%d%H%M%S")
        if len(s) >= 12:
            return datetime.strptime(s[:12], "%Y%m%d%H%M")
        if len(s) >= 10:
            return datetime.strptime(s[:10], "%Y%m%d%H")
    except ValueError:
        return None
    return None


def fmt_range(dmin: Optional[datetime], dmax: Optional[datetime]) -> str:
    """Return formatted range string for two datetimes."""

    if not dmin or not dmax:
        return "-"
    return (
        f"{dmin.year}年{dmin.month}月{dmin.day}日{dmin.hour}時{dmin.minute}分"
        f"～{dmax.month}月{dmax.day}日{dmax.hour}時{dmax.minute}分"
    )


def summarize_series(series: Iterable[object], mapping: dict[int, str]) -> str:
    """Summarize categorical series values with mapping."""

    counts: Counter[str] = Counter()
    for value in series:
        label = "その他"
        try:
            ivalue = int(float(value))
        except (TypeError, ValueError):
            ivalue = None
        if ivalue in mapping:
            label = mapping[ivalue]
        counts[label] += 1

    if not counts:
        return "-"

    return "、".join(f"{label}:{counts[label]}" for label in sorted(counts))


def fmt_tooltip(time_value: object, speed_value: object) -> str:
    """Return tooltip text for folium markers."""

    dt_obj = parse_gps_time(time_value)
    if dt_obj:
        time_text = (
            f"{dt_obj.year}年{dt_obj.month}月{dt_obj.day}日"
            f"{dt_obj.hour}時{dt_obj.minute}分"
        )
    else:
        time_text = "-"

    speed_text = "-"
    try:
        speed_float = float(speed_value)
        if math.isnan(speed_float):
            raise ValueError
        speed_text = f"{int(round(speed_float))}km/h"
    except (TypeError, ValueError):
        pass

    return f"GPS時刻: {time_text}\n速度: {speed_text}"


def discover_csv_files(directory: Path, pattern: str) -> List[Path]:
    """Return a sorted list of CSV files matching ``pattern`` inside ``directory``."""

    if not directory.exists():
        return []
    return sorted(path for path in directory.glob(pattern) if path.is_file())


def read_route_data(csv_path: Path) -> pd.DataFrame:
    """Read required columns from the given CSV path."""

    usecols = [LON_COL, LAT_COL, FLAG_COL, TYPE_COL, USE_COL, TIME_COL, SPEED_COL]
    df = pd.read_csv(
        csv_path,
        header=None,
        usecols=usecols,
        dtype=str,
        engine="c",
        sep=DELIM,
    )

    df = df[usecols].copy()
    df.columns = ["lon", "lat", "flag", "type", "use", "time", "speed"]

    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["flag"] = pd.to_numeric(df["flag"], errors="coerce")
    df["speed"] = pd.to_numeric(df["speed"], errors="coerce")

    print("[DEBUG]", csv_path.name)
    print("lon/lat head:", df["lon"].head().tolist(), df["lat"].head().tolist())
    print("lon range:", df["lon"].min(), "→", df["lon"].max())
    print("lat range:", df["lat"].min(), "→", df["lat"].max())

    if (
        df["lon"].between(20, 50).mean() > 0.8
        and df["lat"].between(120, 150).mean() > 0.8
    ):
        df[["lon", "lat"]] = df[["lat", "lon"]]

    df = df.dropna(subset=["lon", "lat", "flag"])
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
        self.root.title("CSV選択")

        left = tk.Frame(self.root)
        left.pack(side="left", fill="both", expand=False)

        right = tk.Frame(self.root, padx=8, pady=8)
        right.pack(side="right", fill="both", expand=True)

        list_frame = tk.Frame(left)
        list_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.listbox = tk.Listbox(list_frame, width=50, height=20, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        btn_frame = tk.Frame(left)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.up_button = tk.Button(btn_frame, text="▲上へ", command=self.move_up)
        self.up_button.pack(side="left", fill="x", expand=True)

        self.down_button = tk.Button(btn_frame, text="▼下へ", command=self.move_down)
        self.down_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

        info_title = tk.Label(right, text="選択中CSVの情報", font=("Segoe UI", 10, "bold"))
        info_title.pack(anchor="w")

        self.lbl_count = tk.Label(right, text="点数: -")
        self.lbl_range = tk.Label(right, text="GPS時刻: -")
        self.lbl_type = tk.Label(right, text="自動車の種別: -")
        self.lbl_use = tk.Label(right, text="自動車の用途: -")

        for widget in (self.lbl_count, self.lbl_range, self.lbl_type, self.lbl_use):
            widget.pack(anchor="w", pady=2)

        self.status_var = tk.StringVar(value="CSVファイルを選択してください。")
        status_label = tk.Label(self.root, textvariable=self.status_var, anchor="w")
        status_label.pack(side="bottom", fill="x", padx=10, pady=(0, 10))

        self.files: List[Path] = []
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
            self.update_info(None)
        else:
            self.status_var.set("Select a CSV file.")
            self.listbox.selection_clear(0, tk.END)
            self.listbox.activate(0)
            self.listbox.selection_set(0)
            self.on_select()

    def _set_info_defaults(self) -> None:
        self.lbl_count.config(text="データ点数: 0")
        self.lbl_range.config(text="GPS時刻: -")
        self.lbl_type.config(text="自動車の種別: -")
        self.lbl_use.config(text="自動車の用途: -")

    def update_info(self, csv_path: Optional[Path]) -> None:
        if not csv_path:
            self._set_info_defaults()
            return

        try:
            df = pd.read_csv(
                csv_path,
                header=None,
                sep=DELIM,
                usecols=[TYPE_COL, USE_COL, TIME_COL],
                engine="c",
                dtype=str,
            )
        except Exception:
            self._set_info_defaults()
            return

        self.lbl_count.config(text=f"点数: {len(df)}")

        times = [parse_gps_time(value) for value in df.iloc[:, 2].tolist()]
        times = [t for t in times if t]
        if times:
            self.lbl_range.config(text=f"GPS時刻: {fmt_range(min(times), max(times))}")
        else:
            self.lbl_range.config(text="GPS時刻: -")

        self.lbl_type.config(text=f"自動車の種別: {summarize_series(df.iloc[:, 0], TYPE_MAP)}")
        self.lbl_use.config(text=f"自動車の用途: {summarize_series(df.iloc[:, 1], USE_MAP)}")

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
            self.update_info(None)
            return
        index = selection[0]
        csv_path = self.files[index]
        self.update_info(csv_path)
        try:
            df = read_route_data(csv_path)
        except Exception as exc:  # GUI feedback only
            messagebox.showerror("Read error", f"Failed to load CSV:\n{csv_path}\n\n{exc}")
            self.status_var.set(f"{csv_path.name}: failed to load")
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

        for row in df.itertuples(index=False):
            if row.flag == 0:
                style = dict(START_MARKER)
            elif row.flag == 1:
                style = dict(END_MARKER)
            else:
                style = dict(PASS_MARKER)
            folium.CircleMarker(
                location=(row.lat, row.lon),
                tooltip=fmt_tooltip(row.time, row.speed),
                **style,
            ).add_to(fmap)

        for segment in chunk_route_points(
            df[["lon", "lat", "flag"]].itertuples(index=False, name=None)
        ):
            folium.PolyLine(segment, **LINE_STYLE).add_to(fmap)

        out_path = Path(__file__).with_name("map.html")
        fmap.save(out_path.as_posix())
        self.status_var.set(f"Saved map for {csv_path.name} -> {out_path.name}")

        global BROWSER_OPENED
        if not BROWSER_OPENED:
            try:
                webbrowser.open(out_path.as_uri(), new=1)
            except Exception:
                messagebox.showwarning("Browser", "Could not open map in web browser.")
            else:
                BROWSER_OPENED = True

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
