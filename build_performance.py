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
#   経度O=14／緯度P=15 から⊿L[m]を積算してKP[km]を自動算出
ROUTE_LON_COL = 14
ROUTE_LAT_COL = 15

# 解析対象CSV（trip_extractor出力）の列index
COL_TIME  = 6   # G
COL_LON   = 14  # O
COL_LAT   = 15  # P
COL_SPEED = 18  # S

# 出力の丸め（平均速度の小数桁）
ROUND_DIGITS = 1
KP_DECIMALS  = 2   # キロポストの表示小数桁（例：0.00）

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

def build_route_kp(route_path):
    """
    ルートCSVを上から順に辿り、1行目を 0.00 km として
    O列(14)=lon, P列(15)=lat から⊿L[m]をハバーサインで積算し、
    各行のキロポスト[km]配列を作る。
    Returns:
      kp_km   : List[float]  各ルート行のキロポスト[km]
      lat_rad : List[float]  各ルート行の緯度(rad)
      lon_rad : List[float]  各ルート行の経度(rad)
    """
    lats, lons = [], []
    with open(route_path, "r", newline="", encoding="cp932") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            try:
                lon = float(row[ROUTE_LON_COL])
                lat = float(row[ROUTE_LAT_COL])
            except Exception:
                continue
            lons.append(lon)
            lats.append(lat)
    if not lats:
        raise RuntimeError("ルートCSVから座標を読み取れませんでした。列index設定を確認してください。")

    lat_r = [deg2rad(x) for x in lats]
    lon_r = [deg2rad(x) for x in lons]
    kp_km = [0.0]
    for i in range(1, len(lat_r)):
        d = haversine_m(lat_r[i-1], lon_r[i-1], lat_r[i], lon_r[i])  # meters
        kp_km.append(kp_km[-1] + d / 1000.0)  # km 累積
    return kp_km, lat_r, lon_r

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

def iter_csv_rows_with_guess(path, encodings=("cp932", "utf-8-sig", "utf-8")):
    """
    指定ファイルを複数エンコーディングで試行して csv.reader を生成。
    最初に成功したエンコーディングで全行を yield。
    """
    last_err = None
    for enc in encodings:
        try:
            with open(path, "r", newline="", encoding=enc) as f:
                for row in csv.reader(f):
                    yield row
            return
        except Exception as e:
            last_err = e
            continue
    # どれもだめなら最終エラーを投げる
    raise last_err if last_err else RuntimeError(f"Failed to read: {path}")


def write_header(writer):
    # 見本と同じヘッダー3行（50列固定）※時間帯は'を付けてExcelの日付化を防止
    writer.writerow(["自動運転バス運行ルート　パフォーマンス調査"] + [""]*49)
    writer.writerow(["キロ"] + ["速度"]*24 + ["キロ"] + ["台数"]*24)
    time_labels = [f"'{h}-{h+1}" for h in range(24)]
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
    ap = argparse.ArgumentParser(description="paformance001.csv を見本通りに生成（KPはルート座標から自動算出）")
    ap.add_argument("--recursive", action="store_true", help="サブフォルダも探索（既定はRECURSIVEに従う）")
    args = ap.parse_args()

    global RECURSIVE
    if args.recursive:
        RECURSIVE = True

    # ルート読込 → KP[km]自動算出
    kp_km, route_lat_r, route_lon_r = build_route_kp(ROUTE_PATH)
    kp_count = len(kp_km)
    print(f"[INFO] ルート点数(=行数): {kp_count}")

    # 集計器：各ルート行index × 24時間 → [sum_speed, count]
    stats = [[[0.0, 0] for _ in range(24)] for _ in range(kp_count)]

    files = list_input_csvs(INPUT_DIR, RECURSIVE)
    total_files = len(files)
    print(f"[INFO] 検索対象CSV数: {total_files} @ {INPUT_DIR}")

    processing_start = datetime.now()
    print(f"[TIME] Start: {format_datetime(processing_start)}")

    processed_files = 0
    had_warning = False
    if total_files == 0:
        print_progress(total_files, total_files)

    total_rows = 0
    parsed_rows = 0
    matched_rows = 0

    for path in files:
        try:
            for row in iter_csv_rows_with_guess(path):
                if not row:
                    continue
                total_rows += 1
                try:
                    lon = float(row[COL_LON])
                    lat = float(row[COL_LAT])
                    hour = parse_hour(row[COL_TIME])
                    if hour is None or not (0 <= hour <= 23):
                        continue
                    spd = float(row[COL_SPEED])
                    if spd < 0 or spd > 300:  # 常識的範囲
                        continue
                    parsed_rows += 1
                except Exception:
                    continue

                d, idx = nearest_route_index(lat, lon, route_lat_r, route_lon_r)
                if idx < 0 or d > RADIUS_M:
                    continue
                s, c = stats[idx][hour]
                stats[idx][hour] = [s + spd, c + 1]
                matched_rows += 1
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
        for i in range(kp_count):
            km_str = f"{kp_km[i]:.{KP_DECIMALS}f}"
            avg24, cnt24 = [], []
            for h in range(24):
                s, c = stats[i][h]
                if c > 0:
                    avg24.append(f"{round(s / c, ROUND_DIGITS)}")
                    cnt24.append(str(c))
                else:
                    avg24.append("")
                    cnt24.append("")
            row = [km_str] + avg24 + [km_str] + cnt24
            writer.writerow(row)

    print(f"[DONE] 出力: {OUTPUT_PATH}")
    print(f"[STATS] total_rows={total_rows}, parsed_rows={parsed_rows}, matched_rows={matched_rows}, radius_m={RADIUS_M}")

    processing_end = datetime.now()
    print(f"[TIME] End: {format_datetime(processing_end)}")
    print(f"[TIME] Duration: {format_timedelta(processing_end - processing_start)}")

    if not had_warning:
        print("Congratulations, everything completed successfully.")

if __name__ == "__main__":
    main()
