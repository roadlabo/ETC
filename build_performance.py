import os, csv, glob, math, argparse
from datetime import datetime

# =========================
# ★ スクリプト冒頭で固定指定 ★
# =========================
# trip_extractor.py の出力CSV群フォルダ
INPUT_DIR = r"D:\\path\\to\\trip_extractor_outputs"

# 自動運転ルート.csv のフルパス
ROUTE_PATH = r"D:\\path\\to\\自動運転ルート.csv"

# 出力 paformance001.csv のフルパス
OUTPUT_PATH = r"D:\\path\\to\\paformance001.csv"

# マッチ半径[m]（例：20）
RADIUS_M = 20.0

# ルートCSVの列index（ヘッダー無し）
#   KP列（“そのまま出力する”ラベル）／経度O=14／緯度P=15
ROUTE_KP_COL  = 0   # ←環境に合わせて設定
ROUTE_LON_COL = 14
ROUTE_LAT_COL = 15

# 解析対象CSV（trip_extractor出力）の列index
COL_TIME  = 6   # G
COL_LON   = 14  # O
COL_LAT   = 15  # P
COL_SPEED = 18  # S

# 出力の丸め（平均速度の小数桁）
ROUND_DIGITS = 1

# サブフォルダも探索するなら True
RECURSIVE = True

# 地球半径[m]
EARTH_R = 6_371_000.0
# =========================


def deg2rad(d): return d * math.pi / 180.0

def haversine_m(lat1, lon1, lat2, lon2):
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2.0)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2.0)**2
    return 2.0 * EARTH_R * math.asin(math.sqrt(a))

def parse_hour(s):
    if not s: return None
    s = s.strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try: return datetime.strptime(s, fmt).hour
        except: pass
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S","%Y/%m/%d %H:%M",
                "%Y-%m-%dT%H:%M:%S","%Y-%m-%dT%H:%M",
                "%Y/%m/%dT%H:%M:%S","%Y/%m/%dT%H:%M"):
        try: return datetime.strptime(s, fmt).hour
        except: pass
    try:
        if " " in s: return int(s.split(" ")[1][:2])
        if "T" in s: return int(s.split("T")[1][:2])
    except: pass
    return None

def read_route(route_path):
    """ルートCSVから KPラベル(文字列), lat(rad), lon(rad) の配列を出現順で取得"""
    kp_labels, lat_r, lon_r = [], [], []
    seen = set()
    with open(route_path, "r", newline="", encoding="cp932") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row: continue
            try:
                kp_raw = row[ROUTE_KP_COL]
                lon = float(row[ROUTE_LON_COL])
                lat = float(row[ROUTE_LAT_COL])
            except Exception:
                continue
            # KPラベルは“そのまま文字列”で保持（桁や表記を変えない）
            kp_label = str(kp_raw)
            kp_labels.append(kp_label)
            lat_r.append(deg2rad(lat))
            lon_r.append(deg2rad(lon))
    # 出力順を決めるための“出現順ユニークKPラベル”リスト
    order = []
    for kp in kp_labels:
        if kp not in seen:
            seen.add(kp)
            order.append(kp)
    return kp_labels, lat_r, lon_r, order

def nearest_route_index(lat_deg, lon_deg, route_lat_r, route_lon_r):
    """観測点→ルート最近傍インデックス（総当たり）"""
    lr = deg2rad(lat_deg); lo = deg2rad(lon_deg)
    min_d, min_i = float("inf"), -1
    for i in range(len(route_lat_r)):
        d = haversine_m(lr, lo, route_lat_r[i], route_lon_r[i])
        if d < min_d:
            min_d, min_i = d, i
    return min_d, min_i

def list_input_csvs(input_dir, recursive):
    pattern = "**/*.csv" if recursive else "*.csv"
    return glob.glob(os.path.join(input_dir, pattern), recursive=recursive)

def write_header(writer):
    # 見本と同じヘッダー3行（50列固定）
    writer.writerow(["自動運転バス運行ルート　パフォーマンス調査"] + [""]*49)
    writer.writerow(["キロ"] + ["速度"]*24 + ["キロ"] + ["台数"]*24)
    time_labels = [f"{h}-{h+1}" for h in range(24)]
    writer.writerow(["ポスト"] + time_labels + ["ポスト"] + time_labels)

def format_datetime(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_timedelta(td):
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def print_progress(current, total, width=40):
    if total <= 0:
        bar = "-" * width
        pct = 100.0
    else:
        ratio = min(max(current / total, 0.0), 1.0)
        filled = int(width * ratio)
        bar = "#" * filled + "-" * (width - filled)
        pct = ratio * 100.0
    print(f"\r[PROGRESS] |{bar}| {pct:6.2f}% ({current}/{total})", end="", flush=True)


def main():
    ap = argparse.ArgumentParser(description="paformance001.csv を見本通りに生成（KPはルートCSVの値をそのまま使用）")
    ap.add_argument("--recursive", action="store_true", help="サブフォルダも探索（既定はRECURSIVEに従う）")
    args = ap.parse_args()

    global RECURSIVE
    if args.recursive:
        RECURSIVE = True

    # ルート読込
    kp_labels_seq, route_lat_r, route_lon_r, kp_order = read_route(ROUTE_PATH)
    if not kp_order:
        raise RuntimeError("ルートCSVからKPが読み取れませんでした。ROUTE_KP_COLの設定を確認してください。")

    kp_count = len(kp_order)
    print(f"[INFO] キロポスト数: {kp_count}")

    # 集計器：stats[kp_label][hour] = [sum_speed, count]
    stats = {kp: [[0.0, 0] for _ in range(24)] for kp in kp_order}

    files = list_input_csvs(INPUT_DIR, RECURSIVE)
    total_files = len(files)
    print(f"[INFO] 検索対象CSV数: {total_files} @ {INPUT_DIR}")

    processing_start = datetime.now()
    print(f"[TIME] Start: {format_datetime(processing_start)}")

    processed_files = 0
    had_warning = False
    if total_files == 0:
        print_progress(total_files, total_files)

    for path in files:
        try:
            with open(path, "r", newline="", encoding="cp932") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row: continue
                    try:
                        lon = float(row[COL_LON])
                        lat = float(row[COL_LAT])
                        hour = parse_hour(row[COL_TIME])
                        if hour is None or not (0 <= hour <= 23):
                            continue
                        spd = float(row[COL_SPEED])
                        if spd < 0 or spd > 300:  # 常識的範囲
                            continue
                    except Exception:
                        continue

                    d, idx = nearest_route_index(lat, lon, route_lat_r, route_lon_r)
                    if idx < 0 or d > RADIUS_M:
                        continue
                    kp = kp_labels_seq[idx]  # 最近傍点の“元のKPラベル”
                    bucket = stats.get(kp)
                    if bucket is None:
                        # ルート外のKPが拾われた場合（通常は起きないがガード）
                        continue
                    s, c = bucket[hour]
                    bucket[hour] = [s + spd, c + 1]
        except Exception as e:
            print(f"[WARN] 読み込み失敗: {path} ({e})")
            had_warning = True
        finally:
            processed_files += 1
            print_progress(processed_files, total_files)

    print()

    # 出力（Shift_JIS, CRLF）
    with open(OUTPUT_PATH, "w", newline="", encoding="cp932") as f:
        writer = csv.writer(f, lineterminator="\r\n")
        write_header(writer)
        for kp in kp_order:
            avg24, cnt24 = [], []
            buckets = stats[kp]
            for h in range(24):
                s, c = buckets[h]
                if c > 0:
                    avg24.append(f"{round(s / c, ROUND_DIGITS)}")
                    cnt24.append(str(c))
                else:
                    avg24.append("")
                    cnt24.append("")
            # “キロ”列は左右ともルートCSVのKPラベルをそのまま出力
            row = [kp] + avg24 + [kp] + cnt24
            writer.writerow(row)

    print(f"[DONE] 出力: {OUTPUT_PATH}")

    processing_end = datetime.now()
    print(f"[TIME] End: {format_datetime(processing_end)}")
    print(f"[TIME] Duration: {format_timedelta(processing_end - processing_start)}")

    if not had_warning:
        print("Congratulations, everything completed successfully.")

if __name__ == "__main__":
    main()
