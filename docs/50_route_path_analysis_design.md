# ルート通過交通分析・横断設計

## 実装前調査（2026-09-12）

- main の15_area_screening / 15_UI_area_screening は `15_エリア第1.5スクリーニング/15_area_subtrip_csv` に区域内サブトリップ全体を保存する。設定JSON・summaryはあるがGateと由来契約がない。
- エリア作成は既存15_area_builder、12_polygon_builder系。`12_エリアデータ/15_area.geojson` の analysis_area を採用する。
- 現行20_route_trip_extractor / 20_UIはプロジェクトの10ルートCSVを読み、全トリップを保存するが複数該当ルートを平置き1ファイルへまとめる。
- 50の実体は `src/unreleased/50_Path_Analysis.py` のみ。25mメッシュ、A/B、in/out、Folium、オフライン対応を持つ。新しい重複50エンジンは作らずモードを追加する。
- unreleased/20 の座標定義が逆。現行15・20・21_point_trip_extractor・31_crossroad_trip_performance・50はO=経度/P=緯度。05は自動入替えを持ち、docsの列説明に逆転がある。
- src/commonはUI/news中心。分析契約を追加する。関連BATはruntime/pythonを使用。
- ローカル codex/route-second-screening とmainを関連ソースに限定して比較。旧ブランチ50はオフライン対応前であり、mainの機能を維持する。

## 接続契約

15で作成した起終点種別とGateをsidecarに記録し、20は選択トリップのsidecarと区域ハッシュを継承する。50は由来・区域同一性・全CSVとの対応を検証する。座標形状から由来を推測しない。不明な旧データでは正式通過交通率を無効とする。

Gateは境界起終点を30m以内で決定的に統合。分類と経路訪問セル集計を分離し、道路リンク集計の拡張点とする。CSV本体の列構成と全トリップ保持を維持する。

## 実装・最終報告

1. **調査ファイル**：15_area_screening.py、15_UI_area_screening.py、15_area_builder.html、12_polygon_builder.html、10_UI_route_sampler.py、10_route_sampler.html、現行/未公開20_route_trip_extractor.py、20_UI_route_trip_extractor.py、unreleased/50_Path_Analysis.py、05_trip_viewer.py、unreleased/06_route_mapper_kp.py・10_route_sampler.py、21_point_trip_extractor.py、31_crossroad_trip_performance.py、30_route_performance.py、src/common、offline_leaflet.py、関連BAT・15/20/50/05のdocs、既存tests。src直下の50は存在しないため作成していない。
2. **不整合**：15にGate・由来契約がない。現行20は複数路線該当を平置き1CSVにまとめる。05と未公開20/06の座標が逆。未公開10は定数名と書込み変数が両方逆。20のdocsは未公開CLI・誤った座標・存在しないdocsを参照。Foliumの新しい分離addTo形式を既存オフライン変換が誤認して分析レイヤーを消していた。
3. **座標修正**：O=経度/index14、P=緯度/index15へ統一。05/06の救済入替えは正規データには作用せず、逆転した旧データだけ救済。未公開10は定数と参照の双方を修正し、実際の出力列を維持。15/20/21/31/50の正しかった座標意味は維持。50のNumPy入力にUTF-8 BOM対応を追加。
4. **変更ファイル**：README、docs/05_trip_viewer.md・15_area_screening.md・20_route_trip_extractor.md・50_Path_Analysis.md・本書、src/05_trip_viewer.py・15_area_screening.py・20_route_trip_extractor.py・20_UI_route_trip_extractor.py・30_route_performance.py・offline_leaflet.py、src/unreleased/06_route_mapper_kp.py・10_route_sampler.py・20_route_trip_extractor.py・50_Path_Analysis.py。新設はsrc/common/screening.py・route_path.py・route_path_ui.py、bat/50_UI_route_path_analysis.bat、tests/test_route_path_analysis.py。tests/test_offline_map.pyに描画回帰テストを追加。
5. **フォルダ**：下記構成。15の正式名称と既存サブトリップCSVフォルダを維持し、sidecar CSVは15の親フォルダに分離する。20は1路線1フォルダ、50は選択路線のフォルダへ出力する。
6. **由来仕様**：screening_info.json schema_version=1。15はscreening_stage=1.5、20は2_route＋source_screening_stage。作成日時（UTC）、元データ、パラメータ、区域の相対パスとSHA-256、プログラム名、完了状態、CSV/sidecar/Gateのハッシュを記録。20は元CSVとの照合と全サブトリップ保持を確認し、50も実ファイルと照合する。ハッシュは誤混在検知であり電子署名ではない。
7. **Gate管理**：15で切り出した境界起終点を対象とし、経度・緯度順に走査して30m以内の既存代表点へ統合する。全メンバーが代表点から30m以内となり、長い連鎖による巨大Gate化を避ける。同じ入力なら入力順に依存せず同じ番号。入力集合が増減すると番号は変わり得るため、20/50は同じ15のマスターを引き継ぎ、再クラスタリングしない。
8. **20出力**：ルート名は10のCSV stem。各該当路線へ全トリップを保存し、選択CSVのsidecarを添付する。第1直接入力も維持。再実行は既存CSVとの混在防止のため空の対象フォルダを要求する（前回結果を別の場所へ移動）。
9. **50 UI**：BAT起動→プロジェクト選択→区域などの確認→路線選択→分析実行→結果・主要OD→HTMLを開く。PyQtワーカースレッドで解析し、実行中のUI変更を抑止。路線ごとの件数・由来を表示する。
10. **通過交通率**：Gate→Gate / 選択路線の区域内サブトリップ総数。残りは外内・内外・内内。由来不明はUNKNOWNで保持し、正式率を無効化する。区域不一致、sidecar不一致、異常座標、0件でも正式率は無効。再流入の単位は15の既存統合設定に依存し、車両実台数や母集団全交通量ではない。
11. **OD・CSV・地図**：50_trip_classification.csv、50_path_summary.csv、50_gate_od.csv（方向別件数降順、通過交通内/全交通内割合）、50_mesh.csv（件数・割合・原点・セルサイズ）、50_report.html、50_map.html。表示群は全交通/各分類/各Gate OD。割合はCSVで0～1。各トリップを1回読み、各群へ訪問セル集合を加算する。対象路線・Gate・analysis_areaも地図に表示する。
12. **後方互換**：交差点50の既定CLI、A/B、in/out、既存HTML/BAT名を維持。旧平置きは分類不明として経路表示可能。旧plusNから失われた全所属ルートを復元できないため、旧平置きは「ルート所属未確定」の1選択肢とする。05は1階層の新ルートフォルダにも対応。30はroute_nameによってコピーを所属路線へ限定し二重集計を防ぐ。21/31は変更なし。
13. **検証**：合成データの新フロー11テスト、既存オフライン地図変換への追加1テスト。4分類、複数OD、近接Gate統合・決定性、欠損フォルダ/区域、旧形式、由来/ハッシュ不一致、第1直接入力、15/20全トリップ保持、1回読込、CLI20/50、UI複数路線選択、05座標/救済、30二重集計防止を確認。交差点50はHEAD版とA/B・in/outの4CSVがバイト一致。UI画像で日本語表示を確認。ブラウザーでレポート値とGate OD選択・メッシュ表示を確認。最終テスト結果は下記。
14. **拡張点**：common/route_path.pyのclassifyとvisited_cellsを分離。道路リンク方式を追加する場合は、分類・Gate ODを共用し、visited_cellsに相当する経路バックエンドへ距離・方向・接続性を使うmap matchingを追加する。現状は10m間隔の線分サンプリングによる25mメッシュであり、道路形状の断定ではない。大規模実データの性能測定は未実施。

```text
PROJECT/
├─ 10_ルート(Route)データ/対象路線.csv
├─ 12_エリアデータ/15_area.geojson
├─ 15_エリア第1.5スクリーニング/
│  ├─ screening_info.json
│  ├─ 15_trip_index.csv
│  ├─ gate_master.csv / gate_master.geojson
│  └─ 15_area_subtrip_csv/
│     ├─ area_*.csv
│     └─ screening_info.json  （親sidecar参照）
├─ 20_第２スクリーニング(ルート)/対象路線/
│  ├─ 2nd_route_*.csv
│  ├─ screening_info.json / 15_trip_index.csv
│  └─ gate_master.csv / gate_master.geojson
└─ 50_経路分析/対象路線/
   ├─ 50_report.html / 50_map.html
   └─ 50_trip_classification.csv / 50_path_summary.csv / 50_gate_od.csv / 50_mesh.csv
```

### 検証環境と既存失敗

最終実行：**38テスト中36成功、2件は下記の変更前から存在する失敗**。新規12テストは全成功。src/tests全体のcompileall成功、git diff --check成功。ブラウザーでG01→G02（2トリップ）のラジオ選択とメッシュ切替を確認した。

同梱 `runtime/python/python.exe`（3.11.9）を使用。システムのpythonは2.7なので使用しない。compileallは既存__pycache__のアクセス制限を避け、`-X pycache_prefix=<一時フォルダ>` を指定してsrc/tests全体を検証する。

既存 `test_route_performance_logic` の `test_daily_hourly_summary_and_viewer_can_be_rebuilt_later` と `test_same_trip_is_not_counted_twice_in_same_bucket` は、旧JSONのsummaryキーを前提としてKeyErrorになる。未変更HEADのソース・テストを一時フォルダへ展開して同じ2失敗を再現した。今回この別件のJSON仕様・既存テストは変更していない。
