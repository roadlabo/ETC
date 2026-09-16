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

FIELDS = ['dataset', 'operation_date', 'weekday', 'opid', 'trip_no', 'o_lon', 'o_lat', 'd_lon', 'd_lat', 'status', 'src_files_count']
METHODS = {'style13': '様式1-3OD', 'trip': 'トリップOD'}
EXTRA_FIELDS = ['od_method', 'trip_instance', 'source_file']


def method_of(records):
    methods = {row.get('od_method') or 'style13' for row in records}
    if len(methods) != 1 or not methods.issubset(METHODS):
        raise ValueError('異なるOD方式を混在して集計できません。方式別に読み込んでください。')
    return next(iter(methods))


def prefix(method):
    return '【' + METHODS[method] + '】'


def result_name(result, name):
    return prefix(result.get('method', 'style13')) + result.get('run_id', '') + name


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
    files = sorted(Path(input_dir).rglob('*.csv'))
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
    return output


def extract_trip(input_dir, output, progress=lambda s: None, cancel=lambda: False):
    """Physical first/last rows of each contiguous trip; preserve clipped subtrips.

    Exact duplicate row sequences are counted once across copied CSV files.
    Invalid endpoints are retained as invalid, never replaced by interior points.
    """
    input_dir = Path(input_dir)
    files = sorted(input_dir.rglob('*.csv'))
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
                records[identity]['src_files_count'] += 1
                return
            date, opid, trip = active
            def endpoint(row):
                return tuple(row[j].strip() if len(row) > j else '' for j in (14, 15))
            values = [input_dir.name, date, '月火水木金土日'[datetime.strptime(date, '%Y%m%d').weekday()],
                      opid, trip, *endpoint(first), *endpoint(last), 'OK', 1, 'trip', instance,
                      path.relative_to(input_dir).as_posix()]
            record = dict(zip(FIELDS + EXTRA_FIELDS, values))
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
    write_csv(output, FIELDS + EXTRA_FIELDS, ([r[f] for f in FIELDS + EXTRA_FIELDS] for r in records.values()))
    return output


def read_od(paths, progress=lambda s: None, cancel=lambda: False):
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
            k = (method, *k, row.get('trip_instance', '') if method == 'trip' else '')
            signature = tuple(row.get(f, '') for f in FIELDS[5:10])
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
    zones = [dict(z, bbox=(min(p[0] for p in z['points']), min(p[1] for p in z['points']),
                          max(p[0] for p in z['points']), max(p[1] for p in z['points']))) for z in zones]
    wanted = set(dates)
    if not wanted:
        raise ValueError('対象日がありません。')
    matrix, origins, destinations, excluded = Counter(), Counter(), Counter(), Counter()
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
        origin, destination = assign(o_lon, o_lat, zones), assign(d_lon, d_lat, zones)
        matrix[origin, destination] += 1
        origins[origin] += 1
        destinations[destination] += 1
        points.append(coords)
    labels = sorted({z['name'] for z in zones} | set(origins) | set(destinations))
    return dict(method=method, labels=labels, matrix=matrix, origins=origins, destinations=destinations,
                points=points, dates=sorted(wanted), days=len(wanted), excluded=dict(excluded), zones=zones)


def export(result, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    labels, matrix, days = result['labels'], result['matrix'], result['days']
    for name, divisor in [('od_matrix(all).csv', 1), ('od_matrix(perday).csv', days)]:
        values = [[a, *[matrix[a, b] / divisor for b in labels], result['origins'][a] / divisor] for a in labels]
        values.append(['合計', *[result['destinations'][b] / divisor for b in labels], len(result['points']) / divisor])
        write_csv(output / result_name(result, name), ['O / D', *labels, '合計'], values)
    write_csv(output / result_name(result, 'zone_production_attraction.csv'), ['zone', 'production', 'attraction', 'production_perday', 'attraction_perday'],
              [[a, result['origins'][a], result['destinations'][a], result['origins'][a] / days, result['destinations'][a] / days] for a in labels])
    (output / result_name(result, 'analysis.json')).write_text(json.dumps(dict(od_method=result.get('method','style13'), target_dates=result['dates'], target_days=days, valid_trips=len(result['points']), excluded=result['excluded'], unit='トリップ/日', zone_rule='境界を含む。異なる名称の重複は別枠、区域外は別枠。'), ensure_ascii=False, indent=2), encoding='utf-8')
    return output
