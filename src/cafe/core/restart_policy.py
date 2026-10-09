"""Issue-scoped rate-limit recovery policy, independent of workflow topology."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cafe.settings import SettingUpdateRequest
from cafe.utils.issue_config import (
    issue_config_lock,
    read_issue_config_strict,
    resolve_issue_config_path,
    write_issue_config_atomic,
)

SETTING = "execution.rate_limit_restart_policy"
CONTINUE_LAST_SUCCESS = "continue_last_success"
RECHECK_PRIORITY = "recheck_priority"
RETRY_CONFIGURED_ORDER = "retry_configured_order"


def validate_restart_policy(value: Any) -> str:
    if type(value) is not str or value not in {CONTINUE_LAST_SUCCESS, RECHECK_PRIORITY}:
        raise ValueError(f"{SETTING} must be continue_last_success or recheck_priority")
    return value


def resolve_restart_policy(config: Mapping[str, Any]) -> str:
    execution = config.get("execution", {})
    if not isinstance(execution, Mapping):
        raise ValueError("issue.yaml execution must be a mapping")
    if "rate_limit_restart_policy" not in execution:
        return CONTINUE_LAST_SUCCESS
    return validate_restart_policy(execution["rate_limit_restart_policy"])


def load_restart_context(path: Path) -> dict[str, Any]:
    """Read bounded regular-file interruption evidence without accepting aliases."""
    if not path.exists() and not path.is_symlink():
        return {}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("Restart context must be a bounded regular file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Restart context is unreadable") from exc
    if not isinstance(data, dict):
        raise ValueError("Restart context must be a mapping")
    return data


def restart_eligible(reason: str | None, *, new_invocation: bool = True) -> bool:
    """Only the current typed interruption can qualify a new invocation."""
    return new_invocation and reason == "agent_rate_limit"


@dataclass(frozen=True)
class InvocationOrder:
    """Resolved CLI/model order for one authorized provider invocation.

    Session identity remains a separate contract. Models are already resolved
    for the step; previews and execution reuse this immutable snapshot.
    """

    entries: tuple[tuple[str, str | None], ...]
    configured_entries: tuple[tuple[str, str | None], ...] = ()
    sticky_disposition: str = "absent"


@dataclass(frozen=True)
class RestartPolicyUpdateResult:
    status: str
    changes: dict[str, Any]
    config_path: Path


def update_restart_policy_setting(request: SettingUpdateRequest) -> RestartPolicyUpdateResult:
    value = validate_restart_policy(request.value)
    authority = resolve_issue_config_path(request.config_path, require_registered_worktree=True)

    def build() -> tuple[dict[str, Any], Any]:
        config = read_issue_config_strict(authority)
        resolve_restart_policy(config)
        execution = dict(config.get("execution", {}))
        old = execution.get("rate_limit_restart_policy")
        execution["rate_limit_restart_policy"] = value
        config["execution"] = execution
        return config, old

    if request.preview:
        _, old = build()
        return RestartPolicyUpdateResult(
            "unchanged" if old == value else "proposed",
            {SETTING: {"before": old, "after": value}},
            authority,
        )
    with issue_config_lock(authority):
        config, old = build()
        changes = {SETTING: {"before": old, "after": value}}
        if old == value:
            return RestartPolicyUpdateResult("unchanged", changes, authority)
        write_issue_config_atomic(authority, config)
        return RestartPolicyUpdateResult("saved", changes, authority)


def selected_configured_order_recovery(
    *,
    issue_dir: Path,
    workflow_id: str | None,
    step_name: str,
    iteration: int,
    current_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate one unconsumed, declared human recovery against typed evidence."""
    # Local imports keep the policy owner independent of task declarations.
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.core.human_tasks import (
        AGENT_EXECUTION_INTERRUPTED_TASK_ID,
        AGENT_EXECUTION_INTERRUPTED_TRIGGER,
    )

    if not workflow_id:
        return None
    store = HumanTaskRecordStore(issue_dir)
    if not store.exists:
        return None
    matching = [
        t
        for t in store.tasks()
        if t.workflow_id == workflow_id
        and t.step == step_name
        and t.iteration == iteration
        and t.trigger == AGENT_EXECUTION_INTERRUPTED_TRIGGER
        and t.policy_id == AGENT_EXECUTION_INTERRUPTED_TASK_ID
        and t.status is HumanTaskStatus.COMPLETED
    ]
    if not matching:
        return None
    task = max(matching, key=lambda t: (t.completed_at or "", t.id))
    result = store.get_result(task.id)
    if result is None or result.payload.get("decision") != RETRY_CONFIGURED_ORDER:
        return None
    declared = task.expected_result.get("decisions", [])
    if (
        not any(isinstance(d, Mapping) and d.get("id") == RETRY_CONFIGURED_ORDER for d in declared)
        or task.continuations.get(RETRY_CONFIGURED_ORDER) != step_name
    ):
        raise RuntimeError("Configured-order recovery was not declared for the current step")
    recovery = result.payload.get("restart_recovery")
    binding = {
        "schema_version": 1,
        "decision": RETRY_CONFIGURED_ORDER,
        "workflow_id": workflow_id,
        "human_task_id": task.id,
        "step": step_name,
        "iteration": iteration,
    }
    if not isinstance(recovery, Mapping) or any(recovery.get(k) != v for k, v in binding.items()):
        raise RuntimeError("Configured-order recovery does not match this workflow invocation")
    interruption = recovery.get("interruption")
    if (
        not isinstance(interruption, Mapping)
        or not restart_eligible(interruption.get("reason"))
        or not isinstance(interruption.get("id"), str)
        or not interruption["id"]
        or any(interruption.get(k) != binding[k] for k in ("workflow_id", "step", "iteration"))
    ):
        raise RuntimeError("Configured-order recovery has no typed rate-limit evidence")
    consumed = current_data.get("restart_recovery_consumption", {})
    if (
        isinstance(consumed, Mapping)
        and consumed.get("result_id") == result.id
        and consumed.get("human_task_id") == task.id
        and consumed.get("interruption_id") == interruption.get("id")
        and consumed.get("invocation_id")
    ):
        return None
    if current_data.get("agent_interruption") != dict(interruption):
        return None  # A newer interruption requires a new human decision.
    return {**dict(recovery), "result_id": result.id}
