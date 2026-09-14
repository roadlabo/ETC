import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from common import project_settings as settings
from PyQt6 import QtWebEngineWidgets
from PyQt6.QtWidgets import QApplication
TEST_APP = QApplication.instance() or QApplication(['shared_project_test'])


class SharedProjectTests(unittest.TestCase):
    def test_persistence_across_processes_and_invalid_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / '日本語 project'
            project.mkdir()
            state = Path(tmp) / 'settings/project.json'
            with patch.object(settings, 'STATE_PATH', state):
                self.assertIsNone(settings.get_project())
                settings.set_project(project)
                self.assertEqual(settings.get_project(), project.resolve())
                code = ('import sys;sys.path.insert(0,sys.argv[1]);from common import project_settings as s;'
                        'from pathlib import Path;s.STATE_PATH=Path(sys.argv[2]);print(s.get_project())')
                result = subprocess.check_output([sys.executable, '-X', 'utf8', '-c', code, str(ROOT / 'src'), str(state)], encoding='utf-8')
                self.assertEqual(result.strip(), str(project.resolve()))
                project.rmdir()
                self.assertIsNone(settings.get_project())
                state.write_text('broken', encoding='utf-8')
                self.assertIsNone(settings.get_project())

    def test_native_zoning_saves_bom_and_rejects_escape(self):
        spec = importlib.util.spec_from_file_location('zoning12', ROOT / 'src/12_polygon_builder.py')
        zoning = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(zoning)
        data = '\ufeffZone,135,35,136,35,135,36\r\n'.encode('utf-8')
        payload = base64.b64encode(data).decode('ascii')
        with tempfile.TemporaryDirectory() as tmp:
            saved = zoning.save_zoning(tmp, '12_ゾーニングデータ', 'zones.csv', payload)
            self.assertEqual(Path(saved).read_bytes(), data)
            for name in ['../escape.csv', 'bad.csv.', 'bad.txt']:
                with self.assertRaises(ValueError):
                    zoning.save_zoning(tmp, '12_ゾーニングデータ', name, payload)

    def test_tiles_live_outside_src(self):
        import offline_leaflet
        expected = (ROOT / 'tiles/gsi_pale').as_uri() + '/{z}/{x}/{y}.png'
        self.assertEqual(offline_leaflet.LOCAL_GSI_TILE_TEMPLATE, expected)
        self.assertFalse((ROOT / 'src/tiles').exists())
        self.assertTrue((ROOT / 'tiles/gsi_pale').is_dir())

    def test_sampler_restores_project_without_picker(self):
        from PyQt6.QtWidgets import QApplication, QFileDialog
        from PyQt6.QtCore import QEventLoop, QTimer
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, 'STATE_PATH', Path(tmp) / 'state.json'):
            settings.set_project(tmp)
            for name in ['10_UI_route_sampler.py', '11_UI_crossroad_sampler.py']:
                spec = importlib.util.spec_from_file_location('shared_' + name[:2], ROOT / 'src' / name)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                with patch.object(QFileDialog, 'getExistingDirectory') as picker:
                    window = module.MainWindow()
                    loop = QEventLoop()
                    QTimer.singleShot(200, loop.quit)
                    loop.exec()
                    self.assertEqual(window.project_dir, Path(tmp).resolve())
                    picker.assert_not_called()
                    window.close()

    def test_native_zoning_webchannel_restore_and_save(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QEventLoop, QTimer
        app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location('zoning12_web', ROOT / 'src/12_polygon_builder.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.Page.javaScriptAlert = lambda self, origin, text: print('Zoning alert:', text, flush=True)
        module.Page.javaScriptConsoleMessage = lambda self, level, text, line, source: print('Zoning JS:', text, flush=True)
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, 'STATE_PATH', Path(tmp) / 'state.json'):
            settings.set_project(tmp)
            window = module.MainWindow()
            window.show()
            loop = QEventLoop()
            timer = QTimer()
            target = Path(tmp) / '12_ゾーニングデータ/test.csv'
            def poll():
                if target.exists():
                    loop.quit()
                else:
                    window.web.page().runJavaScript("""
                        if (document.getElementById('projectStatus')?.textContent.includes('12_ゾーニングデータ') && !window.testSaving) {
                            window.testSaving = true;
                            ProjectOutput.save('12_ゾーニングデータ', 'test.csv', new Blob(['test']));
                        }
                    """)
            timer.timeout.connect(poll)
            timer.start(100)
            QTimer.singleShot(10000, loop.quit)
            loop.exec()
            timer.stop()
            window.close()
            self.assertTrue(target.exists(), 'WebChannel project restore/save failed')
            self.assertEqual(target.read_text(), 'test')

if __name__ == '__main__':
    unittest.main()
