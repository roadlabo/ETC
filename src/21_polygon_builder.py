"""
Polygon Builder Tool (Folium version)
------------------------------------

Generate an interactive HTML map with Leaflet/OSM for creating and
editing polygons. Existing CSV polygon data can be loaded and embedded
into the map; polygons can be drawn/edited in the browser and exported
back to CSV with the same schema: ``name, x1, y1, x2, y2, ...`` where
``x`` is longitude and ``y`` is latitude.

Run the script and a browser tab will open pointing to the generated
``polygon_map.html``. All polygon editing happens on the browser side
via Leaflet Draw.
"""

from __future__ import annotations

import argparse
import csv
import json
import webbrowser
from pathlib import Path
from typing import List, Sequence

import folium

# Folium map defaults (Tsuyama, Japan)
MAP_CENTER = [35.073, 134.004]
MAP_ZOOM_START = 14


class PolygonData:
    """Simple container for polygon name and coordinates."""

    def __init__(self, name: str, coords: Sequence[Sequence[float]]):
        self.name = name
        self.coords: List[List[float]] = [list(coord) for coord in coords]

    def to_js(self) -> dict:
        # Leaflet uses [lat, lon]
        return {"name": self.name, "coords": [[lat, lon] for lat, lon in self.coords]}

    def to_csv_row(self) -> List[str]:
        row: List[str] = [self.name]
        for lat, lon in self.coords:
            row.extend([f"{lon}", f"{lat}"])
        return row


def load_polygons(csv_path: Path) -> List[PolygonData]:
    polygons: List[PolygonData] = []
    if not csv_path.exists():
        return polygons

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3 or len(row) % 2 == 0:
                continue
            name = row[0].strip()
            try:
                values = list(map(float, row[1:]))
            except ValueError:
                continue
            coords = []
            for i in range(0, len(values), 2):
                lon, lat = values[i], values[i + 1]
                coords.append([lat, lon])
            polygons.append(PolygonData(name, coords))
    return polygons


def _add_draw_controls(m: folium.Map, polygons: List[PolygonData]) -> None:
    map_name = m.get_name()
    polygons_json = json.dumps([p.to_js() for p in polygons])
    script = f"""
    const existingPolygons = {polygons_json};
    const drawnItems = new L.FeatureGroup();
    {map_name}.addLayer(drawnItems);

    function addPolygon(name, coords) {{
        const polygon = L.polygon(coords, {{ color: 'black', weight: 2 }});
        polygon.options.customName = name || 'polygon';
        polygon.bindTooltip(polygon.options.customName, {{ permanent: true, direction: 'center', className: 'polygon-label' }});
        polygon.on('click', function() {{
            const newName = prompt('ポリゴン名を入力してください', polygon.options.customName || '');
            if (newName !== null && newName.trim() !== '') {{
                polygon.options.customName = newName.trim();
                polygon.setTooltipContent(polygon.options.customName);
            }}
        }});
        drawnItems.addLayer(polygon);
    }}

    existingPolygons.forEach(p => addPolygon(p.name, p.coords));

    const drawControl = new L.Control.Draw({{
        edit: {{ featureGroup: drawnItems }},
        draw: {{
            marker: false,
            circle: false,
            circlemarker: false,
            polyline: false,
            rectangle: false,
            polygon: {{ allowIntersection: false, showArea: false }}
        }}
    }});
    {map_name}.addControl(drawControl);

    {map_name}.on(L.Draw.Event.CREATED, function(event) {{
        const layer = event.layer;
        const name = prompt('ポリゴン名を入力してください', 'polygon');
        layer.options.customName = (name && name.trim()) || 'polygon';
        layer.bindTooltip(layer.options.customName, {{ permanent: true, direction: 'center', className: 'polygon-label' }});
        drawnItems.addLayer(layer);
    }});

    function collectPolygons() {{
        const data = [];
        drawnItems.eachLayer(layer => {{
            if (!layer.getLatLngs) return;
            const latlngs = layer.getLatLngs();
            const ring = Array.isArray(latlngs[0][0]) ? latlngs[0] : latlngs;
            const coords = ring.map(pt => [pt.lat, pt.lng]);
            data.push({{ name: layer.options.customName || 'polygon', coords }});
        }});
        return data;
    }}

    function downloadCsv() {{
        const rows = collectPolygons().map(poly => {{
            const flatCoords = poly.coords.flatMap(pair => [pair[1], pair[0]]); // lon, lat order
            return [poly.name, ...flatCoords].join(',');
        }});
        const csvContent = rows.join('\n');
        const blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.setAttribute('href', url);
        link.setAttribute('download', 'polygon_data.csv');
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    }}

    const exportButton = L.control({{ position: 'topright' }});
    exportButton.onAdd = function() {{
        const container = L.DomUtil.create('div', 'export-container');
        const button = L.DomUtil.create('button', 'export-button', container);
        button.type = 'button';
        button.textContent = 'CSV ダウンロード';
        L.DomEvent.on(button, 'click', downloadCsv);
        return container;
    }};
    exportButton.addTo({map_name});
    """

    style = """
    <style>
    .export-container { text-align: right; }
    .export-button {
        background: #1d4ed8;
        color: white;
        border: none;
        padding: 6px 10px;
        border-radius: 4px;
        cursor: pointer;
        box-shadow: 0 2px 6px rgba(0,0,0,0.2);
    }
    .export-button:hover { background: #1e40af; }
    .polygon-label { font-weight: 700; color: #111; text-shadow: 0 1px 2px white; }
    </style>
    """

    draw_css = """
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet-draw@1.0.4/dist/leaflet.draw.css" />
    """
    draw_js_src = "https://cdn.jsdelivr.net/npm/leaflet-draw@1.0.4/dist/leaflet.draw.js"

    m.get_root().header.add_child(folium.Element(draw_css))
    m.get_root().html.add_child(folium.Element(style))
    m.get_root().script.add_child(
        folium.Element(f"<script src='{draw_js_src}'></script>")
    )
    m.get_root().script.add_child(folium.Element(f"<script>{script}</script>"))


def build_map(polygons: List[PolygonData], output_path: Path) -> None:
    m = folium.Map(location=MAP_CENTER, zoom_start=MAP_ZOOM_START, tiles="OpenStreetMap")

    for poly in polygons:
        folium.Polygon(
            locations=[[lat, lon] for lat, lon in poly.coords],
            color="black",
            weight=2,
            fill=False,
            tooltip=poly.name,
        ).add_to(m)

    _add_draw_controls(m, polygons)
    m.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Folium-based polygon builder")
    parser.add_argument("--csv", dest="csv_path", default="polygon_data.csv", help="Input CSV with polygon data")
    parser.add_argument("--output", dest="output", default="polygon_map.html", help="Output HTML file path")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv_path)
    output_path = Path(args.output)

    polygons = load_polygons(csv_path)
    build_map(polygons, output_path)

    if not args.no_browser:
        webbrowser.open(output_path.resolve().as_uri())


if __name__ == "__main__":
    main()
