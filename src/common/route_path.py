"""Route classification independent of the path (mesh / future road-link) backend."""
import csv
import hashlib
import html
import io
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from common.screening import (AREA_FOLDER, AREA_FILE, ROUTE_FOLDER, SECOND_FOLDER, INDEX_FILE,
                              read_info, read_index, read_lon_lat, digest, write_csv, area_role,
                              read_manual_gates, assign_nearest_gates, gate_geojson)
from common.route_od import load_zones, endpoint_label, matrix_data, matrix_html

LABELS = {'ALL': '全交通', 'THROUGH': '通過交通',
          'EXTERNAL_TO_INTERNAL': '外内交通', 'INTERNAL_TO_EXTERNAL': '内外交通',
          'INTERNAL': '内内交通', 'UNKNOWN': '分類不明'}

@dataclass
class RouteTarget:
    name: str
    folder: Path
    files: list
    info: dict
    warning: str = ''

def scan_project(project):
    project = Path(project)
    area_dir = project / AREA_FOLDER
    if not area_dir.is_dir():
        raise ValueError(f'{AREA_FOLDER} がありません。プロジェクト内に作成し、14_area_builder.batで区域を保存してください。')
    area_path = area_dir / AREA_FILE
    if not area_path.is_file():
        raise ValueError(f'{AREA_FOLDER}/{AREA_FILE} がありません。14_area_builder.batで区域を作成・保存してください。')
    area = json.loads(area_path.read_text(encoding='utf-8-sig'))
    features = [f for f in area.get('features', [])
                if area_role(f.get('properties') or {}) == 'analysis_area']
    if not features:
        raise ValueError('14_area.geojson に analysis_area がありません。14_area_builder.batで分析区域を作成してください。')
    for f in features:
        g = f.get('geometry') or {}
        if g.get('type') not in ('Polygon', 'MultiPolygon') or not g.get('coordinates'):
            raise ValueError('analysis_area のポリゴンが不正です')
    root = project / SECOND_FOLDER
    if not root.is_dir():
        raise ValueError(f'{SECOND_FOLDER} がありません。15のエリアスクリーニング後、20_UI_route_trip_extractor.batで作成してください。')
    targets = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        files = sorted(p for p in folder.glob('*.csv') if p.name not in (INDEX_FILE, 'gate_master.csv'))
        info = read_info(folder)
        if files or info:
            reason = provenance_warning(info, area_path)
            targets.append(RouteTarget(info.get('route_name', folder.name), folder, files, info, reason))
    flat = sorted(p for p in root.glob('*.csv') if p.name not in (INDEX_FILE, 'gate_master.csv'))
    if flat:
        # Older plusN filenames do not contain every matched route. Do not invent membership.
        targets.append(RouteTarget('旧形式（平置き・ルート所属未確定）', root, flat, {},
                                   '第1.5スクリーニング由来を確認できません。正式通過交通率は無効です'))
    if not targets:
        raise ValueError('第2スクリーニングの路線データがありません')
    read_manual_gates(area)
    load_zones(project)
    return {'type': 'FeatureCollection', 'features': features}, targets

def provenance_warning(info, area_path):
    if str(info.get('source_screening_stage')) != '1.5':
        return '第1.5スクリーニング由来を確認できません。正式通過交通率は無効です'
    if info.get('screening_stage') != '2_route' or info.get('status') != 'complete' or not info.get('full_trip'):
        return '第2スクリーニングの完了・全トリップ保持を確認できません'
    if info.get('area_role') != 'analysis_area' or info.get('area_sha256') != digest(area_path):
        return '第1.5で使用したanalysis_areaと現在の区域が一致しません'
    return ''

def classify(record):
    pair = (record.get('start_type'), record.get('end_type'))
    return {('GATE', 'GATE'): 'THROUGH', ('GATE', 'INSIDE'): 'EXTERNAL_TO_INTERNAL',
            ('INSIDE', 'GATE'): 'INTERNAL_TO_EXTERNAL', ('INSIDE', 'INSIDE'): 'INTERNAL'}.get(pair, 'UNKNOWN')

def visited_cells(points, engine, lon0, lat0):
    """Reuse 50 projection and segment sampling, without its intersection-only 2km crop."""
    import numpy as np
    arr = np.asarray(points)
    x, y = engine.lonlat_to_xy(arr[:, 0], arr[:, 1], lon0, lat0)
    xy = np.column_stack((x, y))
    cells = {(math.floor(a / engine.CELL_SIZE_M), math.floor(b / engine.CELL_SIZE_M)) for a, b in xy}
    for a, b in zip(xy[:-1], xy[1:]):
        for p in engine._sample_segment(a, b, engine.SAMPLE_STEP_M):
            cells.add((math.floor(p[0] / engine.CELL_SIZE_M), math.floor(p[1] / engine.CELL_SIZE_M)))
    return cells

def analyze(project, target, engine, progress=None):
    project = Path(project)
    area, fresh = scan_project(project)
    zones = load_zones(project)
    target = next(t for t in fresh if t.folder == target.folder)
    records = read_index(target.folder)
    warnings = [target.warning] if target.warning else []
    for filename, key in [(INDEX_FILE, 'trip_index_sha256'), ('gate_master.geojson', 'gate_master_sha256')]:
        p = target.folder / filename
        if not target.warning and (not p.exists() or target.info.get(key) != digest(p)):
            warnings.append(f'{filename}: 第2スクリーニング時の情報と一致しません')
    if not warnings and (set(records) != {p.name for p in target.files} or target.info.get('trip_count') != len(target.files)):
        warnings.append('CSV件数とトリップsidecarが一致しません')
    gates_path = target.folder / 'gate_master.geojson'
    gates = json.loads(gates_path.read_text(encoding='utf-8-sig')) if gates_path.exists() else {'type': 'FeatureCollection', 'features': []}
    gate_ids = {f['properties']['gate_id'] for f in gates['features']}
    manual_gates = read_manual_gates(json.loads((project / AREA_FOLDER / AREA_FILE).read_text(encoding='utf-8-sig')))
    # Use the operator's 14 gate positions and IDs for every route.
    regrouped = {}
    selected_names = {p.name for p in target.files}
    for name, source in records.items():
        if name not in selected_names:
            continue
        row = dict(source)
        try:
            if not all(row.get(f'{s}_type') in ('GATE', 'INSIDE') and
                       (row[f'{s}_type'] != 'GATE' or row.get(f'{s}_gate_id') in gate_ids) and
                       math.isfinite(float(row[f'{s}_lon'])) and math.isfinite(float(row[f'{s}_lat'])) and
                       -180 <= float(row[f'{s}_lon']) <= 180 and -90 <= float(row[f'{s}_lat']) <= 90
                       for s in ('start', 'end')):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        regrouped[name] = row
    assign_nearest_gates(list(regrouped.values()), manual_gates)
    gates = gate_geojson(manual_gates)
    first_geom = area['features'][0]['geometry']
    ring = first_geom['coordinates'][0] if first_geom['type'] == 'Polygon' else first_geom['coordinates'][0][0]
    lon0, lat0 = ring[0][:2]
    counts = Counter({k: 0 for k in LABELS})
    meshes = defaultdict(Counter)
    classified = []
    od_counts = Counter()
    for i, path in enumerate(target.files):
        raw = path.read_bytes()  # exactly once per trip: hash and coordinates share the buffer
        points = []
        invalid = 0
        for row in csv.reader(io.StringIO(raw.decode('utf-8-sig'))):
            p = read_lon_lat(row)
            if p is None:
                invalid += 1
            else:
                points.append(p)
        record = dict(records.get(path.name, {}))
        valid = path.name in regrouped and record.get('sha256') == hashlib.sha256(raw).hexdigest()
        for side in ('start', 'end'):
            kind = record.get(f'{side}_type')
            valid = valid and (kind == 'INSIDE' or (kind == 'GATE' and record.get(f'{side}_gate_id') in gate_ids))
        if not valid and not target.warning:
            warnings.append(f'{path.name}: Gate/sidecar/CSVの対応が不正です')
        od_class = classify(record) if valid and not target.warning else 'UNKNOWN'
        if od_class != 'UNKNOWN':
            record = dict(regrouped[path.name])
            record['origin'] = endpoint_label(record, 'start', zones)
            record['destination'] = endpoint_label(record, 'end', zones)
        else:
            record.update(origin='分類不明', destination='分類不明')
        if invalid or len(points) < 2:
            warnings.append(f'{path.name}: 不正座標または点数不足。経路表示から除外')
        group = f"{record.get('start_gate_id')}_{record.get('end_gate_id')}" if od_class == 'THROUGH' else od_class
        counts['ALL'] += 1
        counts[od_class] += 1
        groups = ['ALL', od_class]
        if od_class == 'THROUGH':
            od_counts[(record['start_gate_id'], record['end_gate_id'])] += 1
            counts[group] += 1
            groups.append(group)
        if not invalid and len(points) >= 2:
            cells = visited_cells(points, engine, lon0, lat0)
            for g in groups:
                meshes[g].update(cells)
        classified.append({**record, 'trip_id': record.get('trip_id', path.stem), 'source_file': path.name,
                           'route_name': target.name, 'od_class': od_class, 'destination_group': group})
        if progress:
            progress(i + 1, len(target.files))
    official = not warnings and counts['ALL'] > 0
    out = project / '50_経路分析' / target.folder.name
    if target.folder == project / SECOND_FOLDER:
        out = project / '50_経路分析' / 'legacy_flat'
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / '50_trip_classification.csv', ['trip_id', 'source_file', 'route_name', 'start_type', 'start_gate_id', 'end_type', 'end_gate_id', 'start_lon', 'start_lat', 'end_lon', 'end_lat', 'origin', 'destination', 'od_class', 'destination_group'], classified)
    labels, full_od = matrix_data(classified)
    with (out / '50_od_matrix.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['O / D', *labels, '合計'])
        for a in labels:
            writer.writerow([a, *(full_od[a, b] for b in labels), sum(full_od[a, b] for b in labels)])
        writer.writerow(['合計', *(sum(full_od[a, b] for a in labels) for b in labels), sum(full_od.values())])
    (out / '50_gate_master.geojson').write_text(json.dumps(gates, ensure_ascii=False), encoding='utf-8')
    total = counts['ALL']
    summary = [{'route_name': target.name, 'od_class': k, 'start_gate': '', 'end_gate': '',
                'trip_count': counts[k], 'share_total': counts[k]/total if total else 0,
                'share_within_class': 1 if counts[k] else 0, 'official': official} for k in LABELS]
    ranking = []
    for (a, b), n in sorted(od_counts.items(), key=lambda item: (-item[1], item[0])):
        ranking.append({'route_name': target.name, 'od_class': 'THROUGH', 'start_gate': a, 'end_gate': b,
                        'trip_count': n, 'share_total': n/total, 'share_within_class': n/counts['THROUGH'], 'official': official})
    fields = ['route_name', 'od_class', 'start_gate', 'end_gate', 'trip_count', 'share_total', 'share_within_class', 'official']
    write_csv(out / '50_path_summary.csv', fields, summary + ranking)
    write_csv(out / '50_gate_od.csv', fields, ranking)
    mesh_rows = ({'group': g, 'cell_x': x, 'cell_y': y, 'trip_count': n, 'share': n/counts[g],
                  'origin_lon': lon0, 'origin_lat': lat0, 'cell_size_m': engine.CELL_SIZE_M}
                 for g, cells in meshes.items() for (x, y), n in sorted(cells.items()))
    write_csv(out / '50_mesh.csv', ['group', 'cell_x', 'cell_y', 'trip_count', 'share', 'origin_lon', 'origin_lat', 'cell_size_m'], mesh_rows)
    route_points = []
    route_path = project / ROUTE_FOLDER / (target.name + '.csv')
    if route_path.parent == project / ROUTE_FOLDER and route_path.is_file():
        with route_path.open(encoding='utf-8-sig', newline='') as f:
            route_points = [p for row in csv.reader(f) if (p := read_lon_lat(row)) is not None]
    render_report(out, target, area, gates, counts, meshes, ranking, warnings, official, lon0, lat0, engine, route_points, zones, labels, full_od)
    return {'output_dir': str(out), 'report': str(out / '50_report.html'), 'counts': dict(counts),
            'official': official, 'through_rate': counts['THROUGH']/total if official else None,
            'ranking': ranking, 'warnings': sorted(set(warnings))}

def render_report(out, target, area, gates, counts, meshes, ranking, warnings, official, lon0, lat0, engine, route_points, zones, labels, full_od):
    import folium
    from offline_leaflet import apply_offline_tile_support
    m = folium.Map(location=[lat0, lon0], zoom_start=14, tiles=None)
    # This report uses only core Leaflet; embed it through the existing helper.
    m.default_js = []
    m.default_css = []
    # All analytical groups are mutually exclusive base layers; background stays visible.
    folium.TileLayer('https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png', attr='国土地理院', overlay=True, control=False).add_to(m)
    folium.GeoJson(area, name='analysis_area', style_function=lambda _: {'fillOpacity': 0.02, 'color': '#333', 'weight': 2}).add_to(m)
    zone_layer = folium.FeatureGroup(name='内エリア（12）')
    for zone in zones:
        folium.Polygon([[y, x] for x, y in zone['points']], color='#7356a6', weight=2,
                       fill_opacity=0.04, tooltip=html.escape(zone['name'])).add_to(zone_layer)
    zone_layer.add_to(m)
    if len(route_points) >= 2:
        route_layer = folium.FeatureGroup(name='対象路線')
        folium.PolyLine([[lat, lon] for lon, lat in route_points], color='#111', weight=5,
                        tooltip=html.escape(target.name)).add_to(route_layer)
        route_layer.add_to(m)
    if gates['features']:
        gate_layer = folium.FeatureGroup(name='14で指定したゲート')
        for gate in gates['features']:
            lon, lat = gate['geometry']['coordinates']
            folium.CircleMarker([lat, lon], radius=5, color='#111', fill=True, fill_opacity=1,
                                tooltip=folium.Tooltip(html.escape(gate['properties']['gate_id']),
                                                      permanent=True, direction='top')).add_to(gate_layer)
        gate_layer.add_to(m)
    k = math.pi/180 * 6371000
    for group in list(LABELS) + [f"{r['start_gate']}_{r['end_gate']}" for r in ranking]:
        layer = folium.FeatureGroup(name=f'{LABELS.get(group, group)} ({counts[group]:,})', overlay=False, show=group == 'ALL')
        for (x, y), n in meshes[group].items():
            size = engine.CELL_SIZE_M
            bounds = [[lat0 + y*size/k, lon0 + x*size/(k*math.cos(math.radians(lat0)))],
                      [lat0 + (y+1)*size/k, lon0 + (x+1)*size/(k*math.cos(math.radians(lat0)))]]
            share = n/counts[group]*100
            style = engine.value_to_style(share, 100)
            folium.Rectangle(bounds, tooltip=f'{n:,} トリップ / 対象交通の {share:.1f}%', **style).add_to(layer)
        layer.add_to(m)
    engine._add_palette_legend(m)
    m.fit_bounds(folium.GeoJson(area).get_bounds())
    folium.LayerControl(collapsed=False).add_to(m)
    (out / '50_map.html').write_text(apply_offline_tile_support(m.get_root().render()), encoding='utf-8')
    escape = html.escape
    total = counts['ALL']
    rate = f"{counts['THROUGH']/total:.1%}" if official else '正式値無効'
    rows = ''.join(f'<tr><td>{label}</td><td>{counts[key]:,}</td><td>{counts[key]/total:.1%}</td></tr>' if total else f'<tr><td>{label}</td><td>0</td><td>—</td></tr>' for key, label in LABELS.items())
    od = ''.join(f"<tr><td>{escape(r['start_gate'])} → {escape(r['end_gate'])}</td><td>{r['trip_count']:,}</td><td>{r['share_within_class']:.1%}</td><td>{r['share_total']:.1%}</td></tr>" for r in ranking)
    warning_html = ''.join(f'<li>{escape(w)}</li>' for w in sorted(set(warnings)))
    if warnings:
        warning_html = '<li>以下の分類・割合は参考表示です。政策説明用の正式値には利用できません。</li>' + warning_html
    report = f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>ルート通過交通分析</title>
    <style>body{{font:16px sans-serif;max-width:1200px;margin:32px auto;color:#163047}}table{{border-collapse:collapse;width:100%;margin:20px 0}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #ccc}}iframe{{width:100%;height:750px;border:0}}.rate{{font-size:32px}}.od-matrix td{{text-align:right}}.od-matrix th{{white-space:nowrap;background:#edf3f7}}.od-matrix thead th{{position:sticky;top:0;z-index:2}}.od-matrix tr>:first-child{{position:sticky;left:0;z-index:1}}.od-matrix thead th:first-child{{z-index:3}}@media print{{iframe{{height:600px}}}}</style>
    <h1>{escape(target.name)}</h1><p>対象トリップ {total:,} / 通過交通率 <strong class="rate">{rate}</strong></p>
    <ul>{warning_html}</ul><p>通過交通 = Gate→Gate。割合の分母は対象ルートを通った区域内サブトリップ総数です。車両実台数ではありません。</p>
    <table><tr><th>交通分類</th><th>トリップ数</th><th>全交通内割合</th></tr>{rows}</table>
    <h2>ODマトリクス（ゲート・内エリア）</h2>
    <p>行がO（出発）、列がD（到着）、単位はトリップです。「内：エリア名」は12_polygon_builderで作成したエリアです。
    エリアに含まれない内の端点は「内：エリア外」、複数エリアに含まれる端点は「内：エリア重複」として別集計します。
    分類不明 {counts['UNKNOWN']:,} 件はマトリクスから除外しています。</p>
    <p>分析区域の境界上の起終点は、14で指定したゲートのうち直線距離が最も近いゲートへ割り当てます。
    ゲートの自動追加・集約は行いません。番号と位置は14の設定を使用し、同距離なら番号の小さいゲートを選びます。</p>
    <p>使用ゾーニングCSV：{escape('、'.join(sorted({Path(z['source']).name for z in zones})))}</p>
    {matrix_html(labels, full_od)}
    <p><a href="50_od_matrix.csv">ODマトリクスCSV</a></p>
    <h2>Gate間ODランキング</h2><table><tr><th>Gate OD</th><th>トリップ数</th><th>通過交通内割合</th><th>全交通内割合</th></tr>{od}</table>
    <h2>25mメッシュ経路地図</h2><p>右上で全交通・分類・指定Gate ODを選択。セルに触れると件数と割合を表示します。</p>
    <iframe src="50_map.html" title="経路地図"></iframe><p><a href="50_map.html">地図を開く</a></p></html>'''
    (out / '50_report.html').write_text(report, encoding='utf-8')
