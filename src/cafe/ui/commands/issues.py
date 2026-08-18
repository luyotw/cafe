"""Issue/config command implementations extracted from cli.py."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import typer
import yaml
from rich.console import Console

from cafe.ui.commands import lifecycle as lifecycle_commands
from cafe.ui.cli_shared import _load_issue_step_names
from cafe.ui.inquirer_prompts import prompt_confirm, prompt_text  # noqa: F401 — kept for type resolution; actual calls go through cli for test-patch compat
from cafe.utils.config import ConfigError, ConfigManager

ALL_PHASES = ["spec", "plan", "develop", "review", "pr"]
console = Console()


# Late-import proxies for test-patch compatibility (tests patch cafe.ui.cli.prompt_confirm etc.)


def _cli_prompt_confirm(*a, **kw):
    from cafe.ui.cli import prompt_confirm as _fn
    return _fn(*a, **kw)


def _cli_prompt_text(*a, **kw):
    from cafe.ui.cli import prompt_text as _fn
    return _fn(*a, **kw)


def set_runtime(runtime_globals: Dict[str, Any]) -> None:
    """No-op retained for backward compatibility.

    Runtime dependencies are now imported directly or defined locally.
    """


def config(
    action: Optional[str] = typer.Argument(
        None, help="Action: set, get, edit, reset, or config key"
    ),
    key: Optional[str] = typer.Argument(None, help="Configuration key"),
    value: Optional[str] = typer.Argument(None, help="Value to set"),
) -> None:
    """Manage CAFE configuration.

    \b
    Examples:
        cafe config
        cafe config set settings.playbook default
        cafe config get settings.playbook
        cafe config edit
        cafe config reset
    """
    config_manager = ConfigManager()
    import os
    import subprocess

    # No arguments: show all config
    if not action:
        try:
            loaded_config = config_manager.load_config()
        except ConfigError as e:
            console.print(f"[red]{e}[/red]")
            return
        console.print("[bold cyan]Current Configuration:[/bold cyan]")
        console.print(yaml.dump(loaded_config, default_flow_style=False, allow_unicode=True))
        return

    # Sub-commands
    if action == "set":
        if not key or not value:
            console.print("[red]Error: 'set' requires both key and value[/red]")
            console.print("Usage: cafe config set <key> <value>")
            raise typer.Exit(1)

        config_manager.set(key, value)
        console.print(f"[green]✓ Set {key} = {value}[/green]")

    elif action == "get":
        if not key:
            console.print("[red]Error: 'get' requires a key[/red]")
            console.print("Usage: cafe config get <key>")
            raise typer.Exit(1)

        val = config_manager.get(key)
        if val is None:
            console.print(f"[yellow]Key not found: {key}[/yellow]")
        else:
            import json

            console.print(f"{key} = {json.dumps(val, indent=2)}")

    elif action == "edit":
        # Open config file in editor
        config_file = config_manager.config_file

        # Check if config file exists
        if not config_file.exists():
            console.print("[red]Error: Configuration file not found.[/red]")
            console.print("[yellow]Please run 'cafe init' first to initialize CAFE.[/yellow]")
            raise typer.Exit(1)

        # Use EDITOR env var, or fallback to vim
        editor = os.environ.get("EDITOR", "vim")

        try:
            subprocess.run([editor, str(config_file)], check=True)
            console.print(f"[green]✓ Config file edited: {config_file}[/green]")
        except subprocess.CalledProcessError:
            console.print("[red]Error: Failed to edit config[/red]")
            raise typer.Exit(1)
        except FileNotFoundError:
            console.print(f"[red]Error: Editor '{editor}' not found[/red]")
            console.print("[dim]Set EDITOR environment variable or install vim[/dim]")
            raise typer.Exit(1)

    elif action == "reset":
        try:
            confirm = _cli_prompt_confirm("Reset configuration to defaults?", default=False)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled[/dim]")
            raise typer.Exit(0)

        if confirm:
            config_manager.reset()
            console.print("[green]✓ Configuration reset to defaults[/green]")
        else:
            console.print("[dim]Cancelled[/dim]")

    else:
        # Treat action as a key for backward compatibility
        # e.g., "cafe config pm" -> get pm
        val = config_manager.get(action)
        if val is None:
            console.print(f"[yellow]Key not found: {action}[/yellow]")
        else:
            import json

            console.print(f"{action} = {json.dumps(val, indent=2)}")


def list_issues() -> None:
    """List all issues."""
    from rich.table import Table

    issues_dir = Path(".cafe/issues")

    if not issues_dir.exists():
        console.print("[yellow]No issues directory found[/yellow]")
        console.print("Run 'cafe prepare' to create your first issue")
        return

    # Get all issue directories (recursively find dirs containing issue.yaml or phase dirs)
    def _find_issues(base_dir: Path) -> list[Path]:
        """Find issue directories by looking for issue.yaml or phase subdirectories."""
        found = []
        for d in sorted(base_dir.iterdir()):
            if not d.is_dir():
                continue
            # A directory is an issue if it contains issue.yaml or any phase dir
            if (d / "issue.yaml").exists() or any((d / phase).exists() for phase in ALL_PHASES):
                found.append(d)
            else:
                # Check subdirectories (for nested issue names like feature/chat-web-ui)
                found.extend(_find_issues(d))
        return found

    issues = _find_issues(issues_dir)

    if not issues:
        console.print("[yellow]No issues found[/yellow]")
        console.print("Run 'cafe prepare' to create your first issue")
        return

    # Create table
    table = Table(title="CAFE Issues", show_header=True, header_style="bold cyan")
    table.add_column("Issue Name", style="green")
    table.add_column("Phases", style="dim")
    table.add_column("Worktree", style="dim")
    table.add_column("Modified", style="dim")

    for issue in sorted(issues, key=lambda x: x.stat().st_mtime, reverse=True):
        # Get worktree path from issue.yaml first
        worktree_path = "-"
        config_file = issue / "issue.yaml"
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    config = yaml.safe_load(f)
                    if config and "worktree_path" in config:
                        worktree_path = config["worktree_path"]
            except Exception:
                # If read fails, keep default value "-"
                pass

        # Use the issue's declared workflow steps so custom flows remain
        # visible; metadata-absent issues retain the legacy fallback inside
        # the shared resolver.
        issue_name = str(issue.relative_to(issues_dir))
        step_resolution_error = None
        try:
            phase_names = _load_issue_step_names(issue_name)
        except ValueError as exc:
            phase_names = []
            step_resolution_error = str(exc)

        # Check which phases exist
        # If worktree_path exists, read phases from worktree location
        phases = []
        if worktree_path != "-":
            # Read phases from worktree/.cafe/issues/{issue_name}/
            worktree_issue_dir = Path(worktree_path) / ".cafe" / "issues" / issue.relative_to(issues_dir)
            if worktree_issue_dir.exists():
                for phase in phase_names:
                    phase_dir = worktree_issue_dir / phase
                    if phase_dir.exists():
                        phases.append(phase)
            # If worktree issue dir doesn't exist, fall back to current location
            if not phases:
                for phase in phase_names:
                    phase_dir = issue / phase
                    if phase_dir.exists():
                        phases.append(phase)
        else:
            # No worktree, read phases from current location
            for phase in phase_names:
                phase_dir = issue / phase
                if phase_dir.exists():
                    phases.append(phase)

        phases_str = (
            step_resolution_error
            if step_resolution_error is not None
            else ", ".join(phases) if phases else "empty"
        )

        # Get last modified time
        mtime = datetime.fromtimestamp(issue.stat().st_mtime)
        mtime_str = mtime.strftime("%Y-%m-%d %H:%M")

        table.add_row(issue_name, phases_str, worktree_path, mtime_str)

    console.print(table)
    console.print(f"\n[dim]Total: {len(issues)} issue(s)[/dim]")


def remove_issue(
    issue_names: Optional[list[str]] = typer.Argument(
        None, help="Names of the issues to delete (supports wildcards like 'test-*')"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Remove one or more issues and all their data."""
    import fnmatch

    # If no arguments, show issue list and prompt for issue name
    if not issue_names:
        list_issues()
        console.print()
        issue_input = _cli_prompt_text("Issue name(s) to remove (space-separated):")
        if not issue_input or not issue_input.strip():
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)
        issue_names = issue_input.strip().split()

    # Expand wildcards
    issues_dir = Path(".cafe/issues")
    expanded_issues = []
    for pattern in issue_names:
        if "*" in pattern or "?" in pattern:
            # Wildcard pattern - find matching issues
            if not issues_dir.exists():
                continue
            matches = [
                d.name
                for d in issues_dir.iterdir()
                if d.is_dir() and fnmatch.fnmatch(d.name, pattern)
            ]
            expanded_issues.extend(matches)
        else:
            # Literal issue name
            expanded_issues.append(pattern)

    # Remove duplicates while preserving order
    seen = set()
    issue_names = []
    for name in expanded_issues:
        if name not in seen:
            seen.add(name)
            issue_names.append(name)

    if not issue_names:
        console.print("[red]No issues matched the given patterns[/red]")
        console.print("\nRun 'cafe ls' to see available issues")
        raise typer.Exit(1)

    # Check all issues exist first
    missing_issues = []
    existing_issues = []
    for issue_name in issue_names:
        issue_path = Path(".cafe/issues") / issue_name
        if not issue_path.exists():
            missing_issues.append(issue_name)
        else:
            existing_issues.append((issue_name, issue_path))

    # Report missing issues
    if missing_issues:
        console.print(f"[red]Issue(s) not found: {', '.join(missing_issues)}[/red]")
        console.print("\nRun 'cafe ls' to see available issues")
        if not existing_issues:
            raise typer.Exit(1)

    # Show what will be deleted
    if not force and existing_issues:
        console.print(f"[yellow]About to delete {len(existing_issues)} issue(s):[/yellow]")
        for issue_name, issue_path in existing_issues:
            console.print(f"  • {issue_name} [dim]({issue_path})[/dim]")
        console.print()

        try:
            confirm = _cli_prompt_confirm(f"Are you sure you want to delete {len(existing_issues)} issue(s)?", default=False)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled[/dim]")
            raise typer.Exit(0)

        if not confirm:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    # Delete the issue directories
    success_count = 0
    for issue_name, issue_path in existing_issues:
        try:
            worktree_path: Optional[Path] = None
            backup_info = "none"
            issue_yaml = issue_path / "issue.yaml"
            if issue_yaml.exists():
                try:
                    config_data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
                except Exception:
                    config_data = {}
                raw_worktree_path = config_data.get("worktree_path")
                if isinstance(raw_worktree_path, str) and raw_worktree_path.strip():
                    worktree_path = Path(raw_worktree_path)
                    if not worktree_path.is_absolute():
                        worktree_path = (Path.cwd() / worktree_path).resolve()

            if worktree_path is not None and worktree_path.exists():
                worktree_issue_dir = worktree_path / ".cafe" / "issues" / issue_name
                if worktree_issue_dir.exists():
                    archive_path = lifecycle_commands._backup_issue_directory(worktree_issue_dir, issue_name)
                    backup_info = str(archive_path)
                    console.print(f"[green]✓[/green] Backed up issue '{issue_name}' to {archive_path}")
                shutil.rmtree(worktree_path)
                console.print(f"[green]✓[/green] Removed worktree '{worktree_path}'")

            shutil.rmtree(issue_path)
            console.print(f"[dim]  Backup: {backup_info}[/dim]")
            console.print(f"[green]✓[/green] Issue '{issue_name}' deleted successfully")
            success_count += 1
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to delete issue '{issue_name}': {e}")

    # Summary
    if len(existing_issues) > 1:
        console.print(
            f"\n[green]{success_count}/{len(existing_issues)} issue(s) deleted successfully[/green]"
        )

    if success_count < len(existing_issues):
        raise typer.Exit(1)
