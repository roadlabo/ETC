# ETC2.0 プローブデータ分析ツール（津山市オリジナルアプリ群）

本リポジトリは、津山市が独自開発した ETC2.0 プローブデータの分析手順とスクリプトをまとめたものです。対象は 12 府県・11 か月・約 5 TB の大規模データ（様式1-2 / 様式1-3）で、職員がスタンドアロン環境で完結できるように設計されています。

最終更新：2025

---

## 1. プロジェクト概要
- 大規模な ETC2.0 プローブデータを用いた自動分析パイプライン。
- 第1スクリーニングから OD ヒートマップまで、全工程を Python スクリプトで実行。
- 出力 CSV は合計 1,504,700 件を生成（第1スクリーニング）。

## 2. 使用するデータ（様式1-2 / 様式1-3）
### 様式1-2（位置情報データ）
- 保存場所：OUT1-2。
- 主な列例：
  - C: 運行日（YYYYMMDD）
  - D: 運行ID
  - I: トリップ番号
  - O: 経度 / P: 緯度
  - S: 速度
  - Y: 2 次メッシュコード
- 第1・第2スクリーニングの基本データとして使用。

### 様式1-3（起終点情報データ）
- 保存場所：OUT1-3。
- 主な列例：
  - A: 運行日
  - B: 運行ID（連番化）
  - C: トリップ数
  - H: トリップ番号
  - L: 起点経度 / M: 起点緯度
  - N: 終点経度 / O: 終点緯度
- OD ヒートマップ作成に利用。

## 3. 分析フロー（全体図）
1. `01_split_by_opid_streaming.py`
   - 第1スクリーニング（運行 ID 単位の CSV 150 万件）
2. `10_route_sampler.py` / `11_crossroad_sampler.py`
   - ルート・ポイント（交差点）定義の作成
3. `15_trip_extractor_route.py` / `16_trip_extractor_point.py`
   - 第2スクリーニング（1 トリップ → 1 CSV）
4. `30_build_performance.py`
   - 20 m ピッチ × 1 時間の性能分析（速度・件数）
5. `31_crossroad_dircount.py`
   - 交差点方向別トリップ数
6. `51_od_heatmap_viewer.py` / `16_trip_od_screening.py`
   - 起終点（OD）ヒートマップ

## 4. 第1スクリーニングの詳細
- スクリプト：`01_split_by_opid_streaming.py`
- 受領 ZIP（様式1-2）から対象 2 次メッシュのデータのみ抽出し、運行 ID ごとに 1 つの CSV を生成。
- データを時系列順に並べ替え、全期間で **1,504,700 ファイル** を出力。
- フォルダ例と件数：
  - `1-R6_6_out(1st)`：131,907 files
  - `2-R6_12-R7_1_out(1st)`：195,840 files
  - `3-R6_3-4_out(1st)`：266,397 files
  - `...`
  - `9-R7_2_out(1st)`：73,285 files

## 5. 第2スクリーニング（ルート／ポイント）の詳細
- 入力：第1スクリーニング出力（1 ファイルに複数トリップを含む）。
- 1 トリップ = 1 CSV に分割し直し、ルート／ポイント通過を判定。

### ルート通過トリップ抽出
- スクリプト：`15_trip_extractor_route.py`
- ルート定義：`10_route_sampler.py` で作成。
- 判定方式：ルートラインからの距離 20 m 以内を通過とみなす。

### ポイント通過トリップ抽出
- スクリプト：`16_trip_extractor_point.py`
- 交差点座標：`11_crossroad_sampler.py` で作成。
- 判定方式：中心点 ±20 m 以内を通過したトリップを HIT と判定（線分判定は最小限で高速化）。

## 6. 出力ファイル名の命名規則
`2nd_{ルート名 or ポイント名}_{曜日名}_{ID名}_{走行日}_{トリップ名}_{自動車の種別}_{自動車の用途}.csv`

例：`2nd_route01_MON_ID000000123456_20250203_t001_E01_F01.csv`

## 7. 各スクリプトの役割一覧
- `01_split_by_opid_streaming.py`：第1スクリーニング（OPID 単位の CSV 生成）。
- `10_route_sampler.py`：調査ルート定義の作成。
- `11_crossroad_sampler.py`：交差点（ポイント）定義の作成。
- `15_trip_extractor_route.py`：ルート通過トリップ抽出（第2スクリーニング）。
- `16_trip_extractor_point.py`：ポイント通過トリップ抽出（第2スクリーニング）。
- `16_trip_od_screening.py`：OD ヒートマップ向けの第2スクリーニング補助。
- `30_build_performance.py`：20 m × 1 h の性能分析（速度・件数）。
- `31_crossroad_dircount.py`：交差点方向別カウント集計（最大 21 行形式）。
- `51_od_heatmap_viewer.py`：OD ヒートマップ表示。

## 8. 性能分析（20 m × 1 h）
- スクリプト：`30_build_performance.py`
- 指定ルートを 20 m ごとに分割し、1 時間単位の平均速度・データ数を算出。
- Excel 形式で出力し、渋滞箇所の把握や比較評価に利用。

## 9. 交差点方向別カウント
- スクリプト：`31_crossroad_dircount.py`
- 第2スクリーニング結果（ポイント版）を入力し、交差点の方向別トリップ数を集計。
- AI カウントとの比較検証にも使用可能。

## 10. OD ヒートマップ（9 パターン）
- スクリプト：`51_od_heatmap_viewer.py` / `16_trip_od_screening.py`
- 様式1-3 と第2スクリーニング結果を統合し、起終点のヒートマップを作成。
- 季節別 9 パターンに対応：
  1. 梅雨（6 月）
  2. 帰省ラッシュ（12〜1 月）
  3. さくらまつり（3〜4 月）
  4. GW（5 月）
  5. 夏（8 月）
  6. 津山まつり（10 月）
  7. 秋（平常）
  8. 秋（森の芸術祭）
  9. 冬（平常）

## 11. 推奨フォルダ構成
```
ETC2.0/
├── 01_received/          # 受領 ZIP（様式1-2,1-3）
├── 02_1st_screening/     # OPID ごとの CSV（150 万件）
├── 03_routes/            # ルート定義
├── 04_crossroads/        # 交差点定義
├── 05_2nd_route/         # 第2スクリーニング（ルート）
├── 06_2nd_point/         # 第2スクリーニング（ポイント）
├── 07_performance/       # 20 m 区間分析
├── 08_dircount/          # 交差点方向別カウント
├── 09_od_heatmap/        # OD ヒートマップ
└── src/                  # Python コード
```

## 12. Quick Start（実行手順）
1. `python 01_split_by_opid_streaming.py`
2. `python 10_route_sampler.py`  # 調査ルート作成
3. `python 11_crossroad_sampler.py`  # 調査ポイント作成
4. `python 15_trip_extractor_route.py`
5. `python 16_trip_extractor_point.py`
6. `python 30_build_performance.py`
7. `python 31_crossroad_dircount.py`
8. `python 51_od_heatmap_viewer.py`

---

## 付録：設計思想
- すべての処理をオフライン・完全自動化し、スタンドアロン PC で厳重管理。
- Python スクリプトは津山市オリジナルアプリとして統一。
- フォルダ構造と命名規則を厳格に揃え、誰が見ても理解できるよう設計。
