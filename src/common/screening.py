"""Shared Youshiki 1-2 and screening provenance contract."""
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

LON_INDEX, LAT_INDEX = 14, 15
OP_DATE_INDEX, OP_ID_INDEX, VEHICLE_TYPE_INDEX, VEHICLE_USE_INDEX = 2, 3, 4, 5
DATE_INDEX, TRIP_NO_INDEX = 6, 8
AREA_FOLDER = '14_エリアデータ'
AREA_FILE = '14_area.geojson'
ZONE_FOLDER = '12_ゾーニングデータ'
ROUTE_FOLDER = '10_ルート(Route)データ'
SECOND_FOLDER = '20_第２スクリーニング(ルート)'
INFO_FILE = 'screening_info.json'
INDEX_FILE = '15_trip_index.csv'
INDEX_FIELDS = ['trip_id', 'source_file', 'start_type', 'start_gate_id', 'end_type', 'end_gate_id', 'start_lon', 'start_lat', 'end_lon', 'end_lat', 'sha256']


def area_role(properties):
    """14 writes area14_role; old GeoJSON can be moved without changing its hash."""
    return (properties.get('area14_role') or properties.get('area15_role') or
            properties.get('role') or properties.get('type') or '').lower()


def project_area_path(project):
    return Path(project) / AREA_FOLDER / AREA_FILE

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def relative(path, project):
    try:
        return Path(path).resolve().relative_to(Path(project).resolve()).as_posix()
    except ValueError:
        return str(Path(path).resolve())

def read_info(folder):
    p = Path(folder) / INFO_FILE
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else {}

def write_info(folder, info):
    Path(folder).mkdir(parents=True, exist_ok=True)
    info = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), **info}
    p = Path(folder) / INFO_FILE
    tmp = p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(p)

def read_index(folder):
    p = contract_folder(folder) / INDEX_FILE
    if not p.exists():
        return {}
    with p.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    result = {r['source_file']: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('トリップsidecarのsource_fileが重複しています')
    return result

def contract_folder(folder):
    folder = Path(folder)
    if folder.name == '15_area_subtrip_csv' and read_info(folder).get('contract_parent'):
        return folder.parent
    return folder

def write_csv(path, fields, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)

def read_lon_lat(row):
    try:
        lon, lat = float(row[LON_INDEX]), float(row[LAT_INDEX])
        if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90:
            return lon, lat
    except (ValueError, TypeError, IndexError):
        pass
    return None

def cluster_gates(rows, radius_m=30):
    """Sorted greedy representatives; every member is within radius of its Gate.

    Sorting makes IDs independent of input order. Avoid transitive chains that
    could combine opposite ends of a long boundary into a single Gate.
    """
    endpoints = sorted((float(r[f'{s}_lon']), float(r[f'{s}_lat']), i, s)
                       for i, r in enumerate(rows) for s in ('start', 'end') if r[f'{s}_type'] == 'GATE')
    gates = []
    buckets = {}
    lat0 = endpoints[0][1] if endpoints else 0
    k = math.pi / 180 * 6371000
    for lon, lat, i, side in endpoints:
        x, y = lon * k * math.cos(math.radians(lat0)), lat * k
        bx, by = math.floor(x / radius_m), math.floor(y / radius_m)
        candidates = [g for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                      for g in buckets.get((bx + dx, by + dy), [])
                      if math.hypot(x-g['_x'], y-g['_y']) <= radius_m]
        if candidates:
            gate = min(candidates, key=lambda g: (math.hypot(x-g['_x'], y-g['_y']), g['gate_id']))
        else:
            gate = {'gate_id': f'G{len(gates)+1:02d}', 'lon': lon, 'lat': lat, '_x': x, '_y': y}
            gates.append(gate)
            buckets.setdefault((bx, by), []).append(gate)
        rows[i][f'{side}_gate_id'] = gate['gate_id']
    return gates

def read_manual_gates(data):
    gates = []
    seen = set()
    for feature in data.get('features', []):
        props = feature.get('properties') or {}
        if area_role(props) != 'gate':
            continue
        geom = feature.get('geometry') or {}
        coordinates = geom.get('coordinates') or []
        gate_id = str(props.get('gate_id', ''))
        if not re.fullmatch(r'G[0-9]+', gate_id) or gate_id in seen:
            raise ValueError('14のゲート番号は重複のないG01形式にしてください。')
        try:
            lon, lat = map(float, coordinates)
            valid = geom.get('type') == 'Point' and math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError(f'{gate_id}: ゲート座標が不正です。14_area_builder.batで修正してください。')
        seen.add(gate_id)
        gates.append({'gate_id': gate_id, 'name': str(props.get('name') or gate_id), 'lon': lon, 'lat': lat})
    if not gates:
        raise ValueError('指定ゲートがありません。14_area_builder.batでゲートを追加して保存してください。')
    return sorted(gates, key=lambda g: (int(g['gate_id'][1:]), g['gate_id']))


def assign_nearest_gates(rows, gates):
    if not gates:
        raise ValueError('14_area_builder.batでゲートを指定してください。')
    for row in rows:
        for side in ('start', 'end'):
            if row[f'{side}_type'] != 'GATE':
                row[f'{side}_gate_id'] = ''
                continue
            lon, lat = map(math.radians, (float(row[f'{side}_lon']), float(row[f'{side}_lat'])))
            def distance(gate):
                glon, glat = map(math.radians, (gate['lon'], gate['lat']))
                # Haversine is monotonic in distance; no radius limit for manual gates.
                return (math.sin((glat-lat)/2)**2 + math.cos(lat)*math.cos(glat)*math.sin((glon-lon)/2)**2,
                        int(gate['gate_id'][1:]), gate['gate_id'])
            row[f'{side}_gate_id'] = min(gates, key=distance)['gate_id']


def gate_geojson(gates):
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'area14_role': 'gate', 'gate_id': g['gate_id'], 'name': g.get('name', g['gate_id'])},
         'geometry': {'type': 'Point', 'coordinates': [g['lon'], g['lat']]}} for g in gates]}


def write_gate_contract(folder, rows, info, gates):
    folder = Path(folder)
    assign_nearest_gates(rows, gates)
    write_csv(folder / INDEX_FILE, INDEX_FIELDS, rows)
    write_csv(folder / 'gate_master.csv', ['gate_id', 'name', 'lon', 'lat'], gates)
    (folder / 'gate_master.geojson').write_text(json.dumps(gate_geojson(gates), ensure_ascii=False), encoding='utf-8')
    write_info(folder, {**info, 'gate_assignment': 'nearest_manual_gate', 'trip_count': len(rows), 'trip_index': INDEX_FILE,
                        'trip_index_sha256': digest(folder / INDEX_FILE),
                        'gate_master_sha256': digest(folder / 'gate_master.geojson')})
