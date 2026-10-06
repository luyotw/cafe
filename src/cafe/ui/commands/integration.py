"""Public destination selection, read-only status and bounded proof inspection."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import typer

from cafe.catalogs.resolver import filesystem_project_roots
from cafe.core.blackboard import BlackboardStore
from cafe.core.integration import IntegrationService, integration_service
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides

integration_app = typer.Typer(
    help="Confirm a human integration destination and inspect delivery evidence"
)


def load_integration(issue: str) -> tuple[IntegrationService, dict]:
    """Read the same effective catalog as task completion; never initialize state."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", issue):
        raise ValueError("Use an issue directory name without path separators")
    issue_dir = Path.cwd() / ".cafe/issues" / issue
    board = BlackboardStore(issue_dir).load_read_only()
    playbook = PlaybookLoader(
        project_root=Path.cwd(),
        read_only=True,
        resolve_presentation=False,
        project_roots=filesystem_project_roots(Path.cwd()),
    ).load(board.playbook_id)
    playbook = apply_issue_playbook_overrides(playbook, issue_dir / "issue.yaml")
    service = integration_service(issue_dir, playbook, board)
    if service is None:
        raise ValueError(
            "This workflow has no integration declaration; review-only completion remains unchanged"
        )
    return service, playbook


def emit(value: dict, json_output: bool) -> None:
    # JSON is also the concise text representation; no state is inferred while rendering.
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2 if json_output else None))


def failure(exc: Exception, json_output: bool) -> None:
    emit(
        {
            "state": "unavailable",
            "reason": str(exc),
            "next_action": "Restore the workflow/selection prerequisite, then retry cafe integration status",
        },
        json_output,
    )
    raise typer.Exit(1)


@integration_app.command("status")
def status(
    issue: str = typer.Option(..., "--issue"), json_output: bool = typer.Option(False, "--json")
) -> None:
    """Read selection, task, report, proof and remaining action without inspection."""
    try:
        service, _ = load_integration(issue)
        emit(service.status(), json_output)
    except (OSError, ValueError) as exc:
        failure(exc, json_output)


@integration_app.command("select")
def select(
    issue: str = typer.Option(..., "--issue"),
    target: str = typer.Option(..., "--target"),
    repository: str = typer.Option(..., "--repository"),
    target_branch: str = typer.Option(..., "--target-branch"),
    feature_branch: Optional[str] = typer.Option(None, "--feature-branch"),
    pr: Optional[int] = typer.Option(None, "--pr"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Stage an explicit candidate and expose its separate human confirmation task."""
    try:
        service, playbook = load_integration(issue)
        if service.blackboard.current_step == "done":
            raise ValueError(
                "Completed delivery is immutable; start a new workflow for another destination"
            )
        revision = service.propose(
            target=target,
            repository=repository,
            target_branch=target_branch,
            feature_branch=feature_branch,
            pr=pr,
        )
        runtime = BlackboardWorkflowRuntime(
            issue_dir=service.issue_dir, playbook=playbook, executor=_no_agent_executor
        )
        runtime.run(start_step=service.declaration.selection_step)
        current, _ = load_integration(issue)
        emit({"revision": revision, "status": current.status()}, json_output)
    except (OSError, ValueError) as exc:
        failure(exc, json_output)


def _no_agent_executor(*args, **kwargs):
    raise ValueError(
        "Integration requires human work or read-only native verification, not an agent executor"
    )


@integration_app.command("verify")
def verify(
    issue: str = typer.Option(..., "--issue"), json_output: bool = typer.Option(False, "--json")
) -> None:
    """Observe and persist current destination proof; continue with cafe workflow."""
    try:
        service, _ = load_integration(issue)
        if service.blackboard.current_step == "done":
            if not service.completion_allowed(completed=True):
                raise ValueError("Stored completed integration association is invalid")
            emit({"status": service.status(), "already_completed": True}, json_output)
            return
        attempt = service.verify()
        emit({"attempt": attempt, "status": service.status()}, json_output)
        if not attempt["success"]:
            raise typer.Exit(1)
    except (OSError, ValueError) as exc:
        failure(exc, json_output)
