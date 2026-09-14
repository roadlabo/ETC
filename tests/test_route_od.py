import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from common.route_od import load_zones, endpoint_label, matrix_data, matrix_html


class RouteODTest(unittest.TestCase):
    def test_only_12_zoning_folder_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = '西,134,35,134.1,35,134.1,35.1,134,35.1\n'
            (root / 'zones.csv').write_text(content, encoding='utf-8')
            (root / '14_エリアデータ').mkdir()
            (root / '14_エリアデータ/zones.csv').write_text(content, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '12_ゾーニングデータ'):
                load_zones(root)
            (root / '12_ゾーニングデータ').mkdir()
            (root / '12_ゾーニングデータ/zones.csv').write_text(content, encoding='utf-8')
            self.assertEqual(len(load_zones(root)), 1)

    def test_builder_names_with_commas_and_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '12_ゾーニングデータ').mkdir()
            (root / '12_ゾーニングデータ/zones.csv').write_text('河原町、伏見町,材木町,134,35,134.1,35,134.1,35.1,134,35.1\n', encoding='utf-8-sig')
            zones = load_zones(root)
            record = {'start_type': 'INSIDE', 'start_lon': 134.05, 'start_lat': 35.05}
            self.assertEqual(endpoint_label(record, 'start', zones), '内：河原町、伏見町,材木町')
            record['start_lon'] = 134
            self.assertEqual(endpoint_label(record, 'start', zones), '内：河原町、伏見町,材木町')
            self.assertEqual(endpoint_label(record, 'start', zones + [{**zones[0], 'name': '別エリア'}]), '内：エリア重複')
            record['start_lon'] = 133
            self.assertEqual(endpoint_label(record, 'start', zones), '内：エリア外')
            record.update(start_type='GATE', start_gate_id='G02')
            self.assertEqual(endpoint_label(record, 'start', zones), 'G02')

    def test_invalid_or_missing_polygons_require_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '12_ゾーニングデータ').mkdir()
            for content in ['', 'bad,134,35,134,35\n', 'bad,nan,35,134,35,135,36\n']:
                (root / '12_ゾーニングデータ/zones.csv').write_text(content, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '12_polygon_builder.bat'):
                    load_zones(root)

    def test_matrix_keeps_direction_diagonal_and_escapes_names(self):
        records = [dict(origin=a, destination=b, od_class=c) for a, b, c in [
            ('G02', '内：<西>', 'EXTERNAL_TO_INTERNAL'),
            ('内：<西>', 'G02', 'INTERNAL_TO_EXTERNAL'),
            ('内：<西>', '内：<西>', 'INTERNAL'),
            ('G02', 'G10', 'THROUGH'),
            ('分類不明', '分類不明', 'UNKNOWN')]]
        labels, counts = matrix_data(records)
        self.assertEqual(labels, ['G02', 'G10', '内：<西>'])
        self.assertEqual(sum(counts.values()), 4)
        self.assertEqual(counts['G10', 'G02'], 0)
        self.assertEqual(counts['内：<西>', '内：<西>'], 1)
        self.assertIn('内：&lt;西&gt;', matrix_html(labels, counts))


if __name__ == '__main__':
    unittest.main()
