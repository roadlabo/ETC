#!/usr/bin/env python3
"""Download only GSI tiles intersecting a supplied Polygon/MultiPolygon GeoJSON."""
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


def segment_intersects_tile(a, b, x, y):
    corners = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)]
    if x <= a[0] <= x + 1 and y <= a[1] <= y + 1:
        return True
    if x <= b[0] <= x + 1 and y <= b[1] <= y + 1:
        return True
    return any(
        segments_intersect(a, b, c, d)
        for c, d in zip(corners, corners[1:] + corners[:1])
    )


def candidate_tiles(rings, zoom):
    """Yield every tile intersected by the supplied boundary or its interior."""
    projected = [[lonlat_to_tile(*p[:2], zoom) for p in ring] for ring in rings]
    min_x = math.floor(min(p[0] for ring in projected for p in ring))
    max_x = math.floor(max(p[0] for ring in projected for p in ring))
    min_y = math.floor(min(p[1] for ring in projected for p in ring))
    max_y = math.floor(max(p[1] for ring in projected for p in ring))
    tiles = set()
    for ring in projected:
        edges = list(zip(ring, ring[1:] + ring[:1]))
        for a, b in edges:
            for x in range(math.floor(min(a[0], b[0])), math.floor(max(a[0], b[0])) + 1):
                for y in range(math.floor(min(a[1], b[1])), math.floor(max(a[1], b[1])) + 1):
                    if segment_intersects_tile(a, b, x, y):
                        tiles.add((x, y))
        for y in range(min_y, max_y + 1):
            scan_y = y + .5
            intersections = []
            for (x1, y1), (x2, y2) in edges:
                if (y1 > scan_y) != (y2 > scan_y):
                    intersections.append(x1 + (scan_y - y1) * (x2 - x1) / (y2 - y1))
            intersections.sort()
            for left, right in zip(intersections[0::2], intersections[1::2]):
                start = math.ceil(left - .5)
                end = math.ceil(right - .5) - 1
                for x in range(max(min_x, start), min(max_x, end) + 1):
                    tiles.add((x, y))
    yield from sorted(tiles)


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
    parser = argparse.ArgumentParser(description="Download GSI pale map tiles within a supplied boundary.")
    parser.add_argument("boundary", type=Path, help="Polygon/MultiPolygon GeoJSON boundary")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "tiles" / "gsi_pale")
    parser.add_argument("--reuse", type=Path, help="folder to search for existing tiles first")
    parser.add_argument("--min-zoom", type=int, default=9)
    parser.add_argument("--max-zoom", type=int, default=18)
    parser.add_argument("--delay", type=float, default=.05, help="download interval in seconds")
    args = parser.parse_args(argv)
    rings = list(iter_rings(json.loads(args.boundary.read_text(encoding="utf-8-sig"))))
    if not rings:
        parser.error("GeoJSON does not contain a Polygon or MultiPolygon")
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
    print(f"Done: reused={copied}, downloaded={downloaded}, existing={skipped}, output={args.output}")


if __name__ == "__main__":
    main()
