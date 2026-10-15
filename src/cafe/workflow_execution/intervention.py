"""Bounded same-step correction of a host-owned provider, using the worker store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.phase_state_mixin import next_runnable_iteration_number
from cafe.core.workflow_control import SupervisorCorrection
from cafe.workflow_execution.worker_launch import WorkerLaunchStore

_PENDING = {"requested", "prepared"}
_IDENTITY = ("worker_id", "workflow_id", "step", "iteration")


def _pending_tasks(issue, workflow):
    return any(
        t.workflow_id == workflow and t.status == HumanTaskStatus.PENDING
        for t in HumanTaskRecordStore(issue).tasks()
    )


def inspect_intervention(issue):
    store = WorkerLaunchStore(issue)
    with store._locked_records() as records:
        candidates = [dict(r) for r in records.values() if r.get("active")]
        from cafe.agents.process_control import process_birth

        def live(record):
            return bool(
                record.get("process_birth")
                and process_birth(record.get("pid")) == record["process_birth"]
            )

        active = [r for r in candidates if r.get("status") == "running" and live(r)]
        pending = [r for r in candidates if r.get("intervention", {}).get("status") in _PENDING]
        if len(active) > 1 or len(pending) > 1 or not candidates:
            raise ValueError("there is no unique workflow worker to inspect")
        record = (
            pending[0]
            if pending
            else active[0]
            if active
            else max(candidates, key=lambda r: r["created_at"])
        )
        verified = live(record) and record.get("status") == "running"
        return {
            "worker_id": record["worker_id"],
            "worker_status": record["status"],
            **record["active"],
            "accepts_interruption": verified
            and record["active"].get("accepts_interruption", False),
            "process_identity_verified": verified,
            "intervention": record.get("intervention"),
            "provider": record.get("provider"),
        }


def request_intervention(issue, identity, *, reason, feedback):
    if (
        set(identity) != set(_IDENTITY)
        or type(identity["iteration"]) is not int
        or identity["iteration"] < 1
    ):
        raise ValueError("exact worker/workflow/step/iteration identity is required")
    if not all(isinstance(v, str) and v.strip() and len(v) <= 4096 for v in (reason, feedback)):
        raise ValueError("reason and corrective feedback must each contain 1..4096 characters")
    payload = {**identity, "reason": reason, "feedback": feedback}
    request_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    store = WorkerLaunchStore(issue)
    with store._locked_records() as records:
        record = records.get(identity["worker_id"])
        if record is None:
            raise ValueError("worker identity is stale")
        existing = record.get("intervention")
        if existing and existing["request_id"] == request_id:
            return dict(existing)
        if existing and existing["status"] in _PENDING:
            raise ValueError("another correction request already owns this worker")
        from cafe.agents.process_control import process_birth

        if (
            not record.get("process_birth")
            or process_birth(record.get("pid")) != record["process_birth"]
        ):
            raise ValueError("worker process identity is stale or unverified")
        active = record.get("active", {})
        if (
            record.get("status") != "running"
            or any(active.get(k) != identity[k] for k in _IDENTITY[1:])
            or not active.get("accepts_interruption")
        ):
            raise ValueError(
                "execution identity is stale, paused, transitioning or not agent-owned"
            )
        if _pending_tasks(issue, identity["workflow_id"]):
            raise ValueError("pending human/capability decisions must retain their owner")
        request = {**payload, "request_id": request_id, "status": "requested"}
        record["intervention"] = request
        return dict(request)


class InterventionControl:
    def __init__(self, issue, worker_id):
        self.issue = Path(issue)
        self.worker_id = worker_id
        self.store = WorkerLaunchStore(issue)
        self.on_quiesced = None
        from cafe.agents.process_control import process_birth
        import os

        self.supports_provider_interruption = process_birth(os.getpid()) is not None

    def ensure_no_pending(self):
        with self.store._locked_records() as records:
            if any(
                r.get("intervention", {}).get("status") in _PENDING
                or r.get("provider", {}).get("state") in {"starting", "running", "unverified"}
                for r in records.values()
            ):
                raise ValueError(
                    "a pending correction or unverified provider freezes advancement; inspect/recover before resuming"
                )

    def _check(self, record):
        request = record.get("intervention")
        if request and request["status"] == "requested":
            if _pending_tasks(self.issue, request["workflow_id"]):
                request["status"] = "refused_pending_task"
                return
            raise SupervisorCorrection()

    def check(self):
        # Atomic sidecar replacement permits read-only checks during output drain.
        record = self.store._read_unlocked()[self.worker_id]
        if record.get("intervention", {}).get("status") == "requested":
            with self.store._locked_records() as records:
                self._check(records[self.worker_id])

    def position(self, state, steps, *, iteration=None):
        with self.store._locked_records() as records:
            record = records[self.worker_id]
            self._check(record)
            step = state.current_step
            definition = steps.get(step, {})
            record["active"] = {
                "workflow_id": state.workflow_id,
                "step": step,
                "iteration": iteration or next_runnable_iteration_number(self.issue / step),
                "accepts_interruption": (
                    self.supports_provider_interruption
                    and definition.get("assignee_type", "agent") == "agent"
                    and step in steps
                    and not definition.get("execution", {}).get("review_policy")
                    and not state.host_wait
                ),
            }

    def begin_boundary(self):
        with self.store._locked_records() as records:
            record = records[self.worker_id]
            self._check(record)
            if "active" in record:
                record["active"]["accepts_interruption"] = False

    def waiting(self):
        with self.store._locked_records() as records:
            record = records[self.worker_id]
            if record.get("active"):
                record["active"]["accepts_interruption"] = False
            if record.get("intervention", {}).get("status") == "requested":
                record["intervention"]["status"] = "refused_host_wait"

    def provider_starting(self):
        with self.store._locked_records() as records:
            record = records[self.worker_id]
            self._check(record)
            if record.get("provider", {}).get("state") in {"starting", "running", "unverified"}:
                raise SupervisorCorrection(
                    "prior provider ownership is unverified; advancement stays frozen"
                )
            record["provider"] = {
                "state": "starting",
                "interruptible": record["active"].get("accepts_interruption", False),
            }
            record["active"]["accepts_interruption"] = False

    def provider_unverified(self):
        with self.store._locked_records() as records:
            records[self.worker_id]["provider"]["state"] = "unverified"

    def provider_started(self, pid):
        with self.store._locked_records() as records:
            record = records[self.worker_id]
            interruptible = record.get("provider", {}).get("interruptible", False)
            record["provider"] = {"pid": pid, "state": "running"}
            record["active"]["accepts_interruption"] = interruptible
        self.check()

    def provider_stopped(self):
        with self.store._locked_records() as records:
            provider = records[self.worker_id].get("provider")
            if provider and provider["state"] != "unverified":
                if provider["state"] == "starting":
                    records[self.worker_id]["active"]["accepts_interruption"] = provider.get(
                        "interruptible", False
                    )
                provider["state"] = "stopped"

    def prepare(self, apply, callback):
        with self.store._locked_records() as records:
            record = records[self.worker_id]
            if not record.get("intervention"):
                raise ValueError("provider quiescence is unverified; advancement remains frozen")
            request = dict(record["intervention"])
            provider = record.get("provider")
            if provider and provider["state"] != "stopped":
                raise ValueError("provider quiescence is unverified; correction remains frozen")
        # Runtime still owns advancement, and phase unwinding released its workspace lease.
        from cafe.core.workspace_lock import workspace_execution_lock

        with workspace_execution_lock(self.issue.resolve().parents[2], blocking=False):
            apply(request)
        with self.store._locked_records() as records:
            records[self.worker_id]["intervention"]["status"] = "prepared"
        self.on_quiesced = callback

    def finish(self):
        # Reacquisition proves the original advancement lease was released.
        from cafe.utils.file_lock import advancement_execution_lock

        with advancement_execution_lock(self.issue, reuse_existing=False):
            with self.store._locked_records() as records:
                request = dict(records[self.worker_id]["intervention"])
            event = _queue_quiescence(self.issue, request)
            with self.store._locked_records() as records:
                record = records[self.worker_id]
                record["intervention"]["status"] = "quiesced"
                record["status"] = "stopped"
        if self.on_quiesced:
            self.on_quiesced(event)


def _queue_quiescence(issue, request):
    from cafe.core.blackboard import BlackboardStore

    board = BlackboardStore(issue)
    state = board.load_or_create(request["step"])
    if not any(
        e.event_type == "supervisor_correction_quiesced"
        and e.data.get("request_id") == request["request_id"]
        for e in state.events
    ):
        board.record_event(
            state,
            "supervisor_correction_quiesced",
            {
                "step": request["step"],
                "request_id": request["request_id"],
                "worker_id": request["worker_id"],
                "reason": request["reason"],
                "correction_step": request["step"],
                "next_action": "cafe make",
            },
        )
    for event in state.events:
        if (
            event.event_type == "workflow_event_callback_enqueued"
            and event.data.get("request_id") == request["request_id"]
        ):
            return event.data
    return board.prepare_workflow_callback_event(
        state,
        {
            "workflow_id": state.workflow_id,
            "issue": issue.name,
            "event_type": "workflow_interruption",
            "step": request["step"],
            "status_code": "INTERRUPTED:supervisor_correction",
            "reason": "supervisor_correction",
            "request_id": request["request_id"],
            "worker_id": request["worker_id"],
            "iteration": request["iteration"],
            "correction_step": request["step"],
            "next_action": "cafe make",
        },
    )


def recover_intervention(issue, identity):
    """Finish a previously verified stop, never kill an externally named PID."""
    from cafe.utils.file_lock import advancement_execution_lock
    from cafe.core.workspace_lock import workspace_execution_lock
    from cafe.core.workflow_control import apply_correction

    store = WorkerLaunchStore(issue)
    with advancement_execution_lock(issue, reuse_existing=False):
        with workspace_execution_lock(issue.resolve().parents[2], blocking=False):
            with store._locked_records() as records:
                record = records.get(identity["worker_id"], {})
                request = record.get("intervention")
                if not request or any(request.get(k) != identity[k] for k in _IDENTITY):
                    raise ValueError("correction recovery identity does not match")
                if request["status"] == "quiesced":
                    return dict(request)
                if request["status"] not in _PENDING:
                    raise ValueError("this correction is not recoverable")
                if record.get("provider") and record["provider"].get("state") != "stopped":
                    raise ValueError(
                        "provider stop was not verified; retain the freeze for diagnosis"
                    )
                request = dict(request)
            apply_correction(issue, request)
            _queue_quiescence(issue, request)
            with store._locked_records() as records:
                record = records[identity["worker_id"]]
                record["intervention"]["status"] = "quiesced"
                record["status"] = "stopped"
            return {**request, "status": "quiesced"}
