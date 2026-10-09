#!/usr/bin/env python3
"""Seed and resume actual legacy state across a clean wheel upgrade."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import apply_human_task_payload, resolve_step_human_task


def seed(root: Path) -> None:
    graph = (
        PlaybookLoader(project_root=root, global_root=root / "global")
        .load_model("standard")
        .model.model_dump(mode="json", exclude_unset=True)
    )
    assert "deliver" not in graph["steps"], "Seed must use the pre-delivery release"
    graph["playbook"]["id"] = "standard"
    for step in graph["steps"].values():
        step.get("initial_input", {}).pop("legacy_presentation", None)
    catalog = root / ".cafe" / "playbooks"
    catalog.mkdir(parents=True, exist_ok=True)
    # This is an explicit project override preserving the old in-flight graph.
    (catalog / "standard.yaml").write_text(yaml.safe_dump(graph, sort_keys=False))
    issue = root / ".cafe" / "issues" / "upgrade"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text("playbook_id: standard\npr:\n  auto_create: false\n")
    iteration = issue / "pr" / "iteration_001"
    iteration.mkdir(parents=True)
    (iteration / "output.md").write_text(
        "# Legacy reviewed result\n\n## Todo List\nNo actionable work.\n"
    )
    (iteration / "iteration.json").write_text(json.dumps({"iteration": 1, "status": "confirmed"}))
    store = BlackboardStore(issue)
    board = store.load_or_create("pr", playbook_id="standard")
    store.set_current_step(board, "user")
    store.update_handoff_contract(
        board,
        from_step="pr",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        source="upgrade-smoke",
    )
    policy, binding = resolve_step_human_task(
        playbook_data=graph, step_name="pr", trigger="confirm_output"
    )
    assert policy.id == "local-review"
    task = HumanTaskRecordStore(issue).materialize(
        workflow_id=board.workflow_id,
        step="pr",
        iteration=1,
        trigger="confirm_output",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    (root / "upgrade-task.json").write_text(
        json.dumps({"id": task.id, "workflow_id": board.workflow_id})
    )


def verify(root: Path) -> None:
    issue = root / ".cafe" / "issues" / "upgrade"
    original = json.loads((root / "upgrade-task.json").read_text())
    loader = PlaybookLoader(project_root=root, global_root=root / "global")
    graph = loader.load("standard", strict=True)
    assert "deliver" not in graph["steps"]
    assert (
        "deliver"
        in PlaybookLoader(project_root=root / "fresh", global_root=root / "global").load(
            "standard", strict=True
        )["steps"]
    )
    store = BlackboardStore(issue)
    board = store.load_or_create("pr", playbook_id="standard")
    assert board.workflow_id == original["workflow_id"]
    payload = {
        "task": "local-review",
        "human_task_id": original["id"],
        "decision": "continue_without_issue",
    }
    for attempt in range(2):  # Replay after reconstruction cannot create a second result.
        result = apply_human_task_payload(
            issue_dir=issue,
            playbook_data=graph,
            blackboard=board,
            from_step="pr",
            trigger="confirm_output",
            raw_payload=payload,
            source="upgrade-smoke",
        )
        if attempt == 0:
            assert result.target == "done", result.rejection
        else:
            assert result.rejection is not None
            assert result.target is None
        board = store.load_or_create("pr", playbook_id="standard")
    assert board.current_step == "done"
    assert len(HumanTaskRecordStore(issue).results()) == 1
    assert not (issue / "delivery").exists(), "Old task does not grant new delivery effects"
    print("Legacy PR task resumed after upgrade; duplicate completion preserved one result.")


def seed_current(root: Path) -> None:
    """Persist a v0.8 delivery review and old accounting with that actual wheel."""
    from cafe.core.types import TokenUsage

    graph = PlaybookLoader(project_root=root, global_root=root / "global").load("standard")
    assert "deliver" in graph["steps"]
    issue = root / ".cafe" / "issues" / "upgrade"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text(
        "playbook_id: standard\nexecution:\n  rate_limit_restart_policy: recheck_priority\n"
    )
    store = BlackboardStore(issue)
    board = store.load_or_create("pr", playbook_id="standard")
    store.set_current_step(board, "user")
    store.update_handoff_contract(
        board, from_step="pr", to_owner=HandoffOwner.USER, to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT, source="upgrade-smoke",
    )
    policy, binding = resolve_step_human_task(
        playbook_data=graph, step_name="pr", trigger="confirm_output"
    )
    assert policy.id == "delivery-review"
    task = HumanTaskRecordStore(issue).materialize(
        workflow_id=board.workflow_id, step="pr", iteration=1,
        trigger="confirm_output", policy_id=policy.id, prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"), continuations=binding.outcomes,
        assignee_type="user",
    )
    (root / "upgrade-current.json").write_text(json.dumps({
        "task": task.to_dict(), "workflow_id": board.workflow_id,
        "usage": TokenUsage(input_tokens=100, output_tokens=20, total_cost_usd=0.125)
        .model_dump(mode="json"),
        "zero_usage": TokenUsage().model_dump(mode="json"),
    }))


def verify_current(root: Path) -> None:
    """Read pending authority unchanged and retain old money after a v0.8 upgrade."""
    from decimal import Decimal
    from cafe.core.cost import summarize_cost
    from cafe.core.restart_policy import RECHECK_PRIORITY, resolve_restart_policy
    from cafe.core.types import TokenUsage

    original = json.loads((root / "upgrade-current.json").read_text())
    issue = root / ".cafe" / "issues" / "upgrade"
    board = BlackboardStore(issue).load_or_create("pr", playbook_id="standard")
    assert board.workflow_id == original["workflow_id"] and board.current_step == "user"
    tasks = HumanTaskRecordStore(issue)
    assert tasks.get_task(original["task"]["id"]).to_dict() == original["task"]
    assert tasks.get_result(original["task"]["id"]) is None
    assert tasks.active_wait_state(board.workflow_id, step="pr") is not None
    assert resolve_restart_policy(yaml.safe_load((issue / "issue.yaml").read_text())) == RECHECK_PRIORITY
    usage = TokenUsage.model_validate(original["usage"])
    assert usage.input_tokens == 100 and usage.output_tokens == 20 and not usage.cost_records
    summary = summarize_cost(usage.cost_records, legacy_cost=usage.total_cost_usd)
    assert summary["legacy"] == Decimal("0.125")
    zero = TokenUsage.model_validate(original["zero_usage"])
    assert summarize_cost(zero.cost_records, legacy_cost=zero.total_cost_usd)["unknown"] == 1
    assert not (issue / "delivery").exists(), "Reading an old review cannot grant effects"
    print("v0.8 pending delivery review, restart policy and historical costs remain readable.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    operations = {"seed": seed, "verify": verify, "seed-current": seed_current,
                  "verify-current": verify_current}
    parser.add_argument("operation", choices=tuple(operations))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    operations[args.operation](args.root.resolve())


if __name__ == "__main__":
    main()
