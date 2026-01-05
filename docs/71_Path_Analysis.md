# 71_Path_Analysis
## 目的（何をする）
単路ポイント（交差点中心など）を基準に、トリップが「どの方向から流入し（in）」「どの方向へ流出するか（out）」を A/B の2方向で判定し、周辺の通過分布を 10m メッシュで集計してヒートマップ表示する。出力は Direction A と Direction B をそれぞれ「in/out を左右に並べて」比較できるHTMLも生成する。
## 位置づけ（分析フロー上のどこ）
- **経路分析**フェーズ（交差点周辺の通過分布可視化）。
- 第2スクリーニング後の様式1-2（トリップ点列）を対象とし、単路ポイント近傍の流入・流出の空間分布を把握する。
## 入力
- `INPUT_DIR`: 第2スクリーニング後の様式1-2 CSV 群（サブフォルダ含め rglob）。
- `POINT_FILE`: 単路ポイント指定 CSV（11_crossroad_sampler.py の出力を想定）。
  - CSV形式（例）: crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg
  - 2行目 branch_no=1 → A方向、3行目 branch_no=2 → B方向
- 列前提（様式1-2）:
  - 経度=14列、緯度=15列（0始まりの usecols 指定）
## 主要パラメータ（スクリプト冒頭で変更）
- 解析範囲・メッシュ
  - `MESH_HALF_SIZE_M`: 解析範囲（既定 ±1000m → 2km四方）
  - `CELL_SIZE_M`: メッシュサイズ（既定 10m）
  - `SAMPLE_STEP_M`: 線分サンプリング間隔（既定 10m）
- 通過判定
  - `CROSS_THRESHOLD_M`: 単路ポイント通過判定の距離閾値（既定 50m）
## ヒートマップ表示の調整（重要）
ヒートマップは「値→色」と「値→透明度」を分けて制御し、少ない所は薄く、多い所は赤く強調する。
- `HEATMAP_VMAX_PERCENTILE`: vmax を最大値ではなく上位パーセンタイルで決める（外れ値で白飛びしない）
  - 例: 99.0 → 上位1%を飽和として扱い強調が出やすい
- `HEATMAP_GAMMA`: 強調度（小さい値ほど“赤いところ”が強調されやすい）
  - 目安: 0.4～0.8
- `HEATMAP_MIN_OPACITY` / `HEATMAP_MAX_OPACITY`: 透明度の下限/上限
  - 目安: min=0.02～0.08, max=0.7～0.95
- `HEATMAP_COLOR_STOPS`: 低→中→高の色（既定: 薄黄→オレンジ→赤）
## A/B 矢印・ラベルの調整
A/B の矢印が潰れて見づらい場合に、線の長さ・太さ・ラベル位置・左右オフセットで視認性を上げる。
- `ARROW_LINE_LENGTH_M`: 矢印の線の長さ
- `ARROW_LABEL_DISTANCE_M`: ラベルを置く距離（線より先に置くと潰れにくい）
- `ARROW_LINE_WEIGHT`: 線の太さ
- `ARROW_LABEL_SIZE_PX` / `ARROW_LABEL_FONT_REM`: 白丸と文字の大きさ
- `ARROW_LABEL_SIDE_OFFSET_M`: A/Bラベルを左右に少しずらして重なり回避
## 出力
`OUTPUT_DIR` に以下を生成する。
1) CSV（% 表記の整数、北が上になるよう上下反転して保存）
- `71_path_matrix_A_in.csv`
- `71_path_matrix_A_out.csv`
- `71_path_matrix_B_in.csv`
- `71_path_matrix_B_out.csv`
2) ヒートマップHTML（10mメッシュ矩形を Folium で塗り分け）
※ファイル名は `POINT_FILE.stem`（拡張子抜き）を prefix とする。
- `{stem}_heatmap_A_in.html`
- `{stem}_heatmap_A_out.html`
- `{stem}_heatmap_B_in.html`
- `{stem}_heatmap_B_out.html`
3) in/out を左右に並べた比較ページ（方向別）
- `{stem}_heatmap_A_in_out.html`（左=in、右=out）
- `{stem}_heatmap_B_in_out.html`（左=in、右=out）
## 実行方法
- スクリプト冒頭で `INPUT_DIR`, `POINT_FILE`, `OUTPUT_DIR` と各種パラメータを設定する
- コマンド例: `python 71_Path_Analysis.py`
- 進捗は 1行更新（改行増殖しない）で表示される:
  - `[71_PathAnalysis] xx.x% (i/total) empty=... started=...`
- 終了時に処理概要、方向別セル統計、方向別HIT数、in→out遷移数を標準出力する
## 判定ロジック（重要なものだけ）
- POINT_FILEから中心座標（lon0,lat0）と A/B 方位角（dirA_deg,dirB_deg）を取得し、「中心→外側（center→outside）」の基準ベクトル v_dir_A/v_dir_B を作る。
- 各トリップ点列（lon/lat）を中心基準のローカルXY(m)へ変換する。
- 中心（原点）に最も近い線分が `CROSS_THRESHOLD_M` 以内になった地点を「通過点」として検出する。
- in/out方向判定は分離して行う（ここが旧仕様からの改善点）:
  - `classify_in_direction`: 流入（外→中心）なので進行ベクトルを反転して A/B に近い方を選ぶ
  - `classify_out_direction`: 流出（中心→外）なので進行ベクトルをそのまま A/B と比較する
- メッシュ集計は in/out で別々に「訪問メッシュ」を作り、A_in/B_in と A_out/B_out に加算する。
- 行列は方向別HIT数で正規化し、整数%（四捨五入）で保存・描画する。
## できること / できないこと（行政向けの注意）
- できること:
  - 単路ポイント周辺の流入/流出分布を A/B 方向別に可視化
  - 範囲（±m）やメッシュ（m）を変えて感度確認
  - in/out を同時に比較し「どこから来てどこへ抜けるか」を直感的に確認
- できないこと:
  - 信号制御・交通規制の因果推定
  - 方向誤判定の自動補正（A/Bの定義が現地の道路形状と整合している必要あり）
  - 10mメッシュは近似であり測量精度を保証しない
## よくあるミス
- POINT_FILE が 11_crossroad_sampler 出力の形式になっておらず、A/B方位角が読めない
- INPUT_DIR が第2スクリーニング後の様式1-2ではなく、経度緯度列が想定と違う
- `CROSS_THRESHOLD_M` が小さすぎて通過点検出が失敗（empty扱いが増える）
- ヒートマップの強調が弱い場合は `HEATMAP_VMAX_PERCENTILE`（例: 99→98）や `HEATMAP_GAMMA`（例: 0.55→0.45）を調整する
## 関連スクリプト
- 前段: [docs/21_point_trip_extractor.md](./21_point_trip_extractor.md), [docs/11_crossroad_sampler.md](./11_crossroad_sampler.md)
- 兄弟: OD 系は [docs/42_OD_extractor.md](./42_OD_extractor.md)
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
