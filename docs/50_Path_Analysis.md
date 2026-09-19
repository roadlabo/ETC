# 50_Path_Analysis（流入側A/B判定・流入/流出経路ヒートマップ）

## 追加：ルート通過交通分析モード

### ゲート・ODマトリクス（2026-09-13更新）

変更は50のルートモードに限定しています。15・20の元データは変更しません。

- 境界端点を14で手動指定した最寄りのゲートへ割り当てます。自動ゲート生成・半径50mの再集約は行いません。ゲートの位置と番号は14の設定をそのまま使い、路線間でも共通です。同距離なら番号の小さいゲートを選びます。
- 地図にゲート番号を常時表示し、12の内部エリアも重ねます。
- 開始前に`14_エリアデータ/14_area.geojson`と`12_ゾーニングデータ`内のポリゴンCSVを確認します。12のCSVがなければ`12_polygon_builder.bat`での作成・保存を案内して停止します。区域や第2スクリーニングの不足も作成案内を表示します。
- HTMLに行O・列DのODマトリクスを追加します。両軸はゲート番号または「内：エリア名」で、内の区分は12のポリゴンと起終点座標で決めます。ポリゴン境界も含みます。どのエリアにも入らない場合は「内：エリア外」、複数の異なる名前のエリアに入る場合は「内：エリア重複」です。分類不明は除外件数を明示します。
- `50_経路分析.xlsx`に日本語の集計条件・統合OD表・交通区分別集計・ゲートOD明細・トリップ分類・メッシュ集計をまとめて出力します。統合OD表の左上見出しは`O \ D`です。表形式のCSVは出力せず、`50_gate_master.geojson`とHTML地図は引き続き出力します。

既存の出力HTMLへの変更反映には、元のプロジェクトを選んで50を再実行してください。

ゲートがない旧エリアデータは14で追加して保存し、15→20→50の順に再実行してください。区域・ゲートの変更後は区域ハッシュが変わります。

### UI外観・フォント（2026-09-12更新）

20系と共通の黒地・緑の計器盤風パネルに統一しました。上部に実行状態と経過時間、左側にプロジェクト・路線選択、右側に対象トリップ数・通過交通数・通過交通率と分類表・主要ODを表示します。小さいウィンドウでは選択パネルをスクロールできます。路線変更時は前路線の結果を消去します。

`QFont::setPointSize: Point size <= 0 (-1)` は表示フォントの警告です。従来の `font-size:14px` ではQtのpointSizeが-1になることを確認したため、50では正のポイントサイズ（標準10pt）を使用し、別ウィンドウになるコンボボックスのアプリ既定フォントも補正します。警告を非表示にする処置ではありません。

新しい `src/assets/logos/logo_50_Path_Analysis.png` は既存15/20ロゴと同系統です。起動時に中央でフェード表示し、右上とウィンドウアイコンにも使用します。右上のロゴは他UIと同じ `ClickableLogoLabel` を使い、道路ラボを開きます。50を配布するときは共通コードとこの画像も含めてください。

検証では旧画面のpointSize=-1、新画面のpointSize=10を確認。アプリ既定をピクセルフォントにした条件でも、全子ウィジェットと文書フォントが正のポイントサイズとなり、ロゴのフェード・解析ワーカー完了・路線切替までQFont警告0件でした。1180×820と960×720で外観を確認しています。全39テスト中37成功、残る2件は既知の30系summaryキー不一致です。

ロゴは組み込みimagegenで生成。参照画像はlogo_20_route_trip_extractor.png、logo_15_area_screening.png。使用プロンプト：

> Create a new matching suite logo for tool 50, using the two supplied images as visual style references. Asset type: a wide 2:1 landscape software splash/header logo PNG. Preserve the same dark navy background, luminous cyan and green roadway/data graphics, purple-blue italic ETC wordmark, metallic white Japanese lettering, and professional dimensional style. Exact text: small upper-left 'Presented by 津山市'; large central 'ETCアナライザー'; bottom subtitle '経路分析'. Replace the screening imagery with a road network and glowing paths connecting two gate portals across a fine square analysis mesh, plus a small bar chart and wireless signal in the same style as references. Keep clean readable Japanese typography, balanced margins, and the same visual family. Do not include NASA lettering or any NASA seal. The result will be displayed in a launch splash and a small header logo; prioritize legibility and cohesive branding.

### 起動・操作

正式版の実体は `src/50_Path_Analysis.py` です（unreleasedから移動済み）。既存の交差点・A/B・in/outモードは既定のまま維持しています。

1. `bat/50_UI_route_path_analysis.bat` を起動します。
2. STEP 1でプロジェクトフォルダを1つ選びます。区域・analysis_area・ルート第2スクリーニングを自動確認します。
3. STEP 2で1路線を選び、件数と第1.5由来状態を確認します。
4. 分析を実行します。CSV・Gate・区域の整合性を検証後、交通分類と主要ODがUIに表示されます。
5. 「HTMLレポート・経路地図を開く」で結果を開きます。地図右上から全交通・通過交通・外内・内外・内内・指定Gate ODを切り替えます。

```bat
runtime\python\python.exe src\50_Path_Analysis.py --mode route --project_dir "D:\PROJECT" --route "対象路線"
```

`--dry_run` は走査のみです。`--route` を省略するとUIを起動します。

出力先：`50_経路分析/対象路線/`。`50_経路分析.xlsx`、`50_report.html`、`50_map.html`、`50_gate_master.geojson` を出力します。

旧版の `50_trip_classification.csv`、`50_od_matrix.csv`、`50_path_summary.csv`、`50_gate_od.csv`、`50_mesh.csv` は、50を再実行すると同じ出力フォルダから削除されます。ユーザーが作成した別名CSVは対象にしません。

通過交通率 = Gate→Gate数 ÷ 選択路線を通った全区域内サブトリップ数。Gate→Insideは外内、Inside→Gateは内外、Inside→Insideは内内です。Gate ODは方向別・件数降順で、通過交通内割合と全交通内割合を保存します。CSVの割合は0～1、UI・HTMLは%表示です。

メッシュは1トリップにつき訪問セルを1回加算し、件数と各表示群内割合を保存します。既存50の25mセル・10m線分サンプリング・投影・配色を再利用し、ルートモードでは交差点の2km範囲制限を解除しています。点間は直線補間であり、道路リンクへのmap matchingではありません。

第1.5由来・区域ハッシュ・全CSVとsidecar・Gateマスターの一致が確認できない場合、正式通過交通率は無効です。旧平置きデータは分類不明として全経路を表示できます。0トリップの場合も正式率を計算しません。

詳細：[横断設計と検証記録](50_route_path_analysis_design.md)。以下は従来の交差点分析の説明です。
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
- O列=経度（index 14）、P列=緯度（index 15）。Foliumには `[lat, lon]` を渡します。
## 実行方法（project_dir 指定 or ダイアログ選択）
### バッチ実行（必須）
```
python 50_Path_Analysis.py --project_dir "...\20260106_0930_2nd_point_中活経路分析_R6_10"
```
### ダイアログで選択（project_dir 未指定）
```
python 50_Path_Analysis.py
```
起動するとフォルダ選択ダイアログが開く（「プロジェクトフォルダを指定してください。」と案内される）。  
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
### メッシュ原点の平行移動（交差点中心＝セル中心）
- メッシュ原点は「交差点中心がセル中心になるように平行移動」して決める。
- これにより交差点中心がメッシュ境界に乗らず、GPS誤差で 50/50 割れが起きにくい。
### 通過判定
- `CROSS_THRESHOLD_M`: 単路ポイント通過判定の距離閾値（既定 50m）
### ヒートマップ表示の調整（重要）
**「下の地図が見えるようにうっすら透過」が基本方針。**  
10段階カラーパレット（色相の段階）＋透過で見やすく調整する。  
メッシュの色は 0–100% を 10段階で表現し、
透明度は 0.4〜0.8 の範囲で値に応じて変化させる。
これにより背景地図の視認性を保ちつつ、
値の強弱を直感的に把握できる。
- `HEATMAP_PALETTE_10`: 10色パレット（低→高）
- 透明度は **0.4〜0.8 を線形補間**（低頻度は薄く、高頻度は濃く）
- vmax は **常に 100% 固定**（0–10, 10–20, …, 90–100 の10段階に固定）
- 凡例も **0–10% … 90–100% の固定10段階** で表示する
### A/B 矢印・ラベルの調整
A/B の矢印が潰れて見づらい場合に、線の長さ・太さ・ラベル位置で視認性を上げる。矢印の向きは outside→center に統一する。
- `ARROW_HEAD_ROTATE_OFFSET_DEG`: 環境で矢じりの向きがずれる場合の補正角
- `ARROW_LINE_LENGTH_M`: 矢印の線の長さ
- `ARROW_LINE_WEIGHT`: 線の太さ
- 丸い「A」表示は廃止し、ラベルは **「流入」「流出」** の文字（背景なし）で表示する
- ラベルは矢印から少し離して配置する
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
