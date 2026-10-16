"""Non-agent delivery entry, using the same approved snapshots and host adapters."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from cafe.delivery.runner import run_delivery
from cafe.utils.file_lock import advancement_execution_lock, WorkerAlreadyRunningError
from cafe.core.workspace_lock import workspace_execution_lock, WorkspaceLeaseBusyError

delivery_app = typer.Typer(help="Execute already-approved delivery actions without an agent.")


@delivery_app.command("run")
def run(
    issue: str = typer.Option(..., "--issue"),
    step: str = typer.Option(..., "--step"),
    iteration: int = typer.Option(1, "--iteration", min=1),
):
    """Run/reconcile the exact approved bundle, including its selected checks and issues."""
    root = Path.cwd().resolve()
    if Path(issue).name != issue or issue in {".", ".."}:
        raise typer.BadParameter("issue must be a literal issue name")
    issue_dir = root / ".cafe" / "issues" / issue
    try:
        with advancement_execution_lock(issue_dir) as acquired:
            if not acquired:
                raise ValueError("workflow is currently executing")
            with workspace_execution_lock(root, blocking=False):
                report, path = run_delivery(
                    root=root, issue_dir=issue_dir, step=step, iteration=iteration
                )
        typer.echo(json.dumps({"result": report, "receipt": str(path)}, ensure_ascii=False))
    except (
        OSError,
        ValueError,
        KeyError,
        WorkerAlreadyRunningError,
        WorkspaceLeaseBusyError,
    ) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
