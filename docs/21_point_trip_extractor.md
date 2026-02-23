# 21_point_trip_extractor（第2スクリーニング：交差点点抽出）

このスクリプトは、「第1スクリーニング等で作られたトリップCSV群」から、指定した交差点（中心点）に近いトリップだけを拾い出し、交差点ごとのフォルダにコピー（保存）します。  
また、1つのCSVの中に複数トリップが混在している場合は、決められた境界ルールで分割し、分割片ごとに判定・保存します。

---

## 1. 何を入力として、何を出力するか

### 入力
- **トリップCSV群**（フォルダ指定）
  - CSVはヘッダー無しを想定し、行をそのまま保持して読み込みます（utf-8-sigで読み込み、読めない文字は無視）:contentReference[oaicite:5]{index=5}
- **交差点CSV群**（フォルダ指定）
  - 交差点ごとに1ファイル（stemが交差点名扱い）
  - 各交差点CSVは「先頭の有効行」から **lon=row[1], lat=row[2]** を読み取り、これを交差点中心点として使います:contentReference[oaicite:6]{index=6}

### 出力
- 交差点ごとの出力フォルダ配下に、条件を満たしたトリップ（または分割片）をCSVとして保存します。
- 保存ファイル名は、元データから拾った情報で機械的に組み立てます（詳細は後述）:contentReference[oaicite:7]{index=7}

---

## 2. 全体の処理の流れ（ざっくり）

1) 入力フォルダ（トリップCSV）を走査して対象ファイル一覧を作ります（再帰オプションあり）:contentReference[oaicite:8]{index=8}  
2) 交差点フォルダから交差点CSV一覧を取得し、中心点（lon,lat）を読み込みます:contentReference[oaicite:9]{index=9}  
3) 交差点ごとに、全トリップCSVを順番に処理します（進捗表示あり）:contentReference[oaicite:10]{index=10}  
4) 各トリップCSVは「境界ルール」に従って分割し、分割片ごとに交差点近傍判定を行います:contentReference[oaicite:11]{index=11}  
5) 判定に合格した分割片だけを、交差点の出力フォルダへ保存します:contentReference[oaicite:12]{index=12}

---

## 3. 交差点に近いかどうかの判定（重要）

このスクリプトは「点（GPS点）が近いか」と「線分（点と点を結ぶ線）が近いか」の両方で判定します。

### 3.1 点ベース判定（基本）
分割片の各行から (lon,lat) を読み取り、交差点中心点との距離を haversine（球面距離）で計算します。  
距離が閾値 `thresh_m` 以下の点が見つかるたびにカウントし、一定数 `min_hits` に達したら **その分割片は合格**です:contentReference[oaicite:13]{index=13}。

- ついでに「交差点中心からの最小距離 `min_dist`」も更新して保持します:contentReference[oaicite:14]{index=14}

### 3.2 線分ベース判定（救済）
点ベースで **1点も閾値内に入らなかった** かつ 点が2つ以上あるときだけ、線分判定に入ります:contentReference[oaicite:15]{index=15}。

やっていることは：
- 交差点中心を原点にしたローカルXYに変換し:contentReference[oaicite:16]{index=16}
- 連続する2点を結ぶ線分について「原点（交差点中心）から線分への最短距離」を求め:contentReference[oaicite:17]{index=17}
- その距離が `thresh_m` 以下の線分が一定回数 `min_hits` に達したら **合格**です:contentReference[oaicite:18]{index=18}

※遠く離れた点同士の線分が誤判定しないように、一定以上遠い（thresh_m*3超）区間はスキップする条件も入っています:contentReference[oaicite:19]{index=19}。

---

## 4. トリップの分割ルール（境界の作り方）

1つのCSVに複数のトリップが入っている場合に備えて、まず「境界インデックスの集合」を作ります。

境界は必ず `{0, len(rows)}` を含みます:contentReference[oaicite:20]{index=20}。  
そのうえで、各行を見ながら次の条件で境界を追加します：

### 4.1 FLAG列のルール
- flag == "0" の行：その行インデックス `idx` を境界にする  
- flag == "1" の行：その次の行 `idx+1` を境界にする:contentReference[oaicite:21]{index=21}

### 4.2 TRIP_NO列の変化ルール
- TRIP_NO が前行までと変わった地点 `idx` を境界にする:contentReference[oaicite:22]{index=22}

最終的に境界をソートし、隣接する境界ペアを (start,end) として分割片を順に作ります（境界から区間を生成）:contentReference[oaicite:23]{index=23}。

---

## 5. 曜日フィルタ（必要なときだけ）

曜日フィルタが指定されている場合、各行の DATE列先頭8桁（YYYYMMDD）から曜日番号を作り、対象曜日以外の行は判定対象から除外します:contentReference[oaicite:24]{index=24}:contentReference[oaicite:25]{index=25}。

- ここでの曜日番号は 1=SUN, 2=MON, ... 7=SAT の体系です:contentReference[oaicite:26]{index=26}

---

## 6. 保存されるファイル名のルール（出力の透明性）

合格した分割片は、次の形式で保存されます:contentReference[oaicite:27]{index=27}：

`2nd_{route_name}_{weekday_part}_ID{opid12}_{primary_date}_{trip_tag}_{etype_tag}_{fuse_tag}.csv`

各パーツの作り方：
- `weekday_part`：分割片内に出現した運行日（YYYYMMDD）の集合から曜日を計算し、`MON-TUE-...` のように並べたもの（なければ UNK）:contentReference[oaicite:28]{index=28}
- `opid12`：OP_ID列から拾った値を12桁ゼロ埋め（拾えなければ 000000000000）:contentReference[oaicite:29]{index=29}
- `primary_date`：OP_DATE列の先頭8桁を最初に拾ったもの（なければ 00000000）:contentReference[oaicite:30]{index=30}:contentReference[oaicite:31]{index=31}
- `trip_tag`：TRIP_NO列を最初に拾って `t000` 形式（拾えなければ t000）:contentReference[oaicite:32]{index=32}
- `etype_tag`：車種区分（VEHICLE_TYPE列）を拾って `E00` 形式（拾えなければ E00）:contentReference[oaicite:33]{index=33}
- `fuse_tag`：用途区分（VEHICLE_USE列）を拾って `F00` 形式（拾えなければ F00）:contentReference[oaicite:34]{index=34}

保存処理自体は「分割片の行をそのままCSVに書き出す」だけで、列の再構成や加工はしません:contentReference[oaicite:35]{index=35}。

---

## 7. 進捗表示と集計（動作確認に使える情報）

交差点ごとに、対象ファイル数・処理率・ヒット数・経過時間・推定残り時間(eta)を1行で更新表示します:contentReference[oaicite:36]{index=36}。  
また、入力フォルダ内に含まれる「運行IDの種類数」も事前に数えて表示します（各CSVの最初の運行IDを拾って集合化）:contentReference[oaicite:37]{index=37}:contentReference[oaicite:38]{index=38}。

---

## 8. 使い方（最低限）

このスクリプトは内部で
- input（トリップCSVのあるフォルダ）
- cross（交差点CSVのあるフォルダ）
- output（出力先フォルダ）
- 半径（radius_m）
- targets（交差点の絞り込み）
- recursive（再帰走査）
などを使って実行されます:contentReference[oaicite:39]{index=39}。

※実際の起動方法（UIやbatからどう呼ぶか）は、運用側（UI/バッチ）に合わせて記載してください。
