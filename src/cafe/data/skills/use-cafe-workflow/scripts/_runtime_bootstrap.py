"""Align standalone Manager helpers before they import any CAFE modules."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def align_checkout_runtime(argv: list[str] | None = None) -> None:
    """Use the requested CAFE checkout, matching the CLI's checkout preference.

    Installed helpers have no reliable source-tree-relative path. Resolve the
    execution project instead, without importing a possibly stale CAFE package.
    Ordinary projects continue to use their installed runtime. Never reload an
    already imported runtime piecemeal.
    """
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--request-file", type=Path)
    args, _ = parser.parse_known_args(argv)
    project = args.project_root
    if project is None and args.request_file is not None:
        try:
            request = json.loads(args.request_file.read_text(encoding="utf-8"))
            if isinstance(request, dict) and request.get("project_root"):
                project = Path(request["project_root"])
        except (OSError, ValueError, TypeError):
            # The owning command reports malformed requests through its schema.
            return
    current = (project or Path.cwd()).expanduser().resolve()
    for candidate in (current, *current.parents):
        pyproject = candidate / "pyproject.toml"
        expected_cli = candidate / "src/cafe/ui/cli.py"
        if not pyproject.is_file() or not expected_cli.is_file():
            continue
        if 'name = "cafe-engine"' not in pyproject.read_text(encoding="utf-8"):
            continue
        source = str((candidate / "src").resolve())
        loaded = sys.modules.get("cafe")
        if loaded is not None:
            actual = getattr(loaded, "__file__", None)
            expected = candidate / "src/cafe/__init__.py"
            if actual is None or Path(actual).resolve() != expected.resolve():
                raise RuntimeError(
                    f"CAFE runtime already loaded from {actual}; expected {expected}. "
                    "Run the helper in a fresh process with the checkout on PYTHONPATH."
                )
        if source in sys.path:
            sys.path.remove(source)
        sys.path.insert(0, source)
        # Formatter subprocesses and other Python children inherit this choice.
        paths = os.environ.get("PYTHONPATH", "").split(os.pathsep)
        os.environ["PYTHONPATH"] = os.pathsep.join(
            [source, *(path for path in paths if path and path != source)]
        )
        return
