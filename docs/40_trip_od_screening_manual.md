# 40_trip_od_screening.py 使い方（様式1-3参照ODリスト生成）

## 何をするか
- 第1 / 第2 どちらのCSVでも「行の中身」だけを見て (運行日, 運行ID, トリップ番号) を抽出します。ファイル名や第1/第2の違いは見ません。
- 抽出した運行日で様式1-3 ZIP を絞り込み、ZIP 内 `data.csv` をストリーミングで検索して OD を引き当てます。
- 1行=1トリップの **「様式1-3参照ODリスト」** を出力します（`OD_extractor.py` の入力）。
- **曜日での絞り込みはデフォルトでは行いません。** 曜日列を常に出力し、必要なら後段の `OD_extractor.py` 側でフィルタします（オプションで `TARGET_WEEKDAYS` を指定すれば本スクリプト側でも絞り込み可能）。

## 入力として必要な列だけ
- C列: 運行日（YYYYMMDD）
- D列: 運行ID
- I列: トリップ番号

上記3列さえあれば第1/第2どちらでもOK。ファイル名形式にも依存しません。

## CONFIG の書き方
```python
OUTPUT_DIR = Path(r"C:\\path\\to\\od_output")
# 曜日フィルタ（通常はNone。絞りたい場合のみ指定）
# None か空集合にすれば全曜日を出力する
TARGET_WEEKDAYS = None
DATASETS = [
    {
        "name": "dataset01",
        "input_dir": Path(r"C:\\path\\to\\inputs"),      # 第1/第2混在でもOK
        "style13_dir": Path(r"C:\\path\\to\\style13"),   # 様式1-3 ZIP 群
        "output_od_list_csv": Path("od_list_style1-3.csv"),
    },
]
```
- `DATASETS` を増やせば複数フォルダを一括処理できます。
- 出力先は `OUTPUT_DIR / output_od_list_csv` です。

## フロー概要
1. **Phase1: 入力CSV走査（key収集）**
   - `input_dir` 直下の `*.csv` を先頭からストリーミングで読み、(運行日, 運行ID, トリップ番号) を set に追加。
   - 曜日判定は `運行日` を `YYYYMMDD` として算出し、出力メタとして保持（通常はスキップしない）。`TARGET_WEEKDAYS` を指定した場合のみ除外する。
   - 集めた運行日を `needed_dates` に保持（ZIP絞り込み用）。キーごとに `src_files_count` で何個の入力CSVから現れたかを記録。
2. **Phase2: 様式1-3 ZIP 走査**
   - `style13_dir` 直下の `*.zip` を列挙。ファイル名から8桁の日付を抜き、`needed_dates` に無いZIPは開かない。
   - 日付が読めないZIPは `unknown_date_zip` として最後にまわす（残件がある場合のみ読む）。
   - ZIP内 `data.csv` をストリーミングで読み、キーが一致したらODを記録。全キーが揃えば即終了。
3. **出力: 様式1-3参照ODリスト**
   - 1行=1トリップ。ODが無くても `MISSING_OD` 行として出力（後追い調査用）。

## 出力列
| 列名 | 内容 |
| --- | --- |
| dataset | DATASETS の `name` |
| operation_date | 運行日 (YYYYMMDD) |
| weekday | 日本語1文字の曜日 |
| opid | 運行ID |
| trip_no | トリップ番号（整数） |
| o_lon, o_lat | 起点経度・緯度 |
| d_lon, d_lat | 終点経度・緯度 |
| status | `OK` / `MISSING_OD` |
| src_files_count | 何個の入力CSVにこのキーが含まれていたか |

## よくあるミスと確認ポイント
- **`input_dir` の階層違い**: CSV が直下にある階層を指定する。サブフォルダは読まない。
- **運行日形式の誤り**: C列が `YYYYMMDD` でないと曜日判定が失敗しスキップ扱いになる。
- **ZIP名に日付が無い**: `OUT1-3_20250124.zip` のように8桁日付が無いと `unknown_date_zip` となり、残件がある場合のみ最後に読む。日付付きに直すと高速。
- **対象曜日の勘違い**: `TARGET_WEEKDAYS` を None または空集合にすれば全曜日（デフォルト）。曜日で絞りたい場合のみ指定する。
- **ODが見つからない**: 運行日/運行ID/トリップ番号の一致、該当日のZIPの存在、`data.csv` の列順 (0:運行日,1:運行ID,7:トリップ,11-14:OD) を確認。
