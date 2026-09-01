"""Dormant version 2 workflow entry that applies driver policy at phase boundaries."""

from __future__ import annotations

from typing import Any, Callable

from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import (
    DriverCoordinator,
    DriverDecision,
    DriverPacket,
    DriverUnavailableError,
    resolve_driver_boundary,
)
from cafe.core.workflow_models import PlaybookRunResult
from cafe.core.workflow_notifications import WorkflowNotificationEvent, WorkflowNotifier

DelegatedDecisionProvider = Callable[[DriverPacket], DriverDecision]


class Version2WorkflowRuntime:
    """Advance an existing phase runtime according to one validated v2 policy."""

    def __init__(
        self,
        phase_runtime: Any,
        policy: DriverPolicyContract,
        *,
        delegated_decision_provider: DelegatedDecisionProvider | None = None,
        notifier: WorkflowNotifier | None = None,
    ) -> None:
        self.phase_runtime = phase_runtime
        self.policy = policy
        self.delegated_decision_provider = delegated_decision_provider
        self.notifier = notifier
        self.coordinator = DriverCoordinator(
            phase_runtime.blackboard_store,
            phase_runtime.blackboard,
        )

    def run(
        self,
        *,
        max_transitions: int = 30,
        start_step: str | None = None,
    ) -> PlaybookRunResult:
        if max_transitions <= 0:
            raise ValueError("max_transitions must be positive")

        # A prior single-step run persists authorization before returning. The
        # next invocation consumes it once before it may run the next phase.
        self.coordinator.consume_next_authorization()
        requested_start = start_step
        last_result: PlaybookRunResult | None = None

        for _ in range(max_transitions):
            result = self.phase_runtime.run(
                start_step=requested_start,
                single_step=True,
            )
            requested_start = None
            last_result = result
            lifecycle = self._lifecycle_stop(result)
            if lifecycle is not None:
                reason = result.final_status_code
                self.coordinator.record_lifecycle(lifecycle, reason=reason)
                if lifecycle != "human_task":
                    self._notify_lifecycle(lifecycle, result)
                return result

            requested_action = str(self.phase_runtime.blackboard.current_step)
            packet = self.coordinator.open_boundary(
                completed_phase=result.final_step,
                requested_action=requested_action,
                boundary_id=self._boundary_id(result, requested_action),
            )
            self._notify_boundary(packet)
            decision = self.coordinator.decision_for(packet.sequence)
            resolution = resolve_driver_boundary(
                self.policy,
                delegated_available=self.delegated_decision_provider is not None,
            )
            if resolution.pause:
                self.coordinator.record_lifecycle(
                    "paused", reason="delegated_driver_unavailable"
                )
                return result

            if decision is None and resolution.requires_decision:
                assert self.delegated_decision_provider is not None
                try:
                    raw_decision = self.delegated_decision_provider(packet)
                except DriverUnavailableError:
                    resolution = resolve_driver_boundary(
                        self.policy,
                        delegated_available=False,
                    )
                    if resolution.pause:
                        self.coordinator.record_lifecycle(
                            "paused", reason="delegated_driver_unavailable"
                        )
                        return result
                    raw_decision = None
                if raw_decision is not None:
                    decision = DriverDecision.model_validate(raw_decision)
            if decision is None and resolution.action_source == "unattended_fallback":
                self.coordinator.record_lifecycle(
                    "fallback", reason="delegated_driver_unavailable"
                )
            if decision is None:
                decision = DriverDecision(
                    workflow_id=packet.workflow_id,
                    sequence=packet.sequence,
                    requested_action=packet.requested_action,
                    action="advance",
                    rationale=resolution.action_source,
                )
            decision = self.coordinator.record_decision(decision)

            if decision.action != "advance":
                self.coordinator.record_lifecycle(
                    "stopped" if decision.action == "stop" else "paused",
                    reason=decision.rationale or decision.action,
                )
                return result
            if resolution.return_after_boundary:
                return result
            if self.coordinator.consume_authorization(packet.sequence) is None:
                return result

        if last_result is None:  # pragma: no cover - guarded by max_transitions.
            raise RuntimeError("version 2 workflow produced no result")
        raise RuntimeError(f"Version 2 workflow reached transition limit ({max_transitions})")

    def _boundary_id(self, result: PlaybookRunResult, requested_action: str) -> str:
        for event in reversed(self.phase_runtime.blackboard.events):
            if event.step != result.final_step:
                continue
            if event.event_type not in {"step_completed", "single_step_completed"}:
                continue
            return f"{event.timestamp}:{result.final_step}:{requested_action}"
        return f"{result.final_step}:{requested_action}"

    def _lifecycle_stop(self, result: PlaybookRunResult) -> str | None:
        current_step = str(self.phase_runtime.blackboard.current_step)
        status = result.final_status_code.upper()
        if result.completed or current_step == "done":
            return "complete"
        if "PERMISSION" in status:
            return "permission"
        if current_step == "user" or any(
            token in status
            for token in ("HUMAN_TASK", "CONFIRM", "CLARIFICATION", "NO_CHANGES")
        ):
            return "human_task"
        if any(token in status for token in ("ERROR", "FAILED", "INTERRUPTED")):
            return "error"
        return None

    def _notify_boundary(self, packet: DriverPacket) -> None:
        if self.notifier is None:
            return
        self.notifier.notify(
            WorkflowNotificationEvent(
                workflow_id=packet.workflow_id,
                event_id=f"boundary-{packet.sequence}",
                event_type="phase_boundary",
                step=packet.completed_phase,
            )
        )

    def _notify_lifecycle(self, lifecycle: str, result: PlaybookRunResult) -> None:
        if self.notifier is None:
            return
        event_type = {
            "complete": "completion",
            "permission": "permission",
            "error": "error",
        }.get(lifecycle)
        if event_type is None:
            return
        self.notifier.notify(
            WorkflowNotificationEvent(
                workflow_id=self.phase_runtime.blackboard.workflow_id,
                event_id=f"{event_type}-{result.final_step}-{result.final_status_code}",
                event_type=event_type,
                step=result.final_step,
            )
        )
