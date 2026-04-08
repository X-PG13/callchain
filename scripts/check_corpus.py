#!/usr/bin/env python3
"""Wrapper for the CallChain corpus regression helper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from callchain.devtools.corpus import main


if __name__ == "__main__":
    raise SystemExit(main(["check", *sys.argv[1:]]))
