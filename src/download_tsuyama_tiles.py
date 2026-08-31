#!/usr/bin/env python3
"""Download only GSI tiles intersecting a supplied Tsuyama boundary GeoJSON."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import time
import urllib.request
from pathlib import Path

GSI_URL = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"


def iter_rings(obj):
    if obj.get("type") == "FeatureCollection":
        for feature in obj["features"]:
            yield from iter_rings(feature)
    elif obj.get("type") == "Feature":
        yield from iter_rings(obj["geometry"])
    elif obj.get("type") == "Polygon":
        yield obj["coordinates"][0]
    elif obj.get("type") == "MultiPolygon":
        for polygon in obj["coordinates"]:
            yield polygon[0]


def lonlat_to_tile(lon, lat, zoom):
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = (1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n
    return x, y


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i, (xi, yi, *_) in enumerate(ring):
        xj, yj = ring[j][:2]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def segments_intersect(a, b, c, d):
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    def on_segment(p, q, r):
        return (min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and
                min(p[1], q[1]) <= r[1] <= max(p[1], q[1]))
    abc, abd, cda, cdb = side(a, b, c), side(a, b, d), side(c, d, a), side(c, d, b)
    if abc == 0 and on_segment(a, b, c):
        return True
    if abd == 0 and on_segment(a, b, d):
        return True
    if cda == 0 and on_segment(c, d, a):
        return True
    if cdb == 0 and on_segment(c, d, b):
        return True
    return (abc > 0) != (abd > 0) and (cda > 0) != (cdb > 0)


def ring_intersects_tile(ring, x, y):
    corners = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)]
    if any(point_in_ring(px, py, ring) for px, py in corners):
        return True
    edges = list(zip(corners, corners[1:] + corners[:1]))
    return any(
        segments_intersect(a, b, c, d)
        for a, b in zip(ring, ring[1:] + ring[:1])
        for c, d in edges
    )


def candidate_tiles(rings, zoom):
    """Yield every tile intersected by the municipal boundary or its interior."""
    projected = [[lonlat_to_tile(*p[:2], zoom) for p in ring] for ring in rings]
    min_x = math.floor(min(p[0] for ring in projected for p in ring))
    max_x = math.floor(max(p[0] for ring in projected for p in ring))
    min_y = math.floor(min(p[1] for ring in projected for p in ring))
    max_y = math.floor(max(p[1] for ring in projected for p in ring))
    vertex_tiles = {(math.floor(x), math.floor(y)) for ring in projected for x, y in ring}
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            if ((x, y) in vertex_tiles or
                    any(point_in_ring(x + .5, y + .5, r) or ring_intersects_tile(r, x, y)
                        for r in projected)):
                yield x, y


def find_reusable(root, z, x, y):
    if not root:
        return None
    relative = Path(str(z)) / str(x) / f"{y}.png"
    for prefix in (Path(root), Path(root) / "tiles", Path(root) / "gsi_pale"):
        path = prefix / relative
        if path.is_file():
            return path
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="津山市境界内の地理院淡色タイルを保存します")
    parser.add_argument("boundary", type=Path, help="津山市境界の Polygon/MultiPolygon GeoJSON")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "tiles" / "gsi_pale")
    parser.add_argument("--reuse", type=Path, help="既存タイルを先に探すフォルダー")
    parser.add_argument("--min-zoom", type=int, default=9)
    parser.add_argument("--max-zoom", type=int, default=18)
    parser.add_argument("--delay", type=float, default=.05, help="ダウンロード間隔（秒）")
    args = parser.parse_args(argv)
    rings = list(iter_rings(json.loads(args.boundary.read_text(encoding="utf-8-sig"))))
    if not rings:
        parser.error("GeoJSON に Polygon または MultiPolygon がありません")
    copied = downloaded = skipped = 0
    for z in range(args.min_zoom, args.max_zoom + 1):
        for x, y in candidate_tiles(rings, z):
            target = args.output / str(z) / str(x) / f"{y}.png"
            if target.is_file():
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            reusable = find_reusable(args.reuse, z, x, y)
            if reusable:
                try:
                    os.link(reusable, target)
                except OSError:
                    shutil.copy2(reusable, target)
                copied += 1
                continue
            request = urllib.request.Request(GSI_URL.format(z=z, x=x, y=y), headers={"User-Agent": "ETC2-Analyzer/1.0"})
            temporary = target.with_suffix(".png.tmp")
            try:
                with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as out:
                    shutil.copyfileobj(response, out)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            downloaded += 1
            time.sleep(args.delay)
    print(f"完了: 再利用={copied}, ダウンロード={downloaded}, 既存={skipped}, 保存先={args.output}")


if __name__ == "__main__":
    main()
