# 50_Path_Analysis（流入側A/B判定・流入/流出経路ヒートマップ）
## これは何をする（目的）
単路ポイント（交差点中心など）を基準に、トリップが「どの流入側から交差点中心へ到達し（A/B）」「中心を通過した後にどこへ向かうか（流入経路/流出経路）」を 25m メッシュで集計し、A方向交通とB方向交通ごとにヒートマップHTMLを作成する。
## どこで使う（位置づけ）
- **経路分析**フェーズ（交差点周辺の通過分布可視化）。
- 第２スクリーニング後の様式1-2（トリップ点列）を対象とし、単路ポイント近傍の流入・流出の空間分布を把握する。
## 入力と前提（3点セット・A/B解釈）
### 3点セット照合（バッチ対象は「揃った交差点のみ」）
プロジェクトフォルダ（`--project_dir`）配下で、以下の **3点セット** が揃った交差点だけを処理する。
- `project_dir/11_交差点(Point)データ/<交差点名>.csv` に交差点定義（CSV）がある
- `project_dir/11_交差点(Point)データ/<交差点名>.jpg`（または `.jpeg`）に背景画像がある
- `project_dir/20_第２スクリーニング/<交差点名>/` に第２スクリーニング済みトリップ（様式1-2 CSV）がある
- 上記が揃った交差点のみがバッチ対象（交差点名は **ファイル名stemとフォルダ名が完全一致**）

### A/B判定の解釈（最優先で確認）
- A/B判定は「交通の進行方向」ではなく **「交差点中心にどちら側から到達したか（流入側）」** で行う。
- 交差点CSVの2行目をA方向、3行目をB方向とし、`dir_deg` は **外側→中心（outside→center）** の方位角として扱う。
- 地図上の矢印も outside→center 向きで描画し、A方向交通画面にはA矢印のみ、B方向交通画面にはB矢印のみを表示する。
- `in` は交差点に入るまでの流入経路、`out` は交差点を出た後の流出経路を指す。

### 列前提（様式1-2）
- 経度=14列、緯度=15列（0始まりの usecols 指定）
## 実行方法（project_dir 指定 or ダイアログ選択）
### バッチ実行（必須）
```
python 50_Path_Analysis.py --project_dir "...\20260106_0930_2nd_point_中活経路分析_R6_10"
```
### ダイアログで選択（project_dir 未指定）
```
python 50_Path_Analysis.py
```
起動するとフォルダ選択ダイアログが開く。  
選択したフォルダに必須フォルダ（`11_交差点(Point)データ` / `20_第２スクリーニング`）が無い場合は
「これはプロジェクトフォルダではありません」の警告が出て再選択になる。
キャンセルすると処理は終了する。
### 対象交差点を絞る（任意）
```
python 50_Path_Analysis.py --project_dir "...\20260106_0930_2nd_point_中活経路分析_R6_10" --targets "鶴山通り,奏天"
```
### ドライラン（走査のみ）
```
python 50_Path_Analysis.py --project_dir "...\20260106_0930_2nd_point_中活経路分析_R6_10" --dry_run
```
### フォルダ構成（固定名）
- `11_交差点(Point)データ/<交差点名>.csv` と `<交差点名>.jpg`（または `.jpeg`）がある
- `20_第２スクリーニング/<交差点名>/` がある
- 出力は `50_経路分析/<交差点名>/` に自動格納される
### 実行時の表示
- 開始直後にサマリが表示される（例）
  ```
  [scan] screen folders : 12
  [scan] point csv      : 12
  [scan] point image    : 12
  [target] ready        : 10
  [skip]   skipped      : 2
  --------------------------------
  ```
- 進捗表示は `[i/total] (xx.x%) ...` の形式で出る
  ```
  [3/10] ( 30.0%) 交差点=鶴山通り start
    screen : ...
    point  : ...csv
    image  : ...jpg
    out    : ...
  [3/10] ( 30.0%) 交差点=鶴山通り done  elapsed=12.3s (ok=2 ng=0 skip=2)
  ```
## 出力（必ずここに出る）
出力ルートは固定：  
`project_dir/50_経路分析/<交差点名>/`

`<交差点名>` は **第２スクリーニング側のフォルダ名** を採用する（見た目が一致し、成果物が追いやすい）。

生成物（prefix=stem は原則 交差点CSVのファイル名由来）:
1) CSV（% 表記の整数、北が上になるよう上下反転して保存）
- `50_path_matrix_A_in.csv`
- `50_path_matrix_A_out.csv`
- `50_path_matrix_B_in.csv`
- `50_path_matrix_B_out.csv`
2) ヒートマップHTML（25mメッシュ矩形を Folium で塗り分け、矢印は表示する方向のみ）
- `{stem}_heatmap_A（流入）.html`
- `{stem}_heatmap_A（流出）.html`
- `{stem}_heatmap_B（流入）.html`
- `{stem}_heatmap_B（流出）.html`
3) 流入/流出を左右に並べた比較ページ（A方向交通/B方向交通）
- `{stem}_heatmap_A方向交通.html`（A矢印のみ。左=流入、右=流出）
- `{stem}_heatmap_B方向交通.html`（B矢印のみ。左=流入、右=流出）

※作業フォルダ構成は `docs/05_work_folder_structure.md` を正とする。  
## スキップとエラー（不足理由の見方）
### スキップ条件（3点セット不足）
3点セットが揃わない交差点は自動的にスキップされ、理由が表示される。

`missing_*` の意味:
- `missing_screen_folder`: `20_第２スクリーニング/<交差点名>/` が見つからない
- `missing_point_csv`: `11_交差点(Point)データ/<交差点名>.csv` が無い
- `missing_point_image`: `11_交差点(Point)データ/<交差点名>.jpg/.jpeg` が無い

ログには期待されるパス（`expected_csv` / `expected_img` / `expected_screen_dir`）も出るので、そこを直せば一発で解決できる。

### エラーの扱い
処理中の例外は **交差点単位で握りつぶさず**、失敗一覧として最後に出る（処理自体は継続）。

#### サンプルログ（短縮）
```
[scan] screen folders : 5
[scan] point csv      : 5
[scan] point image    : 5
[target] ready        : 4
[skip]   skipped      : 1
--------------------------------
[SKIP] 交差点=奏天 reason=missing_point_image
       expected_csv=X:\Project\001\11_交差点(Point)データ\奏天.csv
       expected_img=X:\Project\001\11_交差点(Point)データ\奏天.jpg
       expected_screen_dir=X:\Project\001\20_第２スクリーニング\奏天
[1/4] ( 25.0%) 交差点=1鶴山通り start
[1/4] ( 25.0%) 交差点=1鶴山通り done  elapsed=8.4s (ok=1 ng=0 skip=1)
[50_PathAnalysis] Batch summary
  success = 4
  failed  = 0
  skipped = 1
```
## 主要パラメータ（25m・10段階パレット・透過など）
### 解析範囲・メッシュ
- `HALF_SIDE_M`: 解析範囲（既定 ±1000m → 2km四方）
- `CELL_SIZE_M`: メッシュサイズ（**既定 25m／運用推奨 25m**）
- `SAMPLE_STEP_M`: 線分サンプリング間隔（既定 10m。25mメッシュなら 10m のままでもOK）
### 通過判定
- `CROSS_THRESHOLD_M`: 単路ポイント通過判定の距離閾値（既定 50m）
### ヒートマップ表示の調整（重要）
**「下の地図が見えるようにうっすら透過」が基本方針。**  
10段階カラーパレット（色相の段階）＋透過で見やすく調整する。
- `HEATMAP_PALETTE_10`: 10色パレット（低→高）
- `HEATMAP_MIN_OPACITY` / `HEATMAP_MAX_OPACITY`: 透明度の下限/上限（背景地図が見える範囲で）
- `HEATMAP_VMAX_PERCENTILE`: vmax を上位パーセンタイルで決める（外れ値の白飛び防止）
  - 例: 99.0 → 上位1%を飽和として扱い強調が出やすい
- `HEATMAP_GAMMA`: 強調度（値の分布を上側に寄せる）
  - 目安: 0.8～1.2
### A/B 矢印・ラベルの調整
A/B の矢印が潰れて見づらい場合に、線の長さ・太さ・ラベル位置で視認性を上げる。矢印の向きは outside→center に統一する。
- `ARROW_HEAD_ROTATE_OFFSET_DEG`: 環境で矢じりの向きがずれる場合の補正角
- `ARROW_LINE_LENGTH_M`: 矢印の線の長さ
- `ARROW_LINE_WEIGHT`: 線の太さ
- `ARROW_LABEL_SIZE_PX` / `ARROW_LABEL_FONT_REM`: 白丸と文字の大きさ
## 判定ロジック（必要最小限）
- 交差点CSVから中心座標と A/B 方位角（dir_deg）を取得し、「outside→center」の基準ベクトルを作る。
- 中心（原点）に最も近い線分が `CROSS_THRESHOLD_M` 以内になった地点を「通過点」として検出。
- A/B は **流入側** で判定し、in/out を分けてメッシュ加算する。
- 行列は方向別HIT数で正規化し、整数%で保存・描画する。
## よくあるミス
- A/B を「進行方向」と解釈してしまい、交差点ファイルの dir_deg を outside→center で用意しない
- 3点セットが揃っていない（交差点フォルダ/CSV/JPG/第２スクリーニングフォルダのどれかが欠けている）
- 交差点名のフォルダ名が一致していない
- 経度緯度列が想定と違い、読み込みで空扱いになる
- `CROSS_THRESHOLD_M` が小さすぎて通過点検出が失敗（empty扱いが増える）
## 変更履歴
- 2026-01-07: 71_Path_Analysis.py を 50_Path_Analysis.py に改名（参照・文書も追従）
- 2026-01-xx: --project_dir による一本運用に統一（固定フォルダ名、スキップ理由表示、進捗表示）
- 2026-01-xx: ヒートマップを10段階カラーパレット＋透過に変更
- 2026-01-xx: メッシュサイズ既定を 10m → 25m に変更
