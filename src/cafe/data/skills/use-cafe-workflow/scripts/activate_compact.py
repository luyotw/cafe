#!/usr/bin/env python3
"""Bind a user-confirmed compact proposal to the existing prepared workflow."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from cafe.core.blackboard import BlackboardStore
from cafe.manager.api import ActivateConfirmedContract, activate_confirmed_contract
from cafe.workflow_execution.workflow_hosting import WorkflowHost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--proposal-file", type=Path, required=True)
    parser.add_argument("--confirmed-by", choices=["user"], required=True)
    parser.add_argument("--confirmed-at", required=True)
    args = parser.parse_args()
    try:
        from cafe.core.execution_artifacts import load_execution_artifact
        proposal = load_execution_artifact(args.proposal_file)
        if proposal.get("contract_mode") != "compact":
            raise ValueError("activation requires the exact rendered compact proposal")
        def activate():
            board = BlackboardStore(args.issue_dir).load_read_only()
            if board.playbook_id != proposal["execution"]["playbook_id"]:
                raise ValueError("prepared graph differs from the confirmed proposal")
            return activate_confirmed_contract(ActivateConfirmedContract(
                args.issue_dir, args.issue_dir.name, board.workflow_id, args.confirmed_by,
                datetime.fromisoformat(args.confirmed_at), proposal))
        result = WorkflowHost(args.issue_dir).run(activate, hosting="foreground").result
        print(json.dumps({"status": "activated", "revision": result.revision,
                          "contract_sha256": result.contract_sha256}))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
