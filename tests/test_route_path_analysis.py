import copy
import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from test_area_screening import write_area, row33, write_trip_file
from common.screening import read_index, read_info, cluster_gates, SECOND_FOLDER, digest
from common.route_path import scan_project, analyze, classify

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'src' / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

area15 = load('path_area15', '15_area_screening.py')
route20 = load('path_route20', '20_route_trip_extractor.py')
path50 = load('path_engine50', '50_Path_Analysis.py')
viewer05 = load('path_viewer05', '05_trip_viewer.py')

def fixture(project, multiple=False):
    source = project / 'first'
    source.mkdir()
    area_dir = project / '12_エリアデータ'
    area_dir.mkdir()
    write_area(area_dir / '15_area.geojson')
    # All paths share the target street but have four different endpoint classes.
    paths = [(-.002, .012, .005), (-.002, .006, .005), (.004, .012, .005),
             (.004, .006, .005), (-.002, .012, .0051), (.012, -.002, .005)]
    for i, (start, end, lat) in enumerate(paths):
        middle = [.004, .005, .006] if start < end else [.006, .005, .004]
        points = [start] + middle + [end]
        rows = [row33(str(i+1), 1, f'20250101090{j}00', 135+x, 35+lat) for j, x in enumerate(points)]
        for row in rows:
            row[18] = '30'
        write_trip_file(source / f'{i}.csv', rows)
    out15 = project / area15.FOLDER_OUT
    area15.run_screening(area15.ScreeningConfig(source, area_dir / '15_area.geojson', out15))
    route_dir = project / route20.FOLDER_ROUTE
    route_dir.mkdir()
    rows = [row33('1', 1, '20250101090000', 135+x, 35.005) for x in [.004, .005, .006]]
    write_trip_file(route_dir / '対象路線.csv', rows)
    if multiple:
        write_trip_file(route_dir / '第二路線.csv', rows)
    result = route20.run_second_screening(out15, route_dir, project / SECOND_FOLDER, 30, 3, False, False)
    assert result == 0
    return out15

class RoutePathTest(unittest.TestCase):
    def test_pipeline_counts_full_trip_and_single_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            out15 = fixture(project, multiple=True)
            _, targets = scan_project(project)
            self.assertEqual(len(targets), 2)
            target = targets[0]
            self.assertFalse(target.warning)
            original = read_index(out15 / '15_area_subtrip_csv')
            selected = read_index(target.folder)
            self.assertEqual(len(selected), 6)
            for entry in selected.values():
                source = next(r for r in original.values() if r['trip_id'] == entry['trip_id'])
                # CSV content remains complete (not reduced to route-adjacent points).
                self.assertEqual(digest(out15 / '15_area_subtrip_csv' / source['source_file']), entry['sha256'])
            reads = []
            read_bytes = Path.read_bytes
            def record_read(path):
                if path in target.files:
                    reads.append(path)
                return read_bytes(path)
            with patch.object(Path, 'read_bytes', record_read):
                result = analyze(project, target, path50)
            self.assertEqual(sorted(reads), target.files)
            self.assertTrue(result['official'])
            self.assertEqual(result['counts']['ALL'], 6)
            self.assertEqual(result['counts']['THROUGH'], 3)
            self.assertEqual(result['through_rate'], .5)
            for key in ('EXTERNAL_TO_INTERNAL', 'INTERNAL_TO_EXTERNAL', 'INTERNAL'):
                self.assertEqual(result['counts'][key], 1)
            self.assertEqual([r['trip_count'] for r in result['ranking']], [2, 1])
            self.assertTrue(Path(result['report']).exists())
            mesh = Path(result['output_dir']) / '50_mesh.csv'
            with mesh.open(encoding='utf-8-sig') as f:
                cells = list(csv.DictReader(f))
            self.assertTrue(cells)
            self.assertTrue(all(0 < float(r['share']) <= 1 for r in cells))
            self.assertIn('通過交通率', Path(result['report']).read_text(encoding='utf-8'))

    def test_missing_project_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with self.assertRaisesRegex(ValueError, '12_エリアデータ'):
                scan_project(project)
            area = project / '12_エリアデータ'
            area.mkdir()
            with self.assertRaisesRegex(ValueError, '15_area.geojson'):
                scan_project(project)
            (area / '15_area.geojson').write_text('{"features": []}')
            with self.assertRaisesRegex(ValueError, 'analysis_area'):
                scan_project(project)
            write_area(area / '15_area.geojson')
            with self.assertRaisesRegex(ValueError, '20_第２'):
                scan_project(project)

    def test_legacy_and_stale_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fixture(project)
            _, targets = scan_project(project)
            path = targets[0].files[0]
            path.write_bytes(path.read_bytes() + b'\n')
            result = analyze(project, targets[0], path50)
            self.assertFalse(result['official'])
            self.assertIsNone(result['through_rate'])
            path.rename(project / SECOND_FOLDER / path.name)
            _, targets = scan_project(project)
            legacy = targets[-1]
            self.assertTrue(legacy.warning)
            result = analyze(project, legacy, path50)
            self.assertEqual(result['counts']['UNKNOWN'], 1)
            self.assertFalse(result['official'])

    def test_coordinate_consistency_viewer_swap_and_mesh(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trip.csv'
            rows = [row33('1', 1, '20250101090000', 135.005, 35.005), row33('1', 1, '20250101090100', 135.006, 35.005)]
            for i, row in enumerate(rows):
                row[12], row[18] = str(i), '30'
            write_trip_file(path, rows)
            self.assertEqual(route20._read_lon_lat(rows[0]), (135.005, 35.005))
            xy = path50.load_single_trip(path, 135.005, 35.005)
            np.testing.assert_allclose(xy[0], [0, 0])
            df = viewer05.read_route_data(path)
            self.assertAlmostEqual(df.iloc[0]['lon'], 135.005)
            self.assertAlmostEqual(df.iloc[0]['lat'], 35.005)
            viewer05._swap_latlon_if_needed(df)
            self.assertAlmostEqual(df.iloc[0]['lon'], 135.005)
            for row in rows:
                row[14], row[15] = row[15], row[14]
            write_trip_file(path, rows)
            self.assertAlmostEqual(viewer05.read_route_data(path).iloc[0]['lon'], 135.005)

    def test_gate_determinism_and_four_classes(self):
        rows = [{'start_type': 'GATE', 'start_lon': 135, 'start_lat': lat,
                 'end_type': 'INSIDE'} for lat in [35.0001, 35, 35.003]]
        other = copy.deepcopy(list(reversed(rows)))
        first = cluster_gates(rows)
        second = cluster_gates(other)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(rows[0]['start_gate_id'], rows[1]['start_gate_id'])
        for a, b, expected in [('GATE', 'GATE', 'THROUGH'), ('GATE', 'INSIDE', 'EXTERNAL_TO_INTERNAL'),
                               ('INSIDE', 'GATE', 'INTERNAL_TO_EXTERNAL'), ('INSIDE', 'INSIDE', 'INTERNAL')]:
            self.assertEqual(classify({'start_type': a, 'end_type': b}), expected)

    def test_cli_and_ui_route_selection(self):
        from PyQt6.QtWidgets import QApplication
        from common.route_path_ui import RouteWindow
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fixture(project, True)
            window = RouteWindow(path50, project)
            self.assertEqual(window.routes.count(), 2)
            window.routes.setCurrentIndex(1)
            self.assertIn('第1.5', window.status.text())
            self.assertTrue(window.run_button.isEnabled())
            window.close()
            args = [sys.executable, str(ROOT / 'src/50_Path_Analysis.py'), '--mode', 'route', '--project_dir', str(project), '--route', '対象路線']
            proc = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('"official": true', proc.stdout)

    def test_direct_first_input_and_area_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fixture(project)
            out = project / 'direct-first'
            route20.run_second_screening(project / 'first', project / route20.FOLDER_ROUTE, out, 30, 3, False, False)
            info = read_info(out / '対象路線')
            self.assertEqual(info['source_screening_stage'], '1st_screening')
            self.assertTrue(info['full_trip'])
            self.assertEqual(info['trip_count'], 6)
            area = project / '12_エリアデータ/15_area.geojson'
            area.write_bytes(area.read_bytes() + b'\n')
            _, targets = scan_project(project)
            self.assertIn('一致', targets[0].warning)
            self.assertFalse(analyze(project, targets[0], path50)['official'])

    def test_15_sidecars_and_rerun_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            output = fixture(project)
            self.assertEqual(len(list((output / '15_area_subtrip_csv').glob('*.csv'))), 6)
            self.assertEqual(read_info(output)['trip_index_sha256'], digest(output / '15_trip_index.csv'))
            with self.assertRaisesRegex(ValueError, '空の出力先'):
                area15.run_screening(area15.ScreeningConfig(project / 'first', project / '12_エリアデータ/15_area.geojson', output))

    def test_20_cli_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            output = fixture(project)
            proc = subprocess.run([sys.executable, str(ROOT / 'src/20_route_trip_extractor.py'), '--project', str(project), '--input', str(output), '--dry-run'], capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_route_performance_does_not_double_count_route_copies(self):
        performance = load('path_performance30', '30_route_performance.py')
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fixture(project)
            first = performance.analyze_project(project)['results'][0]['events']
            route_dir = project / route20.FOLDER_ROUTE
            shutil.copy2(route_dir / '対象路線.csv', route_dir / '第二路線.csv')
            second_folder = project / SECOND_FOLDER / '第二路線'
            shutil.copytree(project / SECOND_FOLDER / '対象路線', second_folder)
            info = read_info(second_folder)
            info['route_name'] = '第二路線'
            (second_folder / 'screening_info.json').write_text(json.dumps(info), encoding='utf-8')
            result = performance.analyze_project(project)
            self.assertTrue(first > 0)
            self.assertEqual([r['events'] for r in result['results']], [first, first])

    def test_intersection_engine_regression_against_pre_promotion(self):
        import types
        baseline = types.ModuleType('baseline_path50')
        baseline.__file__ = str(ROOT / 'src/unreleased/50_Path_Analysis.py')
        sys.modules[baseline.__name__] = baseline
        tk_stub = types.ModuleType('tkinter')
        tk_stub.filedialog = tk_stub.messagebox = None
        # Pin the pre-promotion baseline so this still works after committing the move.
        code = subprocess.check_output(['git', 'show', '6f24a3b97c9ecac49fff1b6b369034be3758030b:src/unreleased/50_Path_Analysis.py'], cwd=ROOT)
        with patch.dict(sys.modules, {'tkinter': tk_stub}):
            exec(compile(code, baseline.__file__, 'exec'), baseline.__dict__)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            trips = project / 'trips'
            trips.mkdir()
            points = project / 'cross.csv'
            points.write_text('crossroad_id,center_lon,center_lat,branch_no,branch_name,dir_deg\nx,135,35,1,A,90\nx,135,35,2,B,270\n', encoding='utf-8')
            for i, coords in enumerate(([134.995, 135, 135.005], [135.005, 135, 134.995])):
                rows = [row33('1', 1, '20250101090000', lon, 35) for lon in coords]
                # Baseline loadtxt uses the platform encoding; no BOM for this comparison.
                with (trips / f'{i}.csv').open('w', encoding='utf-8', newline='') as f:
                    csv.writer(f).writerows(rows)
            for engine, output in [(baseline, project / 'before'), (path50, project / 'after')]:
                outputs = engine.run_single_crossroad(trips, points, project / 'cross.jpg', output)
                self.assertTrue(all(p.exists() for p in outputs))
            for before in (project / 'before').glob('*.csv'):
                self.assertEqual(before.read_bytes(), (project / 'after' / before.name).read_bytes())
            log = (project / 'after/LOG.txt').read_text(encoding='utf-8')
            self.assertIn('A方向交通（流入側A）: 1', log)
            self.assertIn('B方向交通（流入側B）: 1', log)

if __name__ == '__main__':
    unittest.main()
