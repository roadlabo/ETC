import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from common import od_analysis as od
from common.od_map import map_html


class ODTests(unittest.TestCase):
    def row(self, date='20260901', opid='001', trip='1', status='OK', coords=(133.9, 35.05, 134, 35.1)):
        return dict(zip(od.FIELDS, ['fixture', date, '火', opid, trip, *coords, status, 1]))

    def test_original_trip_keys_nested_csv_and_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / '15/15_area_subtrip_csv'; source.mkdir(parents=True)
            zips = root / 'zip'; zips.mkdir()
            row = [''] * 16; row[2:4] = ['20260901', '001']; row[8] = '01'
            for name in ('trip.csv', 'trip_s02.csv'):
                od.write_csv(source / name, [], [row, row])
            later = row.copy(); later[2] = '20260902'; later[8] = '1'
            od.write_csv(source / 'next.csv', [], [later])
            sr = [''] * 15; sr[0:2] = ['20260901', '001']; sr[7] = '1'; sr[11:15] = ['133.9', '35.05', '134', '35.1']
            stream = io.StringIO(); csv.writer(stream).writerow(sr)
            with zipfile.ZipFile(zips / 'unknown-name.zip', 'w') as archive:
                archive.writestr('folder/data.csv', stream.getvalue().encode('cp932'))
            output = od.extract(source.parent, zips, root / 'result.csv')
            records, duplicates = od.read_od([output, output])
            self.assertEqual(len(records), 2); self.assertEqual(duplicates, 2)
            self.assertEqual(records[0]['src_files_count'], '2')
            self.assertEqual(records[0]['status'], 'OK'); self.assertEqual(records[1]['status'], 'MISSING_OD')
            self.assertEqual(records[0]['opid'], '001')

    def test_zero_days_boundary_overlap_and_invalid_coordinates(self):
        zones = [dict(name='A', points=[(133.8,35), (133.95,35), (133.95,35.2), (133.8,35.2)]),
                 dict(name='B', points=[(133.95,35), (134.1,35), (134.1,35.2), (133.95,35.2)])]
        dates = od.target_dates('20260901', '20260903', set(range(7)))
        records = [self.row(), self.row(opid='002', coords=(133.95,35.05,135,36)),
                   self.row(opid='003', coords=(float('nan'),35,134,35)), self.row(opid='004', status='MISSING_OD')]
        result = od.analyze(records, zones, dates)
        self.assertEqual(result['days'], 3); self.assertEqual(len(result['points']), 2)
        self.assertEqual(result['matrix']['A','B'], 1)
        self.assertEqual(result['matrix']['【ゾーン重複】','【区域外】'], 1)
        self.assertEqual(result['excluded'], {'座標不正':1, 'MISSING_OD':1})
        with tempfile.TemporaryDirectory() as temp:
            output = od.export(result, Path(temp) / 'out')
            with (output / 'od_matrix(perday).csv').open(encoding='utf-8-sig') as stream:
                rows = list(csv.reader(stream))
            self.assertAlmostEqual(float(rows[-1][-1]), 2/3)
            self.assertEqual(json.loads((output / 'analysis.json').read_text(encoding='utf-8'))['target_days'], 3)
        self.assertIn('window.updateOD', map_html(result))

    def test_conflicting_input_and_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'od.csv'
            od.write_csv(file, od.FIELDS, [self.row().values(), self.row(coords=(130,35,134,35)).values()])
            with self.assertRaisesRegex(ValueError, '異なるOD'): od.read_od([file])
            with self.assertRaises(InterruptedError): od.read_od([file], cancel=lambda: True)

    def test_zone_csv_and_weekdays(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'zones.csv'
            od.write_csv(file, ['A',133,35,134,35,134,36], [])
            self.assertEqual(len(od.load_zones(temp)),1)
        self.assertEqual(od.target_dates('20260901','20260907',{0}),['20260907'])
        with self.assertRaises(ValueError): od.target_dates('20260907','20260901',{0})


if __name__ == '__main__': unittest.main()
