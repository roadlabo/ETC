import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "src" / "download_tsuyama_tiles.py"
SPEC = importlib.util.spec_from_file_location("download_tsuyama_tiles", MODULE_PATH)
tiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tiles)
OFFLINE_LEAFLET_PATH = Path(__file__).parents[1] / "src" / "offline_leaflet.py"
OFFLINE_SPEC = importlib.util.spec_from_file_location("offline_leaflet", OFFLINE_LEAFLET_PATH)
offline_leaflet = importlib.util.module_from_spec(OFFLINE_SPEC)
OFFLINE_SPEC.loader.exec_module(offline_leaflet)


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
                     "12_polygon_builder.html", "15_area_builder.html",
                     "unreleased/10_route_sampler.html"):
            html = (src / name).read_text(encoding="utf-8")
            self.assertIn("offline_map.js", html)
            self.assertIn("addGsiOfflineLayer(map", html)
            self.assertNotIn("unpkg.com/leaflet", html)

    def test_crossroad_sampler_uses_visible_red_guides_and_online_tiles_first(self):
        html = (Path(__file__).parents[1] / "src" / "11_crossroad_sampler.html").read_text(encoding="utf-8")
        self.assertIn('addGsiOfflineLayer(map);', html)
        self.assertNotIn("preferLocal: true", html)
        self.assertIn('var COLOR_LINE = "#D71920"', html)
        self.assertIn('var COLOR_CENTER = "#D71920"', html)
        self.assertIn("var SAVE_ZOOM = 16", html)
        self.assertIn("map.setView(centerLatLng, SAVE_ZOOM", html)
        self.assertNotIn("map.fitBounds", html)
        self.assertNotIn("cdn.jsdelivr.net", html)

    def test_python_map_builders_use_offline_support(self):
        src = Path(__file__).parents[1] / "src"
        for name in (
            "02_UI_existence_trip_counter.py",
            "03_UI_base_zone_estimator.py",
            "05_trip_viewer.py",
            "30_UI_route_performance.py",
            "30_route_performance.py",
            "33_branch_check.py",
            "unreleased/06_route_mapper_kp.py",
            "unreleased/10_route_sampler.py",
            "unreleased/30_route_performance.py",
            "unreleased/41_od_heatmap_viewer.py",
            "unreleased/43_UI_peak30min_od.py",
            "unreleased/50_Path_Analysis.py",
        ):
            text = (src / name).read_text(encoding="utf-8")
            self.assertTrue(
                "addGsiOfflineLayer" in text or "apply_offline_tile_support" in text,
                name,
            )
            self.assertNotIn("tile.openstreetmap", text, name)
            self.assertNotIn("basemaps.cartocdn", text, name)

    def test_folium_html_is_patched_to_local_fallback(self):
        html = """
<html><head>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
</head><body><script>
var map_abc123 = L.map("map");
var tile_layer_abc123 = L.tileLayer(
    "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
    {"maxZoom": 18}
).addTo(map_abc123);
</script></body></html>
"""
        patched = offline_leaflet.apply_offline_tile_support(html)
        self.assertIn("function addGsiOfflineLayer", patched)
        self.assertIn("addGsiOfflineLayer(map_abc123", patched)
        self.assertIn("file:///D:/GitHub/ETC/src/tiles/gsi_pale/{z}/{x}/{y}.png", patched)
        self.assertNotIn("unpkg.com/leaflet", patched)


if __name__ == "__main__":
    unittest.main()
