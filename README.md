# ETC2.0 Analysis Toolkit（RoadLabo）

ETC2.0 プローブデータを用いて、
- 経路抽出（Route）
- 点ベース抽出（Point）
- 交差点挙動解析（Crossroad）
- OD 抽出・可視化（OD）
を段階的に分析するための Python ツール群です。

地方自治体による EBPM（Evidence-Based Policy Making）を想定し、
「生データ → 意味のある交通指標」への変換に主眼を置いています。

---

## 📦 ディレクトリ構成



ETC/
├─ src/ # 実行スクリプト群（番号順＝処理順）
├─ docs/ # マニュアル・設計思想
├─ README.md
└─ requirements.txt


---

## 🔁 処理フロー概要



01 生データ分割
05-06 経路マッピング
10-12 サンプリング・ポリゴン作成
20-21 Trip 抽出
30-32 パフォーマンス・可視化
40-42 OD 抽出・可視化
71 Path / 流入流出解析


番号順に処理することで、段階的に分析精度を高めます。

---

## 🛠 主なスクリプト

| No | Script | 概要 |
|----|-------|------|
| 01 | split_by_opid_streaming | OPID単位でストリーミング分割 |
| 10 | route_sampler | 経路代表点抽出 |
| 11 | crossroad_sampler | 交差点方向ベクトル生成 |
| 20 | route_trip_extractor | 経路Trip抽出 |
| 21 | point_trip_extractor | 任意地点Trip抽出 |
| 31 | crossroad_trip_performance | 交差点性能指標算出 |
| 40 | trip_od_screening | OD前処理 |
| 42 | OD_extractor | OD最終抽出 |
| 41 | od_heatmap_viewer | ODヒートマップ可視化 |

---

## ⚠ 注意
- 本ツールは ETC2.0 データ構造を前提とします
- 個人情報・車両特定につながる用途での利用は禁止します
- 分析結果の解釈は道路管理者責任で行ってください

---

## ✍ Author
RoadLabo  

3️⃣ docs/ 配下の MD 構成（おすすめ）
docs/
├─ 00_concept.md            # 全体思想・なぜこの構成か
├─ 01_pipeline.md           # 番号体系と処理フロー
├─ 10_route_analysis.md     # route 系
├─ 20_trip_extraction.md    # trip 抽出思想
├─ 30_crossroad.md          # 交差点解析の考え方
├─ 40_od_analysis.md        # OD の限界と解釈
├─ 99_faq.md                # よくある誤解

特に重要なのはこれ👇

00_concept.md

40_od_analysis.md

コンサル・記者・有識者は必ずここを見ます。

4️⃣ 各スクリプト冒頭コメント（統一指針）

例：42_OD_extractor.py

"""
42_OD_extractor.py

目的:
    Trip 単位の移動データから OD（起終点）を抽出する。

位置づけ:
    本スクリプトは ETC2.0 分析パイプラインの最終段に位置し、
    「交通挙動」ではなく「移動構造」を把握するための処理である。

注意:
    本 OD は実際の目的地を保証するものではなく、
    観測条件下での推定結果である。
"""


👉 「できること」より「できないこと」を書く
これは技術士・行政用途では超重要です。
