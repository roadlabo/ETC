"""Screening provenance retained beside an OD list for reproducible maps."""
import json
from pathlib import Path

from common.screening import (read_info, read_index, contract_folder, digest,
                              read_manual_gates, area_role, INDEX_FILE)

METADATA = {'15_trip_index.csv', 'gate_master.csv', '15_subtrip_summary.csv',
            '15_excluded_trips.csv', '15_multi_entry_trips.csv'}


def input_files(folder):
    return sorted(p for p in Path(folder).rglob('*.csv') if p.name not in METADATA)


def merge_contexts(contexts):
    gates, boundaries = {}, {}
    screening_folders = set()
    for context in contexts:
        screening_folders.update(context.get('screening_folders', []))
        for gate in context.get('gates', []):
            identity = gate['gate_id']
            if identity in gates and gates[identity] != gate:
                raise ValueError(f'異なるゲート設定が混在しています: {identity}')
            gates[identity] = gate
        for feature in context.get('boundaries', []):
            boundaries[json.dumps(feature, sort_keys=True)] = feature
    return dict(gates=list(gates.values()), boundaries=list(boundaries.values()), screening_folders=sorted(screening_folders))


def screening_context(folder, files, cancel=lambda: False):
    """Match files by the nearest screening contract, never by proximity alone."""
    root = Path(folder).resolve()
    contracts, annotations = {}, {}
    for path in files:
        if cancel():
            raise InterruptedError('処理を中止しました。')
        owner = None
        for parent in path.resolve().parents:
            if (parent / 'screening_info.json').exists():
                owner = contract_folder(parent)
                break
            if parent == root:
                break
        if owner is None:
            continue
        if owner not in contracts:
            info = read_info(owner)
            is_area = (info.get('screening_stage') == '1.5' or info.get('source_screening_stage') == '1.5'
                       or info.get('source_stage') == '1.5' or bool(info.get('gate_assignment')))
            if not is_area:
                contracts[owner] = ({}, {})
                continue
            if not (owner / INDEX_FILE).exists():
                raise ValueError(f'ゲート判定ファイルがありません: {owner / INDEX_FILE}')
            for name, field in [(INDEX_FILE, 'trip_index_sha256'), ('gate_master.geojson', 'gate_master_sha256')]:
                file = owner / name
                if not file.exists() or (info.get(field) and digest(file) != info[field]):
                    raise ValueError(f'ゲート設定が欠損または変更されています: {file}')
            gates = read_manual_gates(json.loads((owner / 'gate_master.geojson').read_text(encoding='utf-8-sig')))
            boundaries = []
            area_name = info.get('area_file', '')
            settings = owner / '15_area_screening_settings.json'
            candidates = []
            if settings.exists():
                area_path = json.loads(settings.read_text(encoding='utf-8-sig')).get('area_geojson')
                if area_path:
                    candidates.append(Path(area_path))
            if area_name:
                candidates.extend(parent / area_name for parent in (owner, *owner.parents))
            area = next((p for p in candidates if p.is_file() and
                         (not info.get('area_sha256') or digest(p) == info['area_sha256'])), None)
            if area is None:
                raise ValueError('スクリーニング時の区域データが見つかりません。14_エリアデータと入力フォルダを同じプロジェクトに配置してください。')
            for feature in json.loads(area.read_text(encoding='utf-8-sig')).get('features', []):
                role = area_role(feature.get('properties') or {})
                if role in ('official_area', 'analysis_area'):
                    boundaries.append(dict(feature, properties={**feature.get('properties', {}), 'od_role': role}))
            contracts[owner] = (read_index(owner), dict(gates=gates, boundaries=boundaries))
        index, context = contracts[owner]
        if not index and not context:
            continue
        entry = index.get(path.name)
        if entry is None or not entry.get('sha256') or digest(path) != entry['sha256']:
            raise ValueError(f'トリップとゲート判定が一致しません。スクリーニングをやり直してください: {path.name}')
        known = {g['gate_id'] for g in context['gates']}
        for side in ('start', 'end'):
            if entry.get(side + '_type') not in ('GATE', 'INSIDE') or (
                entry[side + '_type'] == 'GATE' and entry.get(side + '_gate_id') not in known):
                raise ValueError(f'端点のゲート判定が不正です: {path.name}')
        annotations[path] = entry
    context = merge_contexts(context for _, context in contracts.values())
    context['screening_folders'] = [str(root)]
    return annotations, context


def context_path(path):
    return Path(path).with_suffix('.context.json')


def write_context(path, context):
    context_path(path).write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding='utf-8')


def read_context(path):
    file = context_path(path)
    return json.loads(file.read_text(encoding='utf-8-sig')) if file.exists() else {}
