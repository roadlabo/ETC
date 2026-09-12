"""Shared Youshiki 1-2 and screening provenance contract."""
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

LON_INDEX, LAT_INDEX = 14, 15
OP_DATE_INDEX, OP_ID_INDEX, VEHICLE_TYPE_INDEX, VEHICLE_USE_INDEX = 2, 3, 4, 5
DATE_INDEX, TRIP_NO_INDEX = 6, 8
AREA_FOLDER = '12_エリアデータ'
AREA_FILE = '15_area.geojson'
ROUTE_FOLDER = '10_ルート(Route)データ'
SECOND_FOLDER = '20_第２スクリーニング(ルート)'
INFO_FILE = 'screening_info.json'
INDEX_FILE = '15_trip_index.csv'
INDEX_FIELDS = ['trip_id', 'source_file', 'start_type', 'start_gate_id', 'end_type', 'end_gate_id', 'start_lon', 'start_lat', 'end_lon', 'end_lat', 'sha256']

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

def write_gate_contract(folder, rows, info):
    folder = Path(folder)
    gates = cluster_gates(rows)
    write_csv(folder / INDEX_FILE, INDEX_FIELDS, rows)
    write_csv(folder / 'gate_master.csv', ['gate_id', 'lon', 'lat'], gates)
    (folder / 'gate_master.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'gate_id': g['gate_id']},
         'geometry': {'type': 'Point', 'coordinates': [g['lon'], g['lat']]}} for g in gates]}), encoding='utf-8')
    write_info(folder, {**info, 'gate_cluster_m': 30, 'trip_count': len(rows), 'trip_index': INDEX_FILE,
                        'trip_index_sha256': digest(folder / INDEX_FILE),
                        'gate_master_sha256': digest(folder / 'gate_master.geojson')})
