import os, csv, glob, math, argparse
from datetime import datetime
import random
import re
from typing import Optional
from statistics import median
from pathlib import Path

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

def parse_hour(s: str) -> Optional[int]:
    """
    文字列から“時(0–23)”のみ抽出（年月日無視）。
    対応例:
      2025-01-02 9:03, 2025/01/02T09:03:00.123+09:00, 09:03, 9:03:5, 12時34分 など
    """
    if not s:
        return None
    t = s.strip()
    # 1) 標準: H:MM[:SS[.mmm]][+TZ]
    m = re.search(r'(\d{1,2}):\d{1,2}(?::\d{1,2}(?:\.\d+)?)?', t)
    if m:
        try:
            hh = int(m.group(1))
            return hh if 0 <= hh <= 23 else None
        except Exception:
            return None

    # 2) 和式など "HH時MM分"
    m = re.search(r'(\d{1,2})\s*[時Hh]\s*\d{1,2}', t)
    if m:
        try:
            hh = int(m.group(1))
            return hh if 0 <= hh <= 23 else None
        except Exception:
            return None

    # 3) "HH-MM" のようなハイフン区切り（時刻っぽいもの）
    m = re.search(r'(\d{1,2})-\d{1,2}', t)
    if m:
        try:
            hh = int(m.group(1))
            return hh if 0 <= hh <= 23 else None
        except Exception:
            return None

    # 3-a) 区切り無しの HHMM / HMM （例: '0930', '930'）
    m = re.search(r'\b(\d{3,4})\b', t)  # 3～4桁の連続数字を拾う
    if m:
        num = m.group(1)
        try:
            # 先頭1～2桁を時とみなす（'930'→'9','0930'→'09'）
            hh = int(num[:-2]) if len(num) == 4 else int(num[0])
            if 0 <= hh <= 23:
                return hh
        except Exception:
            return None

    # 3-b) 区切りなしの 14桁: YYYYMMDDHHMMSS（例: 20250224161105）
    m = re.fullmatch(r'(\d{14})', t)
    if m:
        num = m.group(1)
        try:
            hh = int(num[8:10])  # 9～10文字目が「時」
            if 0 <= hh <= 23:
                return hh
        except Exception:
            pass

    return None


def parse_speed(val: str) -> Optional[float]:
    """
    速度セルから最初の数値（float）を抽出。
    許容: カンマ, 前後空白, 単位文字, 全角スペース, 先頭ダッシュ等
    例: "1,234.5", " 67km/h", "—" → None
    """
    if val is None:
        return None
    t = str(val).replace(",", "").replace("\u3000", " ").strip()
    m = re.search(r'[-+]?\d+(?:\.\d+)?', t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
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
    reader = None
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            with open(route_path, "r", newline="", encoding=enc) as f:
                reader = list(csv.reader(f))
            break
        except Exception:
            reader = None
            continue
    if reader is None:
        raise RuntimeError("ルートCSVを読み取れません（文字コード不一致）。")
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
    min_lat = min(lats)
    max_lat = max(lats)
    min_lon = min(lons)
    max_lon = max(lons)
    print(f"[ROUTE] points={len(kp_km)}, length_km={kp_km[-1]:.3f}, "
          f"lat=[{min_lat:.5f},{max_lat:.5f}] lon=[{min_lon:.5f},{max_lon:.5f}]")
    return kp_km, lat_r, lon_r


def nearest_distance_to_polyline_m(lat_deg, lon_deg, route_lat_r, route_lon_r):
    lr = deg2rad(lat_deg)
    lo = deg2rad(lon_deg)
    min_d, min_i = float("inf"), -1
    for i in range(len(route_lat_r)):
        d = haversine_m(lr, lo, route_lat_r[i], route_lon_r[i])
        if d < min_d:
            min_d, min_i = d, i
    return min_d, min_i

def nearest_route_index(lat_deg, lon_deg, route_lat_r, route_lon_r):
    """観測点→ルート最近傍インデックス（総当たり）"""
    return nearest_distance_to_polyline_m(lat_deg, lon_deg, route_lat_r, route_lon_r)

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
    fail_time = 0
    fail_speed = 0
    fail_coord = 0
    geo_match_rows = 0
    diag_samples = []

    for path in files:
        try:
            rows = list(iter_csv_rows_with_guess(path))
            if len(diag_samples) < 2000:
                for r in rows[:200]:
                    diag_samples.append(r)
                    if len(diag_samples) >= 2000:
                        break
            for row in rows:
                if not row:
                    continue
                total_rows += 1
                try:
                    lon = float(row[COL_LON])
                    lat = float(row[COL_LAT])
                except Exception:
                    fail_coord += 1
                    continue

                d, idx = nearest_route_index(lat, lon, route_lat_r, route_lon_r)
                if idx >= 0 and d <= RADIUS_M:
                    geo_match_rows += 1
                else:
                    continue

                hour = parse_hour(row[COL_TIME])
                if hour is None or not (0 <= hour <= 23):
                    fail_time += 1
                    continue

                spd = parse_speed(row[COL_SPEED])
                if spd is None or spd < 0 or spd > 300:
                    fail_speed += 1
                    continue

                parsed_rows += 1

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
    print(f"[STATS] files={len(files)}, total_rows={total_rows}, parsed_rows={parsed_rows}, "
          f"matched_rows={matched_rows}, geo_only_matches={geo_match_rows}, "
          f"fail_time={fail_time}, fail_speed={fail_speed}, fail_coord={fail_coord}, "
          f"radius_m={RADIUS_M}")
    if parsed_rows == 0:
        print("[HINT] parsed_rows=0 → 時刻 or 速度の抽出が全滅の可能性。fail_time / fail_speed の内訳を確認。")
    if matched_rows == 0 and parsed_rows > 0:
        print("[HINT] matched_rows=0 → ルートと観測の距離が常に閾値超過の可能性。RADIUS_Mを一時的に50–100mで試し、"
              "lon/lat入替え（自動検知済）やルート座標の誤差を確認してください。")
    if diag_samples:
        d_list = []
        within20 = within50 = within100 = 0
        for r in random.sample(diag_samples, min(len(diag_samples), 1000)):
            try:
                lon = float(r[COL_LON])
                lat = float(r[COL_LAT])
            except Exception:
                continue
            d, _ = nearest_distance_to_polyline_m(lat, lon, route_lat_r, route_lon_r)
            d_list.append(d)
            if d <= 20:
                within20 += 1
            if d <= 50:
                within50 += 1
            if d <= 100:
                within100 += 1
        if d_list:
            print(f"[DIAG] sample_n={len(d_list)}, d_median={median(d_list):.1f}m, "
                  f"≤20m={within20}, ≤50m={within50}, ≤100m={within100}")
        else:
            print("[DIAG] 距離診断サンプルが数値化できませんでした。列の位置や値を確認してください。")

    # --- デバッグ一致CSV（先頭200件だけ） ---
    try:
        dbg_path = str(Path(OUTPUT_PATH).with_name("debug_matches.csv"))
        out_dbg = []
        count_dbg = 0
        for i, km in enumerate(kp_km):
            for h in range(24):
                s, c = stats[i][h]
                if c > 0:
                    avg = s / c
                    out_dbg.append([f"{km:.{KP_DECIMALS}f}", h, round(avg, ROUND_DIGITS), c])
                    count_dbg += 1
                    if count_dbg >= 200:
                        break
            if count_dbg >= 200:
                break
        if out_dbg:
            with open(dbg_path, "w", newline="", encoding="cp932") as df:
                w = csv.writer(df, lineterminator="\r\n")
                w.writerow(["KP[km]", "hour", "avg_speed", "count"])
                w.writerows(out_dbg)
            print(f"[DEBUG] 一致サンプルを {dbg_path} に出力しました（最大200行）。")
        else:
            print("[DEBUG] 一致サンプルは0件でした。")
    except Exception as e:
        print(f"[DEBUG] debug_matches.csv 出力に失敗: {e}")

    processing_end = datetime.now()
    print(f"[TIME] End: {format_datetime(processing_end)}")
    print(f"[TIME] Duration: {format_timedelta(processing_end - processing_start)}")

    if not had_warning:
        print("Congratulations, everything completed successfully.")

if __name__ == "__main__":
    main()
