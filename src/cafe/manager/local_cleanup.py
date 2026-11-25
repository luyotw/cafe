"""Manager contract authority for cleanup after an accepted local integration."""

import re
from pathlib import Path

from cafe.core.blackboard import BlackboardStore
from cafe.core.packet_io import read_regular_bytes
from cafe.delivery.contracts import ActionSnapshot
from cafe.delivery.local_cleanup import MAX_MANIFEST_BYTES, has_local_delivery_configuration
from cafe.delivery.records import ActionStore

from ._store import ManagerContractMissingError, contract_lock, load_contract
from .closeout import accepted_delivery_task, inspect_closeout


def has_local_integration(issue, config):
    from cafe.core.human_task_records import HumanTaskRecordStore

    records = HumanTaskRecordStore(issue)
    if records.exists:
        state = BlackboardStore(issue).load_read_only()
        try:
            contract, _ = load_contract(issue, workflow_id=state.workflow_id)
        except ManagerContractMissingError:
            contract = {}
        binding = contract.get("closeout_contract", {}).get("delivery_result")
        accepted = accepted_delivery_task(records, state.workflow_id, binding=binding)
        if accepted is not None:
            task, _ = accepted
            match = re.search(r"Action snapshot SHA256: ([0-9a-f]{64})", task.prompt)
            if match is None:
                return "Local delivery SHA256:" in task.prompt
            snapshot = _read_snapshot(issue, match[1], state.workflow_id)
            return snapshot.proposal.mode == "local"
    return has_local_delivery_configuration(issue, config)


def _read_snapshot(issue, identity, workflow):
    snapshot = ActionSnapshot.model_validate_json(read_regular_bytes(
        issue / "delivery" / identity / "actions.json", MAX_MANIFEST_BYTES,
    ))
    if snapshot.digest != identity or snapshot.proposal.workflow_id != workflow:
        raise ValueError("delivery snapshot differs from the accepted workflow")
    return snapshot


def _authorizes_full_cleanup(commands, *, destination, worktree, issue_name):
    for argv in commands:
        if argv == ["cafe", "close"]:
            return True
        # Older confirmed plans may select the package's local recovery
        # command with the same source and retained checkout.
        if (len(argv) == 8 and Path(argv[1]).name == "cleanup_worktree.py"
                and argv[2::2] == ["--project-root", "--worktree", "--issue-name"]
                and Path(argv[3]).resolve() == destination and Path(argv[5]).resolve() == worktree
                and argv[7] == issue_name):
            return True
    return False


def authorize(issue, *, source, checkout, configured_target, linked, archived=False):
    from cafe.manager.costs import validate_issue_identity
    from cafe.workflow_execution.worker_launch import WorkerLaunchStore

    state = BlackboardStore(issue).load_read_only()
    workflow = state.workflow_id
    contract, _ = load_contract(issue, issue_name=issue.name, workflow_id=workflow)
    validate_issue_identity(issue, issue.name, workflow)
    bound = contract["checkout"]
    expected_kind = "worktree" if linked else "current_checkout"
    if (bound.get("kind") != expected_kind
            or (linked and Path(bound.get("path", "")).resolve() != checkout)):
        raise ValueError("local cleanup source differs from the approved checkout")
    if state.ownership_cursor or WorkerLaunchStore(issue).running_positions(workflow):
        raise ValueError("active workflow work blocks local cleanup")
    closeout = inspect_closeout(issue, workflow, archived=archived)
    selected = closeout["selection"] or {}
    identity = selected.get("snapshot")
    if closeout["status"] != "accepted" or selected.get("choice") != "cleanup" or not identity:
        raise ValueError("local cleanup requires successful delivery acceptance and cleanup authority")
    snapshot = _read_snapshot(issue, identity, workflow)
    proposal = snapshot.proposal
    if (proposal.mode != "local" or proposal.source_branch != source
            or proposal.target_branch != configured_target):
        raise ValueError("cleanup source or target differs from the accepted local action")
    destination = Path(proposal.destination).resolve(strict=True)
    if (destination == checkout or str(destination) != proposal.destination
            or not _authorizes_full_cleanup(selected.get("cleanup", []), destination=destination,
                                            worktree=checkout, issue_name=issue.name)):
        raise ValueError("the accepted closeout does not authorize this worktree removal")
    receipt = ActionStore(issue, snapshot).read("integration")
    if not receipt:
        raise ValueError("accepted local integration receipt is missing")
    return proposal, receipt, destination, selected
