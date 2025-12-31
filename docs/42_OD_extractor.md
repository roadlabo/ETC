# 42_OD_extractor
## 目的（何をする）
`40_trip_od_screening.py` が生成した様式1-3参照ODリストを入力に、ゾーン割当・OD マトリクス・発生集中量を一括で算出する。ポリゴン（ゾーン）と東西南北の簡易ゾーンを組み合わせ、行政説明用の集計を生成する。
## 位置づけ（分析フロー上のどこ）
- **OD 集計**フェーズ。
- PDF 用語の「様式1-3参照ODリスト作成 → ODマトリクス → ODヒートマップ」のうち、マトリクスと発生集中量を担当。
## 入力
- `OD_LIST_FILES`: 1 行=1 トリップの OD リスト CSV（複数指定可）。`operation_date, weekday, opid, trip_no, o_lon, o_lat, d_lon, d_lat, status ...` を想定。
- `ZONES_CSV_PATH`: 12_polygon_builder で作成したゾーン定義 CSV。ゾーン名 + lon/lat ペア。
- `TARGET_WEEKDAYS`: 追加の曜日フィルタ（二重チェック用）。
- 津山市中心点 (`TSUYAMA_CENTER_LON/LAT`) は東西南北ゾーン判定で使用。
## 出力
- `OUTPUT_DIR` に 3 ファイルを生成（UTF-8/BOM）。
  - `zone_master.csv`: `zone_id,zone_name` の対応表。
  - `od_matrix.csv`: 行=O ゾーン, 列=D ゾーンのマトリクス。
  - `zone_production_attraction.csv`: ゾーン別発生量・集中量。
- ログとして総行数、フィルタ件数、status 別件数を出力。
## 実行方法
- スクリプト冒頭で `OUTPUT_DIR`, `OD_LIST_FILES`, `ZONES_CSV_PATH`, `TARGET_WEEKDAYS` を設定。
- コマンド例: `python 42_OD_extractor.py`
- 標準出力に進捗と生成ファイルパスを表示。
## 判定ロジック（重要なものだけ）
- OD リストを走査し、`status` が欠損/未マッチでない行のみをカウント（曜日フィルタも適用）。
- `assign_zone` でポリゴン判定し、該当なしは `MISSING` として計上。東西南北判定も実装済み。
- ゾーン集合から ID を付与し、辞書型マトリクスを構築して CSV へ展開。
## できること / できないこと（行政向けの注意）
- できること: 複数 OD リストの統合、ゾーン別発生/集中量算出、ゾーン定義に基づく OD マトリクスの自動生成。
- できないこと: OD リストに無いトリップの推定、ゾーン定義の妥当性チェック、時間帯別集計。`status` が `missing_*` の行は除外されるため、欠測を「0」と見なすのは誤り。
## よくあるミス
- ZONES_CSV_PATH を未設定のまま実行し、全件 `MISSING` になる。
- weekday フィルタを両方（40/42）で設定し、想定より件数が減る。
- ポリゴンの順序や閉合が不正で、ゾーン判定が期待通りにならない。
## 関連スクリプト
- 前段: [docs/40_trip_od_screening.md](./40_trip_od_screening.md), [docs/12_polygon_builder.md](./12_polygon_builder.md)。
- 後段: ヒートマップ確認は [docs/41_od_heatmap_viewer.md](./41_od_heatmap_viewer.md)。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
