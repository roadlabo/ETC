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
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import folium
import pandas as pd
import tkinter as tk
import webbrowser
from tkinter import Tk, filedialog, messagebox, ttk

BROWSER_OPENED = False
AUTO_REFRESH_SECONDS = 0  # disable periodic auto refresh

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


def summarize_set(series: Iterable[object], mapping: dict[int, str]) -> str:
    """Return comma-separated unique labels mapped from ``series``."""

    labels: set[str] = set()
    for value in series:
        label = "その他"
        try:
            ivalue = int(float(value))
        except (TypeError, ValueError):
            ivalue = None
        if ivalue in mapping:
            label = mapping[ivalue]
        labels.add(label)

    if not labels:
        return "-"

    return ", ".join(sorted(labels))


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


def ensure_auto_refresh(out_path: Path) -> None:
    """Deprecated: auto-refresh disabled."""
    return


class RouteMapperApp:
    def __init__(self, directory: Path, pattern: str = "*.csv") -> None:
        self.directory = directory
        self.pattern = pattern
        self.root = tk.Tk()
        self.root.title("CSV選択")
        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=0)

        left = tk.Frame(self.root, padx=6, pady=6)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        list_frame = tk.Frame(left)
        list_frame.grid(row=0, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(list_frame, width=32, height=20, exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        right = ttk.Frame(self.root, padding=(6, 6))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        info_title = ttk.Label(right, text="選択中CSVの情報", font=("Segoe UI", 10, "bold"))
        info_title.grid(row=0, column=0, sticky="w")

        self.lbl_count = ttk.Label(right, text="点数: -")
        self.lbl_range = ttk.Label(right, text="GPS時刻: -")
        self.lbl_type = ttk.Label(right, text="種別: -")
        self.lbl_use = ttk.Label(right, text="用途: -")

        for idx, widget in enumerate((self.lbl_count, self.lbl_range, self.lbl_type, self.lbl_use), start=1):
            widget.grid(row=idx, column=0, sticky="w", pady=2)

        right.rowconfigure(len((self.lbl_count, self.lbl_range, self.lbl_type, self.lbl_use)) + 1, weight=1)

        self.status_var = tk.StringVar(value="CSVファイルを選択してください。")
        status_label = ttk.Label(right, textvariable=self.status_var, anchor="w")
        status_label.grid(row=6, column=0, sticky="ew", pady=(8, 0))

        btn_frame = tk.Frame(self.root, padx=6, pady=4)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        self.up_button = tk.Button(btn_frame, text="▲上へ", command=self.move_up)
        self.up_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.down_button = tk.Button(btn_frame, text="▼下へ", command=self.move_down)
        self.down_button.grid(row=0, column=1, sticky="ew")

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
        self.lbl_count.config(text="点数: 0")
        self.lbl_range.config(text="GPS時刻: -")
        self.lbl_type.config(text="種別: -")
        self.lbl_use.config(text="用途: -")

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

        self.lbl_type.config(text=f"種別: {summarize_set(df.iloc[:, 0], TYPE_MAP)}")
        self.lbl_use.config(text=f"用途: {summarize_set(df.iloc[:, 1], USE_MAP)}")

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

        # disable old auto-refresh call
        # ensure_auto_refresh(out_path)

        self.status_var.set(f"Saved map for {csv_path.name} -> {out_path.name}")

        # ファイルの更新時刻をURLにクエリとして付与
        version = int(out_path.stat().st_mtime)
        url = out_path.as_uri() + f"?v={version}"

        global BROWSER_OPENED
        try:
            if not BROWSER_OPENED:
                # 初回のみ新しいタブで開く
                webbrowser.open(url, new=1)
                BROWSER_OPENED = True
            else:
                # 以降は同じタブを再読み込み（新しいタブを作らない）
                webbrowser.open(url, new=0)
        except Exception:
            messagebox.showwarning("Browser", "Could not open or refresh map in web browser.")

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
