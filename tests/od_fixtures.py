import json
from common import od_analysis as od
from common import screening


def area_fixture(root):
    area_folder = root / '14_エリアデータ'; area_folder.mkdir()
    source = root / '15_第1.5スクリーニング'; source.mkdir()
    data = source / '15_area_subtrip_csv'; data.mkdir()
    gates = [dict(gate_id='G01', name='西ゲート', lon=133.98, lat=35.07),
             dict(gate_id='G02', name='東ゲート', lon=134.04, lat=35.07)]
    features = screening.gate_geojson(gates)['features']
    for role, delta in [('official_area', .015), ('analysis_area', .03)]:
        points = [[134.01-delta,35.04],[134.01+delta,35.04],[134.01+delta,35.10],[134.01-delta,35.10],[134.01-delta,35.04]]
        features.append(dict(type='Feature', properties={'area14_role':role}, geometry=dict(type='Polygon',coordinates=[points])))
    area = area_folder / '14_area.geojson'
    area.write_text(json.dumps(dict(type='FeatureCollection', features=features)), encoding='utf-8')
    entries = []
    for i, (start, end) in enumerate([('INSIDE','INSIDE'),('INSIDE','GATE'),('GATE','INSIDE'),('GATE','GATE')]):
        first = [''] * 16; first[2:4] = ['20260901', str(i)]; first[8]='1'
        last = first.copy()
        first[14:16] = ['133.98' if start == 'GATE' else '134.00', '35.07']
        last[14:16] = ['134.04' if end == 'GATE' else '134.02', '35.07']
        path = data / f'trip{i}.csv'; od.write_csv(path, [], [first,last])
        entries.append(dict(trip_id=str(i), source_file=path.name, start_type=start, end_type=end,
                            start_lon=first[14], start_lat=first[15], end_lon=last[14], end_lat=last[15], sha256=screening.digest(path)))
    screening.write_gate_contract(source, entries, dict(screening_stage='1.5', area_file='14_エリアデータ/14_area.geojson',
                                                       area_sha256=screening.digest(area)), gates)
    screening.write_info(data, {**screening.read_info(source), 'contract_parent':True})
    zones = [dict(name='A',points=[(133.99,35.05),(134.01,35.05),(134.01,35.09),(133.99,35.09)]),
             dict(name='B',points=[(134.01,35.05),(134.03,35.05),(134.03,35.09),(134.01,35.09)])]
    return source, data, zones
