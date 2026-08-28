"""Blackboard-first workflow runtime.

The workflow-core entry point. It keeps blackboard/baton state as the
primary source of truth for step transitions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from cafe.core.active_issue import clear_marker_if_matches
from cafe.core.automatic_steps import (
    AutomaticExecutionResult,
    AutomaticExecutorRegistry,
    default_automatic_executor_registry,
)
from cafe.core.blackboard import (
    BlackboardStore,
    HandoffContract,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.capabilities import (
    CAPABILITY_PR_PUBLISH_ID,
    CAPABILITY_SLACK_HUMAN_TASK_ID,
    default_capability_definition_dirs,
    load_capability_registry,
    run_capability_request,
    validation_rejection_receipt,
)
from cafe.core.human_task_notifications import (
    load_human_task_notification_settings,
    sanitize_human_task_metadata,
)
from cafe.core.human_task_records import HumanTask, HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_tasks import resolve_step_human_task
from cafe.core.playbook import resolve_step_behavior
from cafe.core.questions_schema import validate_questions_xml
from cafe.core.status_codes import (
    PhaseStatusCode,
    StatusCodeParser,
    effective_step_handoff_intents,
    effective_step_status_codes,
    step_on_declares,
    transition_map_key,
)
from cafe.core.workflow_feedback import WorkflowFeedbackLedger
from cafe.core.workflow_models import (
    BatonRejected,
    PlaybookRunResult,
    StepExecutionResult,
    StepInterrupted,
)
from cafe.utils.checklist_validator import validate_checklist

STATUS_TOKEN_PATTERN = re.compile(r"\bCAFE_[A-Z0-9_]+\b")
GOTO_PATTERN = re.compile(r"GOTO\s*:\s*([a-zA-Z0-9_-]+)")
PAUSE_STATUS_CODES = {
    PhaseStatusCode.READY_FOR_REVIEW.value,
    PhaseStatusCode.CONFIRM_OUTPUT.value,
    PhaseStatusCode.ALIGNMENT_CHECKPOINT.value,
    PhaseStatusCode.NEED_CLARIFICATION.value,
    PhaseStatusCode.NEED_PERMISSION.value,
}
SLACK_HUMAN_TASK_TIMEOUT_SEC = 5.0


@dataclass
class StepIterationFrame:
    execution_result: Any
    response: str
    artifacts: Dict[str, str]
    explicit_status_code: Optional[str]
    auto_continue: bool


@dataclass
class PostContractResult:
    status_code: str
    next_step: Optional[str] = None
    terminal_result: Optional[PlaybookRunResult] = None


@dataclass
class HandoffReconciliationResult:
    reconciled: bool
    status_code: str = ""
    contract: Optional[HandoffContract] = None
    iteration_dir: Optional[Path] = None
    missing_evidence: list[str] = field(default_factory=list)
    validated_evidence: list[str] = field(default_factory=list)


@dataclass
class RuntimePositionResolution:
    current_step: str
    realignment_result: Optional[PlaybookRunResult] = None


class BlackboardWorkflowRuntime:
    """Workflow runtime that prefers blackboard/baton-driven transitions."""

    def __init__(
        self,
        *,
        issue_dir: Path,
        playbook: Dict,
        executor: Any,
        automatic_registry: Optional[AutomaticExecutorRegistry] = None,
    ) -> None:
        self.issue_dir = issue_dir
        self.playbook = playbook
        self.executor = executor
        self.automatic_registry = automatic_registry or default_automatic_executor_registry()

        playbook_meta = playbook["playbook"]
        self.playbook_id = str(playbook_meta["id"])
        self.playbook_source = str(getattr(playbook, "source", "unknown"))
        self.steps: Dict = playbook["steps"]
        self.start_step = str(playbook.get("entry_point") or next(iter(self.steps.keys())))
        self._validate_automatic_executor_declarations()

        self.blackboard_store = BlackboardStore(issue_dir)
        self.blackboard = self.blackboard_store.load_or_create(
            self.start_step,
            playbook_id=self.playbook_id,
            tolerate_invalid_baton=True,
        )
        self._replaced_user_handoff: HandoffContract | None = None

    def _validate_automatic_executor_declarations(self) -> None:
        """Reject unavailable automatic authority before recording a workflow visit."""
        for step_name, step_def in self.steps.items():
            if not isinstance(step_def, dict) or self._owner_for_step(step_def) != "auto":
                continue
            declaration = step_def.get("automatic")
            executor_id = declaration.get("executor") if isinstance(declaration, dict) else None
            inputs = declaration.get("inputs") if isinstance(declaration, dict) else None
            if not isinstance(executor_id, str) or not self.automatic_registry.is_registered(
                executor_id
            ):
                raise ValueError(
                    f"Step '{step_name}' automatic executor {executor_id!r} is not registered"
                )
            if not isinstance(inputs, dict):
                raise ValueError(
                    f"Step '{step_name}' has an invalid automatic executor declaration"
                )
            self.automatic_registry.validate_inputs(executor_id, inputs)

    def _repository_root(self) -> Path:
        if self.issue_dir.parent.name == "issues" and self.issue_dir.parent.parent.name == ".cafe":
            return self.issue_dir.parent.parent.parent
        return self.issue_dir.parent

    def _notify_new_human_task(self, task: HumanTask) -> None:
        """Record one source-independent, machine-controlled delivery decision."""
        attempt_id = f"slack-human-task:{task.id}"
        try:
            with self.blackboard_store.capability_receipt_transaction(self.blackboard):
                try:
                    current_task = HumanTaskRecordStore(self.issue_dir).get_task(task.id)
                except Exception:
                    return
                if current_task.status is not HumanTaskStatus.PENDING:
                    self._record_notification_outcome(
                        current_task,
                        attempt_id=attempt_id,
                        code="human_task_notification_not_actionable",
                        outcome="skipped",
                    )
                    return
                self._dispatch_human_task_notification(task, attempt_id=attempt_id)
        except Exception:
            # Lock and persistence failures must not make Slack authoritative over human work.
            return

    def _notification_inputs(self, task: HumanTask) -> dict[str, str]:
        """Return the closed safe payload shared by receipts and capability requests."""
        return {
            "repository": sanitize_human_task_metadata(self._repository_root().name),
            "workflow_id": sanitize_human_task_metadata(task.workflow_id),
            "task_id": sanitize_human_task_metadata(task.id),
            "step": sanitize_human_task_metadata(task.step),
            "task_type": sanitize_human_task_metadata(task.policy_id),
        }

    def _record_notification_outcome(
        self,
        task: HumanTask,
        *,
        attempt_id: str,
        code: str,
        outcome: str,
    ) -> None:
        """Persist a non-dispatch notification decision without task prompt data."""
        self.blackboard_store.upsert_capability_receipt(
            self.blackboard,
            {
                "notification_attempt_id": attempt_id,
                "correlation_id": attempt_id,
                "capability": CAPABILITY_SLACK_HUMAN_TASK_ID,
                "success": False,
                "category": "notification_policy",
                "code": code,
                "decision": "skip",
                "outcome": outcome,
                "inputs": self._notification_inputs(task),
                "outputs": {},
                "workflow_id": task.workflow_id,
                "task_id": task.id,
            },
        )

    def _dispatch_human_task_notification(self, task: HumanTask, *, attempt_id: str) -> None:
        existing_receipt = next(
            (
                receipt
                for receipt in self.blackboard.capability_receipts
                if receipt.get("notification_attempt_id") == attempt_id
            ),
            None,
        )
        if existing_receipt is not None:
            if existing_receipt.get("code") == "slack_notification_attempting":
                interrupted_receipt = dict(existing_receipt)
                interrupted_receipt.update(
                    {
                        "success": False,
                        "category": "adapter_error",
                        "code": "slack_notification_interrupted",
                        "decision": "allow",
                        "outcome": "execution_interrupted",
                    }
                )
                self.blackboard_store.upsert_capability_receipt(
                    self.blackboard, interrupted_receipt
                )
            else:
                dedup_attempt_id = f"{attempt_id}:deduplicated"
                if not any(
                    receipt.get("notification_attempt_id") == dedup_attempt_id
                    for receipt in self.blackboard.capability_receipts
                ):
                    self._record_notification_outcome(
                        task,
                        attempt_id=dedup_attempt_id,
                        code="human_task_notification_deduplicated",
                        outcome="deduplicated",
                    )
            return
        settings = load_human_task_notification_settings()
        if not settings.enabled:
            self._record_notification_outcome(
                task,
                attempt_id=attempt_id,
                code=settings.code,
                outcome=settings.outcome,
            )
            return
        repo_root = self._repository_root()
        notification_inputs = self._notification_inputs(task)
        capability_request = {
            "capability": CAPABILITY_SLACK_HUMAN_TASK_ID,
            "args": notification_inputs,
            "effects": {
                "writes": [],
                "network_destinations": ["hooks.slack.com"],
                "browser_open": [],
            },
            "credentials": ["slack_human_task_webhook"],
            "permissions": {"network": ["hooks.slack.com"]},
        }
        attempting_receipt = {
            "notification_attempt_id": attempt_id,
            "correlation_id": attempt_id,
            "capability": CAPABILITY_SLACK_HUMAN_TASK_ID,
            "success": False,
            "category": "pending",
            "code": "slack_notification_attempting",
            "decision": "pending",
            "outcome": "attempting",
            "inputs": notification_inputs,
            "outputs": {},
            "workflow_id": task.workflow_id,
            "task_id": task.id,
        }
        self.blackboard_store.upsert_capability_receipt(self.blackboard, attempting_receipt)
        try:
            registry = load_capability_registry(default_capability_definition_dirs(repo_root))
            run = run_capability_request(
                repo_root=repo_root,
                registry=registry,
                capability_request=capability_request,
                output_file=self.issue_dir / "blackboard.json",
                timeout_sec=SLACK_HUMAN_TASK_TIMEOUT_SEC,
                trusted_human_task_notification=True,
            )
            receipt = dict(run.receipt)
        except Exception:  # The durable HumanTask remains authoritative on host failure.
            receipt = validation_rejection_receipt(
                capability=CAPABILITY_SLACK_HUMAN_TASK_ID,
                code="slack_notification_internal_error",
                raw_request=capability_request,
                error_detail="slack_notification_internal_error",
            )
        receipt.update(
            {
                "notification_attempt_id": attempt_id,
                "workflow_id": task.workflow_id,
                "task_id": task.id,
            }
        )
        self.blackboard_store.upsert_capability_receipt(self.blackboard, receipt)

    @staticmethod
    def _extract_goto_target(response: str) -> Optional[str]:
        match = GOTO_PATTERN.search(response)
        if not match:
            return None
        return match.group(1)

    @staticmethod
    def _has_event(execution_result: Any, event_type: str) -> bool:
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return False
        return any(isinstance(event, dict) and event.get("type") == event_type for event in events)

    @staticmethod
    def _capability_receipt_satisfied(execution_result: Any, capability_id: str) -> bool:
        """True when a capability left a success receipt.

        ``pr_synced`` remains a legacy success marker for ``cafe.pr.publish``.
        """
        if capability_id == CAPABILITY_PR_PUBLISH_ID and BlackboardWorkflowRuntime._has_event(
            execution_result, "pr_synced"
        ):
            return True
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return False
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "capability_receipt":
                continue
            if event.get("capability") != capability_id:
                continue
            if event.get("success") is True:
                return True
        return False

    @staticmethod
    def _pending_capability_approval(execution_result: Any) -> Optional[dict[str, Any]]:
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return None
        return next(
            (
                event
                for event in events
                if isinstance(event, dict)
                and event.get("type") == "capability_approval_pending"
                and isinstance(event.get("task_id"), str)
            ),
            None,
        )

    @staticmethod
    def _step_declared_capability_ids(step_def: Dict) -> list[str]:
        raw = step_def.get("capability_requests") or []
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    def _required_capability_ids(self, current_step: str) -> list[str]:
        step_def = self.steps.get(current_step, {})
        declared = self._step_declared_capability_ids(step_def)
        behavior = resolve_step_behavior(self.playbook, current_step)
        if behavior.publish_confirmation and not self._step_requires_publish_receipt(current_step):
            return []
        return declared

    def _is_baton_driven_step(self, current_step: str) -> bool:
        return resolve_step_behavior(self.playbook, current_step).completion == "baton" or bool(
            self._required_capability_ids(current_step)
        )

    def _default_pause_intent(self, current_step: str, status_code: str) -> HandoffIntent:
        step_def = self.steps.get(current_step, {})
        if status_code in {
            PhaseStatusCode.READY_FOR_REVIEW.value,
            PhaseStatusCode.CONFIRM_OUTPUT.value,
        } and step_on_declares(step_def, "confirm_output"):
            return HandoffIntent.CONFIRM_OUTPUT
        if status_code == PhaseStatusCode.NEED_CLARIFICATION.value:
            return HandoffIntent.NEED_CLARIFICATION
        if status_code == PhaseStatusCode.ALIGNMENT_CHECKPOINT.value:
            return HandoffIntent.ALIGNMENT_CHECKPOINT
        if status_code == PhaseStatusCode.NEED_PERMISSION.value:
            return HandoffIntent.NEED_PERMISSION
        return HandoffIntent.MANUAL_HANDOFF

    @staticmethod
    def _baton_rejected_prompt(br: BatonRejected) -> str:
        if br.invalid_value:
            value_msg = f"invalid value '{br.invalid_value}'"
        else:
            value_msg = "required field missing from the payload"
        message = (
            f"[BATON ERROR] Your baton was rejected because field '{br.field}' has {value_msg}. "
            f"Valid values are: {br.valid_values}. "
            "Please rewrite next_step.txt with a correct structured baton. "
            "Retry in baton-only mode: do not rewrite output.md, checklist.md, or "
            "questions.xml unless strictly required. "
            "If you are asking the user a question, use to_owner='user', "
            "to_step='user', and intent='need_clarification'. "
            "If your step omits safe defaults, you may keep status_code and source empty; "
            "other required keys must be present and valid."
        )
        if br.field == "to_step":
            message += (
                " The baton must target a valid next step and cannot point back to the same phase."
            )
        return message

    def _same_step_baton_rejected(self, *, current_step: str) -> BatonRejected:
        valid_targets = sorted((set(self.steps.keys()) | {"user", "done"}) - {current_step})
        return BatonRejected(
            field="to_step",
            invalid_value=current_step,
            valid_values=valid_targets,
        )

    def _validate_agent_baton(self, *, current_step: str) -> None:
        contract = self.blackboard_store.load_handoff_contract(
            self.blackboard,
            allowed_steps=list(self.steps.keys()),
        )
        if contract.to_owner != HandoffOwner.AGENT:
            raise RuntimeError(
                f"Baton owner mismatch before step '{current_step}': expected agent, "
                f"got {contract.to_owner.value}"
            )
        if contract.to_step != current_step:
            raise RuntimeError(
                f"Baton target mismatch before step '{current_step}': baton points to "
                f"'{contract.to_step}'"
            )

    def _resolve_runtime_position_from_handoff(self) -> RuntimePositionResolution:
        """Use the structured baton as the runtime position source.

        ``blackboard.current_step`` remains persisted context for observability,
        but an already-valid handoff contract is the authoritative routing
        signal at resume time.
        """
        try:
            contract = self.blackboard_store.load_handoff_contract(
                self.blackboard,
                allowed_steps=list(self.steps.keys()),
            )
        except BatonRejected:
            return RuntimePositionResolution(current_step=self.blackboard.current_step)
        previous = self.blackboard.current_step
        if contract.to_owner == HandoffOwner.AGENT:
            resolved = contract.to_step
        elif contract.to_owner == HandoffOwner.USER:
            resolved = "user"
        else:
            resolved = "done"

        if previous != resolved:
            self.blackboard_store.record_event(
                self.blackboard,
                "runtime_position_realigned",
                {
                    "previous_current_step": previous,
                    "from_step": contract.from_step,
                    "to_owner": contract.to_owner.value,
                    "to_step": contract.to_step,
                    "resolved_step": resolved,
                    "source": contract.source,
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, resolved)
            if contract.to_owner == HandoffOwner.AGENT:
                return RuntimePositionResolution(
                    current_step=resolved,
                    realignment_result=PlaybookRunResult(
                        final_step=previous,
                        final_status_code="BATON_POSITION_REALIGNED",
                        completed=False,
                    ),
                )
        return RuntimePositionResolution(current_step=resolved)

    def _result_from_terminal_position(self, current_step: str) -> Optional[PlaybookRunResult]:
        if current_step not in {"user", "done"}:
            return None
        contract = self.blackboard_store.load_handoff_contract(
            self.blackboard,
            allowed_steps=list(self.steps.keys()),
        )
        status_code = contract.status_code or f"BATON_{contract.intent.value.upper()}"
        if current_step == "done":
            cafe_dir = self.issue_dir.parent.parent
            clear_marker_if_matches(cafe_dir, self.issue_dir.name)
        return PlaybookRunResult(
            final_step=contract.from_step,
            final_status_code=status_code,
            completed=current_step == "done",
        )

    def _resolve_next_step(
        self,
        *,
        current_step: str,
        response: str,
        status_code: str,
    ) -> tuple[Optional[str], str]:
        step = self.steps[current_step]
        transitions = step.get("on", {})
        goto_target = self._extract_goto_target(response)
        if goto_target:
            allowed_targets = {str(target) for target in step.get("allowed_goto", [])}
            if goto_target in allowed_targets:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "goto",
                    {
                        "step": current_step,
                        "goto_target": goto_target,
                    },
                )
                return goto_target, "goto"
            self.blackboard_store.record_event(
                self.blackboard,
                "goto_ignored",
                {
                    "step": current_step,
                    "goto_target": goto_target,
                    "reason": "not in allowed_goto",
                },
            )

        transition_key = status_code
        if status_code:
            try:
                transition_key = transition_map_key(PhaseStatusCode(status_code))
            except ValueError:
                transition_key = status_code
        target = transitions.get(transition_key) if transition_key else None
        if target is None:
            default_target = transitions.get("default")
            if default_target:
                return str(default_target), "default"
            return None, "terminal"
        return str(target), "status"

    @staticmethod
    def _extract_handoff_intent(execution_result: Any) -> Optional[HandoffIntent]:
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return None
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "handoff_intent":
                continue
            raw_intent = event.get("intent")
            if not isinstance(raw_intent, str):
                continue
            try:
                return HandoffIntent(raw_intent)
            except ValueError:
                continue
        return None

    def _resolve_next_step_from_handoff(self, *, current_step: str) -> Optional[str]:
        """Derive the next step from the blackboard handoff contract.

        When an agent omits a status code but the step executor has already
        written a handoff contract (e.g. ``confirm_output`` → ``user``),
        we use the contract's ``to_step`` to advance the workflow.  This
        keeps the engine baton-first rather than status-code-first.
        """
        try:
            contract = self.blackboard_store.load_handoff_contract(
                self.blackboard,
                allowed_steps=list(self.steps.keys()),
            )
        except Exception:
            return None
        if getattr(contract, "from_step", None) != current_step:
            return None
        to_step = getattr(contract, "to_step", None)
        if to_step is None or to_step == current_step:
            return None
        # Validate the target step exists in the playbook or is a known
        # synthetic step (user, done, _done).
        if to_step in self.steps or to_step in {"user", "done", "_done"}:
            return str(to_step)
        return None

    def _restore_interrupted_step_handoff(self, *, current_step: str, reason: str) -> None:
        """Keep the baton pinned to the interrupted step when recovery fails."""
        self.blackboard_store.set_current_step(self.blackboard, current_step)
        self.blackboard_store.update_handoff_contract(
            self.blackboard,
            from_step=current_step,
            to_owner=HandoffOwner.AGENT,
            to_step=current_step,
            intent=HandoffIntent.AWAIT_AGENT,
            source="workflow.interrupted_step",
        )
        self.blackboard_store.record_event(
            self.blackboard,
            "interrupted_handoff_reset",
            {
                "step": current_step,
                "reason": reason,
            },
        )

    def _load_agent_written_handoff_contract(self, *, current_step: str) -> HandoffContract:
        """Load and normalize a baton written by a just-finished agent step.

        Only the structured JSON baton contract is accepted; agents must
        always write structured JSON batons to ``next_step.txt``.
        """
        self.blackboard = self.blackboard_store.load_or_create(current_step)
        contract = self.blackboard_store.load_handoff_contract(
            self.blackboard,
            allowed_steps=list(self.steps.keys()),
        )
        if contract.source == "unknown":
            contract.source = "baton"
        self.blackboard_store.write_handoff_contract(self.blackboard, contract)
        return contract

    def _step_requires_publish_receipt(self, current_step: str) -> bool:
        if not resolve_step_behavior(self.playbook, current_step).publish_confirmation:
            return False
        issue_yaml = self.issue_dir / "issue.yaml"
        if not issue_yaml.exists():
            return False
        try:
            import yaml  # type: ignore[import-untyped]

            data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            return False
        pr_cfg = data.get("pr") or {}
        return pr_cfg.get("auto_create", False) is True

    def _status_from_contract(self, current_step: str, execution_result: Any) -> str:
        contract = self._load_agent_written_handoff_contract(current_step=current_step)
        if contract.to_owner == HandoffOwner.AGENT and contract.to_step == current_step:
            explicit_status_code = getattr(execution_result, "status_code", None)
            if explicit_status_code:
                return str(explicit_status_code)
            return "NO_BATON_TRANSITION"
        explicit_status_code = getattr(execution_result, "status_code", None)
        return (
            contract.status_code
            or str(explicit_status_code or "")
            or f"BATON_{contract.intent.value.upper()}"
        )

    def _load_step_handoff_contract(self, *, current_step: str) -> Optional[HandoffContract]:
        contract = self._load_agent_written_handoff_contract(current_step=current_step)
        if contract.from_step != current_step:
            return None
        if not (contract.to_owner == HandoffOwner.AGENT and contract.to_step == current_step):
            valid_intents = effective_step_handoff_intents(self.steps.get(current_step, {}))
            if contract.intent.value not in valid_intents:
                raise BatonRejected(
                    field="intent",
                    invalid_value=contract.intent.value,
                    valid_values=valid_intents,
                )
        return contract

    @staticmethod
    def _resolve_step_iteration_limit(step_def: Dict) -> Optional[int]:
        raw_limit = step_def.get("max_iterations")
        if raw_limit is None:
            return None
        if isinstance(raw_limit, int):
            return raw_limit
        if isinstance(raw_limit, str) and raw_limit.isdigit():
            return int(raw_limit)
        return None

    @staticmethod
    def _extract_status_like_tokens(
        *, response: str, explicit_status_code: Optional[str]
    ) -> set[str]:
        tokens = set(STATUS_TOKEN_PATTERN.findall(response or ""))
        haystack = f"{response or ''}\n{explicit_status_code or ''}"
        if explicit_status_code:
            tokens.add(explicit_status_code)
        for code in PhaseStatusCode:
            token = code.value
            if not token:
                continue
            if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", haystack, flags=re.IGNORECASE):
                tokens.add(token)
        return tokens

    def _resolve_review_confirmed_successor(self, current_step: str) -> Optional[str]:
        step = self.steps[current_step]
        transitions = step.get("on", {})
        if not isinstance(transitions, dict):
            return None

        confirmed_target = transitions.get(transition_map_key(PhaseStatusCode.CONFIRMED))
        if confirmed_target and confirmed_target != current_step:
            return str(confirmed_target)

        for target in transitions.values():
            if target != current_step:
                return str(target)
        return None

    @staticmethod
    def _normalize_execution_result(
        execution_result: Any,
    ) -> tuple[str, Dict[str, str], Optional[str], bool]:
        auto_continue = False
        explicit_status_code: Optional[str] = None
        if isinstance(execution_result, StepExecutionResult):
            return (
                execution_result.response,
                execution_result.artifacts,
                execution_result.status_code,
                execution_result.auto_continue,
            )
        if isinstance(execution_result, tuple) and len(execution_result) == 3:
            response, artifacts, metadata = execution_result
            if isinstance(metadata, dict):
                auto_continue = bool(metadata.get("auto_continue"))
                raw_status_code = metadata.get("status_code")
                if isinstance(raw_status_code, str):
                    explicit_status_code = raw_status_code
            return response, artifacts, explicit_status_code, auto_continue
        response, artifacts = execution_result
        return response, artifacts, explicit_status_code, auto_continue

    def _parse_legacy_status(
        self,
        *,
        step_def: Dict,
        response: str,
        explicit_status_code: Optional[str],
    ) -> tuple[Optional[PhaseStatusCode], Optional[str], list[PhaseStatusCode]]:
        valid_codes = effective_step_status_codes(step_def)
        allowed_values = {code.value for code in valid_codes}
        goto_target = self._extract_goto_target(response)
        status_code_obj = (
            PhaseStatusCode(explicit_status_code)
            if explicit_status_code in allowed_values
            else None
        )
        if status_code_obj is None:
            status_code_obj = StatusCodeParser.extract(
                response,
                valid_codes=valid_codes,
            )
        return status_code_obj, goto_target, valid_codes

    @staticmethod
    def _validate_assignee_type(step_name: str, step_def: Dict) -> None:
        assignee_type = str(step_def.get("assignee_type", "agent"))
        if assignee_type != "agent":
            raise RuntimeError(
                f"Step '{step_name}' has assignee_type={assignee_type}, which is not "
                "supported in v0.2. "
                "Use v0.3+ or change to assignee_type=agent."
            )

    @staticmethod
    def _owner_for_step(step_def: Dict) -> str:
        owner = str(step_def.get("assignee_type", "agent"))
        if owner not in {"agent", "human", "auto", "hybrid"}:
            raise RuntimeError(f"Step has unsupported assignee_type={owner!r}")
        return owner

    def _ensure_step_visit_within_limit(
        self, *, current_step: str, step_def: Dict, runtime: str
    ) -> int:
        """Return the next visit number or fail before an owner performs work."""
        visits = self.blackboard.step_visit_counts
        visit_count = visits.get(current_step, 0) + 1
        max_iterations = self._resolve_step_iteration_limit(step_def)
        if max_iterations is not None and visit_count > max_iterations:
            self.blackboard_store.record_event(
                self.blackboard,
                "loop_detected",
                {
                    "step": current_step,
                    "visits": visit_count,
                    "max_iterations": max_iterations,
                    "runtime": runtime,
                },
            )
            raise RuntimeError(f"Step '{current_step}' exceeded max_iterations={max_iterations}")
        return visit_count

    def _record_step_visit(self, *, current_step: str, step_def: Dict, runtime: str) -> int:
        """Persist the top-level visit before any owner-specific side effect."""
        visit_count = self._ensure_step_visit_within_limit(
            current_step=current_step,
            step_def=step_def,
            runtime=runtime,
        )
        visits = self.blackboard.step_visit_counts
        visits[current_step] = visit_count
        self.blackboard_store.save(self.blackboard)
        return visit_count

    def _rollback_step_visit(self, *, current_step: str, visit_count: int) -> None:
        """Undo a provisional agent visit when execution never yields a result."""
        visits = self.blackboard.step_visit_counts
        if visits.get(current_step) != visit_count:
            return
        if visit_count > 1:
            visits[current_step] = visit_count - 1
        else:
            visits.pop(current_step, None)
        self.blackboard_store.save(self.blackboard)

    def _materialize_owned_human_task(
        self,
        *,
        current_step: str,
        trigger: str,
        status_code: str,
        runtime: str,
        cursor: Optional[Dict[str, Any]] = None,
    ) -> PlaybookRunResult:
        """Create/recover one owner-declared task without executing an agent."""
        records = HumanTaskRecordStore(self.issue_dir)
        iteration = self._human_task_iteration(current_step)
        try:
            policy, binding = resolve_step_human_task(
                playbook_data=self.playbook,
                step_name=current_step,
                trigger=trigger,
                iteration=iteration,
            )
        except (LookupError, TypeError, ValueError) as exc:
            records.record_configuration_error(
                workflow_id=self.blackboard.workflow_id,
                step=current_step,
                iteration=iteration,
                trigger=trigger,
                reason=str(exc),
            )
            self.blackboard_store.record_event(
                self.blackboard,
                "human_task_configuration_error",
                {"step": current_step, "trigger": trigger, "reason": str(exc)},
            )
            return PlaybookRunResult(
                final_step=current_step,
                final_status_code="HUMAN_TASK_CONFIGURATION_ERROR",
                completed=False,
            )

        replaced_handoff = self._replaced_user_handoff
        self.blackboard_store.update_handoff_contract(
            self.blackboard,
            from_step=current_step,
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.MANUAL_HANDOFF,
            status_code=status_code,
            source="workflow.owner_human",
        )
        contract = self.blackboard.handoff_contract
        if contract is None:
            raise RuntimeError("human-owned task did not create a handoff contract")
        handoff_key = self._human_task_handoff_key(contract)
        if replaced_handoff is None:
            existing_task = next(
                (
                    task
                    for task in records.tasks()
                    if task.workflow_id == self.blackboard.workflow_id
                    and task.status is HumanTaskStatus.PENDING
                    and task.step == current_step
                    and task.iteration == iteration
                    and task.trigger == trigger
                    and task.policy_id == policy.id
                ),
                None,
            )
            if existing_task is not None:
                handoff_key = existing_task.handoff_key

        materialization = records.materialize_with_status(
            workflow_id=self.blackboard.workflow_id,
            step=current_step,
            iteration=iteration,
            trigger=trigger,
            policy_id=policy.id,
            prompt=policy.prompt,
            expected_result=policy.model_dump(mode="json"),
            continuations=binding.outcomes,
            assignee_type="human",
            handoff_key=handoff_key,
            superseded_task_ids=self._superseded_human_task_ids(
                records,
                step=current_step,
                iteration=iteration,
                trigger=trigger,
                policy_id=policy.id,
                replaced_handoff=replaced_handoff,
            ),
        )
        task = materialization.task
        self._notify_new_human_task(task)
        self._replaced_user_handoff = None
        if cursor is not None:
            cursor["task_id"] = task.id
            self.blackboard.ownership_cursor = cursor
            self.blackboard_store.save(self.blackboard)
        self.blackboard_store.set_current_step(self.blackboard, "user")
        if materialization.created:
            self.blackboard_store.record_event(
                self.blackboard,
                "human_task_materialized",
                {"step": current_step, "trigger": trigger, "task_id": task.id, "owner": "human"},
            )
        self.blackboard_store.record_event(
            self.blackboard,
            "workflow_paused",
            {
                "step": current_step,
                "status_code": status_code,
                "reason": "human_owner",
                "runtime": runtime,
            },
        )
        return PlaybookRunResult(
            final_step=current_step,
            final_status_code=status_code,
            completed=False,
            detail=task.id,
        )

    def _run_human_owned_step(
        self, *, current_step: str, step_def: Dict, visit_count: int, runtime: str
    ) -> PlaybookRunResult:
        return self._materialize_owned_human_task(
            current_step=current_step,
            trigger="initial",
            status_code="HUMAN_TASK_PENDING",
            runtime=runtime,
        )

    def _complete_owned_transition(
        self, *, current_step: str, status_code: str, runtime: str, source: str
    ) -> PlaybookRunResult:
        next_step, transition_source = self._resolve_next_step(
            current_step=current_step, response="", status_code=status_code
        )
        if next_step is None:
            return PlaybookRunResult(
                final_step=current_step, final_status_code="NO_STATUS_TRANSITION", completed=False
            )
        if next_step in {"done", "_done"}:
            return self._emit_complete(
                current_step=current_step,
                status_code=status_code,
                next_step=next_step,
                runtime=runtime,
                reason=source,
                update_contract=True,
                contract_source="workflow.owner_transition",
            )
        if next_step not in self.steps:
            raise RuntimeError(f"Unknown owner transition target '{next_step}'")
        self._emit_transition(
            current_step=current_step,
            next_step=next_step,
            status_code=status_code,
            source=transition_source,
            runtime=runtime,
            update_contract=True,
            contract_source="workflow.owner_transition",
        )
        return PlaybookRunResult(
            final_step=current_step, final_status_code=status_code, completed=False
        )

    def _prepare_auto_owned_step(
        self, *, current_step: str, step_def: Dict
    ) -> AutomaticExecutionResult | PlaybookRunResult:
        """Execute and validate a trusted automatic result before workflow mutation."""
        declaration = step_def.get("automatic")
        if not isinstance(declaration, dict):
            raise RuntimeError(f"Step '{current_step}' has no automatic executor declaration")
        executor_id = declaration.get("executor")
        inputs = declaration.get("inputs", {})
        if not isinstance(executor_id, str) or not isinstance(inputs, dict):
            raise RuntimeError(
                f"Step '{current_step}' has an invalid automatic executor declaration"
            )
        try:
            result: AutomaticExecutionResult = self.automatic_registry.execute(executor_id, inputs)
        except Exception as exc:
            self.blackboard_store.record_event(
                self.blackboard,
                "automatic_step_rejected",
                {"step": current_step, "executor": executor_id, "reason": str(exc)},
            )
            return PlaybookRunResult(
                final_step=current_step,
                final_status_code="AUTOMATIC_EXECUTOR_REJECTED",
                completed=False,
                detail=str(exc),
            )

        next_step, _transition_source = self._resolve_next_step(
            current_step=current_step,
            response="",
            status_code=result.intent,
        )
        if next_step is None or (
            next_step not in {"done", "_done"} and next_step not in self.steps
        ):
            reason = (
                f"automatic executor {executor_id!r} returned undeclared intent {result.intent!r}"
                if next_step is None
                else f"automatic executor {executor_id!r} targeted unknown step {next_step!r}"
            )
            self.blackboard_store.record_event(
                self.blackboard,
                "automatic_step_rejected",
                {"step": current_step, "executor": executor_id, "reason": reason},
            )
            return PlaybookRunResult(
                final_step=current_step,
                final_status_code="AUTOMATIC_EXECUTOR_REJECTED",
                completed=False,
                detail=reason,
            )
        return result

    def _run_auto_owned_step(
        self,
        *,
        current_step: str,
        step_def: Dict,
        visit_count: int,
        runtime: str,
        result: AutomaticExecutionResult,
    ) -> PlaybookRunResult:
        """Persist a previously validated automatic completion."""
        declaration = step_def["automatic"]
        executor_id = declaration["executor"]
        self._store_artifacts(result.artifacts)
        self.blackboard_store.record_event(
            self.blackboard,
            "automatic_step_completed",
            {"step": current_step, "executor": executor_id, "intent": result.intent},
        )
        return self._complete_owned_transition(
            current_step=current_step,
            status_code=result.intent,
            runtime=runtime,
            source="automatic_executor",
        )

    @staticmethod
    def _hybrid_target(portion: Dict[str, Any], completion_key: str) -> Optional[Dict[str, str]]:
        raw_on = portion.get("on")
        if not isinstance(raw_on, dict):
            return None
        raw_target = raw_on.get(completion_key)
        return dict(raw_target) if isinstance(raw_target, dict) else None

    @staticmethod
    def _normalize_hybrid_completion_key(completion_key: str) -> str:
        """Map a phase status to the declared hybrid transition key."""
        try:
            return transition_map_key(PhaseStatusCode(completion_key))
        except ValueError:
            return completion_key

    def _captured_hybrid_completion_key(
        self, *, captured: str, current_step: str, portion_id: str
    ) -> Optional[str]:
        """Accept only the portion-local baton shape the runtime requested."""
        try:
            raw_baton = json.loads(captured)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw_baton, dict):
            return None
        expected_source = f"hybrid_portion:{current_step}:{portion_id}"
        if (
            raw_baton.get("from_step") != current_step
            or raw_baton.get("to_owner") != "agent"
            or raw_baton.get("to_step") != current_step
            or raw_baton.get("source") != expected_source
        ):
            return None
        status_code = raw_baton.get("status_code")
        intent = raw_baton.get("intent")
        candidates = [
            self._normalize_hybrid_completion_key(value)
            for value in (status_code, intent)
            if isinstance(value, str) and value
        ]
        if not candidates or len(set(candidates)) != 1:
            return None
        return candidates[0]

    def _run_hybrid_owned_step(
        self,
        *,
        current_step: str,
        step_def: Dict,
        visit_count: int,
        runtime: str,
        portions_remaining: int = 30,
    ) -> PlaybookRunResult:
        """Run only declared portion edges; human results re-enter through a cursor."""
        if portions_remaining <= 0:
            raise RuntimeError(
                f"Hybrid step '{current_step}' exceeded its portion transition limit"
            )
        declaration = step_def.get("hybrid")
        if not isinstance(declaration, dict) or not isinstance(declaration.get("portions"), list):
            raise RuntimeError(f"Step '{current_step}' has no valid hybrid declaration")
        portions = {
            item.get("id"): item
            for item in declaration["portions"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        cursor = self.blackboard.ownership_cursor
        if not isinstance(cursor, dict) or cursor.get("step") != current_step:
            cursor = {
                "step": current_step,
                "portion": declaration.get("entry_portion"),
                "human_boundary_crossed": False,
                "visit_count": visit_count,
            }
            self.blackboard.ownership_cursor = cursor
            self.blackboard_store.save(self.blackboard)
        elif not isinstance(cursor.get("visit_count"), int):
            cursor["visit_count"] = visit_count
            self.blackboard.ownership_cursor = cursor
            self.blackboard_store.save(self.blackboard)
        portion_id = cursor.get("portion")
        portion = portions.get(portion_id)
        if not isinstance(portion, dict):
            raise RuntimeError(f"Hybrid step '{current_step}' cursor names an unknown portion")

        if portion.get("owner") == "human":
            task_id = cursor.get("task_id")
            if isinstance(task_id, str):
                records = HumanTaskRecordStore(self.issue_dir)
                try:
                    task = records.get_task(task_id)
                    result = records.get_result(task_id)
                except Exception:
                    task = None
                    result = None
                if task is not None and result is not None:
                    key = result.payload.get("decision") or result.payload.get("target")
                    target = (
                        self._hybrid_target(portion, str(key)) if isinstance(key, str) else None
                    )
                    if target is None:
                        self.blackboard_store.record_event(
                            self.blackboard,
                            "hybrid_portion_rejected",
                            {
                                "step": current_step,
                                "portion": portion_id,
                                "reason": "undeclared human result",
                            },
                        )
                        return PlaybookRunResult(
                            final_step=current_step,
                            final_status_code="HYBRID_RESULT_REJECTED",
                            completed=False,
                        )
                    cursor.pop("task_id", None)
                    cursor["human_boundary_crossed"] = True
                    if "portion" in target:
                        cursor["portion"] = target["portion"]
                        self.blackboard.ownership_cursor = cursor
                        self.blackboard_store.save(self.blackboard)
                        return self._run_hybrid_owned_step(
                            current_step=current_step,
                            step_def=step_def,
                            visit_count=visit_count,
                            runtime=runtime,
                            portions_remaining=portions_remaining - 1,
                        )
                    self.blackboard.ownership_cursor = None
                    self.blackboard_store.save(self.blackboard)
                    return self._complete_hybrid_step_target(
                        current_step=current_step,
                        target=target["step"],
                        runtime=runtime,
                        source="hybrid_human_result",
                    )
            return self._materialize_owned_human_task(
                current_step=current_step,
                trigger=str(portion_id),
                status_code="HYBRID_HUMAN_TASK_PENDING",
                runtime=runtime,
                cursor=cursor,
            )

        if portion.get("owner") != "agent":
            raise RuntimeError(
                f"Hybrid step '{current_step}' portion {portion_id!r} has invalid owner"
            )
        framed_step = dict(step_def)
        framed_step["hybrid_portion"] = {
            "id": portion_id,
            "instruction": portion.get("instruction", ""),
        }
        frame = self._execute_one_iteration(
            current_step=current_step,
            step_def=framed_step,
            runtime="hybrid_portion",
            hop_count=1,
            visit_count=visit_count,
            extra_prompt=(
                f"[HYBRID PORTION] Execute only declared portion {portion_id!r}. "
                "Return only a declared completion status; do not route the top-level workflow. "
                "If a baton is required, write it only to the supplied portion-local path with "
                f"from_step={current_step!r}, to_owner='agent', to_step={current_step!r}, "
                f"and source='hybrid_portion:{current_step}:{portion_id}'."
            ),
        )
        self._store_artifacts(frame.artifacts)
        completion_key = (
            self._normalize_hybrid_completion_key(frame.explicit_status_code)
            if frame.explicit_status_code is not None
            else None
        )
        captured_key: Optional[str] = None
        captured_invalid = False
        events = getattr(frame.execution_result, "events", [])
        if isinstance(events, list):
            captured = next(
                (
                    event.get("payload")
                    for event in events
                    if isinstance(event, dict) and event.get("type") == "hybrid_portion_baton"
                ),
                None,
            )
            if isinstance(captured, str):
                captured_key = self._captured_hybrid_completion_key(
                    captured=captured,
                    current_step=current_step,
                    portion_id=str(portion_id),
                )
                captured_invalid = captured_key is None
        if captured_invalid:
            self.blackboard_store.record_event(
                self.blackboard,
                "hybrid_portion_rejected",
                {
                    "step": current_step,
                    "portion": portion_id,
                    "reason": "invalid captured baton",
                },
            )
            return PlaybookRunResult(
                final_step=current_step,
                final_status_code="HYBRID_RESULT_REJECTED",
                completed=False,
            )
        if completion_key is not None and captured_key is not None:
            if completion_key != captured_key:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "hybrid_portion_rejected",
                    {
                        "step": current_step,
                        "portion": portion_id,
                        "reason": "conflicting baton and status",
                    },
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code="HYBRID_RESULT_REJECTED",
                    completed=False,
                )
        if completion_key is None:
            completion_key = captured_key
        if completion_key is None:
            parsed, _goto, _valid = self._parse_legacy_status(
                step_def=step_def, response=frame.response, explicit_status_code=None
            )
            completion_key = (
                self._normalize_hybrid_completion_key(parsed.value) if parsed is not None else None
            )
        target = self._hybrid_target(portion, completion_key or "")
        if target is None:
            self.blackboard_store.record_event(
                self.blackboard,
                "hybrid_portion_rejected",
                {"step": current_step, "portion": portion_id, "reason": "undeclared agent result"},
            )
            return PlaybookRunResult(
                final_step=current_step, final_status_code="HYBRID_RESULT_REJECTED", completed=False
            )
        if "portion" in target:
            cursor["portion"] = target["portion"]
            self.blackboard.ownership_cursor = cursor
            self.blackboard_store.save(self.blackboard)
            return self._run_hybrid_owned_step(
                current_step=current_step,
                step_def=step_def,
                visit_count=visit_count,
                runtime=runtime,
                portions_remaining=portions_remaining - 1,
            )
        if not cursor.get("human_boundary_crossed"):
            raise RuntimeError(
                f"Hybrid step '{current_step}' attempted a top-level exit before its human boundary"
            )
        self.blackboard.ownership_cursor = None
        self.blackboard_store.save(self.blackboard)
        return self._complete_hybrid_step_target(
            current_step=current_step,
            target=target["step"],
            runtime=runtime,
            source="hybrid_agent_result",
        )

    def _complete_hybrid_step_target(
        self, *, current_step: str, target: str, runtime: str, source: str
    ) -> PlaybookRunResult:
        if target in {"done", "_done"}:
            return self._emit_complete(
                current_step=current_step,
                status_code="HYBRID_COMPLETED",
                next_step=target,
                runtime=runtime,
                reason=source,
                update_contract=True,
                contract_source="workflow.hybrid_transition",
            )
        if target not in self.steps:
            raise RuntimeError(f"Hybrid target {target!r} is not a declared step")
        self._emit_transition(
            current_step=current_step,
            next_step=target,
            status_code="HYBRID_COMPLETED",
            source=source,
            runtime=runtime,
            update_contract=True,
            contract_source="workflow.hybrid_transition",
        )
        return PlaybookRunResult(
            final_step=current_step, final_status_code="HYBRID_COMPLETED", completed=False
        )

    def _run_non_agent_owner(
        self, *, current_step: str, step_def: Dict, runtime: str
    ) -> Optional[PlaybookRunResult]:
        owner = self._owner_for_step(step_def)
        if owner == "agent":
            return None
        automatic_result: AutomaticExecutionResult | None = None
        if owner == "auto":
            self._ensure_step_visit_within_limit(
                current_step=current_step,
                step_def=step_def,
                runtime=runtime,
            )
            prepared = self._prepare_auto_owned_step(
                current_step=current_step,
                step_def=step_def,
            )
            if isinstance(prepared, PlaybookRunResult):
                return prepared
            automatic_result = prepared
        cursor = self.blackboard.ownership_cursor
        if (
            owner == "hybrid"
            and isinstance(cursor, dict)
            and cursor.get("step") == current_step
            and isinstance(cursor.get("visit_count"), int)
        ):
            visit_count = cursor["visit_count"]
        else:
            visit_count = self._record_step_visit(
                current_step=current_step, step_def=step_def, runtime=runtime
            )
        if owner == "human":
            return self._run_human_owned_step(
                current_step=current_step,
                step_def=step_def,
                visit_count=visit_count,
                runtime=runtime,
            )
        if owner == "auto":
            assert automatic_result is not None
            return self._run_auto_owned_step(
                current_step=current_step,
                step_def=step_def,
                visit_count=visit_count,
                runtime=runtime,
                result=automatic_result,
            )
        return self._run_hybrid_owned_step(
            current_step=current_step, step_def=step_def, visit_count=visit_count, runtime=runtime
        )

    def _execute_one_iteration(
        self,
        *,
        current_step: str,
        step_def: Dict,
        runtime: str,
        hop_count: int,
        visit_count: int,
        validate_assignee_type: bool = False,
        extra_prompt: Optional[str] = None,
        same_invocation_retry: bool = False,
    ) -> StepIterationFrame:
        self.blackboard_store.record_event(
            self.blackboard,
            "step_started",
            {
                "step": current_step,
                "visit": visit_count,
                "hop": hop_count,
                "runtime": runtime,
            },
        )
        feedback_ledger = WorkflowFeedbackLedger(self.issue_dir)
        pending_feedback = feedback_ledger.pending(target_step=current_step)

        try:
            try:
                execution_result = self.executor(
                    current_step,
                    step_def,
                    self.blackboard,
                    extra_prompt=extra_prompt,
                    same_invocation_retry=same_invocation_retry,
                )
            except TypeError:
                try:
                    execution_result = self.executor(
                        current_step,
                        step_def,
                        self.blackboard,
                        extra_prompt=extra_prompt,
                    )
                except TypeError:
                    execution_result = self.executor(current_step, step_def, self.blackboard)
            delivered_feedback = []
            if getattr(execution_result, "agent_invoked", False):
                delivered_feedback = feedback_ledger.consume_delivered(
                    entry.source_identity for entry in pending_feedback
                )
            if delivered_feedback:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_feedback_delivered",
                    {
                        "step": current_step,
                        "source_identities": [
                            entry.source_identity for entry in delivered_feedback
                        ],
                    },
                )
        except KeyboardInterrupt:
            self.blackboard_store.record_event(
                self.blackboard,
                "step_interrupted",
                {
                    "step": current_step,
                    "visit": visit_count,
                    "hop": hop_count,
                    "runtime": runtime,
                    "reason": "keyboard_interrupt",
                },
            )
            raise StepInterrupted(step=current_step, hop=hop_count, reason="interrupted")
        except BaseException as exc:
            # Catch AgentExecutionError (rate_limit, cli_not_found),
            # CriticalPhaseError (the same critical error_types re-raised by
            # the phase layer after exhausting recovery), and any other
            # executor failure so the workflow records a clean interrupted
            # state instead of crashing.
            from cafe.agents.executor import AgentExecutionError
            from cafe.core.types import CriticalPhaseError

            reason = "agent_error"
            detail = str(exc)
            if isinstance(exc, (AgentExecutionError, CriticalPhaseError)) and getattr(
                exc, "error_type", None
            ):
                reason = f"agent_{exc.error_type}"
                detail = getattr(exc, "display_message", None) or str(exc)
            elif detail.startswith("PR sync script failed:"):
                reason = "publish_error"
            elif detail.startswith("PR sync timed out"):
                reason = "publish_error"
            print(f"⚠️  Step execution failed ({reason}): {detail}")
            self.blackboard_store.record_event(
                self.blackboard,
                "step_interrupted",
                {
                    "step": current_step,
                    "visit": visit_count,
                    "hop": hop_count,
                    "runtime": runtime,
                    "reason": reason,
                    "detail": detail,
                },
            )
            raise StepInterrupted(step=current_step, hop=hop_count, reason=reason, detail=detail)
        if validate_assignee_type:
            self._validate_assignee_type(current_step, step_def)

        response, artifacts, explicit_status_code, auto_continue = self._normalize_execution_result(
            execution_result
        )
        return StepIterationFrame(
            execution_result=execution_result,
            response=response,
            artifacts=artifacts,
            explicit_status_code=explicit_status_code,
            auto_continue=auto_continue,
        )

    def _store_artifacts(self, artifacts: Dict[str, str]) -> None:
        for key, value in artifacts.items():
            self.blackboard_store.set_artifact(self.blackboard, key, value)

    def _record_step_completion(
        self,
        *,
        event_type: str,
        current_step: str,
        status_code: str,
        runtime: str,
        visit_count: Optional[int] = None,
        hop_count: Optional[int] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "step": current_step,
            "status_code": status_code,
            "runtime": runtime,
        }
        if visit_count is not None:
            payload["visit"] = visit_count
        if hop_count is not None:
            payload["hop"] = hop_count
        self.blackboard_store.record_event(
            self.blackboard,
            event_type,
            payload,
        )

    def _emit_pause(
        self,
        *,
        current_step: str,
        status_code: str,
        runtime: str,
        reason: str,
        pause_intent: Optional[HandoffIntent] = None,
        update_contract: bool = True,
        contract_source: str = "workflow.pause",
        record_event: bool = True,
    ) -> PlaybookRunResult:
        replaced_handoff = self._replaced_user_handoff
        if (
            replaced_handoff is None
            and update_contract
            and self.blackboard.current_step == "user"
            and self.blackboard.handoff_contract is not None
            and self.blackboard.handoff_contract.to_owner is HandoffOwner.USER
            and self.blackboard.handoff_contract.to_step == "user"
        ):
            replaced_handoff = self.blackboard.handoff_contract
        if update_contract:
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.USER,
                to_step="user",
                intent=pause_intent or HandoffIntent.MANUAL_HANDOFF,
                status_code=status_code,
                source=contract_source,
            )
        self._materialize_user_handoff_task(
            current_step=current_step,
            replaced_handoff=replaced_handoff,
        )
        self._replaced_user_handoff = None
        if record_event:
            self.blackboard_store.record_event(
                self.blackboard,
                "workflow_paused",
                {
                    "step": current_step,
                    "status_code": status_code,
                    "reason": reason,
                    "runtime": runtime,
                },
            )
        self.blackboard_store.set_current_step(self.blackboard, "user")
        return PlaybookRunResult(
            final_step=current_step,
            final_status_code=status_code,
            completed=False,
        )

    def _materialize_user_handoff_task(
        self,
        *,
        current_step: str,
        replaced_handoff: HandoffContract | None = None,
    ) -> None:
        """Create or recover a declared task before exposing the user pause."""
        step_def = self.steps.get(current_step)
        if not isinstance(step_def, dict):
            return
        raw_bindings = step_def.get("human_tasks")
        if not isinstance(raw_bindings, (list, tuple)):
            return
        contract = self.blackboard.handoff_contract
        if (
            contract is None
            or contract.to_owner is not HandoffOwner.USER
            or contract.to_step != "user"
            or contract.from_step != current_step
        ):
            return
        trigger = contract.intent.value
        iteration = self._human_task_iteration(current_step)
        records = HumanTaskRecordStore(self.issue_dir)
        handoff_key = self._human_task_handoff_key(contract)
        try:
            policy, binding = resolve_step_human_task(
                playbook_data=self.playbook,
                step_name=current_step,
                trigger=trigger,
                iteration=iteration,
            )
        except (LookupError, TypeError, ValueError) as exc:
            records.record_configuration_error(
                workflow_id=self.blackboard.workflow_id,
                step=current_step,
                iteration=iteration,
                trigger=trigger,
                reason=str(exc),
            )
            self.blackboard_store.record_event(
                self.blackboard,
                "human_task_configuration_error",
                {"step": current_step, "trigger": trigger, "reason": str(exc)},
            )
            return

        if replaced_handoff is None:
            existing_task = next(
                (
                    task
                    for task in records.tasks()
                    if task.workflow_id == self.blackboard.workflow_id
                    and task.status is HumanTaskStatus.PENDING
                    and task.step == current_step
                    and task.iteration == iteration
                    and task.trigger == trigger
                    and task.policy_id == policy.id
                ),
                None,
            )
            if existing_task is not None:
                handoff_key = existing_task.handoff_key

        materialization = records.materialize_with_status(
            workflow_id=self.blackboard.workflow_id,
            step=current_step,
            iteration=iteration,
            trigger=trigger,
            policy_id=policy.id,
            prompt=policy.prompt,
            expected_result=policy.model_dump(mode="json"),
            continuations=binding.outcomes,
            assignee_type="user",
            handoff_key=handoff_key,
            superseded_task_ids=self._superseded_human_task_ids(
                records,
                step=current_step,
                iteration=iteration,
                trigger=trigger,
                policy_id=policy.id,
                replaced_handoff=replaced_handoff,
            ),
        )
        task = materialization.task
        self._notify_new_human_task(task)
        if materialization.created:
            self.blackboard_store.record_event(
                self.blackboard,
                "human_task_materialized",
                {"step": current_step, "trigger": trigger, "task_id": task.id},
            )

    def _human_task_iteration(self, current_step: str) -> int:
        iteration_dir = self._latest_iteration_dir(current_step)
        if iteration_dir is None:
            return 1
        try:
            return int(iteration_dir.name.removeprefix("iteration_"))
        except ValueError:
            return 1

    def _superseded_human_task_ids(
        self,
        records: HumanTaskRecordStore,
        *,
        step: str,
        iteration: int,
        trigger: str,
        policy_id: str,
        replaced_handoff: HandoffContract | None = None,
    ) -> tuple[str, ...]:
        """Identify only named pending tasks replaced by this handoff.

        A different handoff key or another pending task is never enough to
        trigger cancellation. A newer iteration replaces its matching lineage;
        an in-iteration replacement names its predecessor through the prior
        durable user handoff identity.
        """
        superseded_task_ids = [
            task.id
            for task in records.tasks()
            if task.workflow_id == self.blackboard.workflow_id
            and task.status is HumanTaskStatus.PENDING
            and task.step == step
            and task.trigger == trigger
            and task.policy_id == policy_id
            and task.iteration < iteration
        ]
        if replaced_handoff is not None:
            replaced_handoff_key = self._human_task_handoff_key(replaced_handoff)
            superseded_task_ids.extend(
                task.id
                for task in records.tasks()
                if task.workflow_id == self.blackboard.workflow_id
                and task.status is HumanTaskStatus.PENDING
                and (
                    task.handoff_key == replaced_handoff_key
                    or (
                        task.step == replaced_handoff.from_step
                        and task.handoff_key
                        == "\x1f".join(
                            (
                                task.workflow_id,
                                task.step,
                                str(task.iteration),
                                task.trigger,
                                task.policy_id,
                            )
                        )
                    )
                )
            )
        return tuple(dict.fromkeys(superseded_task_ids))

    def _human_task_handoff_key(self, contract: HandoffContract) -> str:
        """Return the stable identity of one user-facing handoff instance."""
        return ":".join(
            (
                "user-handoff",
                self.blackboard.workflow_id,
                contract.from_step,
                contract.intent.value,
                contract.created_at,
            )
        )

    def _remember_replaced_user_handoff(self) -> None:
        """Retain the exact pending task identity across a start-step replacement."""
        contract = self.blackboard.handoff_contract
        if (
            self.blackboard.current_step == "user"
            and contract is not None
            and contract.to_owner is HandoffOwner.USER
            and contract.to_step == "user"
        ):
            self._replaced_user_handoff = contract

    def _emit_complete(
        self,
        *,
        current_step: str,
        status_code: str,
        next_step: str,
        runtime: str,
        reason: str,
        update_contract: bool = False,
        contract_source: str = "workflow.transition",
    ) -> PlaybookRunResult:
        self.blackboard_store.record_event(
            self.blackboard,
            "workflow_completed",
            {
                "step": current_step,
                "status_code": status_code,
                "next_step": next_step,
                "reason": reason,
                "runtime": runtime,
            },
        )
        self.blackboard_store.set_current_step(self.blackboard, "done")
        if update_contract:
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.DONE,
                to_step="done",
                intent=HandoffIntent.WORKFLOW_COMPLETE,
                status_code=status_code,
                source=contract_source,
            )
        cafe_dir = self.issue_dir.parent.parent
        clear_marker_if_matches(cafe_dir, self.issue_dir.name)
        return PlaybookRunResult(
            final_step=current_step,
            final_status_code=status_code,
            completed=True,
        )

    def _emit_transition(
        self,
        *,
        current_step: str,
        next_step: str,
        status_code: str,
        source: str,
        runtime: str,
        update_contract: bool = True,
        contract_source: str = "workflow.transition",
    ) -> None:
        self.blackboard_store.record_decision(
            self.blackboard,
            {"from": current_step, "to": next_step, "status_code": status_code},
        )
        self.blackboard_store.record_event(
            self.blackboard,
            "transition",
            {
                "from": current_step,
                "to": next_step,
                "status_code": status_code,
                "source": source,
                "runtime": runtime,
            },
        )
        self.blackboard_store.set_current_step(self.blackboard, next_step)
        if update_contract:
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=next_step,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code=status_code,
                source=contract_source,
            )

    def _handle_post_contract(
        self,
        *,
        current_step: str,
        status_code: str,
        runtime: str,
        update_contract_on_transition: bool,
    ) -> Optional[PostContractResult]:
        post_contract = self._load_step_handoff_contract(current_step=current_step)
        if post_contract is None:
            return None
        if post_contract.to_owner == HandoffOwner.AGENT and post_contract.to_step == current_step:
            return None

        resolved_status_code = (
            post_contract.status_code or f"BATON_{post_contract.intent.value.upper()}"
        )
        if post_contract.to_owner == HandoffOwner.USER:
            result = self._emit_pause(
                current_step=current_step,
                status_code=resolved_status_code,
                runtime=runtime,
                reason="external_handoff",
                update_contract=False,
            )
            return PostContractResult(status_code=resolved_status_code, terminal_result=result)

        if post_contract.to_owner == HandoffOwner.DONE:
            result = self._emit_complete(
                current_step=current_step,
                status_code=resolved_status_code,
                next_step=post_contract.to_step,
                runtime=runtime,
                reason="external_handoff",
                update_contract=False,
            )
            return PostContractResult(status_code=resolved_status_code, terminal_result=result)

        next_step = post_contract.to_step
        if next_step not in self.steps:
            raise RuntimeError(f"Unknown baton target '{next_step}' from step '{current_step}'")
        self._emit_transition(
            current_step=current_step,
            next_step=next_step,
            status_code=resolved_status_code,
            source="baton",
            runtime=runtime,
            update_contract=update_contract_on_transition,
        )
        return PostContractResult(status_code=resolved_status_code, next_step=next_step)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat()

    def _latest_iteration_dir(self, current_step: str) -> Optional[Path]:
        step_dir = self.issue_dir / current_step
        if not step_dir.exists():
            return None
        iteration_dirs = sorted(path for path in step_dir.glob("iteration_*") if path.is_dir())
        return iteration_dirs[-1] if iteration_dirs else None

    @staticmethod
    def _questions_required_for_reconciliation(contract: HandoffContract, status_code: str) -> bool:
        return (
            contract.intent == HandoffIntent.NEED_CLARIFICATION
            or contract.status_code == PhaseStatusCode.NEED_CLARIFICATION.value
            or status_code == PhaseStatusCode.NEED_CLARIFICATION.value
        )

    def _capability_receipt_recorded(self, capability_id: str) -> bool:
        for receipt in getattr(self.blackboard, "capability_receipts", []):
            if (
                isinstance(receipt, dict)
                and receipt.get("capability") == capability_id
                and receipt.get("success") is True
            ):
                return True
        for event in getattr(self.blackboard, "events", []):
            data = getattr(event, "data", {})
            if (
                capability_id == CAPABILITY_PR_PUBLISH_ID
                and getattr(event, "event_type", "") == "pr_synced"
            ):
                return True
            if (
                getattr(event, "event_type", "") == "capability_receipt"
                and isinstance(data, dict)
                and data.get("capability") == capability_id
                and data.get("success") is True
            ):
                return True
        return False

    def _missing_capability_receipts(
        self,
        *,
        current_step: str,
        execution_result: Any,
    ) -> list[str]:
        missing: list[str] = []
        for capability_id in self._required_capability_ids(current_step):
            if self._capability_receipt_satisfied(execution_result, capability_id):
                continue
            if self._capability_receipt_recorded(capability_id):
                continue
            missing.append(capability_id)
        return missing

    def _validate_reconciled_handoff(
        self,
        *,
        current_step: str,
        iteration_dir_override: Optional[Path] = None,
    ) -> HandoffReconciliationResult:
        missing: list[str] = []
        validated: list[str] = []
        contract: Optional[HandoffContract] = None

        try:
            contract = self.blackboard_store.load_handoff_contract(
                self.blackboard,
                allowed_steps=list(self.steps.keys()),
            )
        except Exception:
            missing.append("baton_valid")

        status_code = ""
        if contract is not None:
            status_code = contract.status_code or f"BATON_{contract.intent.value.upper()}"
            if contract.from_step != current_step:
                missing.append("baton_from_step")

            downstream = False
            if contract.to_owner == HandoffOwner.AGENT:
                downstream = contract.to_step in self.steps and contract.to_step != current_step
            elif contract.to_owner == HandoffOwner.USER:
                downstream = contract.to_step == "user"
            elif contract.to_owner == HandoffOwner.DONE:
                downstream = contract.to_step == "done"

            if not downstream:
                missing.append("baton_downstream")
            else:
                validated.append("baton")

            if contract.to_owner in {HandoffOwner.AGENT, HandoffOwner.DONE}:
                for capability_id in self._required_capability_ids(current_step):
                    if not self._capability_receipt_recorded(capability_id):
                        missing.append(f"capability_receipt:{capability_id}")

        iteration_dir = iteration_dir_override or self._latest_iteration_dir(current_step)
        if iteration_dir is None:
            missing.append("iteration_dir")
        else:
            output_path = iteration_dir / "output.md"
            if not output_path.exists() or not output_path.read_text(encoding="utf-8").strip():
                missing.append("output_non_empty")
            else:
                validated.append("output")

            checklist_path = iteration_dir / "checklist.md"
            try:
                checklist_result = validate_checklist(checklist_path)
                if checklist_result.is_complete:
                    validated.append("checklist")
                else:
                    missing.append("checklist_complete")
            except FileNotFoundError:
                missing.append("checklist_complete")

            if contract is not None and self._questions_required_for_reconciliation(
                contract, status_code
            ):
                questions_path = iteration_dir / "questions.xml"
                if validate_questions_xml(questions_path):
                    validated.append("questions")
                else:
                    missing.append("questions_valid")

        return HandoffReconciliationResult(
            reconciled=not missing,
            status_code=status_code,
            contract=contract,
            iteration_dir=iteration_dir,
            missing_evidence=missing,
            validated_evidence=validated,
        )

    def _reconciliation_event_exists(
        self,
        *,
        current_step: str,
        status_code: str,
        contract: HandoffContract,
    ) -> bool:
        for event in self.blackboard.events:
            if event.event_type != "step_reconciled":
                continue
            data = event.data
            if (
                data.get("step") == current_step
                and data.get("status_code") == status_code
                and data.get("to_owner") == contract.to_owner.value
                and data.get("to_step") == contract.to_step
            ):
                return True
        return False

    def _record_reconciliation_failed(
        self,
        *,
        current_step: str,
        runtime: str,
        missing_evidence: list[str],
    ) -> None:
        self.blackboard_store.record_event(
            self.blackboard,
            "step_reconciliation_failed",
            {
                "step": current_step,
                "runtime": runtime,
                "reason": "incomplete_handoff",
                "missing_evidence": list(missing_evidence),
            },
        )

    def _patch_reconciled_iteration_metadata(self, result: HandoffReconciliationResult) -> None:
        if result.iteration_dir is None:
            return
        iteration_file = result.iteration_dir / "iteration.json"
        if not iteration_file.exists():
            return
        try:
            data = json.loads(iteration_file.read_text(encoding="utf-8"))
        except Exception:
            return
        changed = False
        if not data.get("status_code"):
            data["status_code"] = result.status_code
            changed = True
        if not data.get("end_time"):
            data["end_time"] = self._now_iso()
            changed = True
        if changed:
            iteration_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def _apply_reconciled_handoff(
        self,
        *,
        current_step: str,
        runtime: str,
        result: HandoffReconciliationResult,
    ) -> PlaybookRunResult:
        if result.contract is None:
            raise RuntimeError("Cannot apply reconciliation without a valid handoff contract")

        contract = result.contract
        status_code = result.status_code
        self._patch_reconciled_iteration_metadata(result)

        if not self._reconciliation_event_exists(
            current_step=current_step,
            status_code=status_code,
            contract=contract,
        ):
            self.blackboard_store.record_event(
                self.blackboard,
                "step_reconciled",
                {
                    "step": current_step,
                    "status_code": status_code,
                    "to_owner": contract.to_owner.value,
                    "to_step": contract.to_step,
                    "runtime": runtime,
                    "reason": "post_interrupt_completed_handoff",
                    "validated_evidence": list(result.validated_evidence),
                },
            )

        if contract.to_owner == HandoffOwner.USER:
            return self._emit_pause(
                current_step=current_step,
                status_code=status_code,
                runtime=runtime,
                reason="reconciled_handoff",
                update_contract=False,
            )

        if contract.to_owner == HandoffOwner.DONE:
            return self._emit_complete(
                current_step=current_step,
                status_code=status_code,
                next_step=contract.to_step,
                runtime=runtime,
                reason="reconciled_handoff",
                update_contract=False,
            )

        self._emit_transition(
            current_step=current_step,
            next_step=contract.to_step,
            status_code=status_code,
            source="reconciled_baton",
            runtime=runtime,
            update_contract=False,
        )
        return PlaybookRunResult(
            final_step=current_step,
            final_status_code=status_code,
            completed=False,
        )

    def _try_reconcile_interrupted_step(
        self,
        *,
        current_step: str,
        runtime: str,
        reason: str,
    ) -> Optional[PlaybookRunResult]:
        if reason in {"interrupted", "keyboard_interrupt", "publish_error"}:
            return None

        result = self._validate_reconciled_handoff(current_step=current_step)

        if not result.reconciled:
            self._record_reconciliation_failed(
                current_step=current_step,
                runtime=runtime,
                missing_evidence=result.missing_evidence,
            )
            return None

        return self._apply_reconciled_handoff(
            current_step=current_step,
            runtime=runtime,
            result=result,
        )

    def _latest_unreconciled_interrupted_step(self) -> Optional[tuple[str, str]]:
        for event in reversed(self.blackboard.events):
            if event.event_type == "step_reconciled":
                return None
            if event.event_type != "step_interrupted":
                continue
            step = str(event.data.get("step", event.step))
            reason = str(event.data.get("reason", "interrupted"))
            if reason in {"interrupted", "keyboard_interrupt", "publish_error"}:
                return None
            return step, reason
        return None

    def _try_resume_reconcile_interrupted_handoff(
        self,
        *,
        runtime_label: str,
    ) -> Optional[PlaybookRunResult]:
        interrupted = self._latest_unreconciled_interrupted_step()
        if interrupted is None:
            return None
        step, reason = interrupted
        return self._try_reconcile_interrupted_step(
            current_step=step,
            runtime=runtime_label,
            reason=reason,
        )

    def _should_attempt_resume_reconciliation(self, *, start_step: Optional[str]) -> bool:
        if start_step is None:
            return True
        try:
            contract = self.blackboard_store.load_handoff_contract(
                self.blackboard,
                allowed_steps=list(self.steps.keys()),
            )
        except Exception:
            return False
        return contract.source == "workflow.consume_handoff" and contract.to_step == start_step

    def _run_baton_driven_pr(
        self,
        *,
        current_step: str,
        max_transitions: int,
        runtime_label: str = "blackboard",
        completion_event_type: str = "step_completed",
        transition_runtime_label: Optional[str] = None,
        transition_contract_source: str = "workflow.transition",
        single_step_mode: bool = False,
    ) -> PlaybookRunResult:
        last_status_code = ""
        effective_transition_runtime = transition_runtime_label or runtime_label

        for hop_count in range(1, max_transitions + 1):
            step_def = self.steps[current_step]
            owned_result = self._run_non_agent_owner(
                current_step=current_step, step_def=step_def, runtime=runtime_label
            )
            if owned_result is not None:
                return owned_result
            _baton_retry_extra_prompt: Optional[str] = None
            try:
                self._validate_agent_baton(current_step=current_step)
            except BatonRejected as br:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "baton_rejected",
                    {
                        "step": current_step,
                        "field": br.field,
                        "invalid_value": br.invalid_value,
                        "valid_values": br.valid_values,
                        "retry": 0,
                        "runtime": runtime_label,
                        "phase": "pre_step_validation",
                    },
                )
                _baton_retry_extra_prompt = self._baton_rejected_prompt(br)
            visit_count = self._record_step_visit(
                current_step=current_step, step_def=step_def, runtime=runtime_label
            )
            for _baton_attempt in range(3):
                try:
                    frame = self._execute_one_iteration(
                        current_step=current_step,
                        step_def=step_def,
                        runtime=runtime_label,
                        hop_count=hop_count,
                        visit_count=visit_count,
                        extra_prompt=_baton_retry_extra_prompt,
                        same_invocation_retry=_baton_attempt > 0,
                    )
                except StepInterrupted as si:
                    reconciled = self._try_reconcile_interrupted_step(
                        current_step=current_step,
                        runtime=runtime_label,
                        reason=si.reason,
                    )
                    if reconciled is not None:
                        return reconciled
                    self._rollback_step_visit(
                        current_step=current_step,
                        visit_count=visit_count,
                    )
                    self._restore_interrupted_step_handoff(
                        current_step=current_step,
                        reason=si.reason,
                    )
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "workflow_paused",
                        {
                            "step": current_step,
                            "status_code": "INTERRUPTED",
                            "reason": si.reason,
                            "runtime": runtime_label,
                        },
                    )
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code=f"INTERRUPTED:{si.reason}",
                        completed=False,
                        detail=si.detail,
                    )
                self._store_artifacts(frame.artifacts)
                try:
                    contract = self._load_agent_written_handoff_contract(current_step=current_step)
                    if contract.to_owner == HandoffOwner.AGENT and contract.to_step == current_step:
                        explicit_status_code = getattr(frame.execution_result, "status_code", None)
                        if not explicit_status_code:
                            raise self._same_step_baton_rejected(current_step=current_step)
                        status_code = str(explicit_status_code)
                    else:
                        status_code = (
                            contract.status_code or f"BATON_{contract.intent.value.upper()}"
                        )
                    break
                except BatonRejected as br:
                    retry_num = _baton_attempt + 1
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "baton_rejected",
                        {
                            "step": current_step,
                            "field": br.field,
                            "invalid_value": br.invalid_value,
                            "valid_values": br.valid_values,
                            "retry": retry_num,
                            "runtime": runtime_label,
                        },
                    )
                    if retry_num >= 3:
                        raise RuntimeError(
                            f"Step '{current_step}' wrote invalid baton 3 times; "
                            f"last error: field '{br.field}' got '{br.invalid_value}', "
                            f"valid values are {br.valid_values}"
                        ) from br
                    _baton_retry_extra_prompt = self._baton_rejected_prompt(br)
            else:
                raise RuntimeError(f"Step '{current_step}' did not produce a valid baton")
            last_status_code = status_code
            self._record_step_completion(
                event_type=completion_event_type,
                current_step=current_step,
                status_code=status_code,
                runtime=runtime_label,
                visit_count=visit_count,
                hop_count=hop_count,
            )
            next_step = contract.to_step

            if contract.to_owner == HandoffOwner.AGENT and next_step == current_step:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "baton_missing_transition",
                    {
                        "step": current_step,
                        "status_code": status_code,
                    },
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code="NO_BATON_TRANSITION",
                    completed=False,
                )

            require_capability_receipts = next_step != "user"
            if (
                resolve_step_behavior(self.playbook, current_step).publish_confirmation
                and next_step != "done"
            ):
                require_capability_receipts = False

            if require_capability_receipts:
                missing_capabilities = self._missing_capability_receipts(
                    current_step=current_step,
                    execution_result=frame.execution_result,
                )
                if missing_capabilities:
                    pending_approval = self._pending_capability_approval(frame.execution_result)
                    if pending_approval is not None:
                        self.blackboard_store.update_handoff_contract(
                            self.blackboard,
                            from_step=current_step,
                            to_owner=HandoffOwner.USER,
                            to_step="user",
                            intent=HandoffIntent.MANUAL_HANDOFF,
                            status_code="CAPABILITY_APPROVAL_PENDING",
                            source="workflow.capability_approval",
                        )
                        self.blackboard_store.record_event(
                            self.blackboard,
                            "capability_approval_requested",
                            {
                                "step": current_step,
                                "capability": pending_approval.get("capability"),
                                "task_id": pending_approval["task_id"],
                                "request_fingerprint": pending_approval.get("request_fingerprint"),
                            },
                        )
                        self.blackboard_store.set_current_step(self.blackboard, "user")
                        return PlaybookRunResult(
                            final_step=current_step,
                            final_status_code="CAPABILITY_APPROVAL_PENDING",
                            completed=False,
                            detail=str(pending_approval["task_id"]),
                        )
                    self.blackboard_store.update_handoff_contract(
                        self.blackboard,
                        from_step=current_step,
                        to_owner=HandoffOwner.AGENT,
                        to_step=current_step,
                        intent=HandoffIntent.AWAIT_AGENT,
                        status_code=status_code,
                        source="workflow.capability_receipt_required",
                    )
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "workflow_blocked",
                        {
                            "step": current_step,
                            "status_code": status_code,
                            "reason": "missing_capability_receipt",
                            "required_event": (
                                "pr_synced"
                                if missing_capabilities == [CAPABILITY_PR_PUBLISH_ID]
                                else "capability_receipt:" + ",".join(missing_capabilities)
                            ),
                            "missing_capabilities": missing_capabilities,
                        },
                    )
                    self.blackboard_store.set_current_step(self.blackboard, current_step)
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code="MISSING_CAPABILITY_RECEIPT",
                        completed=False,
                    )

            if next_step == "done":
                return self._emit_complete(
                    current_step=current_step,
                    status_code=status_code,
                    next_step=next_step,
                    runtime=runtime_label,
                    reason="external_handoff",
                    update_contract=False,
                )

            if next_step == "user":
                return self._emit_pause(
                    current_step=current_step,
                    status_code=status_code,
                    runtime=runtime_label,
                    reason="awaiting_user_input",
                    update_contract=False,
                )

            if next_step not in self.steps:
                raise RuntimeError(f"Unknown baton target '{next_step}' from step '{current_step}'")

            self._emit_transition(
                current_step=current_step,
                next_step=next_step,
                status_code=status_code,
                source="baton",
                runtime=effective_transition_runtime,
                update_contract=True,
                contract_source=transition_contract_source,
            )
            if single_step_mode:
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )
            current_step = next_step

        self.blackboard_store.record_event(
            self.blackboard,
            "hop_limit_reached",
            {
                "step": current_step,
                "max_transitions": max_transitions,
                "last_status_code": last_status_code,
            },
        )
        raise RuntimeError(f"Playbook run reached max transition limit ({max_transitions})")

    def _run_legacy_until_boundary(
        self,
        *,
        current_step: str,
        max_transitions: int,
        runtime_label: str = "legacy_until_boundary",
        completion_event_type: str = "step_completed",
        boundary_transition_runtime: str = "boundary_handoff",
        transition_contract_source: str = "workflow.transition",
        single_step_mode: bool = False,
        pause_record_event: bool = True,
    ) -> PlaybookRunResult:
        last_status_code = ""
        for hop_count in range(1, max_transitions + 1):
            step_def = self.steps[current_step]
            owned_result = self._run_non_agent_owner(
                current_step=current_step, step_def=step_def, runtime=runtime_label
            )
            if owned_result is not None:
                return owned_result
            if self._is_baton_driven_step(current_step):
                return self._run_baton_driven_pr(
                    current_step=current_step, max_transitions=max_transitions - hop_count + 1
                )

            _baton_retry_extra_prompt: Optional[str] = None
            try:
                self._validate_agent_baton(current_step=current_step)
            except BatonRejected as br:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "baton_rejected",
                    {
                        "step": current_step,
                        "field": br.field,
                        "invalid_value": br.invalid_value,
                        "valid_values": br.valid_values,
                        "retry": 0,
                        "runtime": runtime_label,
                        "phase": "pre_step_validation",
                    },
                )
                _baton_retry_extra_prompt = self._baton_rejected_prompt(br)
            visit_count = self._record_step_visit(
                current_step=current_step, step_def=step_def, runtime=runtime_label
            )

            for _baton_attempt in range(3):
                try:
                    frame = self._execute_one_iteration(
                        current_step=current_step,
                        step_def=step_def,
                        runtime=runtime_label,
                        hop_count=hop_count,
                        visit_count=visit_count,
                        validate_assignee_type=True,
                        extra_prompt=_baton_retry_extra_prompt,
                        same_invocation_retry=_baton_attempt > 0,
                    )
                except StepInterrupted as si:
                    reconciled = self._try_reconcile_interrupted_step(
                        current_step=current_step,
                        runtime=runtime_label,
                        reason=si.reason,
                    )
                    if reconciled is not None:
                        return reconciled
                    self._rollback_step_visit(
                        current_step=current_step,
                        visit_count=visit_count,
                    )
                    self._restore_interrupted_step_handoff(
                        current_step=current_step,
                        reason=si.reason,
                    )
                    if pause_record_event:
                        self.blackboard_store.record_event(
                            self.blackboard,
                            "workflow_paused",
                            {
                                "step": current_step,
                                "status_code": "INTERRUPTED",
                                "reason": si.reason,
                                "runtime": runtime_label,
                            },
                        )
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code=f"INTERRUPTED:{si.reason}",
                        completed=False,
                        detail=si.detail,
                    )
                self._store_artifacts(frame.artifacts)
                try:
                    post_contract = self._load_step_handoff_contract(current_step=current_step)
                    if (
                        post_contract is not None
                        and post_contract.to_owner == HandoffOwner.AGENT
                        and post_contract.to_step == current_step
                        and post_contract.source
                        not in {"bootstrap", "workflow.start_step_override"}
                    ):
                        status_code_obj, goto_target, valid_codes = self._parse_legacy_status(
                            step_def=step_def,
                            response=frame.response,
                            explicit_status_code=frame.explicit_status_code,
                        )
                        allowed_status_codes = {code.value for code in valid_codes}
                        status_like_tokens = self._extract_status_like_tokens(
                            response=frame.response,
                            explicit_status_code=frame.explicit_status_code,
                        )
                        invalid_intents = {
                            token
                            for token in status_like_tokens
                            if token not in allowed_status_codes
                        }
                        has_default_transition = bool(step_def.get("on", {}).get("default"))
                        if (
                            status_code_obj is None
                            and not goto_target
                            and not invalid_intents
                            and not has_default_transition
                        ):
                            raise self._same_step_baton_rejected(current_step=current_step)
                    break
                except BatonRejected as br:
                    retry_num = _baton_attempt + 1
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "baton_rejected",
                        {
                            "step": current_step,
                            "field": br.field,
                            "invalid_value": br.invalid_value,
                            "valid_values": br.valid_values,
                            "retry": retry_num,
                            "runtime": runtime_label,
                        },
                    )
                    if retry_num >= 3:
                        raise RuntimeError(
                            f"Step '{current_step}' wrote invalid baton 3 times; "
                            f"last error: field '{br.field}' got '{br.invalid_value}', "
                            f"valid values are {br.valid_values}"
                        ) from br
                    _baton_retry_extra_prompt = self._baton_rejected_prompt(br)
            else:
                post_contract = None
            if post_contract is not None and not (
                post_contract.to_owner == HandoffOwner.AGENT
                and post_contract.to_step == current_step
            ):
                status_code = (
                    post_contract.status_code or f"BATON_{post_contract.intent.value.upper()}"
                )
                last_status_code = status_code
                self._record_step_completion(
                    event_type=completion_event_type,
                    current_step=current_step,
                    status_code=status_code,
                    runtime=runtime_label,
                    visit_count=visit_count,
                    hop_count=hop_count,
                )
                post_contract_result = self._handle_post_contract(
                    current_step=current_step,
                    status_code=status_code,
                    runtime=runtime_label,
                    update_contract_on_transition=False,
                )
                if post_contract_result is not None:
                    status_code = post_contract_result.status_code
                    last_status_code = status_code
                    if post_contract_result.terminal_result is not None:
                        return post_contract_result.terminal_result
                    if post_contract_result.next_step is not None:
                        if single_step_mode:
                            return PlaybookRunResult(
                                final_step=current_step,
                                final_status_code=status_code,
                                completed=False,
                            )
                        current_step = post_contract_result.next_step
                        continue

            status_code_obj, goto_target, valid_codes = self._parse_legacy_status(
                step_def=step_def,
                response=frame.response,
                explicit_status_code=frame.explicit_status_code,
            )
            allowed_status_codes = {code.value for code in valid_codes}
            if status_code_obj is None:
                handoff_next_step: Optional[str] = None
                handoff_transition_source = "terminal"
                if goto_target:
                    handoff_next_step, handoff_transition_source = self._resolve_next_step(
                        current_step=current_step,
                        response=f"{frame.response}\nGOTO:{goto_target}",
                        status_code="",
                    )
                if handoff_next_step is not None:
                    status_code = "NO_STATUS_CODE"
                    last_status_code = status_code
                else:
                    status_like_tokens = self._extract_status_like_tokens(
                        response=frame.response,
                        explicit_status_code=frame.explicit_status_code,
                    )
                    invalid_intents = {
                        token for token in status_like_tokens if token not in allowed_status_codes
                    }
                    if invalid_intents:
                        self.blackboard_store.record_event(
                            self.blackboard,
                            "status_code_invalid",
                            {
                                "step": current_step,
                                "invalid_intents": sorted(invalid_intents),
                                "allowed_status_codes": sorted(allowed_status_codes),
                                "response": frame.response,
                                "runtime": runtime_label,
                            },
                        )
                        return PlaybookRunResult(
                            final_step=current_step,
                            final_status_code="INVALID_STATUS_CODE",
                            completed=False,
                        )
                    else:
                        # Baton-first fallback: when the agent omits a
                        # status code, use the handoff contract on the
                        # blackboard (written by the step executor) to
                        # determine the next step.  This advances the
                        # engine deterministically via the baton model
                        # rather than falling back to status-code transitions.
                        baton_next = self._resolve_next_step_from_handoff(
                            current_step=current_step,
                        )
                        if baton_next is not None:
                            self.blackboard_store.record_event(
                                self.blackboard,
                                "status_code_missing",
                                {
                                    "step": current_step,
                                    "response": frame.response,
                                    "runtime": runtime_label,
                                    "baton_fallback": True,
                                    "fallback_next_step": baton_next,
                                },
                            )
                            handoff_next_step = baton_next
                            handoff_transition_source = "baton"
                            status_code = "NO_STATUS_CODE"
                            last_status_code = status_code
                        else:
                            self.blackboard_store.record_event(
                                self.blackboard,
                                "status_code_missing",
                                {
                                    "step": current_step,
                                    "response": frame.response,
                                    "runtime": runtime_label,
                                },
                            )
                            # The agent returned with neither a status code
                            # nor a baton, so stop at this workflow boundary.
                            return PlaybookRunResult(
                                final_step=current_step,
                                final_status_code="NO_STATUS_CODE",
                                completed=False,
                            )
            else:
                handoff_next_step = None
                handoff_transition_source = "terminal"
                status_code = status_code_obj.value
                last_status_code = status_code

            self._record_step_completion(
                event_type=completion_event_type,
                current_step=current_step,
                status_code=status_code,
                runtime=runtime_label,
                visit_count=visit_count,
                hop_count=hop_count,
            )

            post_contract_result = self._handle_post_contract(
                current_step=current_step,
                status_code=status_code,
                runtime=runtime_label,
                update_contract_on_transition=False,
            )
            if post_contract_result is not None:
                status_code = post_contract_result.status_code
                last_status_code = status_code
                if post_contract_result.terminal_result is not None:
                    return post_contract_result.terminal_result
                if post_contract_result.next_step is not None:
                    if single_step_mode:
                        return PlaybookRunResult(
                            final_step=current_step,
                            final_status_code=status_code,
                            completed=False,
                        )
                    current_step = post_contract_result.next_step
                    continue

            if not frame.auto_continue and status_code in PAUSE_STATUS_CODES:
                pause_intent = self._extract_handoff_intent(
                    frame.execution_result
                ) or self._default_pause_intent(current_step, status_code)
                return self._emit_pause(
                    current_step=current_step,
                    status_code=status_code,
                    runtime=runtime_label,
                    reason="awaiting_user_input",
                    pause_intent=pause_intent,
                    update_contract=True,
                    contract_source="workflow.pause",
                    record_event=pause_record_event,
                )

            review_confirmed_advance = False
            if hasattr(frame.execution_result, "events"):
                review_confirmed_advance = any(
                    isinstance(event, dict) and event.get("type") == "review_confirmed_advance"
                    for event in frame.execution_result.events
                )

            next_step: Optional[str]
            transition_source: str
            if handoff_next_step is not None:
                next_step = handoff_next_step
                transition_source = handoff_transition_source
            else:
                next_step, transition_source = self._resolve_next_step(
                    current_step=current_step,
                    response=(
                        frame.response
                        if goto_target is None
                        else f"{frame.response}\nGOTO:{goto_target}"
                    ),
                    status_code=status_code,
                )
            if review_confirmed_advance and next_step == current_step:
                advanced_step = self._resolve_review_confirmed_successor(current_step)
                if advanced_step is not None:
                    next_step = advanced_step
                    transition_source = "review_confirmed_advance"
            if next_step is None:
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )
            if self._is_baton_driven_step(next_step):
                self._emit_transition(
                    current_step=current_step,
                    next_step=next_step,
                    status_code=status_code,
                    source=transition_source,
                    runtime=boundary_transition_runtime,
                    update_contract=True,
                    contract_source=transition_contract_source,
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            if next_step not in self.steps:
                if next_step in {"done", "_done"}:
                    return self._emit_complete(
                        current_step=current_step,
                        status_code=status_code,
                        next_step=next_step,
                        runtime=runtime_label,
                        reason="status_transition",
                        update_contract=True,
                        contract_source=transition_contract_source,
                    )

                if next_step == "user":
                    return self._emit_pause(
                        current_step=current_step,
                        status_code=status_code,
                        runtime=runtime_label,
                        reason="status_transition_to_user",
                        pause_intent=HandoffIntent.MANUAL_HANDOFF,
                        update_contract=True,
                        contract_source=transition_contract_source,
                    )

                raise RuntimeError(
                    f"Unknown terminal target '{next_step}' from step '{current_step}'"
                )

            self._emit_transition(
                current_step=current_step,
                next_step=next_step,
                status_code=status_code,
                source=transition_source,
                runtime=runtime_label,
                update_contract=True,
                contract_source=transition_contract_source,
            )
            if single_step_mode:
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )
            current_step = next_step

        self.blackboard_store.record_event(
            self.blackboard,
            "hop_limit_reached",
            {
                "step": current_step,
                "max_transitions": max_transitions,
                "last_status_code": last_status_code,
            },
        )
        raise RuntimeError(f"Playbook run reached max transition limit ({max_transitions})")

    def _try_reconcile_current_step(self, *, current_step: str) -> Optional[PlaybookRunResult]:
        """Reconcile an interrupted handoff before re-executing the same step."""
        interrupted = self._latest_unreconciled_interrupted_step()
        if interrupted is None:
            return None
        interrupted_step, reason = interrupted
        if interrupted_step != current_step:
            return None
        return self._try_reconcile_interrupted_step(
            current_step=current_step,
            runtime="single_step_resume",
            reason=reason,
        )

    def _run_single_step(self, *, current_step: str) -> PlaybookRunResult:
        owned_result = self._run_non_agent_owner(
            current_step=current_step,
            step_def=self.steps[current_step],
            runtime="single_step",
        )
        if owned_result is not None:
            return owned_result
        if self._is_baton_driven_step(current_step):
            return self._run_baton_driven_pr(
                current_step=current_step,
                max_transitions=1,
                runtime_label="single_step",
                completion_event_type="single_step_completed",
                transition_contract_source="workflow.single_step",
                single_step_mode=True,
            )

        return self._run_legacy_until_boundary(
            current_step=current_step,
            max_transitions=1,
            runtime_label="single_step",
            completion_event_type="single_step_completed",
            boundary_transition_runtime="single_step",
            transition_contract_source="workflow.single_step",
            single_step_mode=True,
            pause_record_event=False,
        )

    def run(
        self,
        *,
        max_transitions: int = 30,
        start_step: Optional[str] = None,
        single_step: bool = False,
    ) -> PlaybookRunResult:
        if single_step:
            resolution = (
                RuntimePositionResolution(current_step=start_step)
                if start_step is not None
                else self._resolve_runtime_position_from_handoff()
            )
            if resolution.realignment_result is not None:
                return resolution.realignment_result
            current_step = resolution.current_step
            terminal_result = self._result_from_terminal_position(current_step)
            if terminal_result is not None:
                return terminal_result
            if current_step not in self.steps:
                raise ValueError(f"Unknown playbook step '{current_step}'")
            # Preserve a completed handoff from an interrupted executor before
            # an explicit start-step override replaces the baton.
            reconciled = self._try_reconcile_current_step(current_step=current_step)
            if reconciled is not None:
                return reconciled
            if start_step is not None:
                self._remember_replaced_user_handoff()
                self.blackboard_store.set_current_step(self.blackboard, current_step)
                self.blackboard_store.update_handoff_contract(
                    self.blackboard,
                    from_step=current_step,
                    to_owner=HandoffOwner.AGENT,
                    to_step=current_step,
                    intent=HandoffIntent.AWAIT_AGENT,
                    source="workflow.start_step_override",
                )
            return self._run_single_step(current_step=current_step)

        if self._should_attempt_resume_reconciliation(start_step=start_step):
            interrupted = self._latest_unreconciled_interrupted_step()
            if interrupted is not None:
                interrupted_step, interrupted_reason = interrupted
                reconciled = self._try_reconcile_interrupted_step(
                    current_step=interrupted_step,
                    runtime="resume_reconciliation",
                    reason=interrupted_reason,
                )
                if reconciled is not None and (
                    self.blackboard.current_step == interrupted_step
                    or self.blackboard.current_step not in self.steps
                ):
                    return reconciled

        resolution = (
            RuntimePositionResolution(current_step=start_step)
            if start_step is not None
            else self._resolve_runtime_position_from_handoff()
        )
        if resolution.realignment_result is not None:
            return resolution.realignment_result
        current_step = resolution.current_step
        terminal_result = self._result_from_terminal_position(current_step)
        if terminal_result is not None:
            return terminal_result
        if current_step not in self.steps:
            raise ValueError(f"Unknown playbook step '{current_step}'")

        # Preserve a completed handoff from an interrupted executor before
        # an explicit start-step override replaces the baton.
        reconciled = self._try_reconcile_current_step(current_step=current_step)
        if reconciled is not None:
            return reconciled

        if start_step is not None:
            self._remember_replaced_user_handoff()
            self.blackboard_store.set_current_step(self.blackboard, current_step)
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=current_step,
                intent=HandoffIntent.AWAIT_AGENT,
                source="workflow.start_step_override",
            )

        owned_result = self._run_non_agent_owner(
            current_step=current_step,
            step_def=self.steps[current_step],
            runtime="owner_dispatch",
        )
        if owned_result is not None:
            return owned_result

        if not self._is_baton_driven_step(current_step):
            return self._run_legacy_until_boundary(
                current_step=current_step, max_transitions=max_transitions
            )

        return self._run_baton_driven_pr(current_step=current_step, max_transitions=max_transitions)
