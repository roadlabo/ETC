"""Tools shown by the native launcher; paths are relative to the installation."""
from dataclasses import dataclass

OVERVIEW_URL = 'https://etc.roadlabo.com/tool-introduction-video/'


@dataclass(frozen=True)
class Tool:
    number: str
    title: str
    batch: str
    logo: str
    purpose: str
    features: str


TOOLS = (
    Tool('01', '第1スクリーニング', '01_1stScr_UI.bat', 'logo_01_1stScr_UI.png',
         'ETC2.0データから対象地域に関係するトリップを抽出し、分析しやすい形に整理します。',
         '運行IDで分割・対象地域の抽出・時系列整理'),
    Tool('02', '存在トリップカウント', '02_UI_existence_trip_counter.bat', 'logo_02_UI_existence_trip_counter.png',
         '時間帯ごとのトリップ数を集計し、ピークとなる30分間と、その時間帯のゾーンODを求めます。',
         '時間帯別集計・ピーク30分の抽出・OD表'),
    Tool('03', '拠点ゾーン推定', '03_UI_base_zone_estimator.bat', 'logo_03_UI_base_zone_estimator.png',
         '夜間の停留位置などから、運行IDごとに車両の拠点となる居住地・事業所のゾーンを推定します。',
         '拠点の推定・運行IDとゾーンの対応表'),
    Tool('05', 'トリップビューア', '05_trip_viewer.bat', 'logo_05_trip_viewer.png',
         '第1・第2スクリーニング後のトリップを地図に表示し、個々の車両の移動を確認します。',
         '走行軌跡の表示・抽出結果の目視確認'),
    Tool('10', 'ルートファイル作成', '10_route_sampler.bat', 'logo_10_route_sampler.png',
         '地図をクリックして対象ルートを描き、後のスクリーニングや性能分析に使うルートを作成します。',
         'ルート点列・指定間隔で補間・CSV保存'),
    Tool('11', '交差点ファイル作成', '11_crossroad_sampler.bat', 'logo_11_crossroad_sampler.png',
         '分析する交差点の中心位置と枝方向を地図で設定し、交差点分析用のファイルを作成します。',
         '中心・枝の設定・交差点CSVと画像'),
    Tool('12', 'ゾーニングデータ作成', '12_polygon_builder.bat', '',
         '地図上でゾーンを作成・編集し、OD集計などに使うゾーニングデータを保存します。',
         'ポリゴン編集・12_ゾーニングデータへ保存'),
    Tool('14', 'エリア・ゲート設定', '14_area_builder.bat', 'logo_14_area_builder.png',
         '正式区域・分析区域と出入口のゲートを地図で指定し、プロジェクトへ直接保存します。',
         '区域作成・手動ゲート・最寄りゲート割当'),
    Tool('15', 'エリア第1.5スクリーニング', '15_UI_area_screening.bat', 'logo_15_area_screening.png',
         '第1スクリーニング後のトリップから分析区域内の移動を切り出し、指定ゲートを割り当てます。',
         '境界点の補間・サブトリップCSV・20へ'),
    Tool('20', '第2スクリーニング（ルート）', '20_UI_route_trip_extractor.bat', 'logo_20_route_trip_extractor.png',
         '設定したルートを通過するトリップを抽出し、ルート分析に使うデータを作成します。',
         'ルート通過判定・曜日/ID付き・15から抽出'),
    Tool('21', '第2スクリーニング（交差点）', '21_UI_point_trip_extractor.bat', 'logo_21_UI_point_trip_extractor.png',
         '作成した交差点ファイルを使い、指定した交差点・地点を通過するトリップを抽出します。',
         '交差点通過の判定・対象トリップの抽出'),
    Tool('30', 'ルートパフォーマンス分析', '30_UI_route_performance.bat', 'logo_30_route_performance.png',
         'ルート第2スクリーニング後のデータから、区間ごとの速度やトリップ数などを集計します。',
         '方向・時間帯・曜日別集計・Excel帳票'),
    Tool('30-2', 'ルート分析ビューア', '30-2_route_performance_viewer.bat', 'logo_30_route_performance.png',
         'ルートパフォーマンスの分析結果を読み込み、地図上で区間ごとの状況を確認します。',
         'ルート分析結果の読込・地図で確認'),
    Tool('31/32', '交差点パフォーマンス分析', '31_32_crossroad_performance_to_report.bat', 'logo_31_32_crossroad_performance_to_report.png',
         'トリップを流入・流出方向別に分類し、方向別のトリップ比や遅れ時間をまとめます。',
         '通常/小交差点の分析・Excelレポート'),
    Tool('33', '流入・流出 枝判定ビューア', '33_branch_check.bat', 'logo_33_branch_check.png',
         '交差点分析の結果を地図で可視化し、流入・流出の枝判定が正しいか目視で検証します。',
         '枝判定・走行軌跡・分析結果の検証'),
    Tool('40', 'OD分析', '40_UI_od_analysis.bat', 'logo_40_od_analysis.png',
         '様式1-3とトリップ両端のODを切り替え、日平均OD表と起終点・ゾーン別の分布を表示します。',
         '2方式のOD・日平均OD表・地図JPEG保存'),
    Tool('50', '経路分析', '50_UI_route_path_analysis.bat', 'logo_50_Path_Analysis.png',
         '対象路線の交通を通過・外内・内外・内内に分類し、指定ゲートと内エリアのODを確認します。',
         '通過交通率・OD表・25mメッシュ経路'),
    Tool('60', 'オフライン地図作成', '60_オフライン地図作成.bat', 'logo_60_offline_map_tiles.png',
         'インターネット接続のない環境でも地図を使えるよう、必要な範囲の背景地図を事前に保存します。',
         '地図上で範囲指定・国土地理院淡色地図'),
)
