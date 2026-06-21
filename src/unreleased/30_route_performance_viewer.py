from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

VIEWER_PATH = SRC_DIR / "30_route_performance_viewer.py"
spec = importlib.util.spec_from_file_location("route_performance_viewer30", VIEWER_PATH)
viewer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = viewer
spec.loader.exec_module(viewer)


if __name__ == "__main__":
    viewer.main()
