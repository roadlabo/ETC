# 70_crossroad_trip_performance.py マニュアル

## 概要
本スクリプトは、ETC2.0 様式1-2データから交差点の性能（流入・流出方向、所要時間、速度など）を抽出する正式版です。旧来の `31_crossroad_dircount.py` を完全に置き換え、通過判定ロジックは `16_trip_extractor_point.py` と完全一致しています。

**主な特徴**
- 点距離＋線分距離による厳密な通過判定（20 m 判定、MIN_HITS=1）。
- 中心点＝交差点に最も近い座標を採用。
- 前後 5 点の空間・時間情報を詳細に出力。
- 日本語の性能表（performance.csv）を cp932 で保存し、Excel で文字化けなし。
- 曜日フィルタに対応（例：火・水・木のみ）。

## 1. 入力ファイル
### (1) 第2スクリーニング済み様式1-2
- TRIP_ID
- TRIP_NO
- TRIP_DATE
- O：経度
- P：緯度
- G：GPS 時刻（YYYYMMDDhhmmss）
- VEHICLE_TYPE
- VEHICLE_USE
- ...

### (2) 交差点 CSV（11 / 16 系と同一形式）
例：
```
branch_no,dir_deg,...
1,90,東方向
2,270,西方向
3,0,北方向
```
`dir_deg` が方向判定の基準になります。

## 2. スクリプト冒頭の設定
```python
OUTPUT_BASE_DIR = r"C:\path\to\output"

CONFIG = [
    {
        "trip_folder": r"C:\path\to\screening2_folder1",
        "crossroad_file": r"C:\path\to\crossroad1.csv",
    },
]

# 火・水・木のみ処理する場合
TARGET_WEEKDAYS = ["TUE", "WED", "THU"]
# 全曜日対象なら []
```

## 3. 出力ファイル
`<交差点ファイル名>_performance.csv`

例：`09ryutsucentre_performance.csv`

## 4. 出力カラム
### 基本性能項目
- 交差点ファイル名
- 交差点 ID
- 抽出 CSV ファイル名
- 運行日
- 曜日
- 運行 ID
- トリップ ID
- 車種
- 用途
- 流入枝番 / 流出枝番
- 道なり距離
- 所要時間
- 通過速度

### 詳細ポイント（前後 5 点）
以下を順番に横へ追加します（範囲外は空欄）：
- 5P前_経度, 緯度, G
- 4P前_経度, 緯度, G
- 3P前_経度, 緯度, G
- 2P前_経度, 緯度, G
- 1P前_経度, 緯度, G
- 中心点_経度, 緯度, G
- 1P後_経度, 緯度, G
- 2P後_経度, 緯度, G
- 3P後_経度, 緯度, G
- 4P後_経度, 緯度, G
- 5P後_経度, 緯度, G

## 5. 通過判定アルゴリズム（16 / 31 と同一）
- 点距離 20 m 判定
- 線分距離 20 m 判定
- MIN_HITS=1
- 中心点は最小距離点

## 6. 方向（流入 / 流出）判定
- 中心点の 1 つ前の点で流入方向の bearing を計算。
- 中心点の 1 つ後の点で流出方向の bearing を計算。
- 各 bearing を交差点 CSV の `dir_deg` と照合し、角度差が最小の `branch_no` を採用。

## 7. 保存形式
- Shift-JIS (cp932) で保存。
- Excel で文字化けせず、日本語カラム名のまま開けます。

## 8. 実行例
```
python 70_crossroad_trip_performance.py
```
