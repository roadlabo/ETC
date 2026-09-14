import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from test_area_screening import write_area, area15
from common.screening import read_manual_gates, assign_nearest_gates, project_area_path

spec = importlib.util.spec_from_file_location('test_native_builder14', ROOT / 'src/14_area_builder.py')
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)

# Keep one QApplication alive across all WebEngine windows in this process.
from PyQt6.QtWidgets import QApplication
TEST_APP = QApplication.instance() or QApplication(['test_manual_gates'])


class ManualGateTests(unittest.TestCase):
    def test_start_without_project_shows_editor_without_dialog(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QEventLoop, QTimer
        app = QApplication.instance() or QApplication(['test_manual_gates'])
        with patch.object(builder.QFileDialog, 'getExistingDirectory') as choose:
            window = builder.MainWindow()
            window.show()
            loop = QEventLoop()
            state = []
            timer = QTimer()
            def receive(value):
                if value and value.get('ready'):
                    state.append(value)
                    loop.quit()
            def poll():
                window.web.page().runJavaScript("""(() => {
                    map.fire('click', {latlng:L.latLng(35, 135)});
                    document.getElementById('loadGeojson').click();
                    return {ready:document.getElementById('projectStatus')?.textContent === 'プロジェクト未選択',
                            disabled:document.getElementById('saveGeojson')?.disabled,
                            locked:document.getElementById('editingControls').disabled && document.getElementById('map').inert,
                            points:state.official.length, dirty:state.dirty};
                })()""", receive)
            timer.timeout.connect(poll)
            timer.start(100)
            QTimer.singleShot(15000, loop.quit)
            loop.exec()
            timer.stop()
            self.assertTrue(window.isVisible())
            window.close()
            choose.assert_not_called()
            self.assertTrue(state, 'Editor did not initialize without a project')
            self.assertTrue(state[0]['disabled'])
            self.assertTrue(state[0]['locked'])
            self.assertEqual(state[0]['points'], 0)
            self.assertFalse(state[0]['dirty'])
            self.assertFalse(json.loads(window.bridge.saveArea('{}'))['ok'])

    def test_nearest_gate_without_radius_limit_and_inside_unchanged(self):
        gates = [{'gate_id': 'G02', 'lon': 1, 'lat': 0}, {'gate_id': 'G01', 'lon': -1, 'lat': 0}]
        rows = [dict(start_type='GATE', start_lon=x, start_lat=0, end_type='INSIDE', end_gate_id='stale') for x in (-.1, .1, 0)]
        assign_nearest_gates(rows, gates)
        self.assertEqual([r['start_gate_id'] for r in rows], ['G01', 'G02', 'G01'])
        self.assertTrue(all(r['end_gate_id'] == '' for r in rows))
        self.assertEqual(len(gates), 2)

    def test_direct_save_roundtrip_close_gates_and_invalid_save_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            source = project / 'import.geojson'
            write_area(source)
            data = json.loads(source.read_text(encoding='utf-8'))
            close = copy.deepcopy(data['features'][-2])
            close['properties']['gate_id'] = 'G99'
            close['geometry']['coordinates'][1] += .0001
            data['features'].append(close)
            target = Path(builder.save_project(project, data))
            self.assertEqual(target, project_area_path(project))
            self.assertEqual(builder.read_project(project)['data'], data)
            self.assertEqual(len(read_manual_gates(data)), 3)
            before = target.read_bytes()
            data['features'][-1]['properties']['gate_id'] = 'G01'
            with self.assertRaisesRegex(ValueError, '重複'):
                builder.save_project(project, data)
            self.assertEqual(target.read_bytes(), before)

    def test_missing_gates_blocks_15_before_any_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'area.geojson'
            write_area(path)
            data = json.loads(path.read_text(encoding='utf-8'))
            data['features'] = data['features'][:2]
            path.write_text(json.dumps(data), encoding='utf-8')
            out = root / 'output'
            with self.assertRaisesRegex(ValueError, '14_area_builder.bat'):
                area15.run_screening(area15.ScreeningConfig(root, path, out))
            self.assertFalse(out.exists())

    def test_native_webchannel_load_and_save(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QEventLoop, QTimer
        app = QApplication.instance() or QApplication(['test_manual_gates'])
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            path = project_area_path(project)
            path.parent.mkdir()
            write_area(path)
            window = builder.MainWindow(project)
            window.show()
            loop = QEventLoop()
            timer = QTimer()
            phase = {'started': False, 'saved': False}
            def on_ready(ready):
                if ready and not phase['started']:
                    phase['started'] = True
                    window.web.page().runJavaScript("""
                        document.getElementById('mode').value = 'gates';
                        document.getElementById('mode').onchange();
                        if (layers.analysis.getLayers().some(layer => layer instanceof L.Marker)) throw Error('Editable area nodes remain in gate mode');
                        const previousAnalysis = state.analysis;
                        state.analysis = [];
                        const previousCount = state.gates.length;
                        map.fire('click', {latlng:L.latLng(35.0051, 135)});
                        if (state.gates.length !== previousCount || !document.getElementById('status').textContent.includes('分析エリアを先に設定してください')) throw Error('Missing-area guard failed');
                        state.analysis = previousAnalysis;
                        const previousGates = state.gates;
                        state.gates = [];
                        if (validate() !== 'ゲートを設定してください。') throw Error('Missing-gate guard failed');
                        state.gates = previousGates;
                        map.fire('click', {latlng:L.latLng(35.0051, 135)});
                        const list = document.getElementById('gateList');
                        list.value = 'G03'; list.onchange({target:list});
                        document.getElementById('deleteVertex').onclick();
                        document.getElementById('undo').onclick();
                        list.value = 'G03'; list.onchange({target:list});
                        window.prompt = () => '保存テスト';
                        document.getElementById('renameGate').onclick();
                        const marker = layers.gates.getLayers()[2];
                        marker.fire('dragstart'); marker.setLatLng([35.0052, 135.0001]); marker.fire('dragend');
                        document.getElementById('saveGeojson').onclick();
                    """)
            def poll():
                data = json.loads(path.read_text(encoding='utf-8'))
                if any(f['properties'].get('name') == '保存テスト' for f in data['features']):
                    phase['saved'] = True
                    loop.quit()
                elif not phase['started']:
                    window.web.page().runJavaScript("typeof nativeBridge !== 'undefined' && nativeBridge !== null && !document.getElementById('saveGeojson').disabled", on_ready)
            timer.timeout.connect(poll)
            timer.start(100)
            QTimer.singleShot(15000, loop.quit)
            loop.exec()
            print('Native editor result:', phase, flush=True)
            timer.stop()
            diagnostics = []
            if not phase['started']:
                window.web.page().runJavaScript("({status:document.getElementById('status')?.textContent, bridge:typeof nativeBridge, channel:typeof QWebChannel, leaflet:typeof L})",
                                               lambda value: (diagnostics.append(value), loop.quit()))
                QTimer.singleShot(2000, loop.quit)
                loop.exec()
            window.close()
            self.assertTrue(phase['started'], f'WebChannel initialization failed: {diagnostics}')
            self.assertTrue(phase['saved'], 'Native save failed')
            saved = builder.read_project(project)['data']
            gates = read_manual_gates(saved)
            self.assertEqual([g['gate_id'] for g in gates], ['G01', 'G02', 'G03'])
            self.assertAlmostEqual(gates[2]['lat'], 35.0052)
            self.assertAlmostEqual(gates[2]['lon'], 135.0)
            self.assertEqual(saved['metadata']['next_gate_number'], 4)


if __name__ == '__main__':
    unittest.main()
