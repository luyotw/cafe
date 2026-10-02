#!/usr/bin/env python3
"""Legacy Driver entrypoint alias for Manager validation."""

import importlib.util
from pathlib import Path

_manager_path = Path(__file__).with_name("validate_manager_entry.py")
_spec = importlib.util.spec_from_file_location("validate_manager_entry_alias", _manager_path)
if _spec is None or _spec.loader is None:
    raise ImportError("Manager entrypoint is unavailable")
_manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_manager)
validate_entry = _manager.validate_entry
main = _manager.main


if __name__ == "__main__":
    raise SystemExit(main())
