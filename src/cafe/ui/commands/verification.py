"""Commands for creating and checking workflow verification receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import typer

from cafe.verification import (
    VerificationReceiptError,
    check_verification_receipt,
    run_focused_verification,
    run_verification,
)

verification_app = typer.Typer(
    help="Create and validate test receipts tied to a clean Git HEAD."
)


@verification_app.command(
    name="run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def verification_run(
    ctx: typer.Context,
    output_file: Path = typer.Option(..., "--output-file"),
    scope: str = typer.Option("full", "--scope"),
) -> None:
    """Run a command after ``--`` and write an iteration-local receipt."""
    command: List[str] = list(ctx.args)
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        exit_code, receipt_path, payload = run_verification(
            output_file=output_file,
            command=command,
            scope=scope,
        )
    except VerificationReceiptError as exc:
        typer.echo(f"verification_error={exc}", err=True)
        raise typer.Exit(code=2)

    typer.echo(
        json.dumps(
            {
                "receipt": str(receipt_path),
                "valid": payload["valid"],
                "exit_code": payload["exit_code"],
                "scope": payload["scope"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if exit_code:
        raise typer.Exit(code=exit_code)


@verification_app.command(name="check")
def verification_check(
    output_file: Path = typer.Option(..., "--output-file"),
    required_scope: str = typer.Option("full", "--require-scope"),
) -> None:
    """Check that a receipt still covers the current clean Git HEAD."""
    try:
        result = check_verification_receipt(
            output_file=output_file,
            required_scope=required_scope,
        )
    except VerificationReceiptError as exc:
        typer.echo(f"verification_error={exc}", err=True)
        raise typer.Exit(code=2)

    typer.echo(
        json.dumps(
            {
                "command": (result.receipt or {}).get("command"),
                "cwd": ((result.receipt or {}).get("git") or {}).get("cwd"),
                "head": ((result.receipt or {}).get("git") or {}).get("head"),
                "receipt": str(result.receipt_path),
                "reasons": list(result.reasons),
                "scope": (result.receipt or {}).get("scope"),
                "valid": result.valid,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if not result.valid:
        raise typer.Exit(code=1)


@verification_app.command(
    name="focus",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def verification_focus(
    ctx: typer.Context,
    output_file: Path = typer.Option(..., "--output-file"),
) -> None:
    """Replay the verified command with focused selectors supplied after ``--``."""
    selectors: List[str] = list(ctx.args)
    if selectors[:1] == ["--"]:
        selectors = selectors[1:]
    try:
        exit_code, command = run_focused_verification(
            output_file=output_file,
            selectors=selectors,
        )
    except VerificationReceiptError as exc:
        typer.echo(f"verification_error={exc}", err=True)
        raise typer.Exit(code=2)

    typer.echo(
        json.dumps(
            {"command": command, "exit_code": exit_code, "focused": True},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if exit_code:
        raise typer.Exit(code=exit_code)
