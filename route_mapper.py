import csv
import os
import tempfile
import webbrowser
from datetime import datetime
from typing import List, Optional

import folium
import tkinter as tk
from tkinter import messagebox


CSV_EXTENSION = ".csv"


def list_csv_files(folder: str) -> List[str]:
    try:
        entries = [
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if name.lower().endswith(CSV_EXTENSION)
            and os.path.isfile(os.path.join(folder, name))
        ]
    except FileNotFoundError:
        return []

    return sorted(entries)


def parse_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_speed(value: str) -> Optional[float]:
    speed = parse_float(value)
    if speed is None:
        return None
    return speed


def parse_timestamp(value: str) -> str:
    if not value:
        return ""

    patterns = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M",
        "%Y%m%d%H%M%S",
        "%Y%m%d%H%M",
    ]

    for pattern in patterns:
        try:
            dt = datetime.strptime(value, pattern)
            return f"{dt.year}年{dt.month}月{dt.day}日{dt.hour}時{dt.minute}分"
        except ValueError:
            continue

    # Attempt to parse only date part and time part if separated by non-digit
    try:
        cleaned = value.replace("年", "-").replace("月", "-").replace("日", " ")
        cleaned = cleaned.replace("時", ":").replace("分", "")
        dt = datetime.fromisoformat(cleaned)
        return f"{dt.year}年{dt.month}月{dt.day}日{dt.hour}時{dt.minute}分"
    except Exception:
        return value


def trip_flag_label(value: str) -> str:
    flag_map = {
        "0": "起点",
        "1": "終点",
        "2": "通過点",
        "3": "その他",
    }
    return flag_map.get(value, value)


def speed_to_color(speed: Optional[float]) -> str:
    if speed is None:
        return "#808080"
    if speed <= 10:
        return "#ff0000"
    if speed >= 40:
        return "#0000ff"

    ratio = (speed - 10) / 30
    ratio = max(0.0, min(1.0, ratio))
    red = int(255 * (1 - ratio))
    blue = int(255 * ratio)
    return f"#{red:02x}00{blue:02x}"


def load_route_points(csv_path: str):
    points = []
    with open(csv_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            if len(row) < 19:
                continue
            lon = parse_float(row[14])
            lat = parse_float(row[15])
            speed = parse_speed(row[18])
            timestamp = parse_timestamp(row[6])
            flag = trip_flag_label(row[12])

            if lat is None or lon is None:
                continue

            points.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "speed": speed,
                    "timestamp": timestamp,
                    "flag": flag,
                }
            )
    return points


def build_map(points, title: str):
    if not points:
        messagebox.showinfo("情報", "有効な位置情報がありません")
        return

    avg_lat = sum(p["lat"] for p in points) / len(points)
    avg_lon = sum(p["lon"] for p in points) / len(points)

    fmap = folium.Map(location=[avg_lat, avg_lon], zoom_start=13, control_scale=True)

    for idx, point in enumerate(points):
        tooltip_text = (
            f"速度: {point['speed']} km/h\n"
            f"時刻: {point['timestamp']}\n"
            f"トリップ起終点フラグ: {point['flag']}"
        )
        folium.CircleMarker(
            location=[point["lat"], point["lon"]],
            radius=4,
            color="white",
            fill=True,
            fill_color=speed_to_color(point["speed"]),
            fill_opacity=0.9,
            tooltip=tooltip_text,
        ).add_to(fmap)

        if idx > 0:
            prev = points[idx - 1]
            color = speed_to_color(point["speed"])
            folium.PolyLine(
                locations=[
                    [prev["lat"], prev["lon"]],
                    [point["lat"], point["lon"]],
                ],
                color=color,
                weight=4,
                opacity=0.8,
            ).add_to(fmap)

    with tempfile.NamedTemporaryFile(prefix="route_map_", suffix=".html", delete=False) as tmp:
        tmp_path = tmp.name

    fmap.save(tmp_path)
    webbrowser.open(f"file://{tmp_path}")
    print(f"マップを表示しました: {title}")


class RouteMapperApp:
    def __init__(self, folder: str):
        self.folder = folder
        self.root = tk.Tk()
        self.root.title("CSV ルートリスト")
        self.csv_files: List[str] = []
        self.listbox = tk.Listbox(self.root, width=60, height=20)
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.listbox.bind("<Double-Button-1>", self.on_select)
        self.populate_list()

    def populate_list(self):
        csv_files = list_csv_files(self.folder)
        if not csv_files:
            messagebox.showinfo("情報", "CSV ファイルが見つかりません")
            return
        self.csv_files = csv_files
        for path in csv_files:
            self.listbox.insert(tk.END, os.path.basename(path))

    def on_select(self, event):
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]
        csv_path = self.csv_files[index]
        try:
            points = load_route_points(csv_path)
        except Exception as exc:
            messagebox.showerror("エラー", f"CSV の読み込みに失敗しました:\n{exc}")
            return

        build_map(points, os.path.basename(csv_path))

    def run(self):
        self.root.mainloop()


def main():
    folder = input("CSV フォルダのパスを入力してください: ").strip()
    if not folder:
        print("フォルダが指定されていません。")
        return

    if not os.path.isdir(folder):
        print("指定されたフォルダが存在しません。")
        return

    app = RouteMapperApp(folder)
    app.run()


if __name__ == "__main__":
    main()
