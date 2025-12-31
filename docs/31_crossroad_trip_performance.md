# 31_crossroad_trip_performance
## 目的（何をする）
第2スクリーニング済みの様式1-2 CSV と交差点定義を入力し、交差点通過性能（流入方向・流出方向・通過速度・滞留時間など）を算出する。交差点ごとに性能 CSV をまとめて出力する。
## 位置づけ（分析フロー上のどこ）
- **性能評価**フェーズ（交差点）。
- PDF 用語の「交差点性能算出」を担当し、32 のビューアで確認する出力を生成。
- 旧 70 系のロジックを 31 系として整理した正式版。
## 入力
- CONFIG 配列で指定するセットごとに処理。
  - `trip_folder`: 第2スクリーニング済み様式1-2 CSV を含むフォルダ。
  - `crossroad_file`: 交差点定義 CSV（11_crossroad_sampler 出力）。
- 列前提（入力 CSV, 0 始まり）: 運行日=2, 運行ID=3, 種別=4, 用途=5, GPS時刻=6, TRIP_NO=8, 経度=14, 緯度=15。
- `TARGET_WEEKDAYS` で曜日フィルタを適用可能（例: ["TUE","WED","THU"]）。
## 出力
- `OUTPUT_BASE_DIR/{crossroad_name}_performance.csv`（cp932）。
- ヘッダ例: `run_id,trip_no,dir_in,dir_out,dist_in_m,dist_out_m,dwell_time_sec,avg_speed_kmh,` に加え、中心前後 5 点の座標・時刻や枝番号、曜日など詳細列を保持。
## 実行方法
- スクリプト冒頭で `OUTPUT_BASE_DIR`, `CONFIG`, `TARGET_WEEKDAYS` を設定。
- コマンド例: `python 31_crossroad_trip_performance.py`
- セットごとに進捗を標準出力へ表示。入力フォルダに CSV が無ければスキップ。
## 判定ロジック（重要なものだけ）
- TRIP_NO/FLAG に基づきトリップ単位で処理し、交差点中心に最も近い点を通過点として抽出。
- 直前・直後の方位を計算し、交差点定義の `dir_deg` に最も近い枝を流入/流出としてマッチング。距離閾値が超過する場合は該当枝なしとして扱う。
- 中心前後 5 点の座標・時刻を保持し、停留時間や速度を算出。曜日フィルタは TRIP_DATE から計算。
## できること / できないこと（行政向けの注意）
- できること: 交差点ごとの流入/流出方向別性能集計、曜日別のフィルタ、中心周辺の生データを付与した CSV 出力。
- できないこと: 信号現示や交差点形状の自動取得、右左折の確定判定、長時間滞留の原因特定。距離・方位閾値を超える場合は「未マッチ」となるため、解釈は慎重に行う。
## よくあるミス
- CONFIG の `trip_folder` と `crossroad_file` の組み合わせを誤り、座標系が一致せず全件ミスマッチ。
- 入力 CSV の文字コード/改行が異なり、読み込みで警告が出る。
- `TARGET_WEEKDAYS` を設定したまま全曜日を想定して結果件数が減少する。
## 関連スクリプト
- 前段: [docs/21_point_trip_extractor.md](./21_point_trip_extractor.md)（第2スクリーニング）。
- 後段: [docs/32_crossroad_viewer.md](./32_crossroad_viewer.md)（性能 CSV の可視化）。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
