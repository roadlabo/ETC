# 01_split_by_opid_streaming（OPID分割・時系列整列）

## このスクリプトで何をしているか（結論）
`01_split_by_opid_streaming.py` は、ETC2.0 の ZIP を順に読み、ZIP内の指定CSV（既定 `data.csv`）を**ストリーミング処理**して、`OPID` ごとのCSVに分割保存します。最後に、必要であれば出力CSVを**タイムスタンプ列で外部ソート**して時系列整列します。

- 巨大データを一括でメモリに載せない設計（逐次読み出し + チャンクソート + マージ）。
- 同じ OPID が複数 ZIP にまたがっていても、`TERM_OPID.csv` に追記集約。

---

## 入力と出力

### 入力
- `--input_dir` 配下の ZIP ファイル（※現実装は**直下のみ**探索。再帰探索ではない）。
- 各 ZIP の中にある `--inner_csv` で指定したファイル名（既定 `data.csv`）。

### 出力
- `--output_dir` に OPID ごとのCSVを生成。
- ファイル名: `TERM_OPID.csv`（例: `R7_2_ABC12345.csv`）

---

## 処理フロー

### 1) ZIP探索（SCAN）
- `input_dir` 直下の `.zip` を列挙。
- `--zip_digit_keys`（カンマ区切り）に含まれる文字列のいずれかが ZIP 名に含まれるものだけを対象化。

### 2) ZIP内CSVの取得
- 対象 ZIP から `--inner_csv` で指定したファイル名を `getinfo()` で取得。
- ZIP 内に該当ファイルが無ければ、その ZIP は missing としてスキップ。

### 3) CSVをストリーミング読み込み
- `TextIOWrapper` + `csv.reader` で 1 行ずつ処理。
- `UnicodeDecodeError` / `csv.Error` は行スキップして継続。

### 4) OPIDで分割して追記
- OPID は **4列目（index 3）固定**で抽出（現実装に `--opid_col` 引数はない）。
- OPID ごとの `TERM_OPID.csv` を開き、行を追記。
  - 初回出現: 新規作成
  - 既存あり: 追記

### 5) 最終整列（SORT）
- `--do_final_sort` が有効なとき、`TERM_*.csv` をソート。
- ソートキー列は `--timestamp_col`（既定 **6列目 index=6**）。
- 外部ソート方式:
  1. `--chunk_rows` 行ずつ読み、各チャンクをソートして一時CSV出力
  2. 一時CSV群を k-way merge（ヒープ）で統合
- 一時ディレクトリは `--temp_sort_dir`（既定 `_sort_tmp`）。完了後に削除。

---

## 主な引数

### 必須（実運用上）
- `--input_dir`
- `--output_dir`
- `--term_name`

### 任意
- `--inner_csv`（既定 `data.csv`）
- `--zip_digit_keys`（例: `523357,523347`）
- `--encoding`（既定 `utf-8`）
- `--delim`（既定 `,`）
- `--do_final_sort` / `--no_final_sort`
- `--timestamp_col`（既定 `6`）
- `--chunk_rows`（既定 `200000`）
- `--temp_sort_dir`（既定 `_sort_tmp`）

---

## 実行例

```bash
python src/01_split_by_opid_streaming.py \
  --input_dir "D:\\ETC\\00_受領データ" \
  --output_dir "D:\\ETC\\01_OPID分割" \
  --term_name "R7_2" \
  --zip_digit_keys "523357,523347" \
  --timestamp_col 6
```

---

## 例外・スキップの扱い
- ZIP内に `inner_csv` がない: その ZIP はスキップして継続。
- CSV 行で decode/csv エラー: その行のみスキップして継続。
- 出力先ディレクトリがない: 自動作成。

---

## 補足（タイムスタンプ列について）
- 現実装の既定値は `--timestamp_col=6`（0始まり）です。
- 受領CSV仕様が変わる可能性があるなら、ドキュメントには「通常は6列目（index 6）」のように書くのが安全です。
