# 40_trip_od_screening
## 目的（何をする）
第1/第2スクリーニング済みのトリップ CSV から (運行日, 運行ID, トリップ番号) を抽出し、様式1-3 ZIP（`data.csv`）に含まれる起終点座標を引き当てる。「様式1-3参照ODリスト」を生成し、OD 集計前の橋渡しを行う。
## 位置づけ（分析フロー上のどこ）
- **第2スクリーニング後の OD 前処理**。
- PDF 用語での「様式1-3参照ODリスト作成」に相当し、41/42 で可視化・マトリクス化するための入力を作る。
## 入力
- `DATASETS` 配列で複数データセットを定義。
  - `input_dir`: 第1/第2どちらの CSV でも可（行単位でキーを抽出）。
  - `style13_dir`: 様式1-3 ZIP が並ぶフォルダ（ZIP 内 `data.csv` を想定）。
  - `output_od_list_name`: 出力ファイル名（省略時は自動命名）。
- エンコーディング候補は `FILE_ENCODINGS`, `ZIP_ENCODINGS` で順に試行。
- 任意で `TARGET_WEEKDAYS` に曜日集合を指定し、OD 抽出時にフィルタ。
## 出力
- `OUTPUT_DIR/{output_od_list_name}`（UTF-8）。ヘッダ:
  - `dataset, operation_date, weekday, opid, trip_no, o_lon, o_lat, d_lon, d_lat, status, src_files_count`
- status にはヒット状況を記録（例: `matched`, `missing_zip`, `missing_trip`）。
## 実行方法
- スクリプト冒頭の `OUTPUT_DIR`, `TARGET_WEEKDAYS`, `DATASETS` を設定。
- コマンド例: `python 40_trip_od_screening.py`
- 進捗はタイムスタンプ付きで標準出力にログ。
## 判定ロジック（重要なものだけ）
- 入力 CSV をストリーミングし、(operation_date, opid, trip_no) キー集合を構築。
- 様式1-3 ZIP を ZIP 名中の日付でマッチングし、内部 `data.csv` のキーと突き合わせて起終点を引き当て。
- 曜日判定は `operation_date` から算出し、フィルタ指定があればスキップ。
## できること / できないこと（行政向けの注意）
- できること: 第1/第2のどちらの CSV からでも OD キーを抽出、様式1-3 へのリンク切れをステータスで可視化、複数データセットの一括処理。
- できないこと: 様式1-3 ZIP の欠損補完、起終点の再推定、データ遅延や重複の自動解決。`status` が `missing_*` の行は「データ未取得」であり「移動なし」を意味しない。
## よくあるミス
- `style13_dir` の日付と `operation_date` が揃わず、全件 `missing_zip` になる。
- ZIP 内ファイル名が `data.csv` 以外で読み込めず、マッチゼロになる。
- 曜日フィルタを設定したまま全件出力を期待して件数が減る。
## 関連スクリプト
- 前段: [docs/20_route_trip_extractor.md](./20_route_trip_extractor.md) / [docs/21_point_trip_extractor.md](./21_point_trip_extractor.md)。
- 後段: [docs/41_od_heatmap_viewer.md](./41_od_heatmap_viewer.md), [docs/42_OD_extractor.md](./42_OD_extractor.md)。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
