# 15_trip_extractor マニュアル（完全版）
最終更新：2025-11-16  
作者：RoadLabo / Maki  
対象読者：初めてスクリプトを使う自治体職員・技術者・研究者

---

# 1. このスクリプトでできること

**15_trip_extractor.py** は、ETC2.0 プローブデータから  
「特定ルートを走行したトリップだけを自動抽出するツール」です。

このスクリプトは以下の処理を行います：

- 入力フォルダ内の大量の ETC2.0 CSV を自動で読み込む
- sample_route.csv（ルート定義ファイル）と照合
- ルート上の走行点をフラグ判定（距離閾値）
- 連続区間ごとに「トリップ」として切り出し
- 車両情報（種別・用途・運行日・曜日・トリップ番号など）を自動取得
- **規則に基づいたファイル名で出力**

処理フローのイメージ：

ETC2.0 CSV群 → ルート判定 → トリップ切り出し → メタ情報抽出 → 出力

---

# 2. 必要なファイル

project_root/
├── 15_trip_extractor.py
├── sample_route.csv ← ルート定義ファイル（必須）
├── input/ ← ETC2.0 生データ（大量）
└── docs/
└── 15_trip_extractor_manual.md



### sample_route.csv（必須）
- 緯度・経度の点が縦に並んでいるだけのシンプルなCSV  
- route_mapper 系スクリプトで生成可能

---

# 3. 出力ファイル名の仕様（必読 / 最新版）

出力は **以下の形式に完全統一**されています。

2nd_{ROUTE_NAME}_{WEEKDAY}__ID{OPID12}_{YYYYMMDD}_{TRIP3}_{E2}_{F2}.csv



## 3.1 各パーツの意味

| パーツ | 説明 | 例 |
|-------|------|----|
| 2nd | 固定文字列 | 2nd |
| ROUTE_NAME | ルートファイル名（拡張子なし） | sample_route |
| WEEKDAY | 曜日（英語3文字）複数なら `SUN-MON` | MON, SAT-SUN |
| IDOPID12 | 運行ID（12桁）に ID を付加 | ID000123456789 |
| YYYYMMDD | 運行日 | 20250201 |
| TRIP3 | トリップ番号（3桁） | t001 |
| E2 | 車種（E＋2桁） | E01 |
| F2 | 用途（F＋2桁） | F02 |

### 📌 曜日が複数混在する場合
トリップ内に複数の運行日が含まれ、曜日が異なるケースは：

SUN-MON
MON-TUE
SAT-SUN

のように **−（ハイフン）で連結**します。

---

# 4. スクリプトの「設定セクション」の丁寧な解説（初心者向け）

15_trip_extractor.py の中央付近に、次のブロックがあります。

## trip_extractor.py 設定セクション（ユーザーが自由に変更）

THRESH_M = 10.0
MIN_HITS = 4
DRY_RUN = False
VERBOSE = False
RECURSIVE = False
AUDIT_MODE = False

TARGET_WEEKDAYS: set[int] = {2, 3, 4, 5, 6}

DEFAULT_SAMPLE_PATH: Path | None = Path("/path/to/sample_route.csv")
DEFAULT_INPUT_DIR: Path | None = Path("/path/to/input_directory")


ここだけ書き換えれば使えます。  
初心者が迷わないよう、1行ずつ解説します。

---

## 4.1 DEFAULT_SAMPLE_PATH（最重要）

**sample_route.csv の場所を指定**します。

例：

DEFAULT_SAMPLE_PATH = Path("D:/ETC/route/sample_route.csv")
ポイント：

/ で書くとトラブルが少ない

拡張子 .csv を忘れない

## 4.2 DEFAULT_INPUT_DIR（入力データのフォルダ）
ETC2.0 の日別データを置いたフォルダを指定します。

DEFAULT_INPUT_DIR = Path("D:/ETC/input")
## 4.3 THRESH_M（距離の閾値）
ルートから 何 m 以内を走行とみなすか。

初心者は 10.0 のままでOK

取りこぼしが多い時は 15～20 に変更

## 4.4 MIN_HITS（一致点数）
ヒット判定（FLAG=1）がこの数以上あればトリップ確定。

通常は 4 のままでOK

## 4.5 DRY_RUN（試し実行）
python
コードをコピーする
DRY_RUN = True
にすると：

何件ヒットするかだけ確認

ファイルは生成されない

## 4.6 VERBOSE（詳細表示）
初心者は False でOK
調査・デバッグ時に True にすると詳細ログが出る

## 4.7 RECURSIVE（サブフォルダ探索）
フォルダが年別に分かれている場合は True

1階層しかない場合は False

## 4.8 AUDIT_MODE（開発者向け）
距離計算回数などの統計を表示するモード
※初心者は使わなくてOK

## 4.9 TARGET_WEEKDAYS（曜日フィルタ）
曜日番号：

数値	曜日
1	SUN
2	MON
3	TUE
4	WED
5	THU
6	FRI
7	SAT

平日のみ：
TARGET_WEEKDAYS = {2,3,4,5,6}

曜日無制限（すべて対象）：
TARGET_WEEKDAYS = set()

# 5. スクリプトの動作フロー（図解）

(1) sample_route.csv 読み込み
     ↓
(2) 緯度経度をラジアン化
     ↓
(3) 入力CSVを1件ずつ読み込む
     ↓
(4) 距離判定 → FLAG=1 or 0
     ↓
(5) FLAG=1 の連続区間をトリップとして抽出
     ↓
(6) メタ情報抽出（OPID / 日付 / 曜日 / E / F / トリップ番号）
     ↓
(7) 仕様に従ったファイル名を生成
     ↓
(8) output/ に保存


# 6. 使い方（Windows例）
python 15_trip_extractor.py --input input --sample sample_route.csv
または、DEFAULT_* を設定しておけばただの：
python 15_trip_extractor.py
でOK。

# 7. 出力例
output/
 ├── 2nd_sample_route_MON__ID000000123456_20250203_t001_E01_F01.csv
 ├── 2nd_sample_route_SAT-SUN__ID000000987654_20250315_t003_E02_F03.csv

# 8. トラブルシューティング
「出力が0件です」
THRESH_M が小さすぎる → 20m にする

sample_route が間違っている可能性

「曜日が混在しています」
正常です

自動で SAT-SUN などに変換されます

「ファイルパスが見つからない」
Path() 内の " " の中を確認

円記号 \ → スラッシュ / にすると安全

# 9. まとめ（初心者向けクイック表）
設定	意味	初心者推奨
DEFAULT_SAMPLE_PATH	ルートファイル	必ず変更
DEFAULT_INPUT_DIR	入力データ	必ず変更
THRESH_M	距離閾値	10.0
MIN_HITS	判定点数	4
DRY_RUN	試し実行	False
VERBOSE	詳細表示	False
RECURSIVE	サブフォルダ探索	必要に応じて
TARGET_WEEKDAYS	曜日	set() or {2–6}

# 10. お問い合わせ

バグ報告・改良要望は GitHub Issue または RoadLabo まで。




