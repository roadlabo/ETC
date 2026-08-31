#!/usr/bin/env python3
"""Generic entry point for downloading offline GSI pale map tiles."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from download_tsuyama_tiles import main


if __name__ == "__main__":
    main()
