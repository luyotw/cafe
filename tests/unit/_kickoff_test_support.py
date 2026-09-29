"""Load Manager skill scripts from their installed-source locations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = (
    PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/scripts"
)
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


def load_kickoff_module(name: str):
    path = SCRIPT_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"kickoff_test_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load kickoff module: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
