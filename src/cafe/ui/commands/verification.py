"""Commands for optionally recording and checking command verification receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import typer

from cafe.verification import (
    VerificationReceiptError,
    check_verification_receipt,
    reuse_verification_receipt,
    run_focused_verification,
    run_verification,
    verification_log_excerpt,
)
from cafe.verification.profile_runner import check_profile, reuse_profile, run_profile
from cafe.verification.project import resolve_profile_request


def _emit_profile(payload: dict, path: Path) -> None:
    typer.echo(json.dumps({**payload, "receipt": str(path)}, ensure_ascii=False, sort_keys=True))


verification_app = typer.Typer(
    help="Optionally record and validate command receipts tied to a clean Git HEAD."
)


@verification_app.command(
    name="run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def verification_run(
    ctx: typer.Context,
    output_file: Path = typer.Option(..., "--output-file"),
    scope: str = typer.Option("full", "--scope"),
    profile_file: Optional[Path] = typer.Option(None, "--profile-file"),
    profile: Optional[str] = typer.Option(None, "--profile"),
    add_suite: List[str] = typer.Option([], "--add-suite"),
    reuse_from: Optional[Path] = typer.Option(None, "--reuse-from"),
) -> None:
    """Optionally run a command and write an iteration-local log and receipt."""
    command: List[str] = list(ctx.args)
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        request = resolve_profile_request(Path.cwd(), profile_file, profile, add_suite)
        if request is not None:
            if command or scope != "full":
                raise VerificationReceiptError(
                    "profile mode rejects replacement commands and narrowed scope"
                )
            exit_code, receipt_path, payload = run_profile(request, output_file, reuse_from)
            _emit_profile(payload, receipt_path)
            if exit_code:
                raise typer.Exit(code=exit_code if exit_code > 0 else 128 - exit_code)
            return
        if reuse_from is not None:
            raise VerificationReceiptError("--reuse-from requires a profile binding")
        exit_code, receipt_path, payload = run_verification(
            output_file=output_file,
            command=command,
            scope=scope,
        )
    except VerificationReceiptError as exc:
        typer.echo(f"verification_error={exc}", err=True)
        raise typer.Exit(code=2)

    excerpt, output_truncated = verification_log_excerpt(receipt_path)
    if excerpt:
        typer.echo(excerpt, err=bool(exit_code))
    output_log = payload["output_log"]
    typer.echo(
        json.dumps(
            {
                "receipt": str(receipt_path),
                "valid": payload["valid"],
                "exit_code": payload["exit_code"],
                "scope": payload["scope"],
                "output_log": str(receipt_path.parent / output_log["path"]),
                "output_bytes": output_log["size_bytes"],
                "output_truncated": output_truncated,
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
    profile_file: Optional[Path] = typer.Option(None, "--profile-file"),
    profile: Optional[str] = typer.Option(None, "--profile"),
    add_suite: List[str] = typer.Option([], "--add-suite"),
) -> None:
    """Check that a receipt still covers the current clean Git HEAD."""
    try:
        request = resolve_profile_request(Path.cwd(), profile_file, profile, add_suite)
        if request is not None:
            if required_scope != "full":
                raise VerificationReceiptError("profile mode requires full profile scope")
            result = check_profile(request, output_file)
            payload = dict(result.receipt or {})
            payload.update(
                valid=result.valid,
                reasons=list(result.reasons),
                status="passed" if result.valid else "incomplete",
            )
            _emit_profile(payload, result.receipt_path)
            if not result.valid:
                raise typer.Exit(code=1)
            return
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


@verification_app.command(name="reuse")
def verification_reuse(
    source_output_file: Path = typer.Option(..., "--source-output-file"),
    output_file: Path = typer.Option(..., "--output-file"),
    required_scope: str = typer.Option("full", "--require-scope"),
    profile_file: Optional[Path] = typer.Option(None, "--profile-file"),
    profile: Optional[str] = typer.Option(None, "--profile"),
    add_suite: List[str] = typer.Option([], "--add-suite"),
) -> None:
    """Reuse a still-valid receipt for a new iteration on the same clean HEAD."""
    try:
        request = resolve_profile_request(Path.cwd(), profile_file, profile, add_suite)
        if request is not None:
            if required_scope != "full":
                raise VerificationReceiptError("profile mode requires full profile scope")
            checked = check_profile(request, source_output_file, for_reuse=True)
            if not checked.valid:
                payload = dict(checked.receipt or {})
                payload.update(valid=False, status="incomplete", reasons=list(checked.reasons))
                _emit_profile(payload, checked.receipt_path)
                raise typer.Exit(code=1)
            receipt_path, payload = reuse_profile(request, source_output_file, output_file)
            _emit_profile(payload, receipt_path)
            return
        receipt_path, payload = reuse_verification_receipt(
            source_output_file=source_output_file,
            output_file=output_file,
            required_scope=required_scope,
        )
    except VerificationReceiptError as exc:
        typer.echo(f"verification_error={exc}", err=True)
        raise typer.Exit(code=2)

    typer.echo(
        json.dumps(
            {
                "receipt": str(receipt_path),
                "reused": True,
                "reused_from": payload["reused_from"],
                "scope": payload["scope"],
                "valid": payload["valid"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


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
