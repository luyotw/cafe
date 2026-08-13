#!/usr/bin/env python3
"""Atomically install confirmed issue-owned phase execution chains."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from cafe.utils.phase_config import load_phase_step_model


def _load_confirmed_chains(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("confirmed phase chains must be a non-empty JSON object")
    return raw


def _candidate_document(chains: dict[str, Any]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for raw_step, raw_config in chains.items():
        step = str(raw_step).strip()
        if not step or not isinstance(raw_config, dict):
            raise ValueError("each confirmed phase chain must have a step name and mapping")
        allowed = {"name", "role", "clis"}
        unknown = set(raw_config) - allowed
        if unknown:
            raise ValueError(f"unsupported fields for step '{step}': {', '.join(sorted(unknown))}")
        clis = raw_config.get("clis")
        if not isinstance(clis, list) or len(clis) < 2:
            raise ValueError(f"step '{step}' must include a primary and fallback CLI")
        document[step] = dict(raw_config)
    return document


def write_phase_config(*, chains_file: Path, target: Path) -> None:
    document = _candidate_document(_load_confirmed_chains(chains_file))
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        for step in document:
            load_phase_step_model(step_name=step, local_path=temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install confirmed phase chains in the active worktree."
    )
    parser.add_argument("--chains-json", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path(".cafe/phases.yaml"))
    args = parser.parse_args()
    try:
        write_phase_config(chains_file=args.chains_json, target=args.target)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
