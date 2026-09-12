import ast
import csv
import importlib.util
import math
import os
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
sys.path.insert(0, str(SRC))

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

perf = load('mode_perf', SRC / '31_crossroad_trip_performance.py')

class IntersectionModesTest(unittest.TestCase):
    def test_all_six_steps(self):
        # Execute the real nested branch function and record its interpolation requests.
        tree = ast.parse((SRC / '31_crossroad_trip_performance.py').read_text(encoding='utf-8'))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_infer_branch_3step')
        for mode, expected in [('normal', [(20,50),(20,70),(20,100),(10,40),(10,30),(10,20)]), ('small', [(10,25),(10,35),(10,50),(5,20),(5,15),(5,10)])]:
            for is_in in (True, False):
                requests = []
                env = dict(vars(perf), branch_distance_scale=perf.INTERSECTION_MODES[mode][1], points=[], cumdist=[])
                env['interpolate_point_at_distance'] = lambda p,c,d: requests.append(d)
                exec(compile(ast.Module(body=[fn], type_ignores=[]), '<branch>', 'exec'), env)
                env[fn.name](200, is_in)
                distances = [(200-f,200-n) if is_in else (200+n,200+f) for n,f in expected]
                self.assertEqual(requests, [d for pair in distances for d in pair])
        self.assertEqual([s['th'] for s in perf.BRANCH_JUDGE_STEPS], [30,35,40,30,30,30])
        self.assertEqual((perf.CROSSROAD_HIT_DIST_M,perf.CROSSROAD_SEG_HIT_DIST_M,perf.MEASURE_PRE_M,perf.MEASURE_POST_M,perf.CLOSEST_MIN_SEPARATION_M), (20,20,100,20,100))

    def test_ui_buttons_and_cli(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PyQt6.QtWidgets import QApplication, QPushButton, QWidget, QVBoxLayout, QHBoxLayout, QLabel
        app = QApplication.instance() or QApplication([])
        path = SRC / '31_32_UI_crossroad_performance_to_report.py'
        source = path.read_text(encoding='utf-8')
        # Build the actual STEP 4 widgets without launching the rest of the application.
        start = source.index('        self.intersection_mode = "normal"')
        end = source.index('        box1 = StepBox', start)
        import textwrap
        obj = SimpleNamespace(start_batch=Mock())
        env = dict(locals(), self=obj)
        exec(textwrap.dedent(source[start:end]), env)
        obj.btn_run.click(); obj.start_batch.assert_called_with('normal')
        obj.btn_run_small.click(); obj.start_batch.assert_called_with('small')
        self.assertIsInstance(env['run_buttons'], QHBoxLayout)
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_start_step31')
        env = {'Path': Path, '__file__': str(path)}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<ui>', 'exec'), env)
        for mode in ('normal', 'small'):
            obj = SimpleNamespace(cards={'A': SimpleNamespace(paths={'out31': 'unused.csv'})},
                project_dir=ROOT, spin_radius=SimpleNamespace(value=lambda:20), intersection_mode=mode,
                _selected_weekdays_for_cli=lambda:['MON'], _launch_process=Mock(),
                _ensure_file_unlock=lambda path, callback:callback())
            env[fn.name](obj, 'A')
            args = obj._launch_process.call_args.args[0]
            self.assertEqual(args[args.index('--intersection-mode')+1], mode)
            self.assertEqual(args[args.index('--radius-m')+1], '20')

    def test_cli_baseline_and_consumers(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cross_dir = project / '11_交差点(Point)データ'; cross_dir.mkdir()
            input_dir = project / '20_第２スクリーニング' / 'A'; input_dir.mkdir(parents=True)
            cross = cross_dir / 'A.csv'
            with cross.open('w', encoding='cp932', newline='') as f:
                w=csv.writer(f); w.writerow(['ID','lon','lat','branch','unused','dir_deg'])
                for b,angle in [(1,270),(2,90),(3,0)]: w.writerow(['A',139,35,b,'',angle])
            # A west-to-east straight trip and a trip turning north 20m after A.
            with (input_dir/'trips.csv').open('w',encoding='cp932',newline='') as f:
                w=csv.writer(f)
                for trip in (1,2):
                    for i,d in enumerate(range(-150,151,5)):
                        x,y = (d,0) if trip==1 or d<=20 else (20,d-20)
                        row=['']*16
                        row[2:7]=['20250102','run','1','1',(datetime(2025,1,2,8)+timedelta(seconds=i)).strftime('%Y%m%d%H%M%S')]
                        row[8]=str(trip)
                        row[14]=139+x/(perf.EARTH_RADIUS_M*math.pi/180*math.cos(math.radians(35)))
                        row[15]=35+y/(perf.EARTH_RADIUS_M*math.pi/180)
                        w.writerow(row)
            baseline = project/'baseline.py'
            baseline.write_bytes(subprocess.check_output(['git','show','HEAD:src/31_crossroad_trip_performance.py'],cwd=ROOT))
            output = project/'31_交差点パフォーマンス'/'A_performance.csv'
            def run(script, mode=None):
                args=[sys.executable,str(script),'--project',str(project),'--radius-m','20']
                if mode: args += ['--intersection-mode',mode]
                result=subprocess.run(args,capture_output=True)
                self.assertEqual(result.returncode,0,result.stdout.decode('utf-8',errors='replace')+result.stderr.decode('utf-8',errors='replace'))
                with output.open(encoding='cp932',newline='') as f: return list(csv.DictReader(f))
            old=run(baseline)
            normal=run(SRC/'31_crossroad_trip_performance.py','normal')
            default=run(SRC/'31_crossroad_trip_performance.py')
            self.assertEqual(normal,default)
            self.assertEqual(old,[{k:r[k] for k in old[0]} for r in normal])
            small=run(SRC/'31_crossroad_trip_performance.py','small')
            self.assertEqual(len(small),2)
            self.assertEqual(normal[1]['流出枝番'],'3')
            self.assertEqual(small[1]['流出枝番'],'2')
            for a,b in zip(normal,small):
                for key in ['所要時間(s)','計測距離(m)','計測区間_前(m)','計測区間_後(m)','中心最近接距離(m)','中心最近接位置(m)','所要時間算出可否']:
                    self.assertEqual(a[key],b[key],key)
                self.assertEqual(b['枝判定モード'],'小交差点')
                self.assertEqual(b['枝判定距離倍率'],'0.5')
                self.assertEqual(b['角度算出方式'],'IN:10-25m/OUT:10-25m')
            report=load('mode_report',SRC/'32_crossroad_report.py')
            viewer=load('mode_viewer',SRC/'33_branch_check.py')
            for rows in (old,normal,small):
                with output.open('w',encoding='cp932',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
                report.create_excel_report_headless(cross,project/'missing.jpg',output,project/'report.xlsx')
                df=viewer.read_csv_safely(str(output));viewer.ensure_columns(df,viewer.REQUIRED_COLS)
                for key in ['流入枝番','流出枝番','流入角度deg','流出角度deg','流入角度差(deg)','流出角度差(deg)','角度算出方式']:
                    self.assertIn(key,df.columns)
                self.assertEqual(df['角度算出方式'].tolist(),[r['角度算出方式'] for r in rows])
                self.assertTrue(Path(viewer.run_without_gui(['--csv',str(output)])).exists())

if __name__ == '__main__': unittest.main()
