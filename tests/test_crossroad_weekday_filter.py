import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "31_crossroad_trip_performance.py"
spec = importlib.util.spec_from_file_location("crossroad_trip_performance31", MODULE_PATH)
crossroad_performance = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = crossroad_performance
spec.loader.exec_module(crossroad_performance)


class CrossroadWeekdayFilterTest(unittest.TestCase):
    def test_missing_cli_weekdays_means_all_days(self):
        self.assertEqual(crossroad_performance._resolve_target_weekdays(None, None), [])

    def test_all_weekdays_cli_means_all_days(self):
        self.assertEqual(crossroad_performance._resolve_target_weekdays(["ALL"], None), [])

    def test_selected_weekdays_are_normalized_and_deduplicated(self):
        self.assertEqual(
            crossroad_performance._resolve_target_weekdays(["月", "MON", "火"], None),
            ["MON", "TUE"],
        )


if __name__ == "__main__":
    unittest.main()
