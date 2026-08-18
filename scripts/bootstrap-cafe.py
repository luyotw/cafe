#!/usr/bin/env python3
"""Run the CAFE bootstrap directly from a trusted source checkout."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from cafe.install.bootstrap import main  # noqa: E402,I001


if __name__ == "__main__":
    raise SystemExit(main(["--source", str(REPOSITORY_ROOT), *sys.argv[1:]]))
