#!/usr/bin/env python3
"""Read existing recovery records without granting authority or changing state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 30
MAX_HISTORY_BYTES = 4 * 1024 * 1024


def inspect_budget(issue_dir: Path, task_id: str, *, stop_requested: bool = False) -> dict:
    """Count durable choices for one workflow/step/iteration, never callback hops."""
    store = HumanTaskRecordStore(issue_dir)
    if store.file_path.stat().st_size > MAX_HISTORY_BYTES:
        raise ValueError("Recovery history exceeds the inspection bound")
    before = store.file_path.read_bytes()
    tasks = store.tasks()
    results = {result.task_id: result for result in store.results()}
    task = next((task for task in tasks if task.id == task_id), None)
    if task is None or task.policy_id != "agent-execution-interrupted":
        raise ValueError("Expected an existing agent interruption task")
    wait = store.get_wait_state(task_id)
    if before != store.file_path.read_bytes():
        raise ValueError("Recovery history changed during inspection; inspect again")
    scope = [
        prior
        for prior in tasks
        if (prior.workflow_id, prior.step, prior.iteration)
        == (task.workflow_id, task.step, task.iteration)
        and prior.policy_id == task.policy_id
    ]
    used = 0
    for prior in scope:
        result = results.get(prior.id)
        if result is not None:
            if prior.status is not HumanTaskStatus.COMPLETED:
                raise ValueError("Recovery result conflicts with task status")
            decision = result.payload.get("decision")
            if decision not in {"retry", "retry_fresh_session"}:
                raise ValueError("Recovery result has an unknown decision")
            # A user-selected fresh session cannot renew the automatic budget.
            used += 1
    active = [prior for prior in scope if prior.status is HumanTaskStatus.PENDING]
    if len(active) > 1:
        raise ValueError("Multiple pending recovery tasks require diagnosis")
    pending = (
        task.status is HumanTaskStatus.PENDING
        and wait.released_at is None
        and task.continuations.get("retry") == task.step
    )
    remaining = max(0, MAX_RETRIES - used)
    action = (
        "retain_pause"
        if stop_requested
        else (
            "ignore_callback"
            if not pending
            else "user_handoff" if not remaining else "inspect_retry_safety"
        )
    )
    return {
        "workflow_id": task.workflow_id,
        "step": task.step,
        "iteration": task.iteration,
        "task_id": task.id,
        "retries_used": used,
        "retries_remaining": remaining,
        "delay_seconds": RETRY_DELAY_SECONDS,
        "action": action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--stop-requested", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect_budget(args.issue_dir, args.task_id, stop_requested=args.stop_requested)
    except (OSError, ValueError) as exc:
        print(json.dumps({"action": "retain_pause", "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
