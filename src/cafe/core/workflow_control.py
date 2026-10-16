"""Neutral cooperative control for the host currently owning a workflow."""

from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps

_control = ContextVar("workflow_control", default=None)


class SupervisorCorrection(BaseException):
    """Intentional correction; never a provider failure, user cancel or retry."""


@contextmanager
def bind_control(control):
    token = _control.set(control)
    try:
        yield
    finally:
        _control.reset(token)


def current_control():
    return _control.get()


def check_control():
    control = current_control()
    if control is not None:
        control.check()


def controlled_boundary(function):
    """Serialize accepted requests against committing a completed owner boundary."""

    @wraps(function)
    def guarded(owner, *args, **kwargs):
        control = current_control()
        if control is None:
            return function(owner, *args, **kwargs)
        control.begin_boundary()
        try:
            return function(owner, *args, **kwargs)
        finally:
            control.position(owner.blackboard, owner.steps)

    return guarded


def apply_correction(issue_dir, request):
    """Commit same-step corrective input without accepting output or resetting budgets."""
    from cafe.core.blackboard import BlackboardStore, HandoffOwner, HandoffIntent
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.core.phase_state_mixin import next_runnable_iteration_number
    from cafe.core.packet_io import atomic_write_bytes

    store = BlackboardStore(issue_dir)
    state = store.load_or_create(request["step"])
    if state.workflow_id != request["workflow_id"] or state.current_step != request["step"]:
        raise ValueError("correction no longer matches the authoritative workflow owner")
    if any(
        t.workflow_id == state.workflow_id and t.status == HumanTaskStatus.PENDING
        for t in HumanTaskRecordStore(issue_dir).tasks()
    ):
        raise ValueError("pending decisions must be resolved by their authorized owner")
    directory = issue_dir / request["step"]
    iteration = next_runnable_iteration_number(directory)
    path = directory / f"iteration_{iteration:03d}" / "user_input.md"
    marker = f"<!-- cafe-supervisor-correction:{request['request_id']} -->"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker not in original:
        text = (
            original
            + "\n\n"
            + marker
            + "\n"
            + request["reason"]
            + "\n"
            + request["feedback"]
            + "\nPreserve unfinished obligations and existing authority. Reconcile unknown effects before any replay.\n"
        )
        atomic_write_bytes(path, text.encode("utf-8"))
    if not any(
        e.event_type == "supervisor_correction_prepared"
        and e.data.get("request_id") == request["request_id"]
        for e in state.events
    ):
        contract = store.build_handoff_contract(
            from_step=request["step"],
            to_owner=HandoffOwner.AGENT,
            to_step=request["step"],
            intent=HandoffIntent.AWAIT_AGENT,
            status_code="INTERRUPTED:supervisor_correction",
            source="workflow.supervisor_correction",
        )
        state.handoff_contract = contract
        state.host_wait = None
        store.record_event(
            state,
            "supervisor_correction_prepared",
            {
                "request_id": request["request_id"],
                "worker_id": request["worker_id"],
                "step": request["step"],
                "iteration": request["iteration"],
                "reason": request["reason"],
                "feedback_file": str(path),
            },
            baton_contract=contract,
        )
    return state
