#!/usr/bin/env python3
"""Validate one Driver entry and return only Driver-owned projections.

Preflight, cache invalidation, confirmation, callback routing, and projection
installation remain owned by ``use-cafe-workflow``.  This adapter is its sole
route into the durable contract application package.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from cafe.driver import DriverEntryRequest, Freshness, evaluate_driver_entry


def _mapping_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("fresh facts must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("fresh facts must be a JSON object")
    return parsed


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def validate_entry(
    *, issue_dir: Path, issue_name: str, workflow_id: str, fresh_facts: Mapping[str, Any]
) -> dict[str, Any]:
    """Fail before Driver work when current authority cannot be proved unchanged."""
    result = evaluate_driver_entry(
        DriverEntryRequest(
            issue_dir=issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
            fresh_facts=fresh_facts,
        )
    )
    if result.freshness is not Freshness.SAME_SEMANTICS:
        raise ValueError(f"Driver contract requires {result.freshness.value} recovery")
    return {
        "contract_sha256": result.contract_sha256,
        "revision": result.revision,
        "runtime": _plain(result.runtime),
        "event": _plain(result.event),
        "proactive_review": _plain(result.proactive_review),
        "phase_model_authority": _plain(result.phase_model_authority),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a Driver entry before Driver-owned work."
    )
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--fresh-facts", type=_mapping_json, required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                validate_entry(
                    issue_dir=args.issue_dir,
                    issue_name=args.issue_name,
                    workflow_id=args.workflow_id,
                    fresh_facts=args.fresh_facts,
                ),
                sort_keys=True,
            )
        )
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
