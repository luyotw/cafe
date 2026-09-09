"""Explicit user-owned lifecycle trust commands."""

import sys
from pathlib import Path

import typer

from cafe.core.lifecycle_trust import LifecycleTrustStore, declare_lifecycle_trust

trust_app = typer.Typer(help="Manage user-owned lifecycle script trust")


@trust_app.command("lifecycle")
def lifecycle(script: Path, stage: str = typer.Option(...), cwd: Path = typer.Option(...), write: list[Path] = typer.Option(...)) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise typer.Abort()
    if not typer.confirm(f"Trust {script.resolve()} for lifecycle stage {stage}?"):
        raise typer.Abort()
    declaration = declare_lifecycle_trust(LifecycleTrustStore(), script=script, stage=stage, cwd=cwd, writable_roots=tuple(write))
    typer.echo(declaration.id)


@trust_app.command("list")
def list_declarations() -> None:
    for declaration in LifecycleTrustStore().list():
        typer.echo(f"{declaration.id}\t{','.join(declaration.stages)}\t{declaration.script}")


@trust_app.command("revoke")
def revoke(declaration_id: str) -> None:
    if not LifecycleTrustStore().revoke(declaration_id):
        raise typer.Exit(1)
