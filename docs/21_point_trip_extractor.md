# 21_point_trip_extractor
## 目的（何をする）
交差点中心（ポイント）に近接するトリップ区間を抽出し、第1/第2スクリーニング済みの様式1-2 互換 CSV を生成する。複数交差点 CSV を一括読み込み、通過判定を距離と線分交差で行う。
## 位置づけ（分析フロー上のどこ）
- **第1/第2スクリーニング**フェーズ（交差点ベース）。
- PDF 用語の「第1/第2スクリーニング（交差点）」に対応し、31/32/71 系の入力となる。
## 入力
- 交差点 CSV（`CROSSROAD_CSV_DIR` 内の *.csv 全件 + `CROSSROAD_CSV_LIST` で追加指定）。列前提: `crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg`（11_crossroad_sampler 出力）。
- トリップ CSV 群 (`--input-dir` または DEFAULT_INPUT_DIR)。列: 経度=14, 緯度=15, FLAG=12, GPS時刻=6, 運行日=2, 運行ID=3, 種別=4, 用途=5, TRIP_NO=8。
- 閾値: `THRESH_M`（中心からの距離[m]）, `MIN_HITS`（点＋線分ヒット数）。
## 出力
- `--output-dir` 配下に `2nd_{crossroad}_{weekday}_ID{opid}_{yyyymmdd}_{tXXX}_{E??}_{F??}.csv`
  - weekday は含まれる曜日略称、TRIP/車種/用途タグ付き。
  - 行は元 CSV の行をそのまま保存（ヘッダなし）。

※作業フォルダ構成は `docs/05_work_folder_structure.md` を正とする。  
本スクリプトの成果物は `{PROJECT_ID}/20_第２スクリーニング/`（該当番号フォルダ）に出力して運用する。
## 実行方法
- コマンド例: `python 21_point_trip_extractor.py --input-dir ./opid_split --output-dir ./screening2_points`
- 交差点 CSV の置き場所: `CROSSROAD_CSV_DIR` を設定するか、`CROSSROAD_CSV_LIST` に個別パスを指定。
- `RECURSIVE=True` でサブフォルダ探索、`TARGET_WEEKDAYS` で曜日フィルタ。
## 判定ロジック（重要なものだけ）
- FLAG と TRIP_NO の境界から区間候補を生成。
- 各区間内の全点について、中心との距離と線分距離を計算し、`MIN_HITS` 以上でヒット。
- 交差点 CSV は複数同時ロードし、`name` 単位で処理。距離計算はハバーサイン近似。
## できること / できないこと（行政向けの注意）
- できること: 交差点近傍通過の抽出、曜日タグ付きの第2スクリーニング結果保存、複数交差点のバッチ処理。
- できないこと: 信号現示や右左折の推定、速度プロファイルの補正、中心点の自動生成。抽出されないことをもって「通過なし」と断定しない。
## よくあるミス
- CROSSROAD_CSV_DIR が空で、ヒットが 0 件のまま進む。
- 閾値 `THRESH_M` を小さくし過ぎて実走行のずれを拾えない。
- 交差点 CSV の列順を変更し、`dir_deg` 読み込みに失敗。
## 関連スクリプト
- 前段: [docs/11_crossroad_sampler.md](./11_crossroad_sampler.md)（交差点定義）。
- 後段: [docs/31_crossroad_trip_performance.md](./31_crossroad_trip_performance.md), [docs/32_crossroad_viewer.md](./32_crossroad_viewer.md), [docs/50_Path_Analysis.md](./50_Path_Analysis.md)。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
