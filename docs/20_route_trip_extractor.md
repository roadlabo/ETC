# 20_route_trip_extractor
## 目的（何をする）
サンプルルートに近接するトリップ区間を抽出し、第1/第2スクリーニング済みの様式1-2 互換 CSV を生成する。運行 ID や曜日でタグ付けしたファイル名により、後続集計のフィルタを容易にする。
## 位置づけ（分析フロー上のどこ）
- **第1/第2スクリーニング**フェーズ（ルートベース）。
- PDF 用語での「第1スクリーニング（粗判定）」「第2スクリーニング（厳格判定）」を一つのスクリプトで行い、`2nd_*.csv` を出力する。
## 入力
- サンプルルート CSV (`--sample` または DEFAULT_SAMPLE_PATH)。列: 緯度=14, 経度=15 を使用。
- トリップ CSV 群 (`--input-dir` または DEFAULT_INPUT_DIR)。FLAG 列=12, GPS時刻=6, 運行日=2, 運行ID=3, 種別=4, 用途=5, TRIP_NO=8 を前提。
- オプション: RECURSIVE=True でサブフォルダも探索、TARGET_WEEKDAYS で曜日フィルタ。
## 出力
- `--output-dir` 直下に `2nd_{route}_{weekday}_ID{opid12}_{yyyymmdd}_{tXXX}_{E??}_{F??}.csv`
  - weekday 部は抽出区間に含まれる曜日略称をソートして連結（例: MON-TUE）。
  - TRIP_NO, 車種/用途をタグに付与。
  - 行内容は元 CSV の行をそのまま保存（ヘッダなし、UTF-8）。
## 実行方法
- コマンド例: `python 20_route_trip_extractor.py --sample sample_route.csv --input-dir ./opid_split --output-dir ./screening2`
- 閾値はスクリプト冒頭の定数で調整: `THRESH_M`（距離[m]）, `MIN_HITS`（一致点数）, `TARGET_WEEKDAYS`（曜日集合）, `DRY_RUN`（保存しない場合 True）。
- 進捗は標準出力にファイル数・ヒット数を表示。
## 判定ロジック（重要なものだけ）
- FLAG と TRIP_NO の変化から境界を構築し、区間候補を生成（長さ 2 行以上）。
- 各区間について: 曜日フィルタ → ハバーサイン距離でサンプルへの最近傍距離を計算 → `MIN_HITS` 以上なら保存。
- 出力ファイル名に曜日・OPID・TRIP_NO・車種/用途タグを付与し、再現性を担保。
## できること / できないこと（行政向けの注意）
- できること: ルート近接条件に基づくトリップ抽出、曜日別のタグ付け、閾値を明示した第2スクリーニング結果の保存。
- できないこと: 測位誤差の補正や進行方向推定、FLAG/TRIP_NO の欠損補完、経路の断定。抽出されないデータを「不存在」とみなすことは避ける。
## よくあるミス
- DEFAULT パスを未設定のまま実行してエラーになる。
- サンプル CSV の列順が異なり、距離計算が常に失敗する。
- `MIN_HITS` を大きくし過ぎて実質的に抽出ゼロになる。
## 関連スクリプト
- 前段: [docs/01_split_by_opid_streaming.md](./01_split_by_opid_streaming.md)（OPID 分割）。
- 後段: [docs/30_build_performance.md](./30_build_performance.md), [docs/40_trip_od_screening.md](./40_trip_od_screening.md)。
- ビューア: [docs/05_route_mapper_simple.md](./05_route_mapper_simple.md), [docs/06_route_mapper_kp.md](./06_route_mapper_kp.md)。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
