"""Playbook and skill management command implementations extracted from cli.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import typer
import yaml
from rich.console import Console

from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook, format_dot, format_text_report
from cafe.skills.importer import SkillImportSummary, import_skills, preview_importable_skills
from cafe.skills.loader import SkillLoader
from cafe.skills.remover import SkillRemoveSummary, remove_skills
from cafe.ui.inquirer_prompts import prompt_checkbox, prompt_confirm  # noqa: F401 — kept for type resolution; actual calls go through cli for test-patch compat

# Late-import proxies for test-patch compatibility (tests patch cafe.ui.cli.prompt_confirm etc.)


def _cli_prompt_confirm(*a, **kw):
    from cafe.ui.cli import prompt_confirm as _fn
    return _fn(*a, **kw)


def _cli_prompt_checkbox(*a, **kw):
    from cafe.ui.cli import prompt_checkbox as _fn
    return _fn(*a, **kw)

# ---------------------------------------------------------------------------
# Typer sub-apps
# ---------------------------------------------------------------------------
playbook_app = typer.Typer(help="Inspect and validate playbooks")
skill_app = typer.Typer(help="Inspect and validate skills")

# ---------------------------------------------------------------------------
# Console and backward-compat runtime bridge
# ---------------------------------------------------------------------------
console = Console()


def set_runtime(runtime_globals: Dict[str, Any]) -> None:
    """No-op retained for backward compatibility.

    Runtime dependencies are now imported directly or defined locally.
    """


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _build_playbook_loader() -> PlaybookLoader:
    """Build playbook loader with cwd-based project root."""
    return PlaybookLoader(project_root=Path.cwd())


def _build_skill_loader() -> SkillLoader:
    """Build skill loader with cwd-based project root."""
    return SkillLoader(project_root=Path.cwd())


# ---------------------------------------------------------------------------
# Playbook commands
# ---------------------------------------------------------------------------

@playbook_app.command(name="list")
def playbook_list() -> None:
    """List resolved playbooks from builtin/global/project catalogs."""
    loader = _build_playbook_loader()
    for name in loader.list_playbooks():
        loaded = loader.load_model(name)
        console.print(f"{name}\t{loaded.source}\t{loaded.path}")


@playbook_app.command(name="show")
def playbook_show(
    name: str = typer.Argument(..., help="Playbook name"),
) -> None:
    """Show the resolved playbook definition."""
    try:
        loaded = _build_playbook_loader().load_model(name)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(yaml.dump(loaded.as_dict(), allow_unicode=True, default_flow_style=False, sort_keys=False))
    console.print(f"\n[dim]source={loaded.source} path={loaded.path}[/dim]")
    for warning in loaded.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


@playbook_app.command(name="validate")
def playbook_validate(
    name: str = typer.Argument(..., help="Playbook name"),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
) -> None:
    """Validate one playbook and print warnings if present."""
    try:
        loaded = _build_playbook_loader().load_model(name, strict=strict)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Valid[/green] {name} source={loaded.source}")
    if loaded.warnings:
        for warning in loaded.warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")


@playbook_app.command(name="simulate")
def playbook_simulate(
    name: str = typer.Argument(..., help="Playbook name"),
    dot: bool = typer.Option(False, "--dot", help="Append a DOT graph of transitions after the summary"),
) -> None:
    """Statically trace playbook transitions (read-only; no agents, hooks, or shell helpers)."""
    loader = _build_playbook_loader()
    try:
        loaded = loader.load_model(name, strict=False)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    try:
        result = analyze_playbook(loaded.model)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(format_text_report(result))
    if dot:
        console.print("")
        console.print(format_dot(result))


# ---------------------------------------------------------------------------
# Skill commands
# ---------------------------------------------------------------------------

@skill_app.command(name="list")
def skill_list() -> None:
    """List resolved skills from builtin/global/project catalogs."""
    try:
        items = _build_skill_loader().discover()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    for item in items:
        console.print(f"{item.name}\t{item.source}\t{item.directory}")


@skill_app.command(name="show")
def skill_show(
    name: str = typer.Argument(..., help="Skill name"),
) -> None:
    """Show resolved skill body and references path."""
    try:
        loader = _build_skill_loader()
        items = {item.name: item for item in loader.discover()}
        body = loader.activate(name)
        item = items[name]
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(body)
    console.print(f"\n[dim]source={item.source} path={item.directory}[/dim]")
    if item.warning:
        console.print(f"[yellow]warning:[/yellow] {item.warning}")


@skill_app.command(name="validate")
def skill_validate(
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
) -> None:
    """Validate all discovered skills."""
    try:
        items = _build_skill_loader().discover(strict=strict)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    warnings = [item.warning for item in items if item.warning]
    console.print(f"[green]Valid[/green] {len(items)} skill(s)")
    for warning in warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


def _print_skill_import_summary(summary: SkillImportSummary) -> None:
    """Print skill import result summary."""
    console.print(f"[green]Imported {summary.imported_count} skill(s)[/green]")
    if summary.skipped_count:
        console.print(f"[yellow]Skipped {summary.skipped_count} item(s)[/yellow]")
    if summary.failed_count:
        console.print(f"[red]Failed {summary.failed_count} item(s)[/red]")

    for item in summary.results:
        if item.status == "imported":
            reason_suffix = f" ({item.reason})" if item.reason else ""
            console.print(f"[green]imported:[/green] {item.name}{reason_suffix}")
        elif item.status == "skipped":
            console.print(f"[yellow]skipped:[/yellow] {item.name} ({item.reason})")
        else:
            console.print(f"[red]failed:[/red] {item.name} ({item.reason})")


def _print_skill_remove_summary(summary: SkillRemoveSummary) -> None:
    """Print skill removal result summary."""
    console.print(f"[green]Removed {summary.removed_count} skill(s)[/green]")
    if summary.skipped_count:
        console.print(f"[yellow]Skipped {summary.skipped_count} item(s)[/yellow]")
    if summary.failed_count:
        console.print(f"[red]Failed {summary.failed_count} item(s)[/red]")

    for item in summary.results:
        if item.status == "removed":
            console.print(f"[green]removed:[/green] {item.name}")
        elif item.status == "skipped":
            console.print(f"[yellow]skipped:[/yellow] {item.name} ({item.reason})")
        else:
            console.print(f"[red]failed:[/red] {item.name} ({item.reason})")


@skill_app.command(name="import")
def skill_import(
    path: str = typer.Argument(..., help="Directory containing one or more skill folders"),
) -> None:
    """Import skill folders into the current project's `.cafe/skills` directory."""
    try:
        skill_names = preview_importable_skills(Path(path))
        console.print(f"[yellow]Found {len(skill_names)} skill(s) to import:[/yellow]")
        for name in skill_names:
            console.print(f"  • {name}")
        console.print()

        if not _cli_prompt_confirm(
            f"Continue importing {len(skill_names)} skill(s)?",
            default=False,
        ):
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

        summary = import_skills(
            Path(path),
            Path.cwd(),
            overwrite_decider=lambda name, destination: _cli_prompt_confirm(
                f"Skill '{name}' already exists at '{destination}'. Overwrite?",
                default=False,
            ),
        )
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    _print_skill_import_summary(summary)


@skill_app.command(name="rm")
def skill_rm(
    names: Optional[list[str]] = typer.Argument(None, help="Names of skills to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Remove one or more project skills."""
    project_root = Path.cwd()
    skills_root = project_root / ".cafe" / "skills"

    try:
        if not names:
            available_skills = sorted(
                item.name for item in skills_root.iterdir()
                if item.is_dir() or item.is_symlink()
            ) if skills_root.exists() else []
            if not available_skills:
                console.print("[yellow]No project skills found[/yellow]")
                raise typer.Exit(0)

            selected = _cli_prompt_checkbox(
                message="Select skill(s) to delete: (Press space to select, enter to confirm)",
                choices=available_skills,
            )
            if not selected:
                console.print("[dim]Cancelled[/dim]")
                raise typer.Exit(0)
            names = selected
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    names = list(dict.fromkeys(names))
    existing_names = [
        name for name in names
        if (skills_root / name).exists() or (skills_root / name).is_symlink()
    ]

    if not existing_names:
        summary = remove_skills(names, project_root)
        _print_skill_remove_summary(summary)
        raise typer.Exit(1)

    if not force:
        console.print(f"[yellow]About to delete {len(existing_names)} skill(s):[/yellow]")
        for name in existing_names:
            console.print(f"  • {name} [dim]({skills_root / name})[/dim]")
        console.print()
        try:
            confirm = _cli_prompt_confirm(
                f"Are you sure you want to delete {len(existing_names)} skill(s)?",
                default=False,
            )
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled[/dim]")
            raise typer.Exit(0)
        if not confirm:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    summary = remove_skills(names, project_root)
    _print_skill_remove_summary(summary)

    if summary.failed_count:
        raise typer.Exit(1)

    if summary.removed_count == 0:
        raise typer.Exit(1)
