# 41_od_heatmap_viewer
## 目的（何をする）
様式1-3参照ODリスト（40_trip_od_screening の出力）を読み込み、Origin と Destination のヒートマップを生成する。OD の空間的偏りを視覚的に示すビューア。
## 位置づけ（分析フロー上のどこ）
- **ビューア**フェーズ（OD）。
- PDF 用語の「OD ヒートマップ」に対応し、42 でゾーン集計する前に分布を確認する。
## 入力
- `INPUT_CSV_PATH`: `40_trip_od_screening.py` が出力する 1 行=1 トリップの CSV（UTF-8）。列: `o_lon,o_lat,d_lon,d_lat` を使用。
- その他列（weekday 等）はそのまま保持され、集計には影響しない。
## 出力
- `OUTPUT_DIR` 内に以下を生成:
  - `origin_map.html` / `destination_map.html`: Folium ヒートマップ。
  - `index_od_heatmap.html`: 上記 2 ファイルへのリンク集。
  - `od_summary.txt`: 行数や範囲のサマリ。
- 既定のヒートマップ設定: RADIUS=16, BLUR=18, MIN_OPACITY=0.15, MAX_ZOOM=12。
## 実行方法
- スクリプト冒頭で `INPUT_CSV_PATH`, `OUTPUT_DIR` を設定。
- コマンド例: `python 41_od_heatmap_viewer.py`
- 実行後にブラウザが自動で `index_od_heatmap.html` を開き、Origin/Destination の切替が可能。
## 判定ロジック（重要なものだけ）
- pandas で CSV を読み込み、緯度経度を numpy 配列化。
- folium.plugins.HeatMap で O 点・D 点それぞれを描画（同一トリップでも別々に積算）。
- 入力が空の場合はエラーを出さず終了。
## できること / できないこと（行政向けの注意）
- できること: OD リストの粗い空間分布可視化、Origin/Destination の偏り比較、PDF に貼れる HTML 出力。
- できないこと: ズーム別の件数正規化、ゾーン集計、時間帯別比較。ヒートマップ濃淡は相対値であり、絶対交通量ではない。
## よくあるミス
- INPUT_CSV_PATH に 40 系以外のファイルを指定し、列不足で例外。
- 出力先の書き込み権限不足で HTML が生成されない。
- ブラウザのセキュリティ設定でローカル HTML のタイル読込がブロックされる。
## 関連スクリプト
- 前段: [docs/40_trip_od_screening.md](./40_trip_od_screening.md)。
- 後段: [docs/42_OD_extractor.md](./42_OD_extractor.md)（ゾーン集計）。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
