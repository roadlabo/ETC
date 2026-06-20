import importlib.util
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "unreleased" / "30_build_performance.py"
spec = importlib.util.spec_from_file_location("build_performance", MODULE_PATH)
build_performance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_performance)


def make_row(date_value: str, time_value: str) -> list[str]:
    row = [""] * 16
    row[build_performance.COL_DATE] = date_value
    row[build_performance.COL_TIME] = time_value
    return row


class RoutePerformanceReviewFixesTest(unittest.TestCase):
    def test_time_only_values_use_normalized_date_column(self):
        for date_value in ("20250102", "2025-01-02", "2025/01/02"):
            with self.subTest(date_value=date_value):
                dt = build_performance.parse_datetime_from_row(make_row(date_value, "08:09:10"))
                self.assertEqual(dt, datetime(2025, 1, 2, 8, 9, 10))

    def test_directional_kp_crossing_endpoint_inclusion(self):
        kp_m = [0.0, 10.0, 20.0, 30.0]
        self.assertEqual(list(build_performance.crossing_kp_indices(kp_m, 10.0, 20.0)), [2])
        self.assertEqual(list(build_performance.crossing_kp_indices(kp_m, 20.0, 10.0)), [1])

    def test_trip_key_uses_path_not_only_file_name(self):
        row = [""] * (build_performance.COL_TRIP_NO + 1)
        row[build_performance.COL_TRIP_NO] = "42"
        a = ROOT / "tmp_a" / "same.csv"
        b = ROOT / "tmp_b" / "same.csv"
        self.assertNotEqual(build_performance.row_trip_key(a, row, 1), build_performance.row_trip_key(b, row, 1))


if __name__ == "__main__":
    unittest.main()
