#!/usr/bin/env python3
"""Launch or resume a Driver-managed workflow from durable authority."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from cafe.driver import EventCallbackRequest, event_callback_projection
from cafe.driver._store import load_contract
from cafe.workflow_execution.event_callback import resolve_builtin_workflow_event_callback

CALLBACK_ID = "builtin:use-cafe-workflow:workflow_event_callback"
MAX_WORKFLOW_STATE_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("prepared workflow JSON contains duplicate keys")
        result[key] = value
    return result


def _prepared_workflow(issue_dir: Path) -> dict[str, Any]:
    path = issue_dir / "blackboard.json"
    for parent in (issue_dir.parent.parent, issue_dir.parent, issue_dir):
        try:
            metadata = parent.lstat()
        except OSError as exc:
            raise ValueError("prepared workflow checkout is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("prepared workflow checkout is unsafe")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError("prepared workflow state is missing or unreadable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("prepared workflow state is unsafe")
    if metadata.st_size > MAX_WORKFLOW_STATE_BYTES:
        raise ValueError("prepared workflow state exceeds the maximum bounded size")
    try:
        content = path.read_bytes()
        state = json.loads(content.decode("utf-8"), object_pairs_hook=_exact_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("prepared workflow state is unreadable") from exc
    if not isinstance(state, dict):
        raise ValueError("prepared workflow state must be an object")
    for field in ("workflow_id", "playbook_id", "current_step"):
        value = state.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"prepared workflow {field} is missing")
    return state


def _validate_identifier(value: str, label: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _validate_checkout(contract: Mapping[str, Any], project_root: Path) -> None:
    checkout = contract.get("checkout")
    if not isinstance(checkout, Mapping):
        raise ValueError("confirmed checkout identity is missing")
    kind = checkout.get("kind")
    if kind == "current_checkout" and set(checkout) == {"kind"}:
        return
    if kind == "worktree" and set(checkout) == {"kind", "path"}:
        raw_path = checkout.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("confirmed worktree identity is invalid")
        confirmed = Path(raw_path).expanduser()
        if not confirmed.is_absolute():
            confirmed = project_root / confirmed
        if confirmed.resolve() != project_root:
            raise ValueError("current working directory differs from the confirmed worktree")
        return
    raise ValueError("confirmed checkout identity is invalid")


def _validate_event_binding(
    *,
    issue_dir: Path,
    issue_name: str,
    workflow_id: str,
    contract: Mapping[str, Any],
    digest: str,
    project_root: Path,
) -> None:
    binding = resolve_builtin_workflow_event_callback(CALLBACK_ID, project_root=project_root)
    if binding.callback_id != CALLBACK_ID:
        raise ValueError("trusted workflow callback binding changed")
    projection = event_callback_projection(
        EventCallbackRequest(
            issue_dir=issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
        )
    )
    if projection.contract_sha256 != digest:
        raise ValueError("Driver contract changed during event binding validation")
    event = projection.event
    entries = event.get("clis") if isinstance(event, Mapping) else None
    if not isinstance(entries, tuple) or not entries:
        raise ValueError("event-driven Driver CLI order is unavailable")
    projected = [dict(entry) for entry in entries if isinstance(entry, Mapping)]
    driver = contract.get("driver")
    expected = driver.get("clis") if isinstance(driver, Mapping) else None
    if len(projected) != len(entries) or projected != expected:
        raise ValueError("event-driven Driver CLI order differs from the confirmed contract")


def _is_user_boundary(state: Mapping[str, Any]) -> bool:
    handoff = state.get("handoff_contract")
    return state.get("current_step") == "user" or (
        isinstance(handoff, Mapping) and handoff.get("to_owner") == "user"
    )


def _validate_alignment_input(
    raw_input: str | None,
    *,
    issue_dir: Path,
    state: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> str | None:
    if raw_input is None:
        return None
    try:
        payload = json.loads(raw_input, object_pairs_hook=_exact_object)
    except json.JSONDecodeError as exc:
        raise ValueError("alignment input must be a JSON object") from exc
    decision = payload.get("decision") if isinstance(payload, dict) else None
    reason = payload.get("reason") if isinstance(payload, dict) else None
    if not isinstance(decision, str) or not decision.strip():
        raise ValueError("alignment input requires an explicit decision")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("alignment input requires an explicit reason")
    handoff = state.get("handoff_contract")
    if (
        state.get("current_step") != "user"
        or not isinstance(handoff, Mapping)
        or handoff.get("to_owner") != "user"
        or handoff.get("intent") != "alignment_checkpoint"
    ):
        raise ValueError("alignment input does not match the durable workflow boundary")
    from_step = handoff.get("from_step")
    if not isinstance(from_step, str) or not _IDENTIFIER.fullmatch(from_step):
        raise ValueError("alignment input has no valid durable source step")
    reactive = contract.get("reactive_user_handoffs")
    if (
        not isinstance(reactive, Mapping)
        or reactive.get("alignment_checkpoint") != "driver_resolvable_when_clear"
    ):
        raise ValueError("the confirmed Driver contract does not authorize alignment input")
    candidates = sorted((issue_dir / from_step).glob("iteration_*/alignment_request.json"))
    if not candidates:
        raise ValueError("durable alignment request is missing")
    request_path = candidates[-1]
    try:
        metadata = request_path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("durable alignment request is unsafe")
        if metadata.st_size > MAX_WORKFLOW_STATE_BYTES:
            raise ValueError("durable alignment request exceeds the maximum bounded size")
        request = json.loads(
            request_path.read_text(encoding="utf-8"), object_pairs_hook=_exact_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("durable alignment request is unreadable") from exc
    allowed = request.get("allowed_decisions") if isinstance(request, dict) else None
    if not isinstance(allowed, list) or decision not in allowed:
        raise ValueError("alignment decision is not allowed by the durable request")
    return raw_input


def _emit_directive(
    *,
    mode: str,
    action: str,
    worker: str,
    next_wake: list[str],
    guidance: str,
    poll_interval_seconds: int | None = None,
    exit_code: int | None = None,
) -> None:
    directive: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "action": action,
        "worker": worker,
        "next_wake": next_wake,
    }
    if poll_interval_seconds is not None:
        directive["poll_interval_seconds"] = poll_interval_seconds
    if exit_code is not None:
        directive["exit_code"] = exit_code
    print(
        "CAFE_DRIVER_DIRECTIVE " + json.dumps(directive, ensure_ascii=True, separators=(",", ":")),
        flush=True,
    )
    print(guidance)


def _launch_failed(
    mode: str, message: str, *, worker: str = "none", exit_code: int | None = None
) -> int:
    _emit_directive(
        mode=mode,
        action="launch_failed",
        worker=worker,
        next_wake=["user_input"],
        exit_code=exit_code,
        guidance=(
            "Workflow launch failed. Inspect the reported error before deciding whether to retry."
        ),
    )
    print(f"run_workflow.py: {message}", file=sys.stderr)
    return exit_code if exit_code not in (None, 0) else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch or resume CAFE under the confirmed Driver contract."
    )
    parser.add_argument("--issue", required=True)
    parser.add_argument("--playbook", required=True)
    parser.add_argument(
        "--driver-mode", required=True, choices=("attached", "unattended", "event-driven")
    )
    parser.add_argument(
        "--alignment-input",
        help="Explicit JSON for an authorized durable alignment checkpoint.",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
    process_factory: Callable[..., Any] = subprocess.Popen,
) -> int:
    args = _parser().parse_args(argv)
    mode = args.driver_mode
    try:
        issue_name = _validate_identifier(args.issue, "issue name")
        playbook = _validate_identifier(args.playbook, "playbook name")
        project_root = Path(cwd or Path.cwd()).resolve()
        issue_dir = project_root / ".cafe" / "issues" / issue_name
        state = _prepared_workflow(issue_dir)
        if state["playbook_id"] != playbook:
            raise ValueError("requested playbook differs from the prepared workflow")
        workflow_id = state["workflow_id"]
        contract, digest = load_contract(
            issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
        )
        driver = contract.get("driver")
        confirmed_mode = driver.get("mode") if isinstance(driver, Mapping) else None
        if confirmed_mode != mode:
            raise ValueError("requested Driver mode differs from the confirmed contract")
        _validate_checkout(contract, project_root)
        alignment_input = _validate_alignment_input(
            args.alignment_input,
            issue_dir=issue_dir,
            state=state,
            contract=contract,
        )
        if mode == "event-driven":
            _validate_event_binding(
                issue_dir=issue_dir,
                issue_name=issue_name,
                workflow_id=workflow_id,
                contract=contract,
                digest=digest,
                project_root=project_root,
            )
    except (OSError, ValueError) as exc:
        return _launch_failed(mode, str(exc))

    if _is_user_boundary(state) and alignment_input is None:
        _emit_directive(
            mode=mode,
            action="await_user",
            worker="none",
            next_wake=["user_input"],
            guidance=(
                "Workflow is at a durable user-owned boundary. Do not launch or infer an answer."
            ),
        )
        return 0

    command = [
        "cafe",
        "workflow",
        "--issue",
        issue_name,
        "--playbook",
        playbook,
        "--execute",
        "--mute-agent-output",
    ]
    worker = "foreground" if mode == "attached" else "background"
    if mode != "attached":
        command.append("--background")
    if mode == "event-driven":
        command.extend(["--on-workflow-event", CALLBACK_ID])
    if alignment_input is not None:
        command.extend(["--user-input", alignment_input])

    try:
        process = process_factory(command, cwd=str(project_root))
    except OSError as exc:
        return _launch_failed(mode, str(exc), worker=worker)

    if mode == "attached":
        interval = driver["poll_interval_seconds"]
        _emit_directive(
            mode=mode,
            action="wait",
            worker=worker,
            next_wake=["process_exit", "user_input"],
            poll_interval_seconds=interval,
            guidance=(
                "Foreground workflow started successfully. Retain the foreground handle and wait "
                f"the full confirmed {interval}-second interval before polling."
            ),
        )

    returncode = process.wait()
    if returncode != 0:
        return _launch_failed(
            mode,
            f"CAFE exited with status {returncode}",
            worker=worker,
            exit_code=returncode,
        )

    if mode == "unattended":
        _emit_directive(
            mode=mode,
            action="yield",
            worker=worker,
            next_wake=["user_input"],
            guidance=(
                "Background workflow started successfully. Yield now; inspect durable state only "
                "when the user returns."
            ),
        )
    elif mode == "event-driven":
        _emit_directive(
            mode=mode,
            action="yield",
            worker=worker,
            next_wake=["workflow_event_callback", "user_input"],
            guidance=(
                "Background workflow started successfully. End the current Driver turn now. Do not "
                "poll with sleep, ps, write_stdin, cafe status, or cafe task ls."
            ),
        )
    else:
        try:
            completed_state = _prepared_workflow(issue_dir)
        except ValueError as exc:
            return _launch_failed(mode, str(exc), worker=worker)
        if _is_user_boundary(completed_state):
            _emit_directive(
                mode=mode,
                action="await_user",
                worker="none",
                next_wake=["user_input"],
                guidance="Workflow reached a durable user-owned boundary. Do not infer an answer.",
            )
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
