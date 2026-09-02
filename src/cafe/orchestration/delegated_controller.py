"""Temporary outer adapter for the unreleased delegated driver mode.

The workflow core is deliberately unaware of this controller.  It uses the
core's explicit one-transition primitive only where a delegated decision must
gate an otherwise eligible successor.  #456 will replace this bounded adapter
with supervised orchestration.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any, Callable

from cafe.core.blackboard import HandoffIntent, HandoffOwner
from cafe.orchestration.driver_policy import DelegatedDriverPolicy, DriverPolicyContract
from cafe.orchestration.driver_runtime import (
    DriverCoordinator,
    DriverDecision,
    DriverModelMismatchError,
    DriverPacket,
    DriverUnavailableError,
)
from cafe.core.workflow_models import PlaybookRunResult

DelegatedDecisionProvider = Callable[[DriverPacket], DriverDecision]
DriverPolicyAuthority = Callable[[], AbstractContextManager[DriverPolicyContract]]


class DelegatedWorkflowController:
    """Compose delegated decisions around a mode-neutral workflow runtime."""

    def __init__(
        self,
        phase_runtime: Any,
        policy: DriverPolicyContract,
        *,
        delegated_decision_provider: DelegatedDecisionProvider | None = None,
        policy_authority: DriverPolicyAuthority | None = None,
    ) -> None:
        if not isinstance(policy.driver, DelegatedDriverPolicy):
            raise ValueError("delegated controller requires delegated driver policy")
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
    ) -> PlaybookRunResult:
        if max_transitions <= 0:
            raise ValueError("max_transitions must be positive")

        requested_start = start_step
        last_result: PlaybookRunResult | None = None
        authority = self.policy_authority() if self.policy_authority is not None else nullcontext(self.policy)
        try:
            with authority as current_policy:
                if current_policy != self.policy:
                    self.coordinator.record_lifecycle("paused", reason="driver_policy_changed")
                    return self._policy_pause_result()
                for _ in range(max_transitions):
                    requested_action = requested_start or str(self.phase_runtime.blackboard.current_step)
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
                        return self._boundary_result(packet)
        except (OSError, ValueError):
            self.coordinator.record_lifecycle("paused", reason="driver_policy_invalidated")
            return self._policy_pause_result()

        if last_result is None:  # pragma: no cover - guarded by max_transitions.
            raise RuntimeError("version 2 workflow produced no result")
        raise RuntimeError(f"delegated workflow reached transition limit ({max_transitions})")

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
        if result.detail:
            return f"transition:{result.detail}:{requested_action}"
        for event in reversed(self.phase_runtime.blackboard.events):
            if event.event_type != "transition":
                continue
            if (
                str(event.data.get("from", "")) != result.final_step
                or str(event.data.get("to", "")) != requested_action
            ):
                continue
            transition_id = event.data.get("transition_id")
            if isinstance(transition_id, str) and transition_id:
                return f"transition:{transition_id}:{requested_action}"
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
            token in status
            for token in ("HUMAN_TASK", "CONFIRM_OUTPUT", "CLARIFICATION", "NO_CHANGES")
        ):
            return "human_task"
        if any(token in status for token in ("ERROR", "FAILED", "INTERRUPTED")):
            return "error"
        if current_step == executed_step:
            return "error"
        return None
