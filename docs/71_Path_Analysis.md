# 71_Path_Analysis（流入側A/B判定・流入/流出経路ヒートマップ）
## 目的（何をする）
単路ポイント（交差点中心など）を基準に、トリップが「どの流入側から交差点中心へ到達し（A/B）」「中心を通過した後にどこへ向かうか（流入経路/流出経路）」を 10m メッシュで集計し、A方向交通とB方向交通ごとにヒートマップHTMLを作成する。
## 位置づけ（分析フロー上のどこ）
- **経路分析**フェーズ（交差点周辺の通過分布可視化）。
- 第2スクリーニング後の様式1-2（トリップ点列）を対象とし、単路ポイント近傍の流入・流出の空間分布を把握する。
## 判定の前提（最優先で確認）
- A/B判定は「交通の進行方向」ではなく「交差点中心にどちら側から到達したか（流入側）」で行う。交差点ファイル2行目をA方向、3行目をB方向とし、`dir_deg` は「外側→中心（outside→center）」の方位角として扱う。
- 地図上の矢印も outside→center 向きで描画し、A方向交通画面にはA矢印のみ、B方向交通画面にはB矢印のみを表示する。
- `in` は交差点に入るまでの流入経路、`out` は交差点を出た後の流出経路を指す。
## 入力
- `INPUT_DIR`: 第2スクリーニング後の様式1-2 CSV 群（サブフォルダ含め rglob）。
- `POINT_FILE`: 単路ポイント指定 CSV（11_crossroad_sampler.py の出力を想定）。
  - CSV形式（例）: crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg
  - 2行目 branch_no=1 → A方向（outside→center の方位角）, 3行目 branch_no=2 → B方向（outside→center の方位角）
- 列前提（様式1-2）:
  - 経度=14列、緯度=15列（0始まりの usecols 指定）
## 主要パラメータ（スクリプト冒頭で変更）
- 解析範囲・メッシュ
  - `MESH_HALF_SIZE_M`: 解析範囲（既定 ±250m → 500m四方）
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
A/B の矢印が潰れて見づらい場合に、線の長さ・太さ・ラベル位置・左右オフセットで視認性を上げる。矢印の向きは outside→center に統一する。
- `ARROW_HEAD_ROTATE_OFFSET_DEG`: 環境で矢じりの向きがずれる場合の補正角
- `ARROW_LINE_LENGTH_M`: 矢印の線の長さ
- `ARROW_LABEL_DISTANCE_M`: ラベルを置く距離（線より先に置くと潰れにくい）
- `ARROW_LINE_WEIGHT`: 線の太さ
- `ARROW_LABEL_SIZE_PX` / `ARROW_LABEL_FONT_REM`: 白丸と文字の大きさ
- `ARROW_LABEL_SIDE_OFFSET_M`: A/Bラベルを左右に少しずらして重なり回避
## 出力
`OUTPUT_DIR` に以下を生成する（prefix は `POINT_FILE.stem`）。
1) CSV（% 表記の整数、北が上になるよう上下反転して保存）
- `71_path_matrix_A_in.csv`
- `71_path_matrix_A_out.csv`
- `71_path_matrix_B_in.csv`
- `71_path_matrix_B_out.csv`
2) ヒートマップHTML（10mメッシュ矩形を Folium で塗り分け、矢印は表示する方向のみ）
- `{stem}_heatmap_A（流入）.html`
- `{stem}_heatmap_A（流出）.html`
- `{stem}_heatmap_B（流入）.html`
- `{stem}_heatmap_B（流出）.html`
3) 流入/流出を左右に並べた比較ページ（A方向交通/B方向交通）
- `{stem}_heatmap_A方向交通.html`（A矢印のみ。左=流入、右=流出）
- `{stem}_heatmap_B方向交通.html`（B矢印のみ。左=流入、右=流出）
## 実行方法
- スクリプト冒頭で `INPUT_DIR`, `POINT_FILE`, `OUTPUT_DIR` と各種パラメータを設定する
- コマンド例: `python 71_Path_Analysis.py`
- 進捗は 1行更新（改行増殖しない）で表示される:
  - `[71_PathAnalysis] xx.x% (i/total) empty=... started=...`
- 終了時に処理概要、方向別セル統計、方向別HIT数、in→out遷移数を標準出力する
## 判定ロジック（重要なものだけ）
- POINT_FILEから中心座標（lon0,lat0）と A/B 方位角（dirA_deg,dirB_deg）を取得し、「outside→center」の基準ベクトル v_dir_A/v_dir_B を作る。
- 各トリップ点列（lon/lat）を中心基準のローカルXY(m)へ変換する。
- 中心（原点）に最も近い線分が `CROSS_THRESHOLD_M` 以内になった地点を「通過点」として検出する。
- A/B は流入側で判定し、in/out を分けて処理する:
  - `classify_direction`: 交差点直前点 → 仮想通過点（outside→center）のベクトルを A/B 基準と比較して流入側を判定
  - `classify_out_direction`: 交差点直後点 → 仮想通過点のベクトルを outside→center に揃えて A/B 基準と比較し、流出側を判定
- メッシュ集計は in/out で別々に「訪問メッシュ」を作り、A_in/B_in と A_out/B_out に加算する。
- 行列は方向別HIT数で正規化し、整数%（四捨五入）で保存・描画する。
## できること / できないこと（行政向けの注意）
- できること:
  - 交差点中心への流入側（A/B）で分けた流入/流出分布を可視化
  - 範囲（±m）やメッシュ（m）を変えて感度確認
  - in/out を同時に比較し「どこから来てどこへ抜けるか」を直感的に確認
- できないこと:
  - 信号制御・交通規制の因果推定
  - 方向誤判定の自動補正（交差点ファイルのA/B定義が現地の道路形状と一致している必要あり）
  - 10mメッシュは近似であり測量精度を保証しない
## よくあるミス
- A/B を「進行方向」と解釈してしまい、交差点ファイルの dir_deg を outside→center で用意しない
- POINT_FILE が 11_crossroad_sampler 出力の形式になっておらず、A/B方位角が読めない
- INPUT_DIR が第2スクリーニング後の様式1-2ではなく、経度緯度列が想定と違う
- `CROSS_THRESHOLD_M` が小さすぎて通過点検出が失敗（empty扱いが増える）
- ヒートマップの強調が弱い場合は `HEATMAP_VMAX_PERCENTILE`（例: 99→98）や `HEATMAP_GAMMA`（例: 0.55→0.45）を調整する
## 関連スクリプト
- 前段: [docs/21_point_trip_extractor.md](./21_point_trip_extractor.md), [docs/11_crossroad_sampler.md](./11_crossroad_sampler.md)
- 兄弟: OD 系は [docs/42_OD_extractor.md](./42_OD_extractor.md)
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
