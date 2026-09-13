"""Named internal zones exported by 12_polygon_builder and complete OD tables."""
import csv
import html
import math
from pathlib import Path
from collections import Counter


def load_zones(project):
    project = Path(project)
    paths = sorted(set(project.glob('*.csv')) | set((project / '12_エリアデータ').rglob('*.csv')))
    zones = []
    for path in paths:
        parsed = []
        try:
            with path.open(encoding='utf-8-sig', newline='') as stream:
                for row in csv.reader(stream):
                    if not row:
                        continue
                    # Older builder exports did not quote commas in zone names.
                    start = len(row)
                    while start > 1:
                        try:
                            float(row[start - 1])
                        except ValueError:
                            break
                        start -= 1
                    values = [float(v) for v in row[start:]]
                    if len(values) < 6 or len(values) % 2:
                        raise ValueError('invalid polygon row')
                    points = list(zip(values[::2], values[1::2]))
                    if any(not math.isfinite(x) or not math.isfinite(y) or not -180 <= x <= 180 or not -90 <= y <= 90 for x, y in points):
                        raise ValueError('invalid coordinates')
                    if len(set(points)) < 3:
                        raise ValueError('invalid polygon')
                    name = ','.join(row[:start]).strip()
                    if not name:
                        raise ValueError('empty name')
                    parsed.append({'name': name, 'points': points, 'source': str(path)})
        except (ValueError, UnicodeError):
            continue
        zones.extend(parsed)
    if not zones:
        raise ValueError('12で作成したゾーニングCSVがありません（または形式が不正です）。'
                         '12_polygon_builder.batでエリアを作成し、プロジェクト直下または12_エリアデータ内にCSVを保存してください。')
    return zones


def covers(point, polygon):
    x, y = point
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        ax, ay = a
        bx, by = b
        cross = (x-ax)*(by-ay) - (y-ay)*(bx-ax)
        if abs(cross) <= 1e-12 and min(ax, bx)-1e-12 <= x <= max(ax, bx)+1e-12 and min(ay, by)-1e-12 <= y <= max(ay, by)+1e-12:
            return True
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


def endpoint_label(record, side, zones):
    if record.get(f'{side}_type') == 'GATE':
        return record[f'{side}_gate_id']
    point = (float(record[f'{side}_lon']), float(record[f'{side}_lat']))
    names = sorted({z['name'] for z in zones if covers(point, z['points'])})
    if len(names) == 1:
        return f'内：{names[0]}'
    return '内：エリア重複' if names else '内：エリア外'


def matrix_data(records):
    counts = Counter((r['origin'], r['destination']) for r in records if r['od_class'] != 'UNKNOWN')
    labels = sorted({v for pair in counts for v in pair},
                    key=lambda v: (0, int(v[1:])) if v.startswith('G') and v[1:].isdigit() else (1, v))
    return labels, counts


def matrix_html(labels, counts):
    esc = html.escape
    head = '<tr><th>O（出発） ＼ D（到着）</th>' + ''.join(f'<th>{esc(v)}</th>' for v in labels) + '<th>合計</th></tr>'
    rows = []
    for a in labels:
        rows.append(f'<tr><th>{esc(a)}</th>' + ''.join(f'<td>{counts[a, b]:,}</td>' for b in labels) + f'<th>{sum(counts[a, b] for b in labels):,}</th></tr>')
    foot = '<tr><th>合計</th>' + ''.join(f'<th>{sum(counts[a, b] for a in labels):,}</th>' for b in labels) + f'<th>{sum(counts.values()):,}</th></tr>'
    return '<div style="overflow:auto;max-height:650px"><table class="od-matrix"><thead>' + head + '</thead><tbody>' + ''.join(rows) + '</tbody><tfoot>' + foot + '</tfoot></table></div>'
