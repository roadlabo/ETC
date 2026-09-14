import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "31_crossroad_trip_performance.py"
spec = importlib.util.spec_from_file_location("crossroad_trip_performance31", MODULE_PATH)
crossroad_perf = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = crossroad_perf
spec.loader.exec_module(crossroad_perf)


def point_at_m(pos_m: float) -> tuple[float, float]:
    return 0.0, pos_m / (crossroad_perf.EARTH_RADIUS_M * 3.141592653589793 / 180.0)


def dt14(seconds: int) -> str:
    return (datetime(2025, 1, 2, 8, 0, 0) + timedelta(seconds=seconds)).strftime("%Y%m%d%H%M%S")


class CrossroadStoreStopTest(unittest.TestCase):
    def test_store_stop_is_scoped_to_current_pass(self):
        points = [
            point_at_m(0),
            point_at_m(50),
            point_at_m(100),
            point_at_m(120),
            point_at_m(240),
            point_at_m(280),
            point_at_m(280),
            point_at_m(280),
            point_at_m(280),
            point_at_m(320),
        ]
        gps_times = [dt14(s) for s in (0, 30, 60, 90, 120, 150, 210, 270, 330, 360)]

        first_pass = crossroad_perf.judge_store_stop_trip(
            points,
            gps_times,
            100.0,
            None,
            300.0,
        )
        second_pass = crossroad_perf.judge_store_stop_trip(
            points,
            gps_times,
            300.0,
            100.0,
            None,
        )

        self.assertFalse(first_pass[0])
        self.assertTrue(second_pass[0])
        self.assertEqual(second_pass[4], crossroad_perf.STORE_REASON_OK)

    def test_store_stop_dwell_does_not_merge_interrupted_visits(self):
        target_points = [
            {"idx": 0, "lat": 0.0, "lon": 0.0, "dt": datetime(2025, 1, 2, 8, 0, 0)},
            {"idx": 1, "lat": 0.0, "lon": 0.0, "dt": datetime(2025, 1, 2, 8, 0, 30)},
            {"idx": 2, "lat": 0.0, "lon": 0.0, "dt": datetime(2025, 1, 2, 8, 1, 0)},
            {"idx": 3, "lat": 0.0, "lon": 0.001, "dt": datetime(2025, 1, 2, 8, 2, 0)},
            {"idx": 4, "lat": 0.0, "lon": 0.0, "dt": datetime(2025, 1, 2, 8, 4, 0)},
            {"idx": 5, "lat": 0.0, "lon": 0.0, "dt": datetime(2025, 1, 2, 8, 4, 30)},
            {"idx": 6, "lat": 0.0, "lon": 0.0, "dt": datetime(2025, 1, 2, 8, 5, 0)},
        ]

        stays = crossroad_perf._continuous_cluster_stays(target_points)

        self.assertTrue(stays)
        self.assertLess(max(stay["stay_sec"] for stay in stays), crossroad_perf.STORE_STAY_MIN_SEC)


if __name__ == "__main__":
    unittest.main()
