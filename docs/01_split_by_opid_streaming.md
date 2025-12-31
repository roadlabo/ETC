# 01_split_by_opid_streaming
## 目的（何をする）
ZIP に格納された ETC2.0 生データをストリーミングで読み出し、運行 ID（OPID）ごとに CSV を分割する。必要に応じて時系列ソートまで自動化し、後続の第1/第2スクリーニングが扱いやすい粒度に整える。
## 位置づけ（分析フロー上のどこ）
- **前処理**: 第1スクリーニング前のデータ整形。
- **PDF 用語**: 「対象エリアを含む2次メッシュのデータを全検索」→「運行ID毎に集計→時系列ソート」の担当。
## 入力
- INPUT_DIR に置かれた ZIP 群（ファイル名に `ZIP_DIGIT_KEYS` のいずれかを含むものだけを対象）。
- 各 ZIP に含まれる `INNER_CSV`（既定: `data.csv`）。
- 列前提（0 始まり）: D列=OPID(3), G列=GPS時刻(6)。他列は無加工で透過。
## 出力
- `OUTPUT_DIR/{TERM_NAME}_{opid}.csv` : OPID ごとの分割結果。書き込み時は追記モード。
- `DO_FINAL_SORT=True` の場合、`SORT_GLOB` に一致するファイルをチャンク分割→マージし、時刻列（`TIMESTAMP_COL`）でソート済みに置換。作業ディレクトリ `_sort_tmp` を自動生成/掃除。
## 実行方法
- スクリプト先頭の設定（入力/出力パス、ZIP_DIGIT_KEYS、INNER_CSV、エンコーディング、ソート有無）を編集。
- コマンド例: `python 01_split_by_opid_streaming.py`
- 進捗は標準出力にパーセント表示。
## 判定ロジック（重要なものだけ）
- ZIP 内 CSV をストリーミング読み込みし、行ごとの OPID を確認してファイルを切替。
- 時刻ソートはチャンクサイズ `CHUNK_ROWS` ごとに外部ソートし、ヒープでマージ（メモリ節約型）。
- 不正行（列不足・デコード失敗）はスキップし、処理継続を優先。
## できること / できないこと（行政向けの注意）
- できること: 大容量 ZIP を展開せずに分割、運行単位の欠損なく保存、時系列整列済み CSV を後段へ渡す。
- できないこと: 測位誤差や欠損値の補正、OPID の欠落推定、ZIP 名の規則性チェック。ZIP 内に `INNER_CSV` が無い場合は自動で探しに行かない。
## よくあるミス
- `ZIP_DIGIT_KEYS` が一致せずファイルが無視される。
- `TIMESTAMP_COL` の列番号を誤り、ソートが無効になる。
- `OUTPUT_DIR` の空き容量不足（OPID 数だけファイルが増える）。
## 関連スクリプト
- 前段: なし（生データ起点）。
- 後段: [docs/20_route_trip_extractor.md](./20_route_trip_extractor.md), [docs/21_point_trip_extractor.md](./21_point_trip_extractor.md)（第1/第2スクリーニングの入力）。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
