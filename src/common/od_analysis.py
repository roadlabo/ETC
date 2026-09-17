"""Original-trip OD analysis. No Qt dependency; dates always include zero-trip days."""
import csv
import io
import hashlib
import json
import math
import re
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from common.route_od import covers
from common.od_context import input_files, screening_context, write_context, read_context, merge_contexts

FIELDS = ['dataset', 'operation_date', 'weekday', 'opid', 'trip_no', 'o_lon', 'o_lat', 'd_lon', 'd_lat', 'status', 'src_files_count']
METHODS = {'style13': '様式1-3OD', 'trip': 'トリップOD'}
EXTRA_FIELDS = ['od_method', 'trip_instance', 'source_file']
ENDPOINT_FIELDS = ['o_type', 'o_gate_id', 'd_type', 'd_gate_id']
TRAFFIC_TYPES = ['内々交通', '内外交通', '外内交通', '外外交通']


def screening_dates(folder, progress=lambda s: None, cancel=lambda: False):
    first = last = None
    for i, path in enumerate(input_files(folder)):
        for n, row in enumerate(rows(path, cancel)):
            if n % 10000 == 0:
                check_cancel(cancel)
            try:
                date = key(row[2], row[3], row[8])[0]
            except (IndexError, ValueError):
                continue
            first = min(first, date) if first else date
            last = max(last, date) if last else date
        progress(f'対象日の確認: {i + 1:,} CSV')
    if first is None:
        raise ValueError('スクリーニングCSVの対象日を取得できません。')
    return first, last


def method_of(records):
    methods = {row.get('od_method') or 'style13' for row in records}
    if len(methods) != 1 or not methods.issubset(METHODS):
        raise ValueError('異なるOD方式を混在して集計できません。方式別に読み込んでください。')
    return next(iter(methods))


def prefix(method):
    return '【' + METHODS[method] + '】'


def result_name(result, name):
    return prefix(result.get('method', 'style13')) + name


def combined_matrix(result):
    matrix = Counter()
    for counts in result['traffic'].values():
        matrix.update(counts)
    gates = ['【ゲート】' + g['gate_id'] + ' ' + g['name'] for g in result['gates']]
    labels = list(result['labels']) + gates
    return labels, matrix


def display_label(label):
    if label.startswith('【ゲート】'):
        gate_id, _, name = label[len('【ゲート】'):].partition(' ')
        return 'ゲート ' + gate_id + ((' ' + name) if name and name != gate_id else '')
    return label


def check_cancel(cancel):
    if cancel():
        raise InterruptedError('処理を中止しました。')


def encoding_for(opener, cancel=lambda: False):
    # Validate the entire stream before yielding anything: retries cannot duplicate rows.
    for encoding in ('utf-8-sig', 'cp932'):
        try:
            with opener() as raw, io.TextIOWrapper(raw, encoding=encoding) as stream:
                while stream.read(1024 * 1024):
                    check_cancel(cancel)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ValueError('CSVの文字コードを判定できません（UTF-8 / CP932）。')


def rows(path, cancel=lambda: False):
    path = Path(path)
    encoding = encoding_for(lambda: path.open('rb'), cancel)
    with path.open(encoding=encoding, newline='') as stream:
        yield from csv.reader(stream)


def key(date, opid, trip):
    date, opid, trip = date.strip(), opid.strip(), trip.strip()
    if not re.fullmatch(r'\d{8}', date):
        raise ValueError('invalid operation date')
    datetime.strptime(date, '%Y%m%d')
    if not opid or not trip.isdigit():
        raise ValueError('invalid trip key')
    return date, opid, str(int(trip))


def coordinates(row):
    try:
        values = tuple(float(row[f'{side}_{axis}']) for side in ('o', 'd') for axis in ('lon', 'lat'))
        if all(math.isfinite(v) and abs(v) <= (180 if i % 2 == 0 else 90) for i, v in enumerate(values)):
            return values
    except (ValueError, TypeError, KeyError):
        pass
    return None


def write_csv(path, fields, records):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(records)


def extract(input_dir, zip_dir, output, progress=lambda s: None, cancel=lambda: False):
    files = input_files(input_dir)
    _, context = screening_context(input_dir, files, cancel)
    zips = sorted(Path(zip_dir).rglob('*.zip'))
    if not files or not zips:
        raise ValueError('入力CSVまたは様式1-3 ZIPがありません。')
    wanted = Counter()
    for i, path in enumerate(files):
        check_cancel(cancel)
        seen = set()
        for n, row in enumerate(rows(path, cancel)):
            if n % 10000 == 0:
                check_cancel(cancel)
            try:
                seen.add(key(row[2], row[3], row[8]))
            except (IndexError, ValueError):
                continue
        wanted.update(seen)
        progress(f'入力CSV {i + 1}/{len(files)} ・元トリップ {len(wanted):,} 件')
    if not wanted:
        raise ValueError('運行日・運行ID・トリップ番号を取得できません。スクリーニング済みCSVを選択してください。')
    found, conflicts = {}, set()
    needed_dates = {k[0] for k in wanted}
    # ZIP naming convention is the same operation-date index used by legacy 40.
    zips = [p for p in zips if not (match := re.search(r'(?<!\d)(20\d{6})(?!\d)', p.name)) or match.group(1) in needed_dates]
    for i, path in enumerate(zips):
        check_cancel(cancel)
        progress(f'様式1-3照合 {i + 1}/{len(zips)} ・一致 {len(found):,}/{len(wanted):,} 件')
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir() and m.filename.lower().endswith('.csv')]
            preferred = [m for m in members if Path(m.filename).name.lower() == 'data.csv']
            if preferred:
                members = preferred
            for member in members:
                encoding = encoding_for(lambda: archive.open(member), cancel)
                with archive.open(member) as raw, io.TextIOWrapper(raw, encoding=encoding, newline='') as stream:
                    for n, row in enumerate(csv.reader(stream)):
                        if n % 10000 == 0:
                            check_cancel(cancel)
                        try:
                            k = key(row[0], row[1], row[7])
                            if k not in wanted or len(row) < 15:
                                continue
                            value = tuple(v.strip() for v in row[11:15])
                            if k in found and found[k] != value:
                                conflicts.add(k)
                            found[k] = value
                        except (IndexError, ValueError):
                            continue
    records = []
    for k in sorted(wanted):
        date, opid, trip = k
        values = found.get(k, ('', '', '', ''))
        status = 'CONFLICT' if k in conflicts else ('OK' if k in found else 'MISSING_OD')
        records.append([Path(input_dir).name, date, '月火水木金土日'[datetime.strptime(date, '%Y%m%d').weekday()], opid, trip, *values, status, wanted[k], 'style13', '', ''])
    check_cancel(cancel)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output, FIELDS + EXTRA_FIELDS, records)
    write_context(output, context)
    return output


def extract_trip(input_dir, output, progress=lambda s: None, cancel=lambda: False):
    """Physical first/last rows of each contiguous trip; preserve clipped subtrips.

    Exact duplicate row sequences are counted once across copied CSV files.
    Invalid endpoints are retained as invalid, never replaced by interior points.
    """
    input_dir = Path(input_dir)
    files = input_files(input_dir)
    annotations, context = screening_context(input_dir, files, cancel)
    records = {}
    for i, path in enumerate(files):
        check_cancel(cancel)
        active, first, last, digest = None, None, None, None

        def finish():
            if active is None:
                return
            instance = digest.hexdigest()
            identity = (*active, instance)
            if identity in records:
                annotation = annotations.get(path, {})
                expected = [annotation.get(s + suffix, default) for s in ('start', 'end')
                            for suffix, default in [('_type', 'INSIDE'), ('_gate_id', '')]]
                if [records[identity][f] for f in ENDPOINT_FIELDS] != expected:
                    raise ValueError(f'同じトリップに異なるゲート判定があります: {path.name}')
                records[identity]['src_files_count'] += 1
                return
            date, opid, trip = active
            def endpoint(row):
                return tuple(row[j].strip() if len(row) > j else '' for j in (14, 15))
            values = [input_dir.name, date, '月火水木金土日'[datetime.strptime(date, '%Y%m%d').weekday()],
                      opid, trip, *endpoint(first), *endpoint(last), 'OK', 1, 'trip', instance,
                      path.relative_to(input_dir).as_posix()]
            record = dict(zip(FIELDS + EXTRA_FIELDS, values))
            annotation = annotations.get(path, {})
            for side, source in [('o', 'start'), ('d', 'end')]:
                record[side + '_type'] = annotation.get(source + '_type', 'INSIDE')
                record[side + '_gate_id'] = annotation.get(source + '_gate_id', '')
            if coordinates(record) is None:
                record['status'] = 'INVALID_COORDINATES'
            records[identity] = record

        for n, row in enumerate(rows(path, cancel)):
            if n % 10000 == 0:
                check_cancel(cancel)
            try:
                current = key(row[2], row[3], row[8])
            except (IndexError, ValueError):
                if active is not None and any(row):
                    raise ValueError(f'トリップ途中のキーが不正です: {path} 行{n + 1}')
                continue
            if current != active:
                finish()
                active, first, digest = current, row, hashlib.sha256()
            last = row
            digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n')
        finish()
        progress(f'トリップOD {i + 1}/{len(files)} CSV ・{len(records):,} トリップ')
    if not records:
        raise ValueError('スクリーニング済みCSVにトリップがありません。')
    check_cancel(cancel)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = FIELDS + EXTRA_FIELDS + ENDPOINT_FIELDS
    write_csv(output, fields, ([r[f] for f in fields] for r in records.values()))
    write_context(output, context)
    return output


def read_od(paths, progress=lambda s: None, cancel=lambda: False):
    paths = list(paths)
    context = merge_contexts(read_context(path) for path in paths)
    records, seen = [], {}
    duplicates = 0
    for path in paths:
        iterator = iter(rows(path, cancel))
        header = next(iterator, [])
        if not set(FIELDS[1:10]).issubset(header):
            raise ValueError(f'ODリストの必須列がありません: {path}')
        for n, cells in enumerate(iterator):
            if n % 10000 == 0:
                check_cancel(cancel)
                progress(f'ODリスト読込 ・{len(records):,} 件')
            row = dict(zip(header, cells))
            row['_context'] = context
            method = row.get('od_method') or 'style13'
            if method not in METHODS:
                raise ValueError(f'未対応のOD方式です: {method}')
            row['od_method'] = method
            try:
                k = key(row['operation_date'], row['opid'], row['trip_no'])
            except (ValueError, KeyError) as exc:
                raise ValueError(f'ODリストのキーが不正です: {path} 行{n + 2}') from exc
            if method == 'trip' and not row.get('trip_instance'):
                raise ValueError('トリップODの区間識別子がありません。入力CSVから作り直してください。')
            if method == 'trip' and any(field in header for field in ENDPOINT_FIELDS):
                for side in ('o', 'd'):
                    kind, gate_id = row.get(side + '_type'), row.get(side + '_gate_id', '')
                    if kind not in ('INSIDE', 'GATE') or (kind == 'GATE') != bool(gate_id):
                        raise ValueError(f'ODリストの端点種別・ゲート番号が不正です: {path} 行{n + 2}')
            k = (method, *k, row.get('trip_instance', '') if method == 'trip' else '')
            signature = tuple(row.get(f, '') for f in FIELDS[5:10] + ENDPOINT_FIELDS)
            if k in seen:
                if seen[k] != signature:
                    raise ValueError(f'同じトリップに異なるODがあります: {k}')
                duplicates += 1
                continue
            seen[k] = signature
            records.append(row)
    if not records:
        raise ValueError('ODリストが空です。')
    method_of(records)
    return records, duplicates


def target_dates(start, end, weekdays):
    first, last = (datetime.strptime(s, '%Y%m%d') for s in (start, end))
    if first > last or not weekdays:
        raise ValueError('開始日・終了日と対象曜日を確認してください。')
    result = []
    while first <= last:
        if first.weekday() in weekdays:
            result.append(first.strftime('%Y%m%d'))
        first += timedelta(days=1)
    if not result:
        raise ValueError('対象日がありません。')
    return result


def load_zones(folder):
    paths = sorted(Path(folder).rglob('*.csv'))
    zones = []
    for path in paths:
        for row in rows(path):
            if not row:
                continue
            try:
                values = [float(v) for v in row[1:]]
                if len(values) < 6 or len(values) % 2 or not row[0].strip() or row[0].strip() in ('【区域外】', '【ゾーン重複】'):
                    raise ValueError()
                points = list(zip(values[::2], values[1::2]))
                if len(set(points)) < 3 or any(not math.isfinite(v) or abs(v) > (180 if i % 2 == 0 else 90) for i, v in enumerate(values)):
                    raise ValueError()
                zones.append({'name': row[0].strip(), 'points': points})
            except ValueError as exc:
                raise ValueError(f'ゾーンCSVが不正です: {path.name}') from exc
    if not zones:
        raise ValueError('12_ゾーニングデータに有効なゾーンCSVがありません。')
    return zones


def assign(lon, lat, zones):
    names = set()
    for zone in zones:
        bbox = zone.get('bbox')
        if bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        if covers((lon, lat), zone['points']):
            names.add(zone['name'])
    return next(iter(names)) if len(names) == 1 else ('【区域外】' if not names else '【ゾーン重複】')


def analyze(records, zones, dates, progress=lambda s: None, cancel=lambda: False):
    method = method_of(records)
    contexts = {id(r.get('_context')): r.get('_context', {}) for r in records}
    context = merge_contexts(contexts.values())
    screening_folders = sorted(set(context['screening_folders']) |
                              {r['dataset'] for r in records if r.get('dataset') and not r.get('_context', {}).get('screening_folders')})
    gates = {g['gate_id']: g for g in context['gates']}
    zones = [dict(z, bbox=(min(p[0] for p in z['points']), min(p[1] for p in z['points']),
                          max(p[0] for p in z['points']), max(p[1] for p in z['points']))) for z in zones]
    wanted = set(dates)
    if not wanted:
        raise ValueError('対象日がありません。')
    matrix, origins, destinations, excluded = Counter(), Counter(), Counter(), Counter()
    traffic = {name: Counter() for name in TRAFFIC_TYPES}
    gate_origins, gate_destinations = Counter(), Counter()
    heat_o, heat_d = [], []
    points = []
    for i, row in enumerate(records):
        if i % 1000 == 0:
            check_cancel(cancel)
            progress(f'ゾーン集計 {i:,}/{len(records):,} 件')
        if row['operation_date'] not in wanted:
            excluded['対象日外'] += 1
            continue
        coords = coordinates(row)
        if row.get('status') != 'OK':
            excluded[row.get('status') or 'statusなし'] += 1
            continue
        if coords is None:
            excluded['座標不正'] += 1
            continue
        o_lon, o_lat, d_lon, d_lat = coords
        endpoints = []
        flags = []
        for side, lon, lat, counts, gate_counts, heat in (
                ('o', o_lon, o_lat, origins, gate_origins, heat_o),
                ('d', d_lon, d_lat, destinations, gate_destinations, heat_d)):
            is_gate = method == 'trip' and row.get(side + '_type') == 'GATE'
            flags.append(is_gate)
            if is_gate:
                gate_id = row.get(side + '_gate_id')
                if gate_id not in gates:
                    raise ValueError('ゲートの位置情報がありません。ODリストと同名の.context.jsonを一緒に配置するか、ODリストを作り直してください。')
                gate_counts[gate_id] += 1
                endpoints.append('【ゲート】' + gate_id + ' ' + gates[gate_id]['name'])
            else:
                zone = assign(lon, lat, zones)
                endpoints.append(zone)
                counts[zone] += 1
                heat.append((lon, lat))
        origin, destination = endpoints
        category = TRAFFIC_TYPES[int(flags[0]) * 2 + int(flags[1])]
        traffic[category][origin, destination] += 1
        if not any(flags):
            matrix[origin, destination] += 1
        points.append(coords)
    labels = sorted({z['name'] for z in zones} | set(origins) | set(destinations))
    return dict(method=method, labels=labels, matrix=matrix, origins=origins, destinations=destinations,
                points=points, dates=sorted(wanted), days=len(wanted), excluded=dict(excluded), zones=zones,
                traffic=traffic, heat_o=heat_o, heat_d=heat_d, gates=list(gates.values()),
                gate_origins=gate_origins, gate_destinations=gate_destinations, boundaries=context['boundaries'],
                screening_folders=screening_folders)


def export(result, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    from common.od_workbook import write_workbook
    days = result['days']
    write_workbook(result, output / result_name(result, 'OD集計.xlsx'))
    metadata = dict(od_method=result.get('method', 'style13'), target_dates=result['dates'], target_days=days,
                    project_folder=result.get('project_folder', ''), screening_folders=result.get('screening_folders', []),
                    valid_trips=len(result['points']), excluded=result['excluded'], unit='トリップ/日',
                    traffic_counts={kind: sum(counts.values()) for kind, counts in result['traffic'].items()},
                    zone_rule='境界を含む。異なる名称の重複は別枠、区域外は別枠。',
                    gate_rule='ゲート判定は第1.5の端点情報。統合OD表は4区分すべて。分布は非ゲート端点のみ。')
    (output / result_name(result, 'analysis.json')).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return output
