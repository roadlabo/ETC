import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "src" / "download_tsuyama_tiles.py"
SPEC = importlib.util.spec_from_file_location("download_tsuyama_tiles", MODULE_PATH)
tiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tiles)


class OfflineMapTests(unittest.TestCase):
    def test_geojson_rings_and_tile_filter(self):
        obj = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
            [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        ]}}
        rings = list(tiles.iter_rings(obj))
        self.assertEqual(len(rings), 1)
        self.assertTrue(list(tiles.candidate_tiles(rings, 3)))

    def test_reuses_standard_xyz_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tiles" / "9" / "12" / "34.png"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"png")
            self.assertEqual(tiles.find_reusable(folder, 9, 12, 34), path)

    def test_map_pages_use_local_leaflet_and_offline_layer(self):
        src = Path(__file__).parents[1] / "src"
        for name in ("10_route_sampler.html", "11_crossroad_sampler.html",
                     "12_polygon_builder.html", "15_area_builder.html"):
            html = (src / name).read_text(encoding="utf-8")
            self.assertIn('src="offline_map.js"', html)
            self.assertIn("addGsiOfflineLayer(map)", html)
            self.assertNotIn("unpkg.com/leaflet", html)


if __name__ == "__main__":
    unittest.main()
