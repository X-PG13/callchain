#!/usr/bin/env python3
"""Wrapper for the CallChain vendored corpus archive verification helper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from callchain.devtools.corpus import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["verify-archive", *sys.argv[1:]]))
