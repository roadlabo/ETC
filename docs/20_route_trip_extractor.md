# 20 第2スクリーニング（ルート）

`src/20_route_trip_extractor.py` は対象ルート付近を通ったトリップを選別し、**トリップ全体**を保存します。ルート近傍だけの切り抜きはしません。

## 入力・操作

`bat/20_UI_route_trip_extractor.bat` を起動し、プロジェクトと第1または第1.5スクリーニング入力フォルダを選択します。第1.5の親出力フォルダを選んだ場合は `15_area_subtrip_csv` を自動選択します。入力由来はログに表示します。

ルートは `10_ルート(Route)データ/*.csv`。様式1-2と同様、**O列=経度（index 14）、P列=緯度（index 15）**です。C=運行日、D=運行ID、E=種別、F=用途、G=GPS時刻、I=トリップ番号、M=起終点フラグです。

```bat
runtime\python\python.exe src\20_route_trip_extractor.py --project "D:\PROJECT" --input "D:\PROJECT\15_エリア第1.5スクリーニング" --radius-m 30 --min-route-points 3
```

`--recursive` は入力サブフォルダ探索、`--dry-run` は保存なしの判定です。対象曜日は既存 `TARGET_WEEKDAYS`（初期値は全曜日）を使い、実行時の値をメタデータに保存します。

## 判定と保存

M列フラグとI列トリップ番号で候補を分割し、既定では同一路線のサンプル点3点以上の30m以内を通る候補を採用します。第1.5入力はsidecarとCSVのハッシュ、単一サブトリップであることも検証します。

```text
20_第２スクリーニング(ルート)/
└─ 対象路線/
   ├─ 2nd_route_*.csv
   ├─ screening_info.json
   ├─ 15_trip_index.csv
   ├─ gate_master.csv       （第1.5由来）
   └─ gate_master.geojson   （第1.5由来）
```

複数路線に該当したトリップは各路線へ1回ずつ保存します。ファイル名は従来の `2nd_route_連番_路線_曜日_ID...` 形式です。既存CSVがある路線フォルダへの再実行は混在を防ぐため停止します。以前の結果を別の場所へ移動してから再実行してください。

`screening_info.json` は `screening_stage=2_route`、`source_screening_stage=1.5` または `1st_screening`、路線名・元ルート・区域ハッシュ・件数・半径・必要点数・曜日・再帰探索・作成日時・プログラム・`full_trip=true` を保存します。Gateと起終点種別は15の情報を継承します。CSV本体には列を追加しません。

第1直接入力は従来どおり使用できます。メタデータがない入力を第1.5由来とみなすことはありません。

## 後工程と旧形式

- 05ビューアーは平置きCSVと1階層のルート別CSVに対応し、sidecarを除外します。
- 30ルートパフォーマンスはsidecarを除外し、新形式の各CSVを宣言された路線だけへ集計します。同じサブトリップの複数路線へのコピーによる二重集計を防ぎます。
- 50のルートモードは旧平置きも読み込みます。ただし旧 `_plusN` ファイルには全所属路線名がないため「旧形式・ルート所属未確定」として全経路を表示し、正式通過交通率は無効にします。路線別の正式分析には15→20の再実行が必要です。
- 正式な由来情報付き出力は現行 `src/20_route_trip_extractor.py` を使用してください。旧CLIは配布対象外です。

関連: [15エリアスクリーニング](15_area_screening.md)、[50経路分析](50_Path_Analysis.md)、[横断設計・仕様](50_route_path_analysis_design.md)。
