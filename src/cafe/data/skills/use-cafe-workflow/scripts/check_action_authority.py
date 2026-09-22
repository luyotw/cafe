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


def assess_confirmed_closeout_command(
    request: dict[str, Any], closeout_plan: dict[str, Any]
) -> dict[str, str]:
    """Match one requested argv to the exact command in a confirmed plan.

    The caller must load and validate the durable Driver contract before calling
    this helper.  A generic workflow scope is never substituted for this exact
    comparison.
    """
    if set(request) != {"stage", "index", "argv"}:
        raise ValueError("closeout request requires only stage, index, and argv")
    stage = request["stage"]
    index = request["index"]
    argv = request["argv"]
    if stage not in {"deliver", "cleanup"}:
        raise ValueError("closeout stage must be deliver or cleanup")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("closeout index must be a non-negative integer")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) for item in argv)
        or not argv[0]
    ):
        raise ValueError("closeout argv must be a non-empty string array")
    if set(closeout_plan) != {"deliver", "cleanup"}:
        raise ValueError("closeout plan requires only deliver and cleanup")
    commands = closeout_plan[stage]
    if not isinstance(commands, list) or index >= len(commands):
        return {"decision": "user_handoff", "reason": "closeout_command_missing"}
    command = commands[index]
    if not isinstance(command, dict) or set(command) != {"argv"} or command["argv"] != argv:
        return {"decision": "user_handoff", "reason": "closeout_command_mismatch"}
    return {"decision": "confirmed_closeout_command", "reason": "exact_confirmed_argv"}


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
