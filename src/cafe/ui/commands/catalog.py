"""Playbook and skill management command implementations extracted from cli.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, TypeVar

import typer
import yaml
from rich.console import Console

from cafe.catalogs.migration import AgentSnapshotMigrator
from cafe.catalogs.resolver import (
    MAX_CATALOG_OPERATION_ENTRIES,
    CatalogKind,
    CatalogOperationLimitError,
    CatalogResolver,
)
from cafe.catalogs.sync import CatalogSyncError, CatalogSyncService, ComparisonPage
from cafe.core.playbook import confirmation_gate_steps
from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook, format_dot, format_text_report
from cafe.skills.global_installer import GlobalSkillSyncSummary, sync_global_skills
from cafe.skills.importer import SkillImportSummary, import_skills, preview_importable_skills
from cafe.skills.loader import SkillLoader, canonical_skill_name
from cafe.skills.remover import SkillRemoveSummary, remove_skills
from cafe.ui.inquirer_prompts import (  # noqa: F401 — kept for type resolution; actual calls go through cli for test-patch compat
    prompt_checkbox,
    prompt_confirm,
)

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
catalog_app = typer.Typer(help="Compare and publish project CAFE catalogs")

_HUMAN_DETAIL_LIMIT = 50
_Detail = TypeVar("_Detail")


def _print_bounded_details(
    items: Sequence[_Detail],
    render: Callable[[_Detail], str],
    *,
    inspection_hint: str,
) -> None:
    """Print concise human details with a complete inspection path."""
    for item in items[:_HUMAN_DETAIL_LIMIT]:
        console.print(f"  {render(item)}")
    omitted = len(items) - _HUMAN_DETAIL_LIMIT
    if omitted > 0:
        console.print(f"  … {omitted} more; use {inspection_hint} to inspect")


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


def _build_catalog_service() -> CatalogSyncService:
    """Build the trusted catalog service for the active project view."""
    return CatalogSyncService(CatalogResolver(project_root=Path.cwd()))


def _parse_catalog_kinds(values: list[str]) -> list[CatalogKind]:
    if not values:
        return list(CatalogKind)
    try:
        kinds = [CatalogKind(value) for value in values]
    except ValueError as exc:
        raise CatalogSyncError(
            "Catalog kind must be one of: playbook, phase, agent"
        ) from exc
    if len(set(kinds)) != len(kinds):
        raise CatalogSyncError("Duplicate --kind values are not allowed")
    return kinds


def _print_catalog_report(report) -> None:
    """Print a bounded human report while retaining complete JSON output separately."""
    differences = report.differences
    if not differences:
        return
    console.print(
        f"[yellow]{len(differences)} project catalog difference(s)[/yellow] "
        f"token={report.token}"
    )
    _print_bounded_details(
        differences,
        lambda item: (
            f"{item.entry_id}\t{item.reason}\t"
            f"{item.project_digest[:12]} → {item.global_digest[:12]}"
        ),
        inspection_hint="--json or --entry",
    )


def _report_catalog_operation_limit(
    error: CatalogOperationLimitError,
    *,
    json_output: bool,
    page: Optional[ComparisonPage] = None,
) -> None:
    if json_output:
        payload = (
            page.as_dict()
            if page is not None
            else {
                "schema_version": 1,
                "status": "over_budget",
                "entry_limit": error.limit,
            }
        )
        typer.echo(json.dumps(payload, sort_keys=True))
    else:
        console.print(f"[red]Error: {error}[/red]")
        if page is not None:
            _print_bounded_details(
                page.affected_entry_ids,
                str,
                inspection_hint="--json",
            )
            if page.next_cursor is not None:
                console.print(
                    "  Continue with "
                    f"[bold]--after-entry {page.next_cursor}[/bold]"
                )


@catalog_app.command(name="check")
def catalog_check(
    kinds: list[str] = typer.Option(
        [], "--kind", help="Catalog kind; repeat for playbook, phase, or agent"
    ),
    entries: list[str] = typer.Option(
        [], "--entry", help="Stable entry ID; repeat to limit the comparison"
    ),
    after_entry: Optional[str] = typer.Option(
        None,
        "--after-entry",
        help="Continue bounded over-budget discovery after this stable entry ID",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit complete machine JSON"),
) -> None:
    """Compare intentional project entries with their Global destinations."""
    service: Optional[CatalogSyncService] = None
    selected_kinds: list[CatalogKind] = []
    try:
        selected_kinds = _parse_catalog_kinds(kinds)
        if after_entry is not None and entries:
            raise CatalogSyncError("--after-entry cannot be combined with --entry")
        service = _build_catalog_service()
        if after_entry is not None:
            page = service.compare_page(
                kinds=selected_kinds,
                after_entry_id=after_entry,
            )
            _report_catalog_operation_limit(
                CatalogOperationLimitError(MAX_CATALOG_OPERATION_ENTRIES),
                json_output=json_output,
                page=page,
            )
            raise typer.Exit(1)
        report = service.compare(
            kinds=selected_kinds,
            entry_ids=entries or None,
        )
    except CatalogOperationLimitError as exc:
        page = (
            service.compare_page(kinds=selected_kinds)
            if service is not None
            else None
        )
        _report_catalog_operation_limit(exc, json_output=json_output, page=page)
        raise typer.Exit(1)
    except (CatalogSyncError, OSError, ValueError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)
    if json_output:
        typer.echo(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        _print_catalog_report(report)


@catalog_app.command(name="sync-global")
def catalog_sync_global(
    kinds: list[str] = typer.Option(
        [], "--kind", help="Catalog kind; repeat for playbook, phase, or agent"
    ),
    entries: list[str] = typer.Option(
        [], "--entry", help="Stable entry ID; repeat to limit the comparison"
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="Exact token returned by catalog check"
    ),
    approved: list[str] = typer.Option(
        [], "--approve", help="Approved entry ID; repeat for an exact selection"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit complete machine JSON"),
) -> None:
    """Publish an explicitly approved project selection to Global settings."""
    try:
        selected_kinds = _parse_catalog_kinds(kinds)
        service = _build_catalog_service()
        if token is not None or approved:
            if token is None or not approved:
                raise CatalogSyncError(
                    "Non-interactive publication requires --token and at least one --approve"
                )
            selected = approved
            comparison_token = token
        else:
            report = service.compare(
                kinds=selected_kinds,
                entry_ids=entries or None,
            )
            _print_catalog_report(report)
            if not report.differences:
                return
            selected = _cli_prompt_checkbox(
                message="Select project catalog entries to publish to Global:",
                choices=[item.entry_id for item in report.differences],
            )
            if not selected:
                console.print("[dim]Cancelled[/dim]")
                return
            if not _cli_prompt_confirm(
                f"Publish {len(selected)} entry(s) approved by token {report.token[:12]}?",
                default=False,
            ):
                console.print("[dim]Cancelled[/dim]")
                return
            comparison_token = report.token
        result = service.sync(
            comparison_token,
            selected,
            kinds=selected_kinds,
            entry_ids=entries or None,
        )
    except CatalogOperationLimitError as exc:
        _report_catalog_operation_limit(exc, json_output=json_output)
        raise typer.Exit(1)
    except (CatalogSyncError, OSError, ValueError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)
    if json_output:
        typer.echo(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        console.print(f"[green]Published {len(result.updated)} catalog entry(s)[/green]")
        _print_bounded_details(
            result.updated,
            str,
            inspection_hint="--json",
        )


@catalog_app.command(name="migrate-agents")
def catalog_migrate_agents(
    token: Optional[str] = typer.Option(
        None, "--token", help="Exact token returned by migration preview"
    ),
    decisions: list[str] = typer.Option(
        [],
        "--decision",
        help="Digest-bound ENTRY_ID=preserve|retire decision; repeat for every entry",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit complete machine JSON"),
) -> None:
    """Preview or apply conservative legacy project-agent migration decisions."""
    try:
        resolver = CatalogResolver(project_root=Path.cwd())
        migrator = AgentSnapshotMigrator(resolver)
        if token is None and not decisions:
            preview = migrator.preview()
            payload = {
                "schema_version": 1,
                "status": "preview",
                "token": preview.token,
                "items": [
                    {
                        "entry_id": item.entry_id,
                        "path": str(item.path),
                        "digest": item.digest,
                        "fallback_digest": item.fallback_digest,
                        "classification": item.status,
                        "effect": item.effect,
                    }
                    for item in preview.items
                ],
            }
            if json_output:
                typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            else:
                console.print(
                    f"[yellow]{len(preview.items)} legacy project agent(s)[/yellow] "
                    f"token={preview.token}"
                )
                _print_bounded_details(
                    preview.items,
                    lambda item: f"{item.entry_id}\t{item.status}\t{item.effect}",
                    inspection_hint="--json",
                )
            return
        if token is None or not decisions:
            raise CatalogSyncError(
                "Migration apply requires --token and one --decision for every preview item"
            )
        parsed: dict[str, str] = {}
        for decision in decisions:
            if "=" not in decision:
                raise CatalogSyncError(f"Invalid migration decision: {decision!r}")
            entry_id, action = decision.rsplit("=", 1)
            if entry_id in parsed:
                raise CatalogSyncError(f"Duplicate migration decision: {entry_id}")
            parsed[entry_id] = action
        result = migrator.apply(token, parsed)
        payload = {
            "schema_version": 1,
            "status": "completed",
            "retired": [str(path) for path in result.retired],
            "preserved": [str(path) for path in result.preserved],
            "manifest": str(result.manifest),
        }
    except (CatalogSyncError, OSError, ValueError, RuntimeError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        console.print(f"[green]Migration completed[/green] manifest={result.manifest}")


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

    console.print(
        yaml.dump(
            loaded.as_dict(),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    )
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


@playbook_app.command(name="confirmation-gates")
def playbook_confirmation_gates(
    name: str = typer.Argument(..., help="Playbook name"),
) -> None:
    """List planned user confirmation candidates declared by a playbook."""
    try:
        loaded = _build_playbook_loader().load_model(name)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    gates = confirmation_gate_steps(loaded.model)
    console.print(f"Playbook: {loaded.model.playbook.id}")
    console.print(f"Conversation locale: {loaded.model.playbook.conversation_locale}")
    console.print("Confirmation gates (steps declaring on.confirm_output):")
    if gates:
        for step_name in gates:
            console.print(f"  - {step_name}")
    else:
        console.print("  (none)")
    console.print(
        "[dim]Reactive clarification, permission, and alignment pauses are not "
        "kickoff confirmation candidates.[/dim]"
    )


@playbook_app.command(name="simulate")
def playbook_simulate(
    name: str = typer.Argument(..., help="Playbook name"),
    dot: bool = typer.Option(
        False,
        "--dot",
        help="Append a DOT graph of transitions after the summary",
    ),
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
        item = items.get(name) or items[canonical_skill_name(name)]
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


def _print_global_skill_sync_summary(summary: GlobalSkillSyncSummary) -> None:
    """Print user-level CLI skill installation and update results."""
    if not summary.results:
        console.print(
            "[yellow]No supported CLI agents detected. Use --cli to target one explicitly.[/yellow]"
        )
        return
    console.print(
        f"[green]Synced {len(summary.results)} installation(s)[/green]: "
        f"{summary.installed_count} installed, {summary.updated_count} updated, "
        f"{summary.unchanged_count} unchanged"
    )
    if summary.failed_count:
        console.print(f"[red]{summary.failed_count} failed[/red]")

    for item in summary.results:
        if item.status == "failed":
            console.print(
                f"[red]failed:[/red] {item.cli}/{item.skill} -> "
                f"{item.destination} ({item.reason})"
            )
        else:
            style = "dim" if item.status == "unchanged" else "green"
            console.print(
                f"[{style}]{item.status}:[/{style}] "
                f"{item.cli}/{item.skill} -> {item.destination}"
            )


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


@skill_app.command(name="sync-global")
def skill_sync_global(
    skills: Optional[list[str]] = typer.Argument(
        None,
        help="Bundled skill names; defaults to the CAFE workflow helper skills",
    ),
    cli_names: Optional[list[str]] = typer.Option(
        None,
        "--cli",
        help=(
            "Target CLI instead of auto-detection; repeat for multiple "
            "(claude, codex, copilot, cursor, gemini)"
        ),
    ),
) -> None:
    """Install helper skills for detected CLIs or explicit --cli targets."""
    try:
        summary = sync_global_skills(
            skill_names=skills or None,
            cli_names=cli_names or None,
        )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    _print_global_skill_sync_summary(summary)
    if summary.failed_count:
        raise typer.Exit(1)


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
