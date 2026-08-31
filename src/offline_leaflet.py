from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote


SRC_DIR = Path(__file__).resolve().parent
LOCAL_GSI_TILE_TEMPLATE = (SRC_DIR / "tiles" / "gsi_pale").as_uri() + "/{z}/{x}/{y}.png"


def offline_map_script_tag() -> str:
    return '<script src="offline_map.js"></script>'


def leaflet_asset_tags() -> str:
    return (
        '<link rel="stylesheet" href="leaflet/leaflet.css"/>\n'
        '<script src="leaflet/leaflet.js"></script>\n'
        f'{offline_map_script_tag()}'
    )


def embedded_leaflet_assets() -> str:
    css = (SRC_DIR / "leaflet" / "leaflet.css").read_text(encoding="utf-8")
    js = (SRC_DIR / "leaflet" / "leaflet.js").read_text(encoding="utf-8")
    offline = (SRC_DIR / "offline_map.js").read_text(encoding="utf-8")
    return f"<style>\n{css}\n</style>\n<script>\n{js}\n</script>\n<script>\n{offline}\n</script>"


def apply_offline_tile_support(html: str) -> str:
    """Patch a Leaflet/Folium HTML document to use GSI online with local fallback."""
    html = re.sub(
        r'<link[^>]+https://unpkg\.com/leaflet@[^>]+leaflet\.css[^>]*>\s*',
        "",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r'<script[^>]+https://unpkg\.com/leaflet@[^>]+leaflet\.js[^>]*>\s*</script>\s*',
        "",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r'<link[^>]+https://cdn\.jsdelivr\.net/npm/leaflet@[^>]+leaflet\.css[^>]*>\s*',
        "",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r'<script[^>]+https://cdn\.jsdelivr\.net/npm/leaflet@[^>]+leaflet\.js[^>]*>\s*</script>\s*',
        "",
        html,
        flags=re.IGNORECASE,
    )
    if "offline_map.js" not in html and "function addGsiOfflineLayer" not in html:
        html = html.replace("</head>", embedded_leaflet_assets() + "\n</head>")

    tile_pattern = re.compile(
        r"var\s+(tile_layer_[a-f0-9]+)\s*=\s*L\.tileLayer\(\s*"
        r"(?:`[^`]*`|'[^']*'|\"[^\"]*\")\s*,\s*\{.*?\}\s*"
        r"\)\.addTo\((map_[a-f0-9]+)\);",
        flags=re.DOTALL,
    )
    local_template = quote(LOCAL_GSI_TILE_TEMPLATE, safe="/:{}")
    html = tile_pattern.sub(
        rf"var \1 = addGsiOfflineLayer(\2, {{ localUrl: '{local_template}' }});",
        html,
    )
    return html
