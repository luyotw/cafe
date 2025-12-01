"""Global pytest configuration."""

import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    """Add this repo's src directory to sys.path for local imports."""
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"

    if src_dir.is_dir():
        src_path = str(src_dir)
        if src_path not in sys.path:
            sys.path.insert(0, src_path)


_ensure_src_on_path()
