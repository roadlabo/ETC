"""Real Chromium rendering and numbered JPEG export using synthetic OD records."""
import importlib.util
import csv
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
ARTIFACTS = Path(__file__).resolve().parents[1] / 'logs'
ROOT = Path(os.environ.get('ETC_TEST_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'src'))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QFontDatabase, QFont
from PyQt6.QtTest import QTest
from common import od_analysis as engine
from common.od_map import map_html
from common import project_settings

spec = importlib.util.spec_from_file_location('od_ui', ROOT / 'src/40_UI_od_analysis.py')
ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)
APP = QApplication.instance() or QApplication(['od_ui_test'])
for font in Path('C:/Windows/Fonts').glob('YuGoth*.ttc'): QFontDatabase.addApplicationFont(str(font))
APP.setFont(QFont('Yu Gothic UI', 9))


class UITests(unittest.TestCase):
    def wait_for(self, predicate, timeout=15):
        until = time.monotonic() + timeout
        while time.monotonic() < until and not predicate(): QTest.qWait(100)
        self.assertTrue(predicate())

    def test_render_maps_and_numbered_jpeg(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project_settings.STATE_PATH = root / 'project.json'
            zones = [dict(name='中心部', points=[(133.98,35.06),(134.01,35.06),(134.01,35.08),(133.98,35.08)]),
                     dict(name='東エリア', points=[(134.015,35.06),(134.04,35.06),(134.04,35.08),(134.015,35.08)])]
            records = [dict(zip(engine.FIELDS,['test','20260901','火',str(i),'1',133.99+i*.0001,35.065,134.02+i*.0001,35.07,'OK',1])) for i in range(80)]
            result = engine.analyze(records,zones,['20260901','20260902'])
            for name,zonal in [('heatmap.html',False),('zones.html',True)]: (root/name).write_text(map_html(result,zonal),encoding='utf-8')
            window = ui.ODWindow(); window.resize(1366,850); window.show()
            window.loaded_records((records,0,[])); window.end.setDate(ui.QDate(2026,9,2)); window.show_result((result,root))
            window.tabs.setCurrentIndex(1)
            self.wait_for(lambda: window.heat.ready)
            window.tabs.setCurrentIndex(2)
            self.wait_for(lambda: window.zone.ready)
            window.tabs.setCurrentIndex(0)
            ARTIFACTS.mkdir(exist_ok=True)
            window.grab().save(str(ARTIFACTS/'od_matrix_preview.png'))
            for panel,index,prefix in [(window.heat,1,'ODheatmap'),(window.zone,2,'ODzoneheatmap')]:
                window.tabs.setCurrentIndex(index); QTest.qWait(500)
                panel.side.setCurrentIndex(1); panel.palette.setCurrentIndex(1); QTest.qWait(500)
                window.grab().save(str(ARTIFACTS/f'od_map_{index}_preview.png'))
                panel.save_jpeg(); self.wait_for(lambda:(root/f'{prefix}(1).jpg').is_file())
                first = (root/f'{prefix}(1).jpg').read_bytes()
                self.assertFalse(QImage(str(root/f'{prefix}(1).jpg')).isNull())
                panel.save_jpeg(); self.wait_for(lambda:(root/f'{prefix}(2).jpg').is_file())
                self.assertEqual(first,(root/f'{prefix}(1).jpg').read_bytes())
                image = QImage(str(root/f'{prefix}(1).jpg'))
                self.assertGreater(image.width(),500)
                colors = {image.pixelColor(x,y).name() for x in range(0,image.width(),15) for y in range(0,image.height(),15)}
                self.assertGreater(len(colors),30)
            window.close(); QTest.qWait(500)

    def test_worker_pipeline_from_screening_to_daily_matrix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project_settings.STATE_PATH = root / 'settings.json'
            source = root/'input'; source.mkdir(); zips=root/'zip'; zips.mkdir(); zones=root/'12_ゾーニングデータ'; zones.mkdir()
            sr=['']*16; sr[2:4]=['20260901','0001']; sr[8]='1'
            engine.write_csv(source/'trip.csv',[],[sr])
            zr=['']*15; zr[:2]=['20260901','0001']; zr[7]='1'; zr[11:15]=['134','35.07','134.01','35.08']
            text=io.StringIO(); csv.writer(text).writerow(zr)
            with zipfile.ZipFile(zips/'20260901.zip','w') as archive: archive.writestr('data.csv',text.getvalue())
            engine.write_csv(zones/'zones.csv',['中心',133.9,35,134.1,35,134.1,35.2,133.9,35.2],[])
            window=ui.ODWindow(); window.project.setText(str(root)); window.input.setText(str(source)); window.zipdir.setText(str(zips))
            errors=[]; window.failure=errors.append
            window.extract(); self.wait_for(lambda: bool(window.records) or bool(errors))
            self.assertEqual(errors,[]); self.wait_for(lambda: not window.worker.isRunning()); QTest.qWait(100)
            window.end.setDate(ui.QDate(2026,9,3)); window.excluded_dates={'20260902'}; window.day_count()
            window.analyze(); self.wait_for(lambda: window.result is not None or bool(errors))
            self.assertEqual(errors,[]); self.assertEqual(window.result['days'],2)
            self.assertEqual(window.table.item(0,0).text(),'0.50')
            self.assertTrue((window.output/'od_matrix(perday).csv').exists())
            self.wait_for(lambda:not window.worker.isRunning()); QTest.qWait(100); window.close(); QTest.qWait(500)


if __name__ == '__main__': unittest.main()
