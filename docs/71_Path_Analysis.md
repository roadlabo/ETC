# 71_Path_Analysis
## 目的（何をする）
単路ポイント（交差点中心など）を基準に、方向別のメッシュ集計とヒートマップを生成する。A/B 方向を指定し、500m 四方の範囲を 10m メッシュでカウントすることで流入・流出の空間分布を可視化する。
## 位置づけ（分析フロー上のどこ）
- **経路分析**フェーズ。
- PDF 用語の「経路分析（500m四方の可視化、ヒートマップ、20mメッシュ集計）」に対応し、第2スクリーニング後の様式1-2 を対象とする。
## 入力
- `INPUT_DIR`: 第2スクリーニング後の様式1-2 CSV 群（サブフォルダ含め rglob）。
- `POINT_FILE`: 単路ポイント指定 CSV（11_crossroad_sampler で作成した中心＋2方向を想定）。
- 列前提: 経度=14, 緯度=15。
- パラメータ: `MESH_HALF_SIZE_M`（範囲、既定±1000m）、`CELL_SIZE_M`（メッシュ 10m）、`SAMPLE_STEP_M`（線分サンプリング 10m）、`CROSS_THRESHOLD_M`（通過判定 50m）。
## 出力
- `OUTPUT_DIR` に以下を生成:
  - `71_path_matrix_{A|B}_{in|out}.csv`：方向別メッシュカウント（% 表記の整数）。
  - `71_heatmap_{A|B}_{in|out}.html`：Folium で塗り分けたヒートマップ。
  - `71_heatmap_in_AB.html` / `71_heatmap_out_AB.html`：A/B 並列表示の比較ページ。
- 標準出力に処理件数、非ゼロセル数、最大値をログ。
## 実行方法
- スクリプト冒頭で `INPUT_DIR`, `POINT_FILE`, `OUTPUT_DIR`, 各種パラメータを設定。
- コマンド例: `python 71_Path_Analysis.py`
- 進捗は `[71_PathAnalysis] xx%` 形式で標準出力に表示。
## 判定ロジック（重要なものだけ）
- POINT_FILE から中心座標と A/B 方向を読み込み、方向ベクトルを生成。
- 各トリップの点列をローカル XY に変換し、中心からの最近点を通過点として検出。
- 通過点の前後ベクトルから A/B 判定し、10m メッシュにサンプリングした軌跡を `A/B × in/out` に積算。
- メッシュは北が上になるように上下反転して保存し、ヒートマップを Folium で描画。
## できること / できないこと（行政向けの注意）
- できること: 単路ポイントを起点に方向別の流入/流出分布を可視化、メッシュ粒度や範囲を変更して感度確認、A/B を同時比較。
- できないこと: 信号・交通規制の影響推定、サンプルポイントの自動生成、方向誤判定の自動補正。10m メッシュは近似であり、測量精度を保証しない。
## よくあるミス
- POINT_FILE に A/B 方向が入っておらず、ベクトル計算でエラー。
- CELL_SIZE_M を粗くしすぎて（>50m）ヒートマップがブロック状になる。
- INPUT_DIR が第1スクリーニングのままで、通過点検出に失敗。
## 関連スクリプト
- 前段: [docs/21_point_trip_extractor.md](./21_point_trip_extractor.md), [docs/11_crossroad_sampler.md](./11_crossroad_sampler.md)。
- 兄弟: OD 系は [docs/42_OD_extractor.md](./42_OD_extractor.md)。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
