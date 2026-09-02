"""Dormant version 2 workflow entry that applies driver policy at phase boundaries."""

from __future__ import annotations

from contextlib import AbstractContextManager, ExitStack
from typing import Any, Callable

from cafe.core.blackboard import HandoffIntent, HandoffOwner
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import (
    DriverCoordinator,
    DriverDecision,
    DriverModelMismatchError,
    DriverPacket,
    DriverUnavailableError,
    resolve_driver_boundary,
)
from cafe.core.workflow_models import PlaybookRunResult

DelegatedDecisionProvider = Callable[[DriverPacket], DriverDecision]
DriverPolicyAuthority = Callable[[], AbstractContextManager[DriverPolicyContract]]


class Version2WorkflowRuntime:
    """Advance an existing phase runtime according to one validated v2 policy."""

    def __init__(
        self,
        phase_runtime: Any,
        policy: DriverPolicyContract,
        *,
        delegated_decision_provider: DelegatedDecisionProvider | None = None,
        policy_authority: DriverPolicyAuthority | None = None,
    ) -> None:
        self.phase_runtime = phase_runtime
        self.policy = policy
        self.delegated_decision_provider = delegated_decision_provider
        self.policy_authority = policy_authority
        self.coordinator = DriverCoordinator(
            phase_runtime.blackboard_store,
            phase_runtime.blackboard,
        )

    def run(
        self,
        *,
        max_transitions: int = 30,
        start_step: str | None = None,
        single_step: bool = False,
    ) -> PlaybookRunResult:
        if max_transitions <= 0:
            raise ValueError("max_transitions must be positive")

        requested_start = start_step
        last_result: PlaybookRunResult | None = None

        for _ in range(max_transitions):
            with ExitStack() as authority_stack:
                recovered_start = None
                if requested_start is None:
                    recovered_start = self._recovered_start_step_from_lifecycle()
                    requested_start = recovered_start
                    if recovered_start == "done":
                        self.phase_runtime.blackboard_store.set_current_step(
                            self.phase_runtime.blackboard,
                            "done",
                        )
                        result = self.phase_runtime.run(
                            start_step="done",
                            single_step=True,
                        )
                        self.coordinator.record_lifecycle(
                            "complete",
                            reason=result.final_status_code,
                        )
                        return result

                if self.policy_authority is not None:
                    try:
                        current_policy = authority_stack.enter_context(self.policy_authority())
                    except (OSError, ValueError):
                        self.coordinator.record_lifecycle(
                            "paused", reason="driver_policy_invalidated"
                        )
                        return self._policy_pause_result()
                    if current_policy != self.policy:
                        self.coordinator.record_lifecycle("paused", reason="driver_policy_changed")
                        return self._policy_pause_result()

                if self.policy.driver.mode == "delegated":
                    requested_action = recovered_start or str(
                        self.phase_runtime.blackboard.current_step
                    )
                    pending = self.coordinator.pending_boundary(
                        requested_action,
                        policy=self.policy,
                    )
                    if pending is None:
                        pending = self.coordinator.reconcile_missing_boundary(
                            requested_action,
                            policy=self.policy,
                        )
                    if pending is not None and not self._authorize_delegated_boundary(pending):
                        return self._boundary_result(pending)

                executed_step = requested_start or str(self.phase_runtime.blackboard.current_step)
                result = self.phase_runtime.run(
                    start_step=requested_start,
                    single_step=True,
                )
                requested_start = None
                last_result = result
                lifecycle = self._lifecycle_stop(result, executed_step=executed_step)
                if lifecycle is not None:
                    self.coordinator.record_lifecycle(lifecycle, reason=result.final_status_code)
                    return result

                requested_action = str(self.phase_runtime.blackboard.current_step)
                resolution = resolve_driver_boundary(
                    self.policy,
                    delegated_available=self.delegated_decision_provider is not None,
                )
                if resolution.action_source == "attached":
                    self.coordinator.record_lifecycle(
                        "awaiting_initiator", reason="attached_boundary"
                    )
                    return result
                if resolution.action_source == "unattended":
                    if single_step:
                        return result
                    continue

                packet = self.coordinator.open_boundary(
                    completed_phase=result.final_step,
                    requested_action=requested_action,
                    boundary_id=self._boundary_id(result, requested_action),
                    policy=self.policy,
                )
                if not self._authorize_delegated_boundary(
                    packet,
                    consume_authorization=False,
                ):
                    return result
                if single_step:
                    return result

        if last_result is None:  # pragma: no cover - guarded by max_transitions.
            raise RuntimeError("version 2 workflow produced no result")
        raise RuntimeError(f"Version 2 workflow reached transition limit ({max_transitions})")

    def _recovered_start_step_from_lifecycle(self) -> str | None:
        """Resume a durable lifecycle target whose pointer was not published."""
        current_step = str(self.phase_runtime.blackboard.current_step)
        lifecycle_event = next(
            (
                event
                for event in reversed(self.phase_runtime.blackboard.events)
                if event.event_type in {"transition", "workflow_completed"}
            ),
            None,
        )
        if lifecycle_event is None:
            return None
        if lifecycle_event.event_type == "workflow_completed":
            completed_step = str(lifecycle_event.data.get("step", ""))
            target = str(lifecycle_event.data.get("next_step", ""))
            if completed_step == current_step and target in {"done", "_done"}:
                self.phase_runtime.blackboard_store.update_handoff_contract(
                    self.phase_runtime.blackboard,
                    from_step=completed_step,
                    to_owner=HandoffOwner.DONE,
                    to_step="done",
                    intent=HandoffIntent.WORKFLOW_COMPLETE,
                    status_code=str(
                        lifecycle_event.data.get("status_code", "workflow_complete")
                    ),
                    source="workflow.lifecycle_recovery",
                )
                return "done"
            return None
        if str(lifecycle_event.data.get("from", "")) != current_step:
            return None
        target = str(lifecycle_event.data.get("to", ""))
        declared_steps = getattr(self.phase_runtime, "steps", {})
        if not target or target == current_step or target not in declared_steps:
            return None
        return target

    def _authorize_delegated_boundary(
        self,
        packet: DriverPacket,
        *,
        consume_authorization: bool = True,
    ) -> bool:
        decision = self.coordinator.decision_for(packet.sequence)
        if decision is None:
            if self.delegated_decision_provider is None:
                self.coordinator.record_lifecycle("paused", reason="delegated_driver_unavailable")
                return False
            try:
                raw_decision = self.delegated_decision_provider(packet)
                if raw_decision is None:
                    self.coordinator.record_lifecycle(
                        "paused", reason="delegated_driver_returned_no_decision"
                    )
                    return False
                decision = DriverDecision.model_validate(raw_decision)
                decision = self.coordinator.record_decision(decision)
            except (DriverUnavailableError, DriverModelMismatchError, ValueError) as exc:
                if isinstance(exc, DriverModelMismatchError):
                    reason = "delegated_model_mismatch"
                elif isinstance(exc, DriverUnavailableError):
                    reason = "delegated_driver_unavailable"
                else:
                    reason = "delegated_invalid_decision"
                self.coordinator.record_lifecycle("paused", reason=reason)
                return False

        if decision.action != "advance":
            self.coordinator.record_lifecycle(
                "stopped" if decision.action == "stop" else "paused",
                reason=decision.rationale or decision.action,
            )
            return False
        if not consume_authorization:
            return True
        return self.coordinator.consume_authorization(packet.sequence) is not None

    def _policy_pause_result(self) -> PlaybookRunResult:
        return PlaybookRunResult(
            final_step=str(self.phase_runtime.blackboard.current_step),
            final_status_code="DRIVER_POLICY_PAUSED",
            completed=False,
        )

    @staticmethod
    def _boundary_result(packet: DriverPacket) -> PlaybookRunResult:
        return PlaybookRunResult(
            final_step=packet.completed_phase,
            final_status_code="DELEGATED_DRIVER_PAUSED",
            completed=False,
        )

    def _boundary_id(self, result: PlaybookRunResult, requested_action: str) -> str:
        for event in reversed(self.phase_runtime.blackboard.events):
            if event.step != result.final_step:
                continue
            if event.event_type not in {"step_completed", "single_step_completed"}:
                continue
            return f"{event.timestamp}:{result.final_step}:{requested_action}"
        return f"{result.final_step}:{requested_action}"

    def _lifecycle_stop(
        self,
        result: PlaybookRunResult,
        *,
        executed_step: str,
    ) -> str | None:
        current_step = str(self.phase_runtime.blackboard.current_step)
        status = result.final_status_code.upper()
        if result.completed or current_step == "done":
            return "complete"
        if "PERMISSION" in status:
            return "permission"
        if current_step == "user" or any(
            token in status for token in ("HUMAN_TASK", "CONFIRM", "CLARIFICATION", "NO_CHANGES")
        ):
            return "human_task"
        if any(token in status for token in ("ERROR", "FAILED", "INTERRUPTED")):
            return "error"
        if current_step == executed_step:
            return "error"
        return None
