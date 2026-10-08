#!/usr/bin/env python3
"""Read the existing combined delivery acceptance and terminal choice."""

import argparse
import json
from pathlib import Path

from cafe.delivery.closeout import accepted_choice
from cafe.manager._store import load_contract


def inspect(issue_dir, workflow_id):
    contract, sha = load_contract(issue_dir, workflow_id=workflow_id)
    state = json.loads((issue_dir / "blackboard.json").read_text())
    if state.get("workflow_id") != workflow_id or state.get("current_step") != "done":
        raise ValueError("workflow completion must be verified before terminal actions")
    if contract["delivery_contract"].get("terminal_selection") != "delivery_outcome":
        return {"status": "legacy", "selection": None}
    plan = contract["delivery_contract"]["closeout_plan"]
    result = accepted_choice(
        issue_dir,
        workflow_id=workflow_id,
        contract_sha256=sha,
        cleanup=[item["argv"] for item in plan["cleanup"]],
    )
    return {"status": "accepted" if result else "not_recorded", "selection": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--workflow-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.issue_dir, args.workflow_id), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
