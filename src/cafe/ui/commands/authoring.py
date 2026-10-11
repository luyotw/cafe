"""Thin noninteractive adapters for the shared source-authoring library."""

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from cafe.authoring import apply, decode_request, prepare
from cafe.authoring.results import Result

phase_app = typer.Typer(help="Author phase source contracts")


def run(spec, dry_run, apply_mode, format, expect_change, kind):
    if dry_run == apply_mode or format not in {"json", "text"}:
        raise typer.BadParameter(
            "Select exactly one of --dry-run or --apply and --format text|json"
        )
    if expect_change and not apply_mode:
        raise typer.BadParameter("--expect-change requires --apply")
    try:
        text = sys.stdin.read() if spec == "-" else Path(spec).read_text()
        request = decode_request(text)
        if (kind == "phase") != str(request.get("target", "")).endswith("/SKILL.md"):
            raise ValueError("Request target does not match author command")
        result = apply(request, expect_change=expect_change) if apply_mode else prepare(request)
    except Exception as error:
        result = Result(operation="apply" if apply_mode else "prepare", status="rejected")
        result.diagnose("invalid_request", str(error))
    if format == "json":
        typer.echo(json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=False))
    else:
        typer.echo(f"{result.status}: {result.change_digest}")
        if result.diff:
            typer.echo(result.diff, nl=False)
        for diagnostic in result.diagnostics:
            typer.echo(f"{diagnostic['severity']} {diagnostic['code']}: {diagnostic['message']}")
        typer.echo(
            json.dumps(
                {
                    "proposals": result.proposals,
                    "artifact_summary": result.artifact_summary,
                    "confirmation_gates": result.confirmation_gates,
                    "simulation": result.simulation,
                },
                sort_keys=True,
            )
        )
    if result.status == "rejected":
        raise typer.Exit(1)


@phase_app.command("phase")
def phase_author(
    spec: str = typer.Option(..., "--spec"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    apply_mode: bool = typer.Option(False, "--apply"),
    format: str = typer.Option("text", "--format"),
    expect_change: Optional[str] = typer.Option(None, "--expect-change"),
):
    run(spec, dry_run, apply_mode, format, expect_change, "phase")


def playbook_author(
    spec: str = typer.Option(..., "--spec"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    apply_mode: bool = typer.Option(False, "--apply"),
    format: str = typer.Option("text", "--format"),
    expect_change: Optional[str] = typer.Option(None, "--expect-change"),
):
    run(spec, dry_run, apply_mode, format, expect_change, "playbook")
