import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from test_area_screening import write_area, write_trip_file, row33
from common.screening import project_area_path, read_info

spec = importlib.util.spec_from_file_location('layout_ui15', ROOT / 'src/15_UI_area_screening.py')
ui = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ui
spec.loader.exec_module(ui)


class ProjectLayoutTest(unittest.TestCase):
    def test_15_ui_selects_project_area_and_reports_missing_builder(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp, patch.object(ui, 'STATE_PATH', Path(tmp) / 'state.json'):
            project = Path(tmp)
            window = ui.MainWindow()
            try:
                with patch.object(ui.QFileDialog, 'getExistingDirectory', return_value=tmp), patch.object(ui.QMessageBox, 'warning') as warning:
                    window._pick_area()
                    self.assertEqual(Path(window.area_path.text()), project_area_path(project))
                    self.assertEqual(Path(window.output_dir.text()), project / ui.area15.FOLDER_OUT)
                    self.assertIn('14_area_builder.bat', warning.call_args.args[2])
                project_area_path(project).parent.mkdir()
                write_area(project_area_path(project))
                with patch.object(ui.QFileDialog, 'getExistingDirectory', return_value=tmp), patch.object(ui.QMessageBox, 'warning') as warning:
                    window._pick_area()
                    warning.assert_not_called()
            finally:
                window.close()

    def test_15_cli_uses_14_file_and_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            source = project / 'first'
            source.mkdir()
            args = ['--input', str(source), '--project-dir', tmp, '--output', str(project / 'result')]
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(ui.area15.main(args), 2)
            self.assertIn('14_area_builder.bat', errors.getvalue())
            project_area_path(project).parent.mkdir()
            write_area(project_area_path(project))
            write_trip_file(source / 'trip.csv', [row33('1', 1, '20250101090000', 135.004, 35.005),
                                                 row33('1', 1, '20250101090100', 135.006, 35.005)])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ui.area15.main(args), 0)
            self.assertEqual(read_info(project / 'result')['area_file'], '14_エリアデータ/14_area.geojson')


if __name__ == '__main__':
    unittest.main()
