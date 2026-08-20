"""Repository task inbox command group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from cafe.core.task_inbox import TaskInboxError, TaskInboxService


task_app = typer.Typer(help="List, inspect, and complete durable repository tasks")
console = Console()


def _envelope(
    operation: str,
    *,
    data: Optional[dict] = None,
    error: Optional[TaskInboxError] = None,
) -> dict:
    return {
        "ok": error is None,
        "operation": operation,
        "data": data if error is None else None,
        "error": error.to_dict() if error is not None else None,
    }


def _emit_json(payload: dict) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _fail(operation: str, error: TaskInboxError, json_output: bool) -> None:
    if json_output:
        _emit_json(_envelope(operation, error=error))
    else:
        console.print(f"[red]Error ({error.code}):[/red] {error.message}")
        console.print(f"[dim]Next action: {error.recovery}[/dim]")
    raise typer.Exit(1)


@task_app.command("ls")
def list_tasks(
    status: Optional[list[str]] = typer.Option(
        None,
        "--status",
        help="Task status; repeat to include multiple statuses (combined with other filters using AND)",
    ),
    assignee: Optional[str] = typer.Option(None, "--assignee", help="Exact assignee id"),
    workflow: Optional[str] = typer.Option(
        None, "--workflow", help="Exact workflow id or owning issue name"
    ),
    step: Optional[str] = typer.Option(None, "--step", help="Exact originating step"),
    due_state: Optional[str] = typer.Option(
        None, "--due-state", help="Existing due state (currently: unscheduled)"
    ),
    historical: bool = typer.Option(
        False,
        "--historical",
        help="Include completed, cancelled, and configuration-error tasks",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result object"),
) -> None:
    """List repository tasks; pending tasks are shown by default."""
    try:
        tasks = TaskInboxService(Path(".cafe")).list_tasks(
            statuses=set(status) if status else None,
            assignee=assignee,
            workflow=workflow,
            step=step,
            due_state=due_state,
            include_historical=historical,
        )
    except TaskInboxError as exc:
        _fail("list", exc, json_output)
    if json_output:
        _emit_json(
            _envelope(
                "list",
                data={"tasks": [task.to_dict() for task in tasks], "count": len(tasks)},
            )
        )
        return
    table = Table(title="Repository tasks")
    for heading in ("ID", "Issue", "Step", "Status", "Assignee", "Due"):
        table.add_column(heading)
    for task in tasks:
        table.add_row(
            task.id,
            task.issue,
            task.step,
            task.status,
            task.assignee_id or task.assignee_type,
            task.due_state,
        )
    console.print(table)
    console.print(f"[dim]Total: {len(tasks)} task(s)[/dim]")


@task_app.command("inspect")
def inspect_task(
    task_id: str = typer.Argument(..., help="Stable task identifier"),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result object"),
) -> None:
    """Inspect one task's response contract, provenance, and continuation."""
    try:
        detail = TaskInboxService(Path(".cafe")).inspect(task_id)
    except TaskInboxError as exc:
        _fail("inspect", exc, json_output)
    if json_output:
        _emit_json(_envelope("inspect", data={"task": detail.to_dict()}))
        return
    console.print(f"[bold]Task[/bold] {detail.id}")
    console.print(f"Issue: {detail.issue}  Workflow: {detail.workflow_id}")
    console.print(f"Origin: {detail.step} iteration {detail.iteration} ({detail.trigger})")
    console.print(f"Status: {detail.status}  Due: {detail.due_state}")
    console.print(f"Assignment: {detail.assignment['type']} / {detail.assignment['id'] or '-'}")
    console.print(f"Prompt: {detail.prompt}")
    console.print("Expected result:")
    console.print_json(data=detail.expected_result)
    console.print("Continuations:")
    console.print_json(data=detail.continuations)


@task_app.command("complete")
def complete_task_placeholder(
    task_id: str = typer.Argument(..., help="Stable task identifier"),
) -> None:
    """Complete one pending task and resume its owning workflow."""
    raise TaskInboxError(
        "completion_unavailable",
        f"Completion for task {task_id} is not available in this build.",
        recovery="Use task inspection while completion support is installed.",
        task_id=task_id,
    )
