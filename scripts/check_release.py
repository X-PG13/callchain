#!/usr/bin/env python3
"""Wrapper for the CallChain release validation helper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from callchain.devtools.release import main


if __name__ == "__main__":
    raise SystemExit(main(["validate", *sys.argv[1:]]))
