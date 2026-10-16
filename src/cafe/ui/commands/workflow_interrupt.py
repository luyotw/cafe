"""Public exact-identity interruption, inspection and verified-stop recovery."""

from pathlib import Path
import json

import typer


def workflow_interrupt(
    issue: str = typer.Option(..., "--issue"),
    inspect: bool = typer.Option(False, "--inspect"),
    recover: bool = typer.Option(False, "--recover"),
    worker_id: str = typer.Option("", "--worker-id"),
    workflow_id: str = typer.Option("", "--workflow-id"),
    step: str = typer.Option("", "--step"),
    iteration: int = typer.Option(0, "--iteration"),
    reason: str = typer.Option("", "--reason"),
    feedback: str = typer.Option("", "--feedback"),
):
    """Stop an owned agent for same-step correction; explicit resume remains required."""
    from cafe.workflow_execution.intervention import (
        inspect_intervention,
        request_intervention,
        recover_intervention,
    )

    try:
        if not issue or Path(issue).name != issue or issue in {".", ".."}:
            raise ValueError("issue must be one issue name")
        directory = Path.cwd() / ".cafe/issues" / issue
        if not (directory / "blackboard.json").is_file():
            raise ValueError("workflow does not exist")
        identity = {
            "worker_id": worker_id,
            "workflow_id": workflow_id,
            "step": step,
            "iteration": iteration,
        }
        if inspect:
            if recover:
                raise ValueError("select either inspect or recover")
            result = inspect_intervention(directory)
        elif recover:
            result = recover_intervention(directory, identity)
        else:
            result = request_intervention(directory, identity, reason=reason, feedback=feedback)
        typer.echo(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(json.dumps({"status": "refused", "detail": str(exc)}, ensure_ascii=False))
        raise typer.Exit(2)
