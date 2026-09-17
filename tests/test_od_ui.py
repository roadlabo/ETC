"""Real Chromium rendering and numbered JPEG export using synthetic OD records."""
import importlib.util
import csv
import io
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
from openpyxl import load_workbook

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
ARTIFACTS = Path(__file__).resolve().parents[1] / 'logs'
ROOT = Path(os.environ.get('ETC_TEST_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'src'))
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox
from PyQt6.QtGui import QImage, QFontDatabase, QFont
from PyQt6.QtTest import QTest
from common import od_analysis as engine
from common.od_map import map_html
from common import project_settings
from od_fixtures import area_fixture

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

    def test_blank_dates_folder_range_and_spin_button_clicks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); project_settings.STATE_PATH=root/'settings.json'
            window=ui.ODWindow(); window.show(); QTest.qWait(200)
            self.assertTrue(window.isMaximized())
            self.assertFalse(window.options.isEnabled()); self.assertFalse(window.run.isEnabled())
            self.assertTrue(window.create.isEnabled()); self.assertTrue(window.load_button.isEnabled())
            self.assertEqual(window.create.geometry().top(),window.load_button.geometry().top())
            self.assertFalse(hasattr(window,'create_both'))
            window.showNormal(); window.resize(1460,900)
            self.assertEqual(window.start.text().strip(),''); self.assertEqual(window.end.text().strip(),'')
            source=root/'input'; source.mkdir()
            records=[]
            for date in ['20260912','20260831','20260903']:
                row=['']*16; row[2:4]=[date,'001']; row[8]='1'; records.append(row)
            engine.write_csv(source/'trip.csv',[],records)
            window.input.setText(str(source))
            self.wait_for(lambda:window.start.date()==ui.QDate(2026,8,31))
            self.assertEqual(window.end.date(),ui.QDate(2026,9,12))
            self.assertFalse(window.options.isEnabled())
            window.project.setText(str(root))
            with patch.object(ui.QFileDialog,'getExistingDirectory',return_value='') as dialog:
                window.input.browse_button.click()
                self.assertEqual(dialog.call_args.args[2],str(root))
            for panel,index in [(window.heat,1),(window.zone,2)]:
                window.tabs.setCurrentIndex(index); QTest.qWait(200)
                widgets=[panel.opacity,panel.maximum]
                if not panel.zonal: widgets += [panel.radius,panel.blur,panel.gain]
                for widget in widgets:
                    original=widget.value()
                    option=QStyleOptionSpinBox(); widget.initStyleOption(option)
                    up=widget.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,option,QStyle.SubControl.SC_SpinBoxUp,widget)
                    down=widget.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,option,QStyle.SubControl.SC_SpinBoxDown,widget)
                    self.assertFalse(up.intersects(down))
                    QTest.mouseClick(widget,ui.Qt.MouseButton.LeftButton,pos=up.center())
                    self.assertGreater(widget.value(),original)
                    QTest.mouseClick(widget,ui.Qt.MouseButton.LeftButton,pos=down.center())
                    self.assertAlmostEqual(widget.value(),original)
            window.input.clear(); self.assertEqual(window.start.text().strip(),'')
            window.close(); QTest.qWait(300)

    def test_gate_maps_render_labels_toggle_and_traffic_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); project_settings.STATE_PATH=root/'settings.json'
            source, data, zones=area_fixture(root)
            path=engine.extract_trip(source,root/'od.csv'); records,_=engine.read_od([path])
            result=engine.analyze(records,zones,['20260901','20260902'])
            for name,zonal in [('heatmap.html',False),('zones.html',True)]:
                (root/engine.result_name(result,name)).write_text(map_html(result,zonal),encoding='utf-8')
            window=ui.ODWindow(); window.showNormal(); window.resize(1460,900); window.loaded_records((records,0,[str(path)])); window.show_result((result,root))
            self.assertEqual(window.table.item(4,4).text(),'2.00')
            self.assertEqual([window.table.item(r,c).text() for r,c in [(0,1),(0,3),(2,1),(2,3)]],['0.50']*4)
            self.assertNotEqual(window.table.item(0,1).background().color(),window.table.item(0,0).background().color())
            self.assertLessEqual(window.table.columnWidth(0),115)
            ARTIFACTS.mkdir(exist_ok=True); QTest.qWait(200); window.grab().save(str(ARTIFACTS/'od_combined_matrix_preview.png'))
            for panel,index in [(window.heat,1),(window.zone,2)]:
                window.tabs.setCurrentIndex(index); self.wait_for(lambda:panel.ready)
                panel.side.buttons[1].click(); panel.palette.buttons[3].click()
                self.assertEqual(panel.settings()['side'],'d'); self.assertEqual(panel.settings()['palette'],'透明→青→赤')
                self.assertEqual(sum(b.isChecked() for b in panel.side.buttons),1)
                colors=[]
                panel.web.page().runJavaScript('[color(0),color(1/3),color(1),settings.side]',colors.append)
                self.wait_for(lambda:bool(colors)); self.assertEqual(colors[0],[[41,121,212],[41,121,212],[215,25,28],'d'])
                zero=[]
                panel.web.page().runJavaScript('gateLayer.getLayers()[0].options.fillOpacity',zero.append)
                self.wait_for(lambda:bool(zero)); self.assertEqual(zero[0],0)
                if not panel.zonal:
                    pixels=[]
                    panel.web.page().runJavaScript("canvas.getContext('2d').getImageData(0,0,1,1).data[3]",pixels.append)
                    self.wait_for(lambda:bool(pixels)); self.assertEqual(pixels[0],0)
                    self.assertEqual(panel.opacity.geometry().center().y(),panel.radius.geometry().center().y())
                panel.side.buttons[0].click()
                values=[]
                panel.web.page().runJavaScript('[gateLayer.getLayers().length,boundaryLayer.getLayers().length,data.o.length,data.d.length,gateLayer.getLayers().filter(m=>m.getTooltip()).length]',values.append)
                self.wait_for(lambda:bool(values)); self.assertEqual(values[0],[2,2,1,1,2])
                panel.gate_labels.setChecked(False); QTest.qWait(200); values=[]
                panel.web.page().runJavaScript('gateLayer.getLayers().filter(m=>m.getTooltip()).length',values.append)
                self.wait_for(lambda:bool(values)); self.assertEqual(values[0],0)
                panel.gate_labels.setChecked(True); QTest.qWait(300)
                ARTIFACTS.mkdir(exist_ok=True); window.grab().save(str(ARTIFACTS/f'od_gate_map_{index}_preview.png'))
            window.close(); QTest.qWait(300)

    def test_render_maps_and_numbered_jpeg(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project_settings.STATE_PATH = root / 'project.json'
            zones = [dict(name='中心部', points=[(133.98,35.06),(134.01,35.06),(134.01,35.08),(133.98,35.08)]),
                     dict(name='東エリア', points=[(134.015,35.06),(134.04,35.06),(134.04,35.08),(134.015,35.08)])]
            records = [dict(zip(engine.FIELDS,['test','20260901','火',str(i),'1',133.99+i*.0001,35.065,134.02+i*.0001,35.07,'OK',1])) for i in range(80)]
            result = engine.analyze(records,zones,['20260901','20260902'])
            for name,zonal in [('heatmap.html',False),('zones.html',True)]: (root/engine.result_name(result,name)).write_text(map_html(result,zonal),encoding='utf-8')
            window = ui.ODWindow(); window.showNormal(); window.resize(1366,850)
            window.loaded_records((records,0,[])); window.end.setDate(ui.QDate(2026,9,2)); window.show_result((result,root))
            window.tabs.setCurrentIndex(1)
            self.wait_for(lambda: window.heat.ready)
            window.tabs.setCurrentIndex(2)
            self.wait_for(lambda: window.zone.ready)
            window.tabs.setCurrentIndex(0)
            ARTIFACTS.mkdir(exist_ok=True)
            window.grab().save(str(ARTIFACTS/'od_matrix_preview.png'))
            for panel,index,prefix in [(window.heat,1,'ODheatmap'),(window.zone,2,'ODzoneheatmap')]:
                prefix=engine.prefix(result['method'])+prefix
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
            sr[14:16]=['133.95','35.05']
            engine.write_csv(source/'trip.csv',[],[sr])
            zr=['']*15; zr[:2]=['20260901','0001']; zr[7]='1'; zr[11:15]=['134','35.07','134.01','35.08']
            text=io.StringIO(); csv.writer(text).writerow(zr)
            with zipfile.ZipFile(zips/'20260901.zip','w') as archive: archive.writestr('data.csv',text.getvalue())
            engine.write_csv(zones/'zones.csv',['中心',133.9,35,134.1,35,134.1,35.2,133.9,35.2],[])
            window=ui.ODWindow(); window.show(); window.project.setText(str(root)); window.input.setText(str(source)); window.zipdir.setText(str(zips))
            errors=[]; window.failure=errors.append
            window.extract(); self.wait_for(lambda: bool(window.records) or bool(errors))
            self.assertEqual(errors,[]); self.wait_for(lambda: not window.worker.isRunning()); QTest.qWait(100)
            self.assertTrue(window.options.isEnabled()); self.assertTrue(window.run.isEnabled())
            window.end.setDate(ui.QDate(2026,9,3)); window.excluded_dates={'20260902'}; window.day_count()
            window.analyze(); self.wait_for(lambda: window.result is not None or bool(errors))
            self.assertEqual(errors,[]); self.assertEqual(window.result['days'],2)
            self.assertEqual(window.table.item(0,0).text(),'0.50')
            style_result=window.result
            self.assertTrue((window.output/engine.result_name(style_result,'OD集計.xlsx')).exists())
            first_output=window.output
            book=load_workbook(window.output/engine.result_name(style_result,'OD集計.xlsx'))
            conditions=dict(book['集計条件'].values)
            self.assertEqual(conditions['プロジェクトフォルダ名'],str(root.resolve()))
            self.assertEqual(conditions['スクリーニングフォルダ名'],str(source.resolve()))
            self.assertEqual(conditions['ODの由来'],'様式1-3由来')
            book.close()
            self.wait_for(lambda:not window.worker.isRunning()); QTest.qWait(100)
            window.method_buttons['trip'].click()
            self.assertEqual(window.method,'trip'); self.assertIsNone(window.result)
            self.assertFalse(window.options.isEnabled()); self.assertFalse(window.run.isEnabled())
            window.extract(); self.wait_for(lambda:bool(window.records) or bool(errors))
            self.assertEqual(errors,[]); self.wait_for(lambda:not window.worker.isRunning()); QTest.qWait(100)
            window.end.setDate(ui.QDate(2026,9,3)); window.excluded_dates={'20260902'}; window.day_count()
            self.assertEqual(window.records[0]['o_lon'],'133.95'); self.assertFalse(window.zipdir.isEnabled())
            window.analyze(); self.wait_for(lambda:window.result is not None or bool(errors)); self.assertEqual(errors,[])
            self.assertEqual(window.result['method'],'trip')
            self.assertEqual(window.result['dates'],style_result['dates'])
            self.assertTrue((window.output/engine.result_name(window.result,'OD集計.xlsx')).exists())
            self.assertEqual(window.output.parent,root/'40_OD分析')
            self.assertNotEqual(window.output,first_output)
            self.assertTrue(all(p.name.startswith(('【様式1-3OD】','【トリップOD】')) for p in window.output.iterdir()))
            self.wait_for(lambda:not window.worker.isRunning()); QTest.qWait(100)
            window.tabs.setCurrentIndex(1); self.wait_for(lambda:window.heat.ready); QTest.qWait(300)
            window.heat.save_jpeg()
            self.wait_for(lambda:(window.output/'【トリップOD】ODheatmap(1).jpg').is_file())
            self.assertFalse(QImage(str(window.output/'【トリップOD】ODheatmap(1).jpg')).isNull())
            ARTIFACTS.mkdir(exist_ok=True); window.grab().save(str(ARTIFACTS/'od_trip_mode_preview.png'))
            window.method_buttons['style13'].click()
            self.assertIs(window.result,style_result); self.assertEqual(window.result['days'],2)
            self.assertEqual(window.output,first_output)
            self.wait_for(lambda:not window.worker.isRunning()); window.analyze()
            self.wait_for(lambda:window.result is not style_result)
            self.assertNotEqual(window.output,first_output)
            self.assertTrue((first_output/engine.result_name(style_result,'OD集計.xlsx')).exists())
            self.wait_for(lambda:not window.worker.isRunning())
            window.close(); QTest.qWait(500)

    def test_direct_embedded_entrypoint_and_batch_from_other_directory(self):
        with tempfile.TemporaryDirectory(prefix='OD 起動 ') as temp:
            env=os.environ.copy(); env.pop('PYTHONPATH',None); env['ETC_PROJECT_STATE']=str(Path(temp)/'project.json')
            python=ROOT/'runtime/python/python.exe'
            for command in ([str(python),'-B','-X','utf8',str(ROOT/'src/40_UI_od_analysis.py'),'--smoke-test'],
                            ['cmd','/d','/c',str(ROOT/'bat/40_UI_od_analysis.bat'),'--help']):
                result=subprocess.run(command,cwd=temp,env=env,capture_output=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))


if __name__ == '__main__': unittest.main()
