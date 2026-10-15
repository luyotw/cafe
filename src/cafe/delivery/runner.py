"""Run the existing exact approved delivery bundle without a model invocation."""

from __future__ import annotations

from pathlib import Path

from cafe.core.blackboard import BlackboardStore
from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
from cafe.delivery.contracts import DeliveryBinding, DeliveryVerification
from cafe.delivery.prepared import prepared_request
from cafe.delivery.selection import approved_snapshot
from cafe.delivery.service import execute_snapshot
from cafe.playbooks.loader import PlaybookLoader
from cafe.utils.issue_config import read_issue_config_strict


def run_delivery(*, root: Path, issue_dir: Path, step: str, iteration: int):
    config = read_issue_config_strict(issue_dir / "issue.yaml")
    playbook = PlaybookLoader(project_root=root).load(config["playbook_id"])
    store = BlackboardStore(issue_dir)
    if not store.file_path.is_file():
        raise ValueError("delivery requires an existing workflow")
    state = store.load_or_create(playbook.get("entry_point") or next(iter(playbook["steps"])))
    handoff = state.handoff_contract
    if state.current_step != step and not (
        state.current_step == "user" and handoff is not None and handoff.from_step == step
    ):
        raise ValueError("delivery step is not the current workflow owner")
    _snapshot, report, path = execute_delivery(
        root=root,
        issue_dir=issue_dir,
        step=step,
        iteration=iteration,
        playbook=playbook,
        state=state,
    )
    return report, path


def execute_delivery(*, root, issue_dir, step, iteration, playbook, state):
    """Shared validated domain entry for CLI and automatic delivery."""
    definition = playbook["steps"][step]
    binding = DeliveryBinding.model_validate(definition["delivery"])
    snapshot = approved_snapshot(issue_dir, workflow_id=state.workflow_id, binding=binding)
    if binding.publication_artifact:
        from cafe.delivery.selection import validate_publication_reference

        validate_publication_reference(issue_dir, state, binding, snapshot)
    prepared = prepared_request(issue_dir, step)
    if prepared is not None:
        for key in ("mode", "target_branch", "strategy", "destination", "issue_repository"):
            if prepared.get(key, "") != getattr(snapshot.proposal, key):
                raise ValueError("approved action differs from preparation-time delivery choices")
        if (
            DeliveryVerification.model_validate(prepared["verification"])
            != snapshot.proposal.verification
        ):
            raise ValueError("approved verification differs from preparation-time choices")
    output = issue_dir / step / f"iteration_{iteration:03d}" / "output.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    report, path = execute_snapshot(
        root=root,
        issue_dir=issue_dir,
        snapshot=snapshot,
        registry=load_capability_registry(default_capability_definition_dirs(root)),
        step=step,
        iteration=iteration,
        output_file=output,
        timeout=120,
    )

    return snapshot, report, path
