#!/usr/bin/env python3
"""Run CAFE's catalog check and list same-ID content mismatches."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def content_mismatch_entry_ids(catalog_check: dict[str, Any]) -> list[str]:
    entries = catalog_check.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("catalog check entries must be a list")
    entry_ids = {
        entry["entry_id"]
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("reason") == "content_mismatch"
        and isinstance(entry.get("entry_id"), str)
        and entry["entry_id"]
    }
    return sorted(entry_ids)


def _resolve_executable(value: str) -> str:
    if Path(value).name != value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"cafe executable is unavailable: {path}")
        return str(path)
    resolved = shutil.which(value)
    if resolved is None:
        raise ValueError(f"cafe executable is unavailable: {value}")
    return resolved


def run_catalog_check(cafe_executable: str) -> subprocess.CompletedProcess[str]:
    executable = _resolve_executable(cafe_executable)
    try:
        return subprocess.run(
            [executable, "catalog", "check", "--json"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"catalog check could not run: {type(exc).__name__}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Run CAFE's catalog check and list same-ID content mismatches.",
    )
    parser.add_argument("--cafe-executable", default="cafe")
    return parser


def main() -> int:
    try:
        result = run_catalog_check(_parser().parse_args().cafe_executable)
        if result.returncode != 0:
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode
        catalog_check = json.loads(result.stdout)
        if not isinstance(catalog_check, dict):
            raise ValueError("catalog check must return a JSON object")
        entry_ids = content_mismatch_entry_ids(catalog_check)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "catalog_check": catalog_check,
                "content_mismatch_entry_ids": entry_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
