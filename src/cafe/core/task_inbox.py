"""Repository-wide discovery and safe selection of durable human tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional

from cafe.core.blackboard import BlackboardStore
from cafe.utils.issue_paths import iter_issue_directories
from cafe.core.human_task_records import (
    Assignment,
    HumanTask,
    HumanTaskRecordError,
    HumanTaskRecordStore,
    HumanTaskStatus,
    TaskResult,
    WaitState,
)
from cafe.core.human_tasks import (
    AGENT_EXECUTION_INTERRUPTED_TASK_ID,
    AGENT_EXECUTION_INTERRUPTED_TRIGGER,
    AGENT_EXECUTION_RETRY_DECISION,
    HumanTaskPolicy,
    agent_execution_interrupted_human_task,
    resolve_step_human_task,
)
from cafe.core.human_task_snapshot import (
    durable_task_matches_current_handoff,
    resolve_task_inputs,
    uses_special_approval,
)


def load_issue_task_playbook(
    *, project_root: Path, issue_dir: Path, playbook_id: str,
) -> dict[str, Any]:
    """Load the effective machine declarations for the owning issue."""
    from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides

    playbook = PlaybookLoader(project_root=project_root, resolve_presentation=False).load(playbook_id)
    return apply_issue_playbook_overrides(playbook, issue_dir / "issue.yaml")


class TaskInboxError(RuntimeError):
    """One actionable, machine-stable inbox failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        recovery: str,
        task_id: Optional[str] = None,
        issue: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery
        self.task_id = task_id
        self.issue = issue
        self.workflow_id = workflow_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "recovery": self.recovery,
            "task_id": self.task_id,
            "issue": self.issue,
            "workflow_id": self.workflow_id,
        }


@dataclass(frozen=True)
class TaskSummary:
    """Stable public projection used by both human and JSON renderers."""

    id: str
    issue: str
    workflow_id: str
    step: str
    iteration: int
    trigger: str
    policy_id: str
    status: str
    assignee_type: str
    assignee_id: Optional[str]
    due_state: str
    created_at: str
    completed_at: Optional[str]
    cancelled_at: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "issue": self.issue,
            "workflow_id": self.workflow_id,
            "step": self.step,
            "iteration": self.iteration,
            "trigger": self.trigger,
            "policy_id": self.policy_id,
            "status": self.status,
            "assignment": {"type": self.assignee_type, "id": self.assignee_id},
            "due_state": self.due_state,
            "timestamps": {
                "created_at": self.created_at,
                "completed_at": self.completed_at,
                "cancelled_at": self.cancelled_at,
            },
        }


@dataclass(frozen=True)
class TaskDetail:
    """Inspection projection for one uniquely selected task."""

    id: str
    issue: str
    workflow_id: str
    step: str
    iteration: int
    trigger: str
    policy_id: str
    prompt: str
    expected_result: dict[str, Any]
    continuations: dict[str, str]
    assignment: dict[str, Optional[str]]
    status: str
    due_state: str
    wait: dict[str, Any]
    result: Optional[dict[str, Any]]
    capability_approval: Optional[dict[str, Any]]
    timestamps: dict[str, Optional[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "issue": self.issue,
            "workflow_id": self.workflow_id,
            "provenance": {
                "step": self.step,
                "iteration": self.iteration,
                "trigger": self.trigger,
                "policy_id": self.policy_id,
            },
            "prompt": self.prompt,
            "expected_result": self.expected_result,
            "continuations": self.continuations,
            "assignment": self.assignment,
            "status": self.status,
            "due_state": self.due_state,
            "wait": self.wait,
            "result": self.result,
            "capability_approval": self.capability_approval,
            "timestamps": self.timestamps,
        }


@dataclass(frozen=True)
class CompletionPreflight:
    """Live ownership evidence captured before response validation and mutation."""

    task: HumanTask
    issue: str
    issue_dir: Path
    workflow_id: str
    playbook_id: str


@dataclass(frozen=True)
class _Record:
    issue: str
    issue_dir: Path
    workflow_id: str
    playbook_id: str
    task: HumanTask
    assignment: Assignment
    wait: WaitState
    result: Optional[TaskResult]


class TaskInboxService:
    """Read canonical per-issue stores as one fail-closed repository inbox."""

    def __init__(
        self, cafe_dir: Path = Path(".cafe"), *, archive_root: Optional[Path] = None
    ) -> None:
        self.cafe_dir = Path(cafe_dir)
        project_key = str(self.cafe_dir.parent.resolve()).lstrip("/").replace("/", "-")
        self.archive_root = archive_root or (
            Path.home() / ".cafe" / "projects" / project_key / "archived"
        )

    def list_tasks(
        self,
        *,
        statuses: Optional[set[str]] = None,
        assignee: Optional[str] = None,
        workflow: Optional[str] = None,
        step: Optional[str] = None,
        due_state: Optional[str] = None,
        include_historical: bool = False,
    ) -> tuple[TaskSummary, ...]:
        requested_statuses = statuses or (
            {status.value for status in HumanTaskStatus}
            if include_historical
            else {HumanTaskStatus.PENDING.value}
        )
        allowed_statuses = {status.value for status in HumanTaskStatus}
        if not requested_statuses <= allowed_statuses:
            invalid = sorted(requested_statuses - allowed_statuses)
            raise TaskInboxError(
                "invalid_filter",
                f"Unsupported task status filter: {', '.join(invalid)}.",
                recovery=f"Use one of: {', '.join(sorted(allowed_statuses))}.",
            )
        if due_state not in (None, "unscheduled"):
            raise TaskInboxError(
                "invalid_filter",
                f"Unsupported due-state filter: {due_state}.",
                recovery="Use 'unscheduled'; canonical tasks currently declare no due timestamp.",
            )
        records = self._scan()
        selected = (
            record
            for record in records
            if record.task.status.value in requested_statuses
            and (assignee is None or record.assignment.assignee_id == assignee)
            and (workflow is None or record.workflow_id == workflow or record.issue == workflow)
            and (step is None or record.task.step == step)
            and (due_state is None or due_state == "unscheduled")
        )
        return tuple(self._summary(record) for record in selected)

    def inspect(self, task_id: str) -> TaskDetail:
        return self._detail(self._select(task_id))

    def inspect_with_snapshot(self, task_id: str) -> tuple[TaskDetail, dict[str, Any]]:
        record = self._select(task_id)
        return self._detail(record), self._snapshot(record)

    def _snapshot(self, record: _Record) -> dict[str, Any]:
        task = record.task
        unavailable = {"schema_version": 1, "status": "unavailable", "version": None}
        if task.status is not HumanTaskStatus.PENDING or record.wait.released_at or record.result:
            return {**unavailable, "reason": "task_not_pending"}
        if task.capability_approval is not None:
            return {**unavailable, "status": "not_applicable", "reason": "special_approval"}
        try:
            blackboard = BlackboardStore(record.issue_dir).load_read_only()
            if (blackboard.workflow_id != task.workflow_id
                    or not durable_task_matches_current_handoff(task, blackboard)):
                return {**unavailable, "reason": "stale_task"}
            playbook = load_issue_task_playbook(
                project_root=self.cafe_dir.parent.resolve(),
                issue_dir=record.issue_dir, playbook_id=record.playbook_id,
            )
            step_definition = playbook["steps"][task.step]
            if uses_special_approval(task, step_definition):
                return {**unavailable, "status": "not_applicable", "reason": "special_approval"}
            if task.trigger != AGENT_EXECUTION_INTERRUPTED_TRIGGER:
                from cafe.skills.loader import SkillLoader

                _, binding = resolve_step_human_task(
                    playbook_data=playbook, step_name=task.step, trigger=task.trigger,
                    iteration=task.iteration, skill_loader=SkillLoader(resolve_presentation=False),
                )
                if binding.task_id != task.policy_id or binding.outcomes != task.continuations:
                    return {**unavailable, "reason": "continuation_changed"}
            inputs = resolve_task_inputs(
                record.issue_dir, step=task.step, iteration=task.iteration,
                policy=HumanTaskPolicy.model_validate(task.expected_result),
                blackboard=blackboard, step_definition=step_definition,
            )
        except (OSError, LookupError, TypeError, ValueError):
            return {**unavailable, "reason": "task_context_unavailable"}
        return inputs.to_snapshot(task, blackboard)

    def inspect_read_only(self, task_id: str) -> TaskDetail:
        """Project a durable task without refreshing runtime-owned task contracts."""
        return self._detail(self._select(task_id, refresh=False))

    def preflight_completion(self, task_id: str) -> CompletionPreflight:
        record = self._select(task_id)
        if record.task.status is not HumanTaskStatus.PENDING:
            raise TaskInboxError(
                "task_not_pending",
                f"Task {task_id} is {record.task.status.value}, not pending.",
                recovery="List pending tasks and select an active task.",
                task_id=task_id,
                issue=record.issue,
                workflow_id=record.workflow_id,
            )
        if record.wait.released_at is not None or record.result is not None:
            raise TaskInboxError(
                "stale_task",
                f"Task {task_id} no longer has one active wait.",
                recovery="Inspect the task history and choose a currently pending task.",
                task_id=task_id,
                issue=record.issue,
                workflow_id=record.workflow_id,
            )
        return CompletionPreflight(
            task=record.task,
            issue=record.issue,
            issue_dir=record.issue_dir,
            workflow_id=record.workflow_id,
            playbook_id=record.playbook_id,
        )

    def _select(self, task_id: str, *, refresh: bool = True) -> _Record:
        identifier = str(task_id).strip()
        matches = [record for record in self._scan() if record.task.id == identifier]
        if not matches:
            archived_issues = self._archived_issues_for(identifier)
            if archived_issues:
                raise TaskInboxError(
                    "archived_workflow",
                    f"Task {identifier!r} belongs to archived issue(s): "
                    + ", ".join(archived_issues)
                    + ".",
                    recovery="Restore the owning issue explicitly before completing this task.",
                    task_id=identifier,
                    issue=archived_issues[0] if len(archived_issues) == 1 else None,
                )
            raise TaskInboxError(
                "task_not_found",
                f"No repository task has identifier {identifier!r}.",
                recovery="Run `cafe task ls` and retry with an exact task identifier.",
                task_id=identifier,
            )
        if len(matches) != 1:
            issues = ", ".join(record.issue for record in matches)
            raise TaskInboxError(
                "duplicate_task_id",
                f"Task identifier {identifier!r} occurs in multiple workflows: {issues}.",
                recovery="Repair the duplicate durable records before retrying.",
                task_id=identifier,
            )
        return self._refresh_legacy_retry_contract(matches[0]) if refresh else matches[0]

    @staticmethod
    def _refresh_legacy_retry_contract(record: _Record) -> _Record:
        """Preserve the legacy additive fresh-session upgrade, never opt into recheck.

        Current interruption snapshots remain unchanged until workflow supersession.
        Only the original retry-only builtin contract receives its preexisting
        fresh-session choice; issue settings cannot broaden the declaration here.
        """
        task = record.task
        if (
            task.status is not HumanTaskStatus.PENDING
            or record.wait.released_at is not None
            or record.result is not None
            or task.capability_approval is not None
            or task.trigger != AGENT_EXECUTION_INTERRUPTED_TRIGGER
            or task.policy_id != AGENT_EXECUTION_INTERRUPTED_TASK_ID
            or task.continuations != {AGENT_EXECUTION_RETRY_DECISION: task.step}
        ):
            return record
        try:
            snapshot = HumanTaskPolicy.model_validate(task.expected_result)
        except ValueError:
            return record
        if (
            snapshot.id != task.policy_id
            or snapshot.pattern != task.trigger
            or snapshot.input_schema != "decision"
            or tuple(d.id for d in snapshot.decisions) != (AGENT_EXECUTION_RETRY_DECISION,)
        ):
            return record
        policy, binding = agent_execution_interrupted_human_task(step_name=task.step)
        try:
            refreshed = HumanTaskRecordStore(record.issue_dir).refresh_pending_contract(
                workflow_id=record.workflow_id,
                task_id=task.id,
                prompt=policy.prompt,
                expected_result=policy.model_dump(mode="json"),
                continuations=binding.outcomes,
            )
        except HumanTaskRecordError as exc:
            raise TaskInboxError(
                "task_contract_refresh_failed",
                f"Pending interruption task {task.id} could not adopt legacy recovery choices: {exc}",
                recovery="Inspect the owning workflow state before completing this task.",
                task_id=task.id,
                issue=record.issue,
                workflow_id=record.workflow_id,
            ) from exc
        return replace(record, task=refreshed)

    def _archived_issues_for(self, task_id: str) -> list[str]:
        """Identify an archived owner without treating archives as live inbox data."""
        if not self.archive_root.is_dir():
            return []
        matches: list[str] = []
        for issue_dir in iter_issue_directories(self.archive_root):
            store = HumanTaskRecordStore(issue_dir)
            if not store.exists:
                continue
            try:
                if any(task.id == task_id for task in store.tasks()):
                    matches.append(issue_dir.relative_to(self.archive_root).as_posix())
            except (HumanTaskRecordError, OSError):
                continue
        return matches

    def _scan(self) -> tuple[_Record, ...]:
        issues_dir = self.cafe_dir / "issues"
        if not issues_dir.is_dir():
            return ()
        records: list[_Record] = []
        for issue_dir in iter_issue_directories(issues_dir):
            issue_name = issue_dir.relative_to(issues_dir).as_posix()
            store = HumanTaskRecordStore(issue_dir)
            if not store.exists:
                continue
            try:
                metadata = self._workflow_metadata(issue_dir)
                tasks = store.tasks()
                assignments = {task.id: store.get_assignment(task.id) for task in tasks}
                waits = {task.id: store.get_wait_state(task.id) for task in tasks}
                results = {task.id: store.get_result(task.id) for task in tasks}
            except (HumanTaskRecordError, OSError, ValueError, json.JSONDecodeError) as exc:
                raise TaskInboxError(
                    "corrupt_store",
                    f"Task records for issue {issue_name!r} are unsafe to read: {exc}",
                    recovery="Repair or restore this issue's durable task and blackboard records.",
                    issue=issue_name,
                ) from exc
            workflow_ids = {task.workflow_id for task in tasks}
            if workflow_ids and workflow_ids != {metadata["workflow_id"]}:
                raise TaskInboxError(
                    "workflow_mismatch",
                    f"Task records and blackboard disagree for issue {issue_name!r}.",
                    recovery="Restore matching workflow identity before using the inbox.",
                    issue=issue_name,
                    workflow_id=metadata["workflow_id"],
                )
            records.extend(
                _Record(
                    issue=issue_name,
                    issue_dir=issue_dir,
                    workflow_id=metadata["workflow_id"],
                    playbook_id=metadata["playbook_id"],
                    task=task,
                    assignment=assignments[task.id],
                    wait=waits[task.id],
                    result=results[task.id],
                )
                for task in tasks
            )
        return tuple(
            sorted(records, key=lambda item: (item.issue, item.task.created_at, item.task.id))
        )

    @staticmethod
    def _workflow_metadata(issue_dir: Path) -> dict[str, str]:
        path = issue_dir / "blackboard.json"
        if not path.is_file():
            raise ValueError("blackboard.json is missing")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("blackboard.json must contain an object")
        workflow_id = raw.get("workflow_id")
        playbook_id = raw.get("playbook_id")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("blackboard workflow_id is missing")
        if not isinstance(playbook_id, str) or not playbook_id.strip():
            raise ValueError("blackboard playbook_id is missing")
        return {"workflow_id": workflow_id, "playbook_id": playbook_id}

    @staticmethod
    def _summary(record: _Record) -> TaskSummary:
        task = record.task
        return TaskSummary(
            id=task.id,
            issue=record.issue,
            workflow_id=record.workflow_id,
            step=task.step,
            iteration=task.iteration,
            trigger=task.trigger,
            policy_id=task.policy_id,
            status=task.status.value,
            assignee_type=record.assignment.assignee_type,
            assignee_id=record.assignment.assignee_id,
            due_state="unscheduled",
            created_at=task.created_at,
            completed_at=task.completed_at,
            cancelled_at=task.cancelled_at,
        )

    @staticmethod
    def _detail(record: _Record) -> TaskDetail:
        task = record.task
        return TaskDetail(
            id=task.id,
            issue=record.issue,
            workflow_id=record.workflow_id,
            step=task.step,
            iteration=task.iteration,
            trigger=task.trigger,
            policy_id=task.policy_id,
            prompt=task.prompt,
            expected_result=dict(task.expected_result),
            continuations=dict(task.continuations),
            assignment={
                "type": record.assignment.assignee_type,
                "id": record.assignment.assignee_id,
            },
            status=task.status.value,
            due_state="unscheduled",
            wait={
                "reason": record.wait.pause_reason,
                "active": record.wait.released_at is None,
                "created_at": record.wait.created_at,
                "released_at": record.wait.released_at,
            },
            result=record.result.to_dict() if record.result else None,
            capability_approval=(
                dict(task.capability_approval) if task.capability_approval is not None else None
            ),
            timestamps={
                "created_at": task.created_at,
                "completed_at": task.completed_at,
                "cancelled_at": task.cancelled_at,
            },
        )
