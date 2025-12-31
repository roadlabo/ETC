# 01_pipeline

分析フロー（★分析フロー.pdf）に沿った処理順の全体像です。**src のファイル名と番号が正**であり、ここに記載のスクリプト以外を前提にしません。各段階は「第1スクリーニング → 第2スクリーニング → ビューア → 設定ファイル作成 → OD → 経路分析」の語彙で統一します。

## フロー概要
1. **前処理（01）**: ZIP 群をストリーミングし、運行 ID ごとに分割・時系列ソート。
2. **設定ファイル作成・ビューア（05–12）**: ルート定義、交差点方位、ゾーンポリゴンを GUI/ブラウザで準備。生成したファイルは後段の抽出・可視化で再利用。
3. **第1/第2スクリーニング（20–21）**: ルート・交差点近傍のトリップを閾値付きで抽出し、様式1-2 に準拠した CSV を得る。
4. **性能評価・ビューア（30–32）**: 抽出済みトリップを用い、速度・滞留などの性能指標を算出し、レポート出力・GUI 確認を行う。
5. **OD 系（40–42）**: 様式1-2 から様式1-3参照 OD リストを生成し、ゾーニングを適用して OD マトリクスやヒートマップを作る。
6. **経路分析（71）**: 単路ポイントを中心に 500m 四方〜20m メッシュで方向別可視化・集計を行い、流入流出の傾向を確認する。

## 各段階のスクリプト
- **前処理**
  - [01_split_by_opid_streaming.py](./01_split_by_opid_streaming.md): ZIP を OPID 単位に分割し、時間ソートをオプション実行。
- **設定ファイル作成 / ビューア**
  - [05_route_mapper_simple.py](./05_route_mapper_simple.md): トリップ・ルート CSV を一覧選択して Folium で表示。
  - [06_route_mapper_kp.py](./06_route_mapper_kp.md): 05 の KP ラベル付き版。
  - [10_route_sampler.py](./10_route_sampler.md): Web UI でルート定義 CSV を作成（第1/第2共用）。
  - [11_crossroad_sampler.py](./11_crossroad_sampler.md): 交差点中心＋分岐方位を CSV/画像保存。
  - [12_polygon_builder.html](./12_polygon_builder.md): ゾーンポリゴンをブラウザで編集し CSV 保存。
- **第1/第2スクリーニング**
  - [20_route_trip_extractor.py](./20_route_trip_extractor.md): サンプルルートへの近接判定でトリップ抽出、`2nd_*.csv` を出力。
  - [21_point_trip_extractor.py](./21_point_trip_extractor.md): 交差点中心への距離で抽出、`2nd_point_*.csv` などを出力。
- **性能評価・ビューア**
  - [30_build_performance.py](./30_build_performance.md): ルート沿いの速度・通過性能を KPI として構築。
  - [31_crossroad_trip_performance.py](./31_crossroad_trip_performance.md): 交差点通過性能（流入/流出/滞留）を算出。
  - [32_crossroad_viewer.py](./32_crossroad_viewer.md): 31 の出力を GUI で確認し、画像・Excel へエクスポート。
- **OD 系**
  - [40_trip_od_screening.py](./40_trip_od_screening.md): 様式1-2 から様式1-3参照 OD リストを生成（第2スクリーニングの後段）。
  - [41_od_heatmap_viewer.py](./41_od_heatmap_viewer.md): OD リストを Origin/Destination のヒートマップに可視化。
  - [42_OD_extractor.py](./42_OD_extractor.md): ゾーン割当、OD マトリクス、発生集中量を一括生成。
- **経路分析**
  - [71_Path_Analysis.py](./71_Path_Analysis.md): 単路ポイントに対する方向別メッシュ集計と可視化。
- **ビューア出力例**
  - [map.html](./map.md): 05/06 が生成する HTML のサンプルとして扱う。

## PDF で強調する要点の反映
- 対象エリアを含む **2 次メッシュを全検索**したうえで運行 ID ごとに集計し、**時系列ソート**後に段階処理。
- 「第1スクリーニング → 第2スクリーニング → ビューア → 設定ファイル作成」という流れを維持し、設定ファイルは 05–12 系で再利用。
- **様式1-3参照 OD リスト → OD マトリクス → OD ヒートマップ**を 40–42 系で連結。
- 経路分析では **500m 四方の可視化、20m メッシュ集計、方向別ヒートマップ**を実施し、規模感は「11か月・約5TB・12府県」を目安に記載。

## 整合性チェック（運用時の確認観点）
- ここに列挙したスクリプト名のみが src/ に存在することを確認（追加・削除時は必ず更新）。
- docs/ 配下は src と同じ basename の MD を1つずつ持つこと（例外: 00_concept.md / 01_pipeline.md / 99_faq.md / legacy）。
- 参照しているファイル・リンクがすべて存在することを `docs/_generated_src_inventory.md` と突き合わせて確認。
