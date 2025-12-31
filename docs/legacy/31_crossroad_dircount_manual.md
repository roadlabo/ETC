※この文書は旧版です。最新の手順は docs 配下の同名スクリプトMDを参照してください。

# 31_crossroad_extractor.py 説明書（Markdown版）

最終更新：2025-02-17
作者：RoadLabo / Maki  
対象読者：交差点別の交通量・方向分析を行う担当者

---

## 1. 概要

**31_crossroad_extractor.py** は、ETC2.0 トリップデータから交差点通過の「進入方向」と「退出方向」を判定し、通過1回につき1行のレコードとして出力する解析スクリプトです。11_crossroad_sampler.py で作成した交差点情報（中心点・道路方向）をもとに、各トリップがどの方向から入り、どの方向へ出たかを推定します。結果は交通量調査、方向別 OD 集計、右折直進左折比率などの後続分析に利用できます。

処理の全体像：

```
ETC2.0トリップCSV ──────┐
crossroad 定義CSV群 ──┼──▶ 31_crossroad_extractor.py ───▶ crossroad_extracted.csv
crossroad 定義JPG群 ──┘        （交差点IDは CSV の crossroad_id 列で管理）
```

---

## 2. 入出力の全体像

- **入力1：トリップデータ** — 15_trip_extractor.py が出力する形式に準拠した CSV 群。
- **入力2：交差点情報** — 11_crossroad_sampler.py が生成した交差点定義 CSV（ファイル名は任意）。交差点IDは CSV 内の **crossroad_id 列** で管理する。
- **参考情報：交差点スクリーンショット** — 11_crossroad_sampler.py が CSV 保存時に同名ベースで出力する JPG。31_crossroad_extractor.py では直接利用しないが、資料化や目視確認用に保管しておく。
- **出力：crossroad_extracted.csv** — 交差点通過ごとに1行を記録。必要に応じて交差点ID列を付与する。

---

## 3. 出力ファイルの仕様（最重要）

### 3.1 crossroad_extracted.csv の列構成（A〜O列＋必要に応じてP列）

| 列 | 内容 |
| --- | --- |
| A | スクリーニング区分 |
| B | ルート名 |
| C | 曜日名 |
| D | 運行ID |
| E | 運行日 |
| F | トリップ番号 |
| G | 自動車の種別 |
| H | 自動車の用途 |
| I | 進入方向 branch_no |
| J | 退出方向 branch_no |
| K | 2Point前 時刻 |
| L | 交差点中心通過 時刻 |
| M | 2Point後 時刻 |
| N | 2Point前 → 2Point後 の距離（道なり） |
| O | 速度（距離 / 時間差） |
| P | 交差点ID（crossroad_id, 任意列。複数交差点をまとめて出力する場合に付与） |

**特徴**

- 1トリップに複数回 HIT すれば複数行出力。
- 時刻はトリップログから直接抽出。
- 距離は「累積距離差」または「区間距離の和」。
- 速度は km/h または m/s（実装側で統一）。

---

## 4. 必要ファイル

### 4.1 交差点情報（前処理）
- 交差点定義 CSV：ファイル名は任意（例：`tsuyama_station_north.csv`、`route53_cross1.csv` など）。
- 交差点定義 JPG：CSV と同じベース名のスクリーンショット画像（任意）。

交差点CSVの列仕様（11_crossroad_sampler.py と共通）：

| 列名 | 必須/任意 | 内容 |
| --- | --- | --- |
| crossroad_id | 必須 | 交差点ID。ファイル名ではなくこの列を正とする。 |
| center_lon | 必須 | 交差点中心の経度（WGS84） |
| center_lat | 必須 | 交差点中心の緯度（WGS84） |
| branch_no | 必須 | 枝番号（1,2,3,...） |
| dir_deg | 必須 | 方位角（北=0°, 東=90°, 時計回りで 0〜360） |
| branch_name | 任意 | 枝のラベル。欠損時は空文字として扱う。 |

※ 中心ラベル "Centre" は CSV には含まれません。中心点情報は `center_lon` / `center_lat` から取得します。

### 4.2 トリップデータ
- 15_trip_extractor.py が生成する形式のトリップ CSV。最低限必要な列：緯度（lat）／経度（lon）／タイムスタンプ／累積距離（または座標から距離計算可能なデータ）／スクリーニング区分／ルート名／曜日名／運行ID／運行日／トリップ番号／自動車種別／用途。

---

## 5. 実行方法

```bash
python 31_crossroad_extractor.py \
    --input ./trip_data/trip_*.csv \
    --crossroad-dir ./crossroads \
    --output ./output/crossroad_extracted.csv
```

### 主なオプション

| オプション | 内容 |
| --- | --- |
| --input | トリップ CSV ファイル（複数指定可） |
| --crossroad-dir | 交差点定義 CSV が格納されたフォルダ（ファイル名は任意、*.csv を全て読み込み） |
| --output | 出力 CSV ファイル名 |
| --verbose | 詳細ログ（任意） |

---

## 6. 交差点通過判定ロジック（詳細）

### 6-1. 20m以内の点をチェック（最優先）
1. 交差点中心点とトリップ各ポイントの距離を計算し、20m以内の点があれば HIT。  
2. その集合のうち最も近い点を **交差点中心通過点（`idx_center`）** とする。

### 6-2. 直進で点が離れている場合（線分距離評価）
ETC2.0 は直進区間だと 200m に 1ポイントしかないケースがあるため、補助判定を行います。

1. 中心点から **200m以内のポイント** のみ抽出（高速化）。
2. 抽出範囲内の線分（`p[i]→p[i+1]`）について、線分と中心点の最近接距離が 20m 以内かを判定。
3. 20m 以内の線分があれば HIT。線分両端点のうち中心に近い方を `idx_center` とする。

### 6-3. 進入方向・退出方向の判定
1. **前後ポイントの抽出**  
   - `idx_before = idx_center - 2`（なければ -1 や 0 を利用）  
   - `idx_after  = idx_center + 2`（なければ +1 や末尾を利用）
2. **方位角を計算**  
   - 進入：`P_center - P_before`  
   - 退出：`P_after - P_center`  
   - 各ベクトルの方位角（北=0°, 東=90°）を求める。
3. **方向との一致判定**  
   - crossroadXXX.csv の `direction` に対し角度差が最も小さい `branch_no` を選択。  
   - 進入方向 → I列、退出方向 → J列に出力。

---

## 7. 時刻・距離・速度の計算

### 7-1. 時刻
- K：`timestamp[idx_before]`
- L：`timestamp[idx_center]`
- M：`timestamp[idx_after]`

### 7-2. 道なり距離
- 累積距離がある場合：`distance = cumdist[idx_after] - cumdist[idx_before]`  
- 累積距離がない場合：`distance = sum(haversine(p[i], p[i+1]) for i in range(idx_before, idx_after))`

### 7-3. 速度
- `速度 = 距離 / (M - K)`
- km/h にする場合：`speed = distance_m / 秒 * 3.6`

---

## 8. 複数回通過の処理

- 1トリップ中に同じ交差点を複数回通過した場合、通過ごとに1行出力。
- GPSノイズ対策として、中心通過時間が近すぎるもの（例：30秒以内）は1回にまとめるデバウンス処理を入れるとよい（任意仕様）。

---

## 9. パフォーマンス最適化

1. **バウンディングボックス判定** — 交差点中心点を中心に半径500mなどの簡易チェックを行い、明らかに遠いトリップはスキップ。
2. **線分距離計算を限定** — 中心から200m以内の線分のみ計算し、高速化。
3. **ログの軽量化** — print の多用を避け、進捗は1行更新方式や進捗バー風表示にする。

---

## 10. 進捗表示例

```
[31_crossroad_extractor] Start. trip_files=1289, crossroads=12
[31_crossroad_extractor] (85/1289) file=trip_00085.csv, hits=2
[31_crossroad_extractor] (123/1289) file=trip_00123.csv, hits=0
...
[31_crossroad_extractor] Done. total_hits=3542, time=01:23:09
```

---

## 11. エラー・トラブルシューティング

- **HITしない**  
  - 交差点中心から20m以内の点が存在しない。  
  - 線分距離判定でも20m以内が無い。  
  - sampler 側の方向指定ミスの可能性あり。
- **抽出結果の方向がおかしい**  
  - crossroadXXX.csv の方向クリックが逆になっている。  
  - トリップの座標精度が低い場合も想定。
- **計算が遅い**  
  - バウンディングボックス距離を調整（例：500m→300m）。  
  - print を減らす。  
  - 線分距離計算を Shapely に変えると高速になる場合もある。

---

## 12. まとめ

1. 11_crossroad_sampler.py で交差点方向を登録。
2. 本スクリプトでトリップを解析。
3. 出力 CSV を使って交差点交通量表を作成。右折／左折／直進の分析に活用可能。

地方交通計画や交差点改良・シミュレーションに向けた基礎データを効率的に作成できるのが本スクリプトの強みです。
