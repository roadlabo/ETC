# ETC Tools

ETC2.0 解析のためのスクリプト集です。  
主に「ETCプローブデータからの運行ID別抽出」と「走行ルートの地図可視化」を行います。

---

## 📦 1. ZIP → 運行IDごとに抽出・整列

**スクリプト**：`src/split_by_opid_streaming.py`

このスクリプトは、複数のZIPに格納された `data.csv` を順に読み込み、  
4列目の **運行ID** ごとに分割して `<時期名>_<運行ID>.csv` として出力します。  
抽出後、各ファイルを **7列目（GPS時刻）** で昇順に並べ替えます。

### 🔧 主な特徴
- ZIPを**解凍せず**にストリーミング処理（メモリ効率◎）
- 出力CSVは**ヘッダーなし**、**時系列順**で整列済み
- コンソールには進捗のみを表示  
  - `Extract: XX%`（ZIP処理進捗）  
  - `Sort: XX%`（整列進捗）
- 大規模データにも対応（外部ソート方式・一時ファイルは自動削除）

### ▶ 実行方法

1. スクリプト先頭の設定を自分の環境に合わせて編集：
```python
   INPUT_DIR = r"D:\...\R7年2月_OUT1-2"
   OUTPUT_DIR = r"D:\...\out(1st)"
   TERM_NAME = "R7_2"
   ZIP_DIGIT_KEYS = ["523357","523347","523450","523440"]
````

2. 実行：

   ```bash
   python src/split_by_opid_streaming.py
   ```
3. 出力：

   ```
   D:\...\out(1st)\R7_2_000000123456.csv
   D:\...\out(1st)\R7_2_000000123789.csv
   ...
   ```

詳細な技術仕様は [`docs/split_by_opid_streaming_report.md`](docs/split_by_opid_streaming_report.md) を参照。

---

## 🗺️ 2. 走行ルートをブラウザで地図表示

**スクリプト**：`src/route_mapper_simple.py`

このツールは、上記で生成された運行IDごとのCSVファイルを地図上に可視化します。

### 🔧 主な特徴

* フォルダ選択 → CSVリスト表示 → 選択するとブラウザにルート描画
* 背景地図はOpenStreetMap（foliumを使用）
* 起点・終点を明確に表示：

  * 起点：白抜き赤丸＋赤字「S」
  * 終点：白抜き青丸＋青字「G」
  * 通過点：黒丸
* 一つのマップウィンドウ内で更新（新しいタブを増やさない）

### ▶ 実行方法

```bash
python src/route_mapper_simple.py
```

1. フォルダ選択ダイアログが開くので、運行ID CSVが入っているフォルダを指定。
2. 右側のリストからCSVを選択すると、ブラウザで走行ルートを描画。

---

## ⚙️ 環境セットアップ

Python **3.10以上** 推奨。
次のコマンドで必要ライブラリを一括インストールします：

```bash
pip install -r requirements.txt
```

**requirements.txt**

```
pandas>=2.0
folium>=0.16
tqdm>=4.66
```

---

## 📁 推奨ディレクトリ構成

```
ETC/
├─ src/
│   ├─ split_by_opid_streaming.py
│   └─ route_mapper_simple.py
│
├─ docs/
│   ├─ split_by_opid_streaming_report.md
│   └─ route_mapper_simple_guide.md
│
├─ requirements.txt
├─ .gitignore
└─ README.md
```

---

## 🧭 作者メモ

* 入力CSVは**ETC2.0様式Ⅰ-2（走行履歴情報）**を想定。
* 抽出後CSVの形式は：

  ```
  [0]不明, [1]日時, [2]..., [3]運行ID, [6]GPS時刻, [12]起終点フラグ, [15]緯度, [16]経度, ...
  ```
* 起終点フラグ：

  * `0`：起点
  * `1`：終点
  * `2`：通過点
  * `3`：その他

---

## 📄 ライセンス

MIT License（予定）
© RoadLabo

```
