"""Repository task inbox command group."""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from cafe.core.blackboard import BlackboardStore
from cafe.core.capability_approvals import CapabilityApprovalError
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.human_tasks import HumanTaskPolicy
from cafe.core.task_inbox import TaskInboxError, TaskInboxService
from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides
from cafe.ui.commands import workflow as workflow_commands
from cafe.ui.human_tasks import (
    apply_capability_approval_payload,
    apply_capability_cancellation,
    apply_human_task_payload,
    collect_human_task_payload,
    durable_task_matches_current_handoff,
)

task_app = typer.Typer(help="List, inspect, and complete durable repository tasks")
console = Console()


def _render_capability_approval(approval: dict[str, Any]) -> None:
    """Render the exact reviewed effects without exposing credential values."""
    effects = approval.get("effects")
    effects = effects if isinstance(effects, dict) else {}
    rows = (
        ("Capability", approval.get("capability")),
        ("Risk", approval.get("risk")),
        ("Arguments", approval.get("argument_summary")),
        ("Writes", effects.get("writes")),
        ("Network destinations", effects.get("network_destinations")),
        ("Credential names", approval.get("credentials")),
        ("Permissions", approval.get("permissions")),
        ("Expected outputs", approval.get("expected_outputs")),
    )
    table = Table(title="Capability approval: exact reviewed request")
    table.add_column("Field")
    table.add_column("Reviewed value")
    for label, value in rows:
        table.add_row(label, json.dumps(value, ensure_ascii=False, sort_keys=True))
    console.print(table)


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


def _resume_issue_workflow(issue: str, playbook: str) -> None:
    """Run the exact owning issue without consulting or changing the active marker."""
    workflow_commands.workflow(
        playbook=playbook,
        issue=issue,
        start_step=None,
        single_step=False,
        background=False,
        dry_run=False,
        user_input=None,
        add_dir=[],
    )


def _load_result(
    result: Optional[str], result_file: Optional[Path]
) -> Optional[str | dict[str, object]]:
    if result is not None and result_file is not None:
        raise TaskInboxError(
            "invalid_response",
            "Use either --result or --result-file, not both.",
            recovery="Choose one non-interactive response source and retry.",
        )
    raw = result
    if result_file is not None:
        try:
            raw = result_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise TaskInboxError(
                "invalid_response",
                f"Cannot read result file: {exc}",
                recovery="Provide a readable UTF-8 JSON result file.",
            ) from exc
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TaskInboxError(
            "invalid_response",
            f"Result is not valid JSON: {exc.msg}.",
            recovery="Submit a JSON object matching the task's expected result.",
        ) from exc
    if not isinstance(payload, (dict, str)):
        raise TaskInboxError(
            "invalid_response",
            "Result JSON must be an object or string.",
            recovery="Inspect the task and submit its declared response shape.",
        )
    return payload


@task_app.command("ls")
def list_tasks(
    status: Optional[list[str]] = typer.Option(
        None,
        "--status",
        help=(
            "Task status; repeat to include multiple statuses "
            "(combined with other filters using AND)"
        ),
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
    if detail.capability_approval is not None:
        _render_capability_approval(detail.capability_approval)
    console.print("Expected result:")
    console.print_json(data=detail.expected_result)
    console.print("Continuations:")
    console.print_json(data=detail.continuations)


@task_app.command("complete")
def complete_task(
    task_id: str = typer.Argument(..., help="Stable task identifier"),
    result: Optional[str] = typer.Option(None, "--result", help="Non-interactive JSON response"),
    result_file: Optional[Path] = typer.Option(
        None, "--result-file", help="Read a non-interactive JSON response from a file"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result object"),
) -> None:
    """Complete one pending task and resume its owning workflow."""
    service = TaskInboxService(Path(".cafe"))
    try:
        preflight = service.preflight_completion(task_id)
        raw_payload = _load_result(result, result_file)
        applied: Any
        if preflight.task.capability_approval is not None:
            approval = dict(preflight.task.capability_approval)
            if raw_payload is None:
                _render_capability_approval(approval)
                decision = typer.prompt("Capability decision (approve/deny)").strip().lower()
                raw_payload = {
                    "decision": decision,
                    "workflow_id": preflight.workflow_id,
                    "task_id": task_id,
                    "request_fingerprint": approval["fingerprint"],
                    "correlation_id": approval["correlation_id"],
                }
            try:
                blackboard = BlackboardStore(preflight.issue_dir).load_or_create(
                    preflight.task.step, playbook_id=preflight.playbook_id
                )
                applied = apply_capability_approval_payload(
                    issue_dir=preflight.issue_dir,
                    blackboard=blackboard,
                    task=preflight.task,
                    raw_payload=raw_payload,
                )
            except CapabilityApprovalError as exc:
                raise TaskInboxError(
                    "invalid_response",
                    str(exc),
                    recovery=(
                        "Inspect capability_approval and submit an exact structured "
                        "approve or deny decision."
                    ),
                    task_id=task_id,
                    issue=preflight.issue,
                    workflow_id=preflight.workflow_id,
                ) from exc
        elif raw_payload is None:
            try:
                policy = HumanTaskPolicy.model_validate(preflight.task.expected_result)
            except (TypeError, ValueError) as exc:
                raise TaskInboxError(
                    "corrupt_task",
                    f"Task {task_id} has an invalid response contract: {exc}",
                    recovery="Repair the task's declared expected result before retrying.",
                    task_id=task_id,
                    issue=preflight.issue,
                    workflow_id=preflight.workflow_id,
                ) from exc
            raw_payload = collect_human_task_payload(policy)
        if preflight.task.capability_approval is None and isinstance(raw_payload, dict):
            raw_payload = dict(raw_payload)
            raw_payload.setdefault("task", preflight.task.policy_id)
            raw_payload["human_task_id"] = task_id
        assert raw_payload is not None

        if preflight.task.capability_approval is None:
            # Reload immediately before the existing locked validator/mutator so a
            # stale concurrent completion cannot proceed on old ownership evidence.
            preflight = service.preflight_completion(task_id)
            playbook_data = PlaybookLoader(project_root=Path.cwd()).load(preflight.playbook_id)
            playbook_data = apply_issue_playbook_overrides(
                playbook_data, preflight.issue_dir / "issue.yaml"
            )
            blackboard = BlackboardStore(preflight.issue_dir).load_or_create(
                preflight.task.step, playbook_id=preflight.playbook_id
            )
            applied = apply_human_task_payload(
                issue_dir=preflight.issue_dir,
                playbook_data=playbook_data,
                blackboard=blackboard,
                from_step=preflight.task.step,
                trigger=preflight.task.trigger,
                raw_payload=raw_payload,
                source="command"
                if result is not None or result_file is not None
                else "interactive",
            )
            if applied.rejection is not None or applied.target is None:
                message = (
                    applied.rejection.message
                    if applied.rejection is not None
                    else "The response did not select a continuation."
                )
                raise TaskInboxError(
                    "invalid_response",
                    message,
                    recovery="Inspect the expected result and submit one declared response.",
                    task_id=task_id,
                    issue=preflight.issue,
                    workflow_id=preflight.workflow_id,
                )
        try:
            if json_output:
                # The workflow runner is historically stdout-oriented. Capture its
                # presentation output so the task command retains a one-document
                # stdout contract; durable workflow files remain the progress log.
                with redirect_stdout(StringIO()):
                    _resume_issue_workflow(preflight.issue, preflight.playbook_id)
            else:
                _resume_issue_workflow(preflight.issue, preflight.playbook_id)
        except (OSError, ValueError, RuntimeError, typer.Exit) as exc:
            raise TaskInboxError(
                "workflow_resume_failed",
                f"Task {task_id} was completed, but its owning workflow could not resume: {exc}",
                recovery=(
                    f"Run `cafe workflow --issue {preflight.issue} --playbook "
                    f"{preflight.playbook_id} --execute` to continue the owning workflow; "
                    "do not complete the task again."
                ),
                task_id=task_id,
                issue=preflight.issue,
                workflow_id=preflight.workflow_id,
            ) from exc
        detail = service.inspect(task_id)
    except TaskInboxError as exc:
        _fail("complete", exc, json_output)
    except (OSError, ValueError, RuntimeError, typer.Exit) as exc:
        _fail(
            "complete",
            TaskInboxError(
                "workflow_unavailable",
                f"The owning workflow cannot be resumed: {exc}",
                recovery="Restore the issue playbook/workflow metadata, then retry the exact task.",
                task_id=task_id,
            ),
            json_output,
        )
    if json_output:
        _emit_json(
            _envelope(
                "complete",
                data={
                    "task": detail.to_dict(),
                    "workflow": {
                        "issue": preflight.issue,
                        "id": preflight.workflow_id,
                        "playbook": preflight.playbook_id,
                        "continuation": applied.target,
                    },
                },
            )
        )
        return
    console.print(
        f"[green]Completed[/green] task {task_id}; resumed issue {preflight.issue} "
        f"at {applied.target}."
    )


@task_app.command("cancel")
def cancel_task(
    task_id: str = typer.Argument(..., help="Stable task identifier"),
    reason: str = typer.Option(..., "--reason", help="Why the task was cancelled"),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result object"),
) -> None:
    """Cancel one capability approval or an exact stale ordinary task."""
    service = TaskInboxService(Path(".cafe"))
    try:
        preflight = service.preflight_completion(task_id)
        blackboard = BlackboardStore(preflight.issue_dir).load_or_create(
            preflight.task.step, playbook_id=preflight.playbook_id
        )
        applied: Any = None
        if preflight.task.capability_approval is not None:
            applied = apply_capability_cancellation(
                issue_dir=preflight.issue_dir,
                blackboard=blackboard,
                task=preflight.task,
                reason=reason,
            )
            if json_output:
                with redirect_stdout(StringIO()):
                    _resume_issue_workflow(preflight.issue, preflight.playbook_id)
            else:
                _resume_issue_workflow(preflight.issue, preflight.playbook_id)
        else:
            if durable_task_matches_current_handoff(preflight.task, blackboard):
                raise TaskInboxError(
                    "active_task",
                    "The current ordinary task must be completed with its declared response.",
                    recovery=(
                        "Complete the current task, or use an explicit workflow start "
                        "to replace it."
                    ),
                    task_id=task_id,
                    issue=preflight.issue,
                    workflow_id=preflight.workflow_id,
                )
            HumanTaskRecordStore(preflight.issue_dir).cancel(
                workflow_id=preflight.workflow_id,
                task_id=task_id,
                reason=reason,
            )
            BlackboardStore(preflight.issue_dir).record_event(
                blackboard,
                "stale_human_task_cancelled",
                {"task_id": task_id, "reason": reason},
            )
        detail = service.inspect(task_id)
    except TaskInboxError as exc:
        _fail("cancel", exc, json_output)
    except (CapabilityApprovalError, OSError, ValueError, RuntimeError, typer.Exit) as exc:
        _fail(
            "cancel",
            TaskInboxError(
                "workflow_unavailable",
                f"The task could not be cancelled safely: {exc}",
                recovery="Inspect the exact task state and retry if it is still pending.",
                task_id=task_id,
            ),
            json_output,
        )
    if json_output:
        _emit_json(
            _envelope(
                "cancel",
                data={
                    "task": detail.to_dict(),
                    "workflow": {
                        "issue": preflight.issue,
                        "id": preflight.workflow_id,
                        "playbook": preflight.playbook_id,
                        "continuation": applied.target if applied is not None else None,
                    },
                },
            )
        )
        return
    if applied is not None:
        console.print(
            f"[yellow]Cancelled[/yellow] capability task {task_id}; resumed issue "
            f"{preflight.issue} at {applied.target}."
        )
    else:
        console.print(
            f"[yellow]Cancelled[/yellow] stale task {task_id}; "
            f"issue {preflight.issue} remains at {blackboard.current_step}."
        )
