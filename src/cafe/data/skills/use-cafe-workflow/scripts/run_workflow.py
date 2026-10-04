#!/usr/bin/env python3
"""Launch or resume a Manager-managed workflow from durable authority."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_PYTHON_STARTUP_ENVIRONMENT_KEYS = ("PYTHONHOME", "PYTHONPATH")
_ISOLATED_BOOTSTRAP_ENVIRONMENT_KEY = "CAFE_WORKFLOW_DRIVER_ISOLATED_RUNTIME"
_WRAPPER_RELATIVE_PATH = Path("cafe/data/skills/use-cafe-workflow/scripts/run_workflow.py")


def _sanitized_python_environment() -> dict[str, str]:
    """Return an environment that cannot redirect Python imports."""
    environment = dict(os.environ)
    for key in _PYTHON_STARTUP_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    return environment


def _absolute_interpreter() -> str:
    """Return the interpreter that loaded this Manager wrapper."""
    if not sys.executable:
        raise RuntimeError("Manager interpreter is unavailable")
    return str(Path(sys.executable).absolute())


def _bootstrap_isolated_runtime() -> None:
    """Restart the executable wrapper once without ambient Python path overrides."""
    if os.environ.get(_ISOLATED_BOOTSTRAP_ENVIRONMENT_KEY) == "1":
        return
    environment = _sanitized_python_environment()
    environment[_ISOLATED_BOOTSTRAP_ENVIRONMENT_KEY] = "1"
    interpreter = _absolute_interpreter()
    try:
        os.execvpe(
            interpreter,
            [interpreter, "-I", str(Path(__file__).resolve()), *sys.argv[1:]],
            environment,
        )
    except OSError as exc:
        print(f"run_workflow.py: unable to start isolated Manager runtime: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    _bootstrap_isolated_runtime()

# The executable bootstrap intentionally precedes every CAFE import.
import cafe  # noqa: E402
from cafe.manager import (  # noqa: E402
    EventCallbackRequest,
    Freshness,
    ManagerEntryRequest,
    evaluate_manager_entry,
    event_callback_projection,
)
from cafe.manager._store import load_contract  # noqa: E402
from cafe.workflow_execution.event_callback import (  # noqa: E402
    resolve_builtin_workflow_event_callback,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conversation_locale_adapter import (  # noqa: E402, I001
    effective_conversation_locale,
)

CALLBACK_ID = "builtin:use-cafe-workflow:workflow_event_callback"
MAX_WORKFLOW_STATE_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _role_api(issue_dir: Path):
    manager_contract = issue_dir / "manager" / "contract.json"
    driver_contract = issue_dir / "driver" / "contract.json"
    if manager_contract.exists() or driver_contract.exists():
        from cafe.manager._store import select_authority_directory

        authority = select_authority_directory(issue_dir)
    else:
        authority = issue_dir / "manager"
    if authority.name == "driver":
        from cafe import driver as api
    else:
        from cafe import manager as api
    return api


def _wrapper_source_root() -> Path | None:
    """Return the source root when this is a bundled CAFE wrapper."""
    script = Path(__file__).resolve()
    for parent in script.parents:
        if script == parent / _WRAPPER_RELATIVE_PATH:
            return parent
    return None


def _loaded_cafe_source_root() -> Path:
    """Return the source root that supplied the loaded CAFE package."""
    package_file = getattr(cafe, "__file__", None)
    if not isinstance(package_file, str):
        raise ValueError("Manager runtime CAFE package has no source file")
    resolved = Path(package_file).resolve()
    if resolved.name != "__init__.py" or resolved.parent.name != "cafe":
        raise ValueError("Manager runtime CAFE package source is invalid")
    return resolved.parent.parent


def _global_wrapper_matches_runtime(runtime_source_root: Path) -> bool:
    """Verify a globally synced wrapper matches the loaded runtime's bundled copy."""
    try:
        return Path(__file__).resolve().read_bytes() == (
            runtime_source_root / _WRAPPER_RELATIVE_PATH
        ).read_bytes()
    except OSError:
        return False


def _validated_host_interpreter() -> str:
    """Ensure the wrapper and loaded runtime agree before launching CAFE."""
    wrapper_source_root = _wrapper_source_root()
    loaded_source_root = _loaded_cafe_source_root()
    if wrapper_source_root is not None:
        if loaded_source_root != wrapper_source_root:
            raise ValueError("Manager runtime source differs from the bundled workflow wrapper")
    elif not _global_wrapper_matches_runtime(loaded_source_root):
        raise ValueError("Manager runtime source differs from the global workflow wrapper")
    return _absolute_interpreter()


def _workflow_child_environment() -> dict[str, str]:
    """Keep workflow control-plane imports fixed without leaking path overrides."""
    environment = _sanitized_python_environment()
    environment.pop(_ISOLATED_BOOTSTRAP_ENVIRONMENT_KEY, None)
    environment["CAFE_SKIP_ENTRYPOINT_CHECK"] = "1"
    return environment


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains duplicate keys")
        result[key] = value
    return result


def _mapping_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value, object_pairs_hook=_exact_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("fresh facts must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("fresh facts must be a JSON object")
    return parsed


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


def _main_checkout_root(project_root: Path) -> Path:
    marker = project_root / ".git"
    try:
        metadata = marker.lstat()
    except OSError as exc:
        raise ValueError("relative worktree identity requires Git checkout metadata") from exc
    if stat.S_ISDIR(metadata.st_mode):
        return project_root
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Git checkout metadata is unsafe")
    if metadata.st_size > 4096:
        raise ValueError("Git checkout metadata exceeds the maximum bounded size")
    try:
        marker_text = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("Git checkout metadata is unreadable") from exc
    prefix = "gitdir: "
    if not marker_text.startswith(prefix):
        raise ValueError("Git checkout metadata is invalid")
    git_dir = Path(marker_text[len(prefix) :])
    if not git_dir.is_absolute():
        git_dir = project_root / git_dir
    git_dir = git_dir.resolve()
    common_marker = git_dir / "commondir"
    try:
        common_metadata = common_marker.lstat()
    except OSError as exc:
        raise ValueError("Git common checkout metadata is unavailable") from exc
    if stat.S_ISLNK(common_metadata.st_mode) or not stat.S_ISREG(common_metadata.st_mode):
        raise ValueError("Git common checkout metadata is unsafe")
    if common_metadata.st_size > 4096:
        raise ValueError("Git common checkout metadata exceeds the maximum bounded size")
    try:
        common_text = common_marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("Git common checkout metadata is unreadable") from exc
    if not common_text:
        raise ValueError("Git common checkout metadata is invalid")
    common_dir = Path(common_text)
    if not common_dir.is_absolute():
        common_dir = git_dir / common_dir
    return common_dir.resolve().parent


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
            confirmed = _main_checkout_root(project_root) / confirmed
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
) -> Path:
    binding = resolve_builtin_workflow_event_callback(CALLBACK_ID, project_root=project_root)
    if binding.callback_id != CALLBACK_ID:
        raise ValueError("trusted workflow callback binding changed")
    api = _role_api(issue_dir)
    projection_request = EventCallbackRequest(
            issue_dir=issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
        )
    projection = (
        event_callback_projection(projection_request)
        if api is cafe.manager
        else api.event_callback_projection(projection_request)
    )
    if projection.contract_sha256 != digest:
        raise ValueError("Manager contract changed during event binding validation")
    event = projection.event
    entries = event.get("clis") if isinstance(event, Mapping) else None
    if not isinstance(entries, tuple) or not entries:
        raise ValueError("event-driven Manager CLI order is unavailable")
    projected = [dict(entry) for entry in entries if isinstance(entry, Mapping)]
    manager = contract.get("manager", contract.get("driver"))
    expected = manager.get("clis") if isinstance(manager, Mapping) else None
    if len(projected) != len(entries) or projected != expected:
        raise ValueError("event-driven Manager CLI order differs from the confirmed contract")
    return binding.script


def _validate_host_callback_transport(script: Path, issue_dir: Path) -> None:
    # A local Desktop stdio session has a thread ID but no daemon control
    # endpoint. Catch this before launching hours of background phase work.
    spec = importlib.util.spec_from_file_location("cafe_callback_transport_check", script)
    if spec is None or spec.loader is None:
        raise ValueError("trusted workflow callback transport check is unavailable")
    callback = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(callback)
    callback.validate_bound_host_transport(issue_dir)


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
    checkpoint_policy = (
        "manager_resolvable_when_clear"
        if isinstance(contract.get("manager"), Mapping)
        else "driver_resolvable_when_clear"
    )
    if (
        not isinstance(reactive, Mapping)
        or reactive.get("alignment_checkpoint") != checkpoint_policy
    ):
        raise ValueError("the confirmed workflow contract does not authorize alignment input")
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
    conversation_locale: dict[str, str] | None = None,
) -> None:
    directive: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "action": action,
        "worker": worker,
        "next_wake": next_wake,
    }
    if conversation_locale is not None:
        # Read, never re-resolved: the Manager mirrors the generic authority so a
        # resumed turn cannot drift to another language.
        directive["conversation_locale"] = conversation_locale
    if poll_interval_seconds is not None:
        directive["poll_interval_seconds"] = poll_interval_seconds
    if exit_code is not None:
        directive["exit_code"] = exit_code
    print(
        "CAFE_MANAGER_DIRECTIVE " + json.dumps(directive, ensure_ascii=True, separators=(",", ":")),
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
        description="Launch or resume CAFE under the confirmed Manager contract."
    )
    parser.add_argument("--issue", required=True)
    parser.add_argument("--playbook", required=True)
    parser.add_argument("--manager-mode", dest="manager_mode", choices=("attached", "unattended", "event-driven"))
    parser.add_argument("--driver-mode", dest="legacy_manager_mode", choices=("attached", "unattended", "event-driven"))
    parser.add_argument(
        "--fresh-facts",
        type=_mapping_json,
        required=True,
        help="Rebuilt current Manager facts as JSON.",
    )
    parser.add_argument(
        "--alignment-input",
        help="Explicit JSON for an authorized durable alignment checkpoint.",
    )
    parser.add_argument(
        "--user-handoff",
        help="Explicit JSON for a user-owned durable handoff redirect.",
    )
    parser.add_argument(
        "--user-input",
        help="Explicit user input for a user-owned workflow boundary.",
    )
    return parser


def _validate_user_handoff_input(raw: str | None) -> str | None:
    """Validate the transport envelope; the CLI validates durable task authority."""
    if raw is None:
        return None
    try:
        payload = json.loads(raw, object_pairs_hook=_exact_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("user handoff must be valid JSON") from exc
    expected = {"type", "workflow_id", "human_task_id", "target", "input", "request_id"}
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("type") != "user_handoff":
        raise ValueError("user handoff has an invalid envelope")
    if any(
        not isinstance(payload.get(key), str) or not payload[key].strip()
        for key in expected - {"type"}
    ):
        raise ValueError("user handoff fields must be non-empty strings")
    if len(payload["input"].encode("utf-8")) > 65_536:
        raise ValueError("user handoff input exceeds the 64 KiB limit")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _validate_user_input(raw: str | None) -> str | None:
    """Bound user-owned free text before forwarding it to the authoritative CLI."""
    if raw is None:
        return None
    if not raw.strip():
        raise ValueError("user input must not be empty")
    if len(raw.encode("utf-8")) > 65_536:
        raise ValueError("user input exceeds the 64 KiB limit")
    return raw


def run(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
    process_factory: Callable[..., Any] = subprocess.Popen,
) -> int:
    try:
        args = _parser().parse_args(argv)
        mode = args.manager_mode or args.legacy_manager_mode or "unattended"
        if (
            args.manager_mode is not None
            and args.legacy_manager_mode is not None
            and args.manager_mode != args.legacy_manager_mode
        ):
            raise ValueError("Manager and legacy Driver mode inputs conflict")
        if args.manager_mode is None and args.legacy_manager_mode is None:
            raise ValueError("--manager-mode is required")
        interpreter = _validated_host_interpreter()
        issue_name = _validate_identifier(args.issue, "issue name")
        playbook = _validate_identifier(args.playbook, "playbook name")
        project_root = Path(cwd or Path.cwd()).resolve()
        issue_dir = project_root / ".cafe" / "issues" / issue_name
        state = _prepared_workflow(issue_dir)
        if state["playbook_id"] != playbook:
            raise ValueError("requested playbook differs from the prepared workflow")
        locale_value, locale_source = effective_conversation_locale(issue_dir)
        conversation_locale = {"value": locale_value, "source": locale_source}
        workflow_id = state["workflow_id"]
        api = _role_api(issue_dir)
        request_type = ManagerEntryRequest if api is cafe.manager else api.DriverEntryRequest
        evaluate = evaluate_manager_entry if api is cafe.manager else api.evaluate_driver_entry
        entry = evaluate(
            request_type(
                issue_dir=issue_dir,
                issue_name=issue_name,
                workflow_id=workflow_id,
                fresh_facts=args.fresh_facts,
            )
        )
        if entry.freshness.value != Freshness.SAME_SEMANTICS.value:
            raise ValueError(f"Manager contract requires {entry.freshness.value} recovery")
        load = load_contract if api is cafe.manager else api._store.load_contract
        contract, digest = load(
            issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
        )
        if digest != entry.contract_sha256:
            raise ValueError("Manager contract changed during entry validation")
        manager = contract.get("manager", contract.get("driver"))
        confirmed_mode = manager.get("mode") if isinstance(manager, Mapping) else None
        if confirmed_mode != mode:
            raise ValueError("requested Manager mode differs from the confirmed contract")
        _validate_checkout(contract, project_root)
        continuation_count = sum(
            value is not None
            for value in (args.alignment_input, args.user_handoff, args.user_input)
        )
        if continuation_count > 1:
            raise ValueError("continuation inputs cannot be combined")
        alignment_input = _validate_alignment_input(
            args.alignment_input,
            issue_dir=issue_dir,
            state=state,
            contract=contract,
        )
        user_handoff = _validate_user_handoff_input(args.user_handoff)
        if user_handoff is not None and not _is_user_boundary(state):
            raise ValueError("explicit user handoff requires a current user-owned boundary")
        user_input = _validate_user_input(args.user_input)
        if user_input is not None and not _is_user_boundary(state):
            raise ValueError("explicit user input requires a current user-owned boundary")
        if mode == "event-driven":
            callback_script = _validate_event_binding(
                issue_dir=issue_dir,
                issue_name=issue_name,
                workflow_id=workflow_id,
                contract=contract,
                digest=digest,
                project_root=project_root,
            )
    except (OSError, ValueError) as exc:
        return _launch_failed(mode, str(exc))

    continuation_input = alignment_input or user_handoff or user_input
    if _is_user_boundary(state) and continuation_input is None:
        _emit_directive(
            mode=mode,
            action="await_user",
            worker="none",
            next_wake=["user_input"],
            guidance=(
                "Workflow is at a durable user-owned boundary. Do not launch or infer an answer."
            ),
            conversation_locale=conversation_locale,
        )
        return 0

    if mode == "event-driven":
        try:
            _validate_host_callback_transport(callback_script, issue_dir)
        except (OSError, ValueError) as exc:
            return _launch_failed(mode, str(exc))

    command = [
        interpreter,
        "-I",
        "-m",
        "cafe.ui.cli",
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
    if continuation_input is not None:
        command.extend(["--user-input", continuation_input])

    try:
        process = process_factory(
            command,
            cwd=str(project_root),
            env=_workflow_child_environment(),
        )
    except OSError as exc:
        return _launch_failed(mode, str(exc), worker=worker)

    if mode == "attached":
        interval = manager["poll_interval_seconds"]
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
            conversation_locale=conversation_locale,
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
            conversation_locale=conversation_locale,
        )
    elif mode == "event-driven":
        _emit_directive(
            mode=mode,
            action="yield",
            worker=worker,
            next_wake=["workflow_event_callback", "user_input"],
            guidance=(
                "Background workflow started successfully. End the current Manager turn now. Do not "
                "poll with sleep, ps, write_stdin, cafe status, or cafe task ls."
            ),
            conversation_locale=conversation_locale,
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
                conversation_locale=conversation_locale,
            )
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
