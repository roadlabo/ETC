# map
## 目的（何をする）
`05_route_mapper_simple.py` / `06_route_mapper_kp.py` が出力する地図 HTML のサンプルとして、レイアウトやスタイルを確認するための参考ファイル。
## 位置づけ（分析フロー上のどこ）
- **ビューア出力例**。処理フローには直接関与しないが、可視化の雰囲気を共有するために保持。
## 入力
- なし（生成済み HTML）。
## 出力
- なし。ブラウザで開いて表示を確認するのみ。
## 実行方法
- `map.html` をブラウザで開く。
## 判定ロジック（重要なものだけ）
- 05/06 の Folium 出力テンプレートに依存。Leaflet/AwesomeMarkers/Bootstrap の CDN を利用。
## できること / できないこと（行政向けの注意）
- できること: 出力 HTML の表示確認、スタイル調整の参考。
- できないこと: データ更新や再描画、分析ロジックへの影響。
## よくあるミス
- ローカルファイルの JS 読み込みをブラウザ設定でブロックしてしまう。
## ドキュメント案内
- [docs/00_concept.md](./00_concept.md)
- [docs/01_pipeline.md](./01_pipeline.md)
- [docs/02_folder_convention.md](./02_folder_convention.md)
## 関連スクリプト
- [docs/05_route_mapper_simple.md](./05_route_mapper_simple.md)
- [docs/06_route_mapper_kp.md](./06_route_mapper_kp.md)
