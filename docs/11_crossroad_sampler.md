# 11_crossroad_sampler
## 目的（何をする）
交差点の中心座標と分岐方位をブラウザで指定し、交差点定義 CSV と地図キャプチャ（JPG）を生成する。交差点性能評価やポイント抽出のための基準ファイルを作る。
## 位置づけ（分析フロー上のどこ）
- **設定ファイル作成**フェーズ。
- PDF 用語での「交差点方向ベクトルの作成」に該当し、21/31/71 系の入力となる。
## 入力
- ブラウザ上での手動指定のみ。初期座標は津山市役所付近（緯度 35.069095 / 経度 134.004512）。
- CROSSROAD_ID で生成ファイル名を制御（`crossroad{ID}.html`）。
## 出力
- `crossroads/crossroad{ID}.csv`: 列は `crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg`。branch_name は空欄のままでも可。
- 同名ベースの JPG: 地図キャプチャを html2canvas で保存。
- `crossroad{ID}.html`: 生成された操作用 HTML（Leaflet UI）。
## 実行方法
- CROSSROAD_ID, OUTPUT_DIR, 初期座標をスクリプト先頭で設定。
- コマンド例: `python 11_crossroad_sampler.py`
- ブラウザが開いたら、左クリックで中心→以降のクリックで枝を追加。右クリックで直前の枝を削除。「保存」で CSV + JPG をダウンロード。
## 判定ロジック（重要なものだけ）
- Leaflet 上で中心と枝を保持し、方位角を球面三角法で算出 (`bearing`)。
- 追加順に branch_no を採番し、中心は1件必須。枝が無い状態では保存を拒否。
## できること / できないこと（行政向けの注意）
- できること: 交差点ごとの中心・方位を簡便に定義し、スクリーンショット付きで保存。
- できないこと: 車線別の詳細形状や信号情報の自動取得、道路台帳へのスナップ。作成した CSV は方位の目安に過ぎず、性能算出時の距離閾値は別途設定が必要。
## よくあるミス
- 中心を置かずに枝を追加しようとして保存不可になる。
- CROSSROAD_ID を変え忘れ、別交差点を上書きする。
- ブラウザのポップアップブロックで保存ダイアログが開かない。
## 関連スクリプト
- 後段: [docs/21_point_trip_extractor.md](./21_point_trip_extractor.md), [docs/31_crossroad_trip_performance.md](./31_crossroad_trip_performance.md), [docs/50_Path_Analysis.md](./50_Path_Analysis.md)。
- フロー全体: [docs/01_pipeline.md](./01_pipeline.md)
