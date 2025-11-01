"""Flask application serving map data and managing CSV selection."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from flask import Flask, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Configuration (adjust as needed)
# ---------------------------------------------------------------------------
INPUT_DIR = Path(r"D:\01仕事\05 ETC2.0分析\生データ\out(1st)")
PATTERN = "R7_2_*.csv"
ENCODING = "utf-8"
DELIM = ","
LON_COL_1B = 2
LAT_COL_1B = 3
FLAG_COL_1B = 13

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------
app = Flask(__name__)

_current: Dict[str, Optional[str]] = {"file": None}
_current_lock = threading.Lock()


def _list_files() -> List[str]:
    """Return sorted list of filenames matching the configured pattern."""
    files = sorted(f.name for f in INPUT_DIR.glob(PATTERN) if f.is_file())
    return files


def _get_current_file() -> Optional[str]:
    """Return the currently selected file, defaulting to the first available."""
    with _current_lock:
        current = _current.get("file")
        if current is None:
            files = _list_files()
            if files:
                _current["file"] = files[0]
                current = files[0]
        return current


def _set_current_file(filename: str) -> bool:
    """Set the current file if it exists; return True on success."""
    files = _list_files()
    if filename not in files:
        return False
    with _current_lock:
        _current["file"] = filename
    return True


@app.route("/")
def index():
    """Render the main map page."""
    return render_template("index.html")


@app.route("/api/files", methods=["GET"])
def api_files():
    files = _list_files()
    return jsonify(files)


@app.route("/api/current", methods=["GET", "POST"])
def api_current():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        filename = data.get("file")
        if not isinstance(filename, str):
            return jsonify({"ok": False, "error": "Missing or invalid 'file' field"}), 400
        if not _set_current_file(filename):
            return jsonify({"ok": False, "error": "File not found"}), 404
        return jsonify({"ok": True})

    current = _get_current_file()
    return jsonify({"file": current})


@app.route("/api/data", methods=["GET"])
def api_data():
    current = _get_current_file()
    if current is None:
        return jsonify({"error": "No files available"}), 404

    file_path = INPUT_DIR / current
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404

    usecols = [LON_COL_1B - 1, LAT_COL_1B - 1, FLAG_COL_1B - 1]
    try:
        df = pd.read_csv(
            file_path,
            header=None,
            usecols=usecols,
            encoding=ENCODING,
            sep=DELIM,
            dtype=str,
        )
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404
    except Exception as exc:  # pragma: no cover - surface the error message
        return jsonify({"error": f"Failed to read CSV: {exc}"}), 500

    df.columns = ["lon", "lat", "flag"]
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["lon", "lat", "flag"])

    # Sanity filter for Japan region
    df = df[(df["lon"].between(120, 150)) & (df["lat"].between(20, 50))]
    if df.empty:
        points: List[List[float]] = []
        segments: List[List[List[float]]] = []
        return jsonify({"file": current, "count": 0, "points": points, "segments": segments})

    df["flag"] = df["flag"].astype(int)

    points = df[["lon", "lat", "flag"]].values.tolist()
    segments: List[List[List[float]]] = []

    lons = df["lon"].tolist()
    lats = df["lat"].tolist()
    flags = df["flag"].tolist()

    for i in range(len(points) - 1):
        if flags[i] == 1 or flags[i + 1] == 0:
            continue
        segment = [[lats[i], lons[i]], [lats[i + 1], lons[i + 1]]]
        segments.append(segment)

    return jsonify(
        {
            "file": current,
            "count": len(points),
            "points": points,
            "segments": segments,
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
