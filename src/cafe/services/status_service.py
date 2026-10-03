"""Service layer for cafe status command."""

import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from cafe.core.audit_events import AuditEventStore
from cafe.core.blackboard import (
    BlackboardState,
    EventEntry,
    HandoffContract,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.git import GitOperations
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.types import PhaseStatus
from cafe.core.usage import phase_stats_without_chat
from cafe.core.workflow_models import BatonRejected

_RUNTIME_PHASE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RUNTIME_ITERATION_DIR = re.compile(r"^iteration_(0*[1-9][0-9]{0,5})$")


class StatusService:
    """Service for building workflow status timeline data."""

    def __init__(self, git_ops: Optional[GitOperations] = None, issues_root: Optional[Path] = None):
        """Initialize status service.

        Args:
            git_ops: GitOperations instance for git context detection
            issues_root: Root directory containing issue workflow data
        """
        self.git_ops = git_ops or GitOperations()
        self.issues_root = issues_root or Path(".cafe/issues")
        self._load_errors: List[Dict[str, str]] = []

    def get_current_issue(self) -> str:
        """Get current issue name from git branch context.

        Returns:
            Current issue name (branch name)

        Raises:
            RuntimeError: If not in a valid git repository or branch
        """
        try:
            branch_name = self.git_ops.get_current_branch()
            if not branch_name:
                raise RuntimeError("Failed to get current branch")
            return branch_name
        except Exception as e:
            raise RuntimeError(f"Failed to detect current issue from git context: {e}")

    @staticmethod
    def _with_declared_diagnostic(issue_dir: Path, workflow_id: str,
                                  status: Dict[str, str],
                                  audit: AuditEventStore) -> Dict[str, str]:
        declaration_path = issue_dir / "status_sources.json"
        if not declaration_path.exists():
            return status
        if (declaration_path.is_symlink() or not declaration_path.is_file()
                or declaration_path.stat().st_size > 4096):
            raise ValueError("status source is invalid")
        declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
        if (not isinstance(declaration, dict) or declaration.get("version") != 1
                or declaration.get("workflow_id") != workflow_id):
            raise ValueError("status source identity is invalid")
        source = declaration.get("diagnostic")
        if not isinstance(source, dict):
            raise ValueError("status source declaration is invalid")
        for key in ("path", "records_key", "time_key", "reason_key", "state", "next"):
            if not isinstance(source.get(key), str) or not source[key]:
                raise ValueError("status source declaration is invalid")
        if type(source.get("schema_version")) is not int or source["schema_version"] < 1:
            raise ValueError("status source schema version is invalid")
        fallback = source.get("audit_fallback")
        if (fallback is not None and (
            not isinstance(fallback, dict)
            or any(not isinstance(fallback.get(key), str) or not fallback[key]
                   for key in ("event_type", "reason_key", "next"))
        )):
            raise ValueError("status audit fallback is invalid")
        relative = Path(source["path"])
        if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                or not (issue_dir / relative).resolve().is_relative_to(issue_dir.resolve())):
            raise ValueError("status source path is unsafe")
        diagnostic_path = issue_dir / relative

        def from_audit() -> Dict[str, str]:
            if fallback is None:
                return status
            event = audit.latest_record(workflow_id, {fallback["event_type"]})
            if event is not None:
                status.update({
                    "State": source["state"],
                    "Reason": str(event["data"].get(fallback["reason_key"], "unknown")),
                    "Next": fallback["next"],
                })
            return status

        if not diagnostic_path.exists():
            return from_audit()
        if (diagnostic_path.is_symlink() or not diagnostic_path.is_file()
                or diagnostic_path.stat().st_size > 256 * 1024):
            raise ValueError("status diagnostic is invalid")
        raw = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        if (not isinstance(raw, dict)
                or raw.get("schema_version") != source["schema_version"]
                or raw.get("workflow_id") != workflow_id
                or not isinstance(raw.get(source["records_key"]), dict)):
            raise ValueError("status diagnostic identity is invalid")
        records = raw[source["records_key"]]
        if not records:
            return from_audit()
        def occurred_at(record: object) -> datetime:
            if (not isinstance(record, dict)
                    or not isinstance(record.get(source["reason_key"]), str)):
                raise ValueError("status diagnostic record is invalid")
            value = record.get(source["time_key"])
            if not isinstance(value, str):
                raise ValueError("status diagnostic time is invalid")
            instant = datetime.fromisoformat(value)
            if instant.tzinfo is None:
                raise ValueError("status diagnostic time lacks timezone")
            return instant.astimezone(timezone.utc)

        latest = max(records.values(), key=occurred_at)
        status.update({
            "State": source["state"],
            "Reason": latest[source["reason_key"]],
            "Next": source["next"],
        })
        return status

    def load_current_state(self, issue_name: str, phase_names: List[str]) -> Dict[str, str]:
        """Project the current handoff and task without creating or repairing records."""
        issue_dir = self.issues_root / issue_name
        status = {"Issue": issue_name, "State": "Unknown"}
        source = issue_dir / "blackboard.json"
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or any(
                not isinstance(raw.get(key), str) or not raw[key].strip()
                for key in ("workflow_id", "current_step")
            ):
                raise ValueError("missing workflow identity")
            state = BlackboardState.from_dict(raw, initial_step=raw["current_step"])
            audit = AuditEventStore(issue_dir)
            status["Workflow"] = state.workflow_id
            source = issue_dir / "next_step.txt"
            baton = HandoffContract.from_dict_with_current_step(
                json.loads(source.read_text(encoding="utf-8")), current_step=state.current_step
            )
            recorded = state.handoff_contract
            if recorded and recorded.to_next_step_dict() != baton.to_next_step_dict():
                raise ValueError("conflicting handoffs")
            if recorded:
                recorded.validate(allowed_steps=phase_names)
                baton.from_step = recorded.from_step
            baton.validate(allowed_steps=phase_names)
            if state.current_step not in {baton.to_step, baton.from_step}:
                raise ValueError("current step and handoff disagree")
            step = (recorded.from_step if recorded else "") or state.current_step
            if baton.to_owner is not HandoffOwner.USER:
                step = baton.to_step
            status.update({"Step": step, "Owner": baton.to_owner.value})

            source = issue_dir / "human_tasks.json"
            store = HumanTaskRecordStore(issue_dir)
            tasks = store.tasks()
            if any(task.workflow_id != state.workflow_id for task in tasks):
                raise ValueError("task belongs to another workflow")
            pending = [task for task in tasks if task.status is HumanTaskStatus.PENDING]
            if pending:
                if len(pending) != 1 or baton.to_owner is not HandoffOwner.USER:
                    raise ValueError("task and handoff disagree")
                task = pending[0]
                wait = store.get_wait_state(task.id)
                if wait.released_at is not None or task.step != step:
                    raise ValueError("task is not the current wait")
                if (
                    baton.intent is not HandoffIntent.MANUAL_HANDOFF
                    and task.trigger != baton.intent.value
                ):
                    raise ValueError("task and handoff require different actions")
                iterations = [
                    int(match.group(1))
                    for directory in (issue_dir / step).glob("iteration_*")
                    if directory.is_dir()
                    and (match := _RUNTIME_ITERATION_DIR.fullmatch(directory.name))
                ]
                if task.iteration != max(iterations, default=1):
                    raise ValueError("task belongs to a different iteration")
                materialized_record = audit.latest_record(
                    state.workflow_id,
                    {"human_task_materialized", "agent_execution_task_materialized"},
                    step=step,
                )
                materialized = (
                    EventEntry.from_dict(materialized_record)
                    if materialized_record is not None
                    else None
                )
                # Runtime may reuse a pending task after refreshing the handoff.
                # Its recorded materialization also supports legacy task keys.
                handoff_key = ":".join(
                    (
                        "user-handoff",
                        state.workflow_id,
                        step,
                        baton.intent.value,
                        recorded.created_at if recorded else baton.created_at,
                    )
                )
                if task.handoff_key != handoff_key and (
                    materialized is None or materialized.data.get("task_id") != task.id
                ):
                    raise ValueError("task has no current handoff identity")
                status.update(
                    {
                        "State": "Waiting for user",
                        "Reason": (
                            "checklist validation failed"
                            if recorded and recorded.status_code == "CHECKLIST_VALIDATION_FAILED"
                            else task.trigger.replace("_", " ")
                        ),
                        "Task": task.id,
                        "Next": f"cafe task inspect {shlex.quote(task.id)}",
                    }
                )
                return self._with_declared_diagnostic(
                    issue_dir, state.workflow_id, status, audit
                )
            if any(task.status is HumanTaskStatus.CONFIGURATION_ERROR for task in tasks):
                raise ValueError("task configuration error")

            source = issue_dir / "blackboard.json"
            if baton.to_owner is HandoffOwner.DONE:
                if state.current_step != "done":
                    raise ValueError("completion and current step disagree")
                status.update({"State": "Completed", "Reason": "Workflow completed"})
                return self._with_declared_diagnostic(
                    issue_dir, state.workflow_id, status, audit
                )
            if state.current_step == "done":
                raise ValueError("completed pointer has a nonterminal handoff")
            if baton.to_owner is HandoffOwner.USER:
                status.update(
                    {
                        "State": "Waiting for user",
                        "Reason": f"{baton.intent.value}; no current HumanTask is available",
                        "Next": f"cafe task ls --issue {shlex.quote(issue_name)}",
                    }
                )
                return self._with_declared_diagnostic(
                    issue_dir, state.workflow_id, status, audit
                )

            # Ignore notification/callback events, and do not let an old pause
            # override a subsequent start, transition, or completed human task.
            boundaries = {
                "workflow_paused",
                "workflow_interruption",
                "workflow_blocked",
                "step_started",
                "step_completed",
                "single_step_completed",
                "human_task_completed",
                "transition",
                "workflow_completed",
            }
            latest_record = audit.latest_record(state.workflow_id, boundaries)
            latest = EventEntry.from_dict(latest_record) if latest_record is not None else None
            status.update(
                {
                    "State": "Awaiting agent",
                    "Reason": baton.intent.value,
                    "Next": f"cafe show {shlex.quote(step)} output",
                }
            )
            if latest and latest.step == step:
                if latest.event_type in {
                    "workflow_paused",
                    "workflow_interruption",
                    "workflow_blocked",
                }:
                    status.update(
                        {
                            "State": "Paused",
                            "Reason": str(
                                latest.data.get("reason")
                                or latest.data.get("status_code")
                                or latest.event_type
                            ),
                        }
                    )
                elif latest.event_type == "step_started":
                    status["State"] = "Agent step in progress"
            return self._with_declared_diagnostic(issue_dir, state.workflow_id, status, audit)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, BatonRejected):
            # Do not expose raw parser text or manufacture a task/recovery choice.
            status["State"] = "Unknown"
            status["Reason"] = f"Missing, invalid, or conflicting workflow records: {source}"
            status["Next"] = "Inspect the recorded state before continuing; no action was taken."
            return status

    def load_phase_status(self, issue_name: str, phase_name: str) -> Optional[Dict[str, Any]]:
        """Load phase status from status.json or synthesize it from workflow state.

        Args:
            issue_name: Name of the issue
            phase_name: Name of the phase (spec, plan, develop, review, pr)

        Returns:
            Dictionary containing phase status information, or None if no phase data exists
        """
        status_file = self.issues_root / issue_name / phase_name / "status.json"

        if status_file.exists():
            try:
                with open(status_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                raise RuntimeError(f"Failed to load phase status from {status_file}: {e}")

        iterations = self.load_iteration_statuses(issue_name, phase_name)
        if not iterations:
            return None

        return self._synthesize_phase_status(issue_name, phase_name, iterations)

    def load_iteration_statuses(self, issue_name: str, phase_name: str) -> List[Dict[str, Any]]:
        """Load all iteration statuses for a phase from context.json files.

        Args:
            issue_name: Name of the issue
            phase_name: Name of the phase

        Returns:
            List of iteration context dictionaries with timestamp (start_time),
            end_time, status_code, cli, model, and stats, ordered by iteration number
        """
        phase_dir = self.issues_root / issue_name / phase_name

        if not phase_dir.exists():
            return []

        iterations = []

        # Read from iteration.json files (with context.json fallback) in each iteration directory
        for iteration_dir in sorted(phase_dir.glob("iteration_*")):
            if not iteration_dir.is_dir():
                continue

            context_file = (
                iteration_dir / "iteration.json"
                if (iteration_dir / "iteration.json").exists()
                else iteration_dir / "context.json"
            )
            if not context_file.exists():
                continue

            try:
                with open(context_file, "r") as f:
                    context_data = json.load(f)
                    # Extract fields needed for status display including token usage
                    iteration_info = {
                        "iteration": context_data.get("iteration"),
                        "timestamp": context_data.get("timestamp"),
                        "end_time": context_data.get("end_time"),
                        "status_code": context_data.get("status_code"),
                        "cli": context_data.get("cli"),
                        "model": context_data.get("model"),
                        "chat_usage": context_data.get("chat_usage", []),
                        "stats": (
                            phase_stats_without_chat(
                                context_data.get("stats"), context_data.get("chat_usage")
                            )
                            if context_data.get("chat_usage")
                            else context_data.get("stats")
                        ),
                    }
                    iterations.append(iteration_info)
            except (json.JSONDecodeError, IOError) as e:
                # Track errors for reporting to user later
                self._load_errors.append(
                    {"file": str(context_file), "error": str(e), "type": "iteration"}
                )
                continue

        return iterations

    def load_chat_usage(self, issue_name: str, phase_names: List[str]) -> List[Dict[str, Any]]:
        """Read existing chat aggregates without inventing timeline iterations."""
        from cafe.utils.issue_config import read_issue_config_strict

        groups = []
        config = self.issues_root / issue_name / "issue.yaml"
        if config.exists():
            try:
                groups.extend(read_issue_config_strict(config).get("chat_usage", []))
            except (OSError, ValueError, yaml.YAMLError) as error:
                self._load_errors.append(
                    {"file": str(config), "error": str(error), "type": "chat_usage"}
                )
                groups.append(dict(cli=None, mode="unavailable", incomplete_calls=1, stats={}))
        for phase in phase_names:
            for iteration in self.load_iteration_statuses(issue_name, phase):
                groups.extend(iteration.get("chat_usage", []))
        return groups

    def get_load_errors(self) -> List[Dict[str, str]]:
        """Get any errors that occurred during file loading.

        Returns:
            List of load error dictionaries containing file path and error message
        """
        return self._load_errors

    def load_context_packets(self, issue_name: str) -> List[Dict[str, Any]]:
        """Project only persisted packet relations from consumer iteration records."""
        issue_dir = self.issues_root / issue_name
        packets: List[Dict[str, Any]] = []
        if not issue_dir.exists():
            return packets
        for phase_dir in sorted(path for path in issue_dir.iterdir() if path.is_dir()):
            if not _RUNTIME_PHASE_NAME.fullmatch(phase_dir.name):
                continue
            for iteration_dir in sorted(phase_dir.glob("iteration_*")):
                match = _RUNTIME_ITERATION_DIR.fullmatch(iteration_dir.name)
                if match is None:
                    continue
                path = iteration_dir / "iteration.json"
                if not path.exists():
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(raw, dict):
                    continue
                try:
                    from cafe.core.context_packet import build_context_packet_diagnostics

                    diagnostics = build_context_packet_diagnostics(raw.get("effective_inputs", {}))
                except ValueError:
                    # Never leak an agent-authored diagnostic record.
                    continue
                for diagnostic in diagnostics:
                    packets.append(
                        {
                            "consumer": phase_dir.name,
                            "iteration": int(match.group(1)),
                            **diagnostic,
                        }
                    )
        return packets

    def _load_workflow_state(
        self, issue_name: str
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Load the current workflow pointer and baton contract if available."""
        issue_dir = self.issues_root / issue_name
        blackboard_file = issue_dir / "blackboard.json"
        next_step_file = issue_dir / "next_step.txt"

        if not blackboard_file.exists():
            return None, None

        try:
            raw = json.loads(blackboard_file.read_text(encoding="utf-8"))
            state = BlackboardState.from_dict(raw, initial_step="spec")
        except Exception:
            return None, None

        if not next_step_file.exists():
            return state.current_step, None

        try:
            payload = json.loads(next_step_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return state.current_step, None
            contract = HandoffContract.from_dict_with_current_step(
                payload,
                current_step=state.current_step,
            )
        except Exception:
            return state.current_step, None

        return state.current_step, contract.to_dict()

    def _synthesize_phase_status(
        self, issue_name: str, phase_name: str, iterations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Derive phase status from iteration contexts and baton state."""
        first_iteration = iterations[0]
        latest_iteration = iterations[-1]
        current_step, baton = self._load_workflow_state(issue_name)

        latest_status_code = latest_iteration.get("status_code")
        latest_end_time = latest_iteration.get("end_time")

        baton_from_step = str(baton.get("from_step", "")) if baton else ""
        baton_to_step = str(baton.get("to_step", "")) if baton else ""
        baton_to_owner = str(baton.get("to_owner", "")) if baton else ""

        paused_in_phase = (
            baton_from_step == phase_name
            and baton_to_step == "user"
            and baton_to_owner == HandoffOwner.USER.value
        )
        awaiting_agent_in_phase = (
            baton_to_step == phase_name and baton_to_owner == HandoffOwner.AGENT.value
        )
        active_in_phase = current_step == phase_name or paused_in_phase or awaiting_agent_in_phase

        status = PhaseStatus.IN_PROGRESS if active_in_phase else PhaseStatus.COMPLETED

        synthesized = {
            "timestamp": first_iteration.get("timestamp"),
            "status": status.value,
            "status_code": latest_status_code,
        }

        if status == PhaseStatus.COMPLETED and latest_end_time:
            synthesized["end_time"] = latest_end_time

        return synthesized
