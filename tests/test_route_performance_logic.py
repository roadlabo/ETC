import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "30_route_performance.py"
if not MODULE_PATH.exists():
    MODULE_PATH = ROOT / "work" / "30_route_performance.py"
spec = importlib.util.spec_from_file_location("route_performance30", MODULE_PATH)
route_performance = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = route_performance
spec.loader.exec_module(route_performance)


def write_route(path: Path, count: int = 5, step: float = 0.001) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        for i in range(count):
            row = [""] * 16
            row[14] = 139.0 + i * step
            row[15] = 35.0
            writer.writerow(row)


def write_trip(path: Path, coords: list[tuple[str, float]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        for idx, (time_text, lon) in enumerate(coords):
            row = [""] * 16
            row[2] = "20250102"
            row[6] = time_text
            row[8] = "trip-1"
            row[14] = lon
            row[15] = 35.0
            writer.writerow(row)


class RoutePerformanceLogicTest(unittest.TestCase):
    def test_coarse_gps_segment_fills_intermediate_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            route_dir = project / route_performance.ROUTE_DIR_CANDIDATES[0]
            trip_dir = project / route_performance.SCREENING_DIR_CANDIDATES[0]
            route_dir.mkdir(parents=True)
            trip_dir.mkdir(parents=True)
            write_route(route_dir / "route_a.csv", step=0.0005)
            write_trip(trip_dir / "trip.csv", [("08:00:00", 139.0), ("08:04:00", 139.002)])

            result = route_performance.analyze_project(project, allowed_dates={"20250102"}, allowed_hours={8})

            self.assertEqual(result["results"][0]["events"], 4)

    def test_same_trip_is_not_counted_twice_in_same_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            route_dir = project / route_performance.ROUTE_DIR_CANDIDATES[0]
            trip_dir = project / route_performance.SCREENING_DIR_CANDIDATES[0]
            route_dir.mkdir(parents=True)
            trip_dir.mkdir(parents=True)
            write_route(route_dir / "route_a.csv", count=3)
            write_trip(
                trip_dir / "trip.csv",
                [
                    ("08:00:00", 139.0),
                    ("08:01:00", 139.001),
                    ("08:02:00", 139.0),
                    ("08:03:00", 139.001),
                ],
            )

            result = route_performance.analyze_project(project, allowed_dates={"20250102"}, allowed_hours={8})

            event_csv = Path(result["results"][0]["events_csv"])
            with event_csv.open("r", encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            bucket_one_events = [row for row in rows if row["bucket_idx"] == "1"]
            self.assertEqual(len(bucket_one_events), 1)


if __name__ == "__main__":
    unittest.main()
