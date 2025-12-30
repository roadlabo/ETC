# 40_trip_od_screening.py 使い方（様式1-3参照ODリスト生成）

## これは何をするスクリプトか
- 「第1（運行IDごと）」または「第2（トリップごと）」のトリップCSVを自動判定し、(運行日, 運行ID, トリップ番号) を抽出します。
- 抽出したキーを使って様式1-3 ZIP（ZIP 内 `data.csv`）をストリーミング検索し、起終点座標を引き当てます。
- 1行=1トリップの **「様式1-3参照ODリスト」** を出力します（後段の `OD_extractor.py` でゾーン集計する前段）。

## 入力データの種類
- **第1（運行IDごと）**: 1ファイルに複数トリップを含む。例: `R6_10_000000000124.csv`
- **第2（トリップごと）**: 1ファイル=1トリップ。例: `2nd_route01_MON_ID000000061071_20250124_t001_E02_F01.csv`
- 入力フォルダには第1/第2が混在していてもOK。スクリプトが自動判定します。

## 自動判定ルール
- **ファイル名判定**
  - 第1: `^R\d+_\d{2}_(\d{12})\.csv$`
  - 第2: `^2nd_(?P<route>.+?)_(?P<wd>[A-Z]{3})_ID(?P<opid>\d{12})_(?P<date>\d{8})_t(?P<trip>\d{3})_E\d+_F\d+\.csv$`
- **フォールバック（unknown対策）**
  - 先頭20行を覗き、`YYYYMMDD` らしい運行日や列数などを手掛かりに推定。
  - それでも判定できない場合はスキップし、`unknown file pattern` を警告します。

## フォルダ指定のしかた（CONFIG セクション）
```python
OUTPUT_DIR = Path(r"C:\\path\\to\\od_output")
TARGET_WEEKDAYS = {"火", "水", "木"}
DATASETS = [
    {
        "name": "dataset01",
        "input_dir": Path(r"C:\\path\\to\\inputs"),  # 第1/第2どちらでもOK
        "style13_dir": Path(r"C:\\path\\to\\style13"),  # 様式1-3 ZIP 群
        "output_od_list_csv": Path("od_list_style1-3.csv"),  # OUTPUT_DIR 配下に作成
    },
]
```
- `DATASETS` を増やせば複数フォルダを一括処理できます。
- `TARGET_WEEKDAYS` に含まれない曜日は `SKIP_WEEKDAY` として出力します。
- 出力先は `OUTPUT_DIR / output_od_list_csv` になります。

### フォルダの見え方（例）
```
inputs/
├─ R6_10_000000000124.csv           # 第1
├─ R6_10_000000000125.csv
├─ 2nd_route01_MON_ID000...t001...  # 第2
└─ 2nd_route01_WED_ID000...t002...
style13/
├─ OUT1-3_20250124.zip
└─ OUT1-3_20250125.zip
```

## 出力ファイル仕様
- 1行=1トリップ。OD が見つからない場合も行を出力します（原因調査用）。
- 列定義:

| 列名 | 内容 |
| --- | --- |
| dataset | DATASETS の name |
| source_kind | split1 / trip2 / unknown |
| operation_date | 運行日 (YYYYMMDD) |
| weekday | 日本語1文字の曜日 |
| opid | 運行ID（12桁） |
| trip_no | トリップ番号（数値文字列） |
| route_name | 第2のファイル名から得たルート名。第1は空でも可 |
| o_lon, o_lat | 起点経度・緯度 |
| d_lon, d_lat | 終点経度・緯度 |
| status | OK / MISSING_OD / SKIP_WEEKDAY / UNKNOWN_INPUT など |
| src_file | 元入力CSV名（追跡用） |

## 典型的な実行例
1. CONFIG を編集（上記例のようにパスを書き換える）
2. 実行
   ```bash
   python src/40_trip_od_screening.py
   ```
3. 出力確認
   - `OUTPUT_DIR/od_list_style1-3.csv` に 1行=1トリップのODリストが生成される
   - ログで Phase1 (入力走査) と Phase2 (ZIP走査) の進捗が 1行更新で表示される

## よくあるトラブルと対処
- **ZIPが重い**: スクリプトはストリーミングで読み、`wanted_keys` を取り切ったら早期終了します。ZIP名から日付が取れる場合は `needed_dates` で対象日だけを読むので高速です。
- **曜日が合わない**: `TARGET_WEEKDAYS` を確認。対象外のものは `SKIP_WEEKDAY` として出力されます。
- **unknown pattern と出る**: ファイル名が正規表現と合っているか確認。先頭20行で推定できない場合は処理対象外になります。
- **ODが見つからない (MISSING_OD)**: 
  - `style13_dir` に該当日の ZIP があるか
  - CSV内の `運行日/運行ID/トリップ番号` が一致しているか
  - トリップ番号のゼロ埋め（001 など）が合っているか
- **フォルダ階層の勘違い**: `input_dir` と `style13_dir` はそれぞれ CSV/ZIP が「直下に並ぶ」階層を指定してください。
