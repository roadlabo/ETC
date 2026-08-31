import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "15_area_screening.py"
spec = importlib.util.spec_from_file_location("area15", MODULE_PATH)
area15 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = area15
assert spec and spec.loader
spec.loader.exec_module(area15)


def write_area(path: Path) -> None:
    features = [
        {
            "type": "Feature",
            "properties": {"area15_role": "official_area", "name": "正式区域"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[135.002, 35.002], [135.008, 35.002], [135.008, 35.008], [135.002, 35.008], [135.002, 35.002]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"area15_role": "analysis_area", "name": "分析区域"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[135.000, 35.000], [135.010, 35.000], [135.010, 35.010], [135.000, 35.010], [135.000, 35.000]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"area15_role": "gate", "gate_id": "ignored"},
            "geometry": {"type": "Point", "coordinates": [135.000, 35.005]},
        },
    ]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")


def write_invalid_area(path: Path) -> None:
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"area15_role": "official_area"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
            },
            {
                "type": "Feature",
                "properties": {"area15_role": "analysis_area"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
            },
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def row(opid: str, trip_no: int, ts: str, lon: str | float, lat: str | float) -> list[str]:
    values = [""] * 16
    values[2] = ts[:8]
    values[3] = opid
    values[6] = ts
    values[8] = str(trip_no)
    values[12] = ""
    values[14] = str(lon)
    values[15] = str(lat)
    return values


def row33(opid: str, trip_no: int, ts: str, lon: str | float, lat: str | float) -> list[str]:
    values = row(opid, trip_no, ts, lon, lat)
    values.extend([f"col{idx:02d}" for idx in range(16, 33)])
    return values


def write_trip_file(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)


def run_case(tmp: Path, files: dict[str, list[list[str]]]):
    in_dir = tmp / "第1 スクリーニング"
    out_dir = tmp / "出力 15"
    in_dir.mkdir()
    area_path = tmp / "area.geojson"
    write_area(area_path)
    for name, rows in files.items():
        write_trip_file(in_dir / name, rows)
    result = area15.run_screening(
        area15.ScreeningConfig(
            input_path=in_dir,
            area_geojson=area_path,
            output_dir=out_dir,
            min_subtrip_distance_m=0,
            min_subtrip_duration_sec=0,
            boundary_tolerance_m=0.1,
        )
    )
    with Path(result["summary_csv"]).open("r", encoding="utf-8-sig", newline="") as fh:
        summaries = list(csv.DictReader(fh))
    subtrip_files = sorted(Path(result["subtrip_csv_dir"]).glob("*.csv"))
    with Path(result["excluded_csv"]).open("r", encoding="utf-8-sig", newline="") as fh:
        excluded = list(csv.DictReader(fh))
    return result, summaries, subtrip_files, excluded


class AreaScreeningTest(unittest.TestCase):
    def test_outside_inside_outside_interpolates_boundaries_and_writes_csv(self):
        with tempfile.TemporaryDirectory() as td:
            result, summaries, subtrip_files, _ = run_case(
                Path(td),
                {"trip.csv": [row("12345", 1, "20250101080000", 134.995, 35.005), row("12345", 1, "20250101081000", 135.005, 35.005), row("12345", 1, "20250101082000", 135.015, 35.005)]},
            )
            self.assertEqual(result["stats"]["subtrips"], 1)
            self.assertEqual(len(summaries), 1)
            self.assertEqual(len(subtrip_files), 1)
            self.assertNotIn("od_csv", result)
            self.assertNotIn("gate_volume_csv", result)
            with subtrip_files[0].open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(rows[0][6], "20250101080500")
            self.assertEqual(rows[-1][6], "20250101081500")
            self.assertEqual(rows[0][12], "0")
            self.assertEqual(rows[-1][12], "1")
            self.assertEqual(summaries[0]["start_time"], "20250101080500")
            self.assertEqual(summaries[0]["end_time"], "20250101081500")

    def test_writes_style_1_3_columns_and_preserves_original_values_except_flags(self):
        with tempfile.TemporaryDirectory() as td:
            original_rows = [
                row33("wide", 1, "20250101080000", 135.003, 35.003),
                row33("wide", 1, "20250101081000", 135.007, 35.007),
            ]
            original_rows[0][12] = "2"
            original_rows[1][12] = "2"
            _, _, subtrip_files, _ = run_case(Path(td), {"wide.csv": original_rows})

            with subtrip_files[0].open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh))

            self.assertEqual([len(r) for r in rows], [33, 33])
            self.assertEqual(rows[0][:12], original_rows[0][:12])
            self.assertEqual(rows[0][13:], original_rows[0][13:])
            self.assertEqual(rows[1][:12], original_rows[1][:12])
            self.assertEqual(rows[1][13:], original_rows[1][13:])
            self.assertEqual(rows[0][12], "0")
            self.assertEqual(rows[1][12], "1")

    def test_outside_to_inside_end_inside_to_outside_all_inside_all_outside(self):
        with tempfile.TemporaryDirectory() as td:
            _, summaries, _, excluded = run_case(
                Path(td),
                {
                    "a.csv": [row("a", 1, "20250101080000", 134.995, 35.005), row("a", 1, "20250101081000", 135.005, 35.005)],
                    "b.csv": [row("b", 1, "20250101080000", 135.005, 35.005), row("b", 1, "20250101081000", 135.015, 35.005)],
                    "c.csv": [row("c", 1, "20250101080000", 135.003, 35.003), row("c", 1, "20250101081000", 135.007, 35.007)],
                    "d.csv": [row("d", 1, "20250101080000", 134.990, 35.005), row("d", 1, "20250101081000", 134.995, 35.005)],
                },
            )
            self.assertEqual(len(summaries), 3)
            ids = {s["original_trip_id"] for s in summaries}
            self.assertTrue(any(v.startswith("a-") for v in ids))
            self.assertTrue(any(v.startswith("b-") for v in ids))
            self.assertTrue(any(v.startswith("c-") for v in ids))
            self.assertTrue(any(e["original_trip_id"].startswith("d-") for e in excluded))

    def test_tangent_multiple_reentries_and_segment_crossing(self):
        with tempfile.TemporaryDirectory() as td:
            _, summaries, _, excluded = run_case(
                Path(td),
                {
                    "tangent.csv": [row("tan", 1, "20250101080000", 134.995, 35.000), row("tan", 1, "20250101081000", 135.005, 35.000), row("tan", 1, "20250101082000", 135.015, 35.000)],
                    "multi.csv": [
                        row("mul", 1, "20250101080000", 134.995, 35.005),
                        row("mul", 1, "20250101081000", 135.005, 35.005),
                        row("mul", 1, "20250101082000", 135.015, 35.005),
                        row("mul", 1, "20250101083000", 135.005, 35.005),
                        row("mul", 1, "20250101084000", 134.995, 35.005),
                    ],
                    "coarse.csv": [row("coarse", 1, "20250101080000", 134.995, 35.006), row("coarse", 1, "20250101082000", 135.015, 35.006)],
                },
            )
            self.assertTrue(any(e["original_trip_id"].startswith("tan-") for e in excluded))
            self.assertEqual(len([s for s in summaries if s["original_trip_id"].startswith("mul-")]), 2)
            self.assertEqual(len([s for s in summaries if s["original_trip_id"].startswith("coarse-")]), 1)

    def test_missing_values_invalid_polygon_and_japanese_path(self):
        with tempfile.TemporaryDirectory(prefix="日本語 パス ") as td:
            tmp = Path(td)
            result, summaries, _, excluded = run_case(
                tmp,
                {
                    "perimeter.csv": [row("per", 1, "20250101080000", 134.995, 35.001), row("per", 1, "20250101081000", 135.015, 35.001)],
                    "missing_coord.csv": [row("miss", 1, "20250101080000", "", 35.005), row("miss", 1, "20250101081000", 135.005, 35.005)],
                    "missing_time.csv": [row("time", 1, "", 134.995, 35.005), row("time", 1, "20250101081000", 135.005, 35.005)],
                    "one_point.csv": [row("one", 1, "20250101080000", 135.005, 35.005)],
                },
            )
            self.assertTrue(Path(result["summary_csv"]).exists())
            self.assertEqual(len(summaries), 1)
            reasons = " ".join(e["reason"] for e in excluded)
            self.assertIn("座標欠損", reasons)
            self.assertIn("日時欠損", reasons)

            invalid = tmp / "invalid.geojson"
            write_invalid_area(invalid)
            with self.assertRaises(ValueError):
                area15.load_area_definition(invalid)


if __name__ == "__main__":
    unittest.main()
