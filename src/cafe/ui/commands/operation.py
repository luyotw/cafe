"""Long-running operation helper commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from cafe.core.blackboard import OperationLogPolicy, OperationMonitoring, OperationRisk
from cafe.core.long_running_operation_helper import (
    get_operation_status,
    run_operation_command,
)
from cafe.playbooks.loader import PlaybookLoader

operation_app = typer.Typer(help="Run or inspect one long-running workflow operation")
console = Console()


def _load_playbook(playbook: str) -> dict:
    return PlaybookLoader().load(playbook)


def _print_payload(payload: dict) -> None:
    console.print(json.dumps(payload, ensure_ascii=False, indent=2))


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
    reason: str = typer.Option("operation_helper_launch", "--reason", help="Operation reason"),
    risk: OperationRisk = typer.Option(OperationRisk.LOW, "--risk"),
    monitoring: OperationMonitoring = typer.Option(OperationMonitoring.FINAL_ONLY, "--monitoring"),
    log_policy: OperationLogPolicy = typer.Option(OperationLogPolicy.SUMMARY_ONLY, "--log-policy"),
    stop_condition: str = typer.Option("", "--stop-condition"),
    recovery: str = typer.Option("", "--recovery"),
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

    result = run_operation_command(
        issue_dir=issue_dir,
        step=step,
        iteration_dir=iteration_dir,
        command=command,
        cwd=cwd,
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
            "started": result.started,
            "handle_path": str(result.handle_path),
        }
    )


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
    _print_payload(operation.to_dict())
