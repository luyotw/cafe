"""Long-running operation helper commands."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from cafe.core.blackboard import (
    LongRunningOperationState,
    OperationLogPolicy,
    OperationMonitoring,
    OperationRecoveryAction,
    OperationRecoveryActor,
    OperationRisk,
    validate_operation_decision,
)
from cafe.core.long_running_operation_helper import (
    get_operation_recovery_status,
    get_operation_status,
    recover_operation,
    run_operation_command,
)
from cafe.playbooks.loader import PlaybookLoader

operation_app = typer.Typer(help="Run or inspect one long-running workflow operation")
console = Console()


def _load_playbook(playbook: str) -> dict:
    return PlaybookLoader().load(playbook)


def _print_payload(payload: dict) -> None:
    console.print(json.dumps(payload, ensure_ascii=False, indent=2), soft_wrap=True)


def _workflow_retry_command(issue_dir: Path, playbook: str) -> str:
    return shlex.join(
        [
            "cafe",
            "workflow",
            "--issue",
            issue_dir.name,
            "--playbook",
            playbook,
            "--execute",
            "--single-step",
        ]
    )


def _recovery_command(
    *,
    issue_dir: Path,
    step: str,
    iteration_dir: Path,
    operation_id: str,
    playbook: str,
) -> str:
    return shlex.join(
        [
            "cafe",
            "operation",
            "recover",
            "--issue-dir",
            str(issue_dir),
            "--step",
            step,
            "--iteration-dir",
            str(iteration_dir),
            "--operation-id",
            operation_id,
            "--action",
            OperationRecoveryAction.RETRY_STEP.value,
            "--authorized-by",
            "HUMAN_OR_DRIVER",
            "--reason",
            "REASON_FOR_RETRY",
            "--playbook",
            playbook,
        ]
    )


@operation_app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run(
    ctx: typer.Context,
    issue_dir: Path = typer.Option(
        ...,
        "--issue-dir",
        help="Path to .cafe/issues/<issue> for the current workflow",
    ),
    step: str = typer.Option(..., "--step", help="Current workflow step"),
    iteration_dir: Path = typer.Option(
        ...,
        "--iteration-dir",
        help="Current step iteration directory",
    ),
    playbook: str = typer.Option("default", "--playbook", help="Playbook name"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Working directory for the command"),
    readable_root: Optional[list[Path]] = typer.Option(
        None, "--readable-root", help="Readable sandbox root; repeat as needed"
    ),
    writable_root: Optional[list[Path]] = typer.Option(
        None, "--writable-root", help="Writable sandbox root; repeat as needed"
    ),
    reason: str = typer.Option("operation_helper_launch", "--reason", help="Operation reason"),
    risk: OperationRisk = typer.Option(..., "--risk"),
    monitoring: OperationMonitoring = typer.Option(..., "--monitoring"),
    log_policy: OperationLogPolicy = typer.Option(..., "--log-policy"),
    stop_condition: str = typer.Option(..., "--stop-condition"),
    recovery: str = typer.Option(..., "--recovery"),
) -> None:
    """Launch one supervised command for the current iteration.

    Put the command after ``--``. If the iteration already has an operation,
    this prints that same operation id and does not launch a duplicate.
    """
    command = list(ctx.args)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        console.print("[red]Error: operation run requires a command after --[/red]")
        raise typer.Exit(2)
    try:
        validate_operation_decision(
            risk=risk,
            monitoring=monitoring,
            log_policy=log_policy,
            stop_condition=stop_condition,
            recovery=recovery,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    result = run_operation_command(
        issue_dir=issue_dir,
        step=step,
        iteration_dir=iteration_dir,
        command=command,
        cwd=cwd,
        readable_roots=readable_root,
        writable_roots=writable_root,
        playbook=_load_playbook(playbook),
        reason=reason,
        risk=risk,
        monitoring=monitoring,
        log_policy=log_policy,
        stop_condition=stop_condition,
        recovery=recovery,
    )
    _print_payload(
        {
            "operation_id": result.operation.operation_id,
            "state": result.operation.state.value,
            "reason": result.operation.reason,
            "exit_code": result.operation.exit_code,
            "started": result.started,
            "handle_path": str(result.handle_path),
        }
    )
    if not result.started and result.operation.state in {
        LongRunningOperationState.FAILED,
        LongRunningOperationState.LOST,
    }:
        raise typer.Exit(1)


@operation_app.command("status")
def status(
    issue_dir: Path = typer.Option(
        ...,
        "--issue-dir",
        help="Path to .cafe/issues/<issue> for the current workflow",
    ),
    step: str = typer.Option(..., "--step", help="Current workflow step"),
    iteration_dir: Path = typer.Option(
        ...,
        "--iteration-dir",
        help="Current step iteration directory",
    ),
    playbook: str = typer.Option("default", "--playbook", help="Playbook name"),
) -> None:
    """Inspect the existing operation without launching phase work again."""
    operation = get_operation_status(
        issue_dir=issue_dir,
        step=step,
        iteration_dir=iteration_dir,
        playbook=_load_playbook(playbook),
    )
    authorization = get_operation_recovery_status(
        issue_dir=issue_dir,
        step=step,
        iteration_dir=iteration_dir,
    )
    payload = operation.to_dict()
    if authorization is not None:
        payload["recovery_authorization"] = authorization.to_dict()
        payload["next_action"] = _workflow_retry_command(issue_dir, playbook)
    elif operation.state in {
        LongRunningOperationState.FAILED,
        LongRunningOperationState.LOST,
    }:
        payload["recovery_required"] = True
        payload["next_action"] = _recovery_command(
            issue_dir=issue_dir,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            playbook=playbook,
        )
    _print_payload(payload)


@operation_app.command("recover")
def recover(
    issue_dir: Path = typer.Option(
        ...,
        "--issue-dir",
        help="Path to .cafe/issues/<issue> for the current workflow",
    ),
    step: str = typer.Option(..., "--step", help="Current workflow step"),
    iteration_dir: Path = typer.Option(
        ...,
        "--iteration-dir",
        help="Iteration containing the terminal operation",
    ),
    operation_id: str = typer.Option(..., "--operation-id", help="Exact operation identity"),
    action: OperationRecoveryAction = typer.Option(..., "--action"),
    authorized_by: OperationRecoveryActor = typer.Option(..., "--authorized-by"),
    reason: str = typer.Option(..., "--reason", help="Audit reason for authorizing recovery"),
    playbook: str = typer.Option("default", "--playbook", help="Playbook name"),
) -> None:
    """Authorize the next normal workflow run to retry a failed/lost step."""
    try:
        result = recover_operation(
            issue_dir=issue_dir,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation_id,
            action=action,
            authorized_by=authorized_by,
            reason=reason,
            playbook=_load_playbook(playbook),
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(2) from exc
    _print_payload(
        {
            **result.authorization.to_dict(),
            "created": result.created,
            "recovery_path": str(result.recovery_path),
            "next_action": _workflow_retry_command(issue_dir, playbook),
        }
    )
