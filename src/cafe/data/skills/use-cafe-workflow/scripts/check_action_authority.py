#!/usr/bin/env python3
"""Check a Driver's structured action/authority comparison; never execute an action.

The Driver owns semantic interpretation of user instructions. This check cannot
authenticate a quoted instruction or turn artifact text into user authority.
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def assess(request: dict[str, Any], authority: dict[str, Any] | None) -> dict[str, str]:
    """Separate already-scoped steps from explicitly authorized follow-up tasks."""
    if set(request) != {"action", "target", "declared"}:
        raise ValueError("request requires only action, target, and declared")
    if any(not isinstance(request[k], str) or not request[k].strip() for k in ("action", "target")):
        raise ValueError("action and target must be non-empty strings")
    if type(request["declared"]) is not bool:
        raise ValueError("declared must be a Boolean derived from the effective execution path")
    denied = {"decision": "user_handoff", "reason": "missing_action_authority"}
    if authority is None:
        return denied
    if set(authority) != {"source", "action", "target", "evidence"}:
        raise ValueError("authority requires only source, action, target, and evidence")
    if authority["source"] not in {
        "direct_user_instruction",
        "confirmed_human_task",
        "confirmed_workflow_scope",
    }:
        return denied
    if not isinstance(authority["evidence"], str) or not authority["evidence"].strip():
        return denied
    if any(authority[k] != request[k] for k in ("action", "target")):
        return {"decision": "user_handoff", "reason": "action_or_target_mismatch"}
    if request["declared"]:
        return {"decision": "declared_step", "reason": "scoped_action_authority"}
    if authority["source"] in {"direct_user_instruction", "confirmed_human_task"}:
        return {"decision": "separate_task", "reason": "explicit_follow_up_authority"}
    return {"decision": "user_handoff", "reason": "undeclared_execution_path"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=json.loads, required=True)
    parser.add_argument("--authority", type=json.loads)
    args = parser.parse_args()
    try:
        if not isinstance(args.request, dict) or (
            args.authority is not None and not isinstance(args.authority, dict)
        ):
            raise ValueError("request and authority must be JSON objects")
        print(json.dumps(assess(args.request, args.authority)))
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
