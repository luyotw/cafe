"""cafe crew subcommand group."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from cafe.ui.inquirer_prompts import prompt_confirm, prompt_list, prompt_text
from cafe.utils.crew import CrewManager, normalize_role_config
from cafe.utils.preset import PresetManager, PresetNotFoundError

crew_app = typer.Typer(help="Manage crew configuration (CLI assignments for each role).")
console = Console()

_ROLES = ["pm", "developer", "reviewer"]


# ---------------------------------------------------------------------------
# Helpers shared across subcommands
# ---------------------------------------------------------------------------

def _load_crew_data(cafe_dir: Path) -> Dict[str, Any]:
    """Return raw crew dict (empty dict if file absent or unreadable)."""
    return CrewManager(cafe_dir).load()


def _save_crew_data(crew_data: Dict[str, Any], cafe_dir: Path) -> None:
    CrewManager(cafe_dir).save(crew_data)


def _chain_to_clis_list(chain) -> List[Dict[str, Any]]:
    """Convert a list of CliEntry objects to the new clis-list dict format."""
    result = []
    for entry in chain:
        item: Dict[str, Any] = {"cli": entry.cli.value}
        if entry.model:
            item["model"] = entry.model
        item.update(entry.phase_models)
        result.append(item)
    return result


def _get_role_chain_as_clis(crew_data: Dict[str, Any], role: str) -> List[Dict[str, Any]]:
    """Return the role's clis list, normalizing old format to new if necessary."""
    role_config = crew_data.get(role, {})
    if not isinstance(role_config, dict):
        return []
    if "clis" in role_config:
        raw = role_config["clis"]
        return raw if isinstance(raw, list) else []
    # Old format — normalize to CliEntry chain then convert back
    chain = normalize_role_config(role_config)
    return _chain_to_clis_list(chain)


def _update_role_clis(crew_data: Dict[str, Any], role: str, clis: List[Dict[str, Any]]) -> None:
    """Replace the clis list for a role, preserving other role-level keys."""
    if role not in crew_data:
        crew_data[role] = {}
    role_cfg = crew_data[role]
    if not isinstance(role_cfg, dict):
        crew_data[role] = {}
        role_cfg = crew_data[role]
    # Remove old-format keys if present
    _OLD_ROLE_KEYS = {"cli", "model", "backup", "models", "spec", "plan", "develop", "review", "pr"}
    for old_key in _OLD_ROLE_KEYS:
        role_cfg.pop(old_key, None)
    role_cfg["clis"] = clis


# ---------------------------------------------------------------------------
# cafe crew list
# ---------------------------------------------------------------------------

@crew_app.command(name="list")
def list_crew() -> None:
    """Display the resolved crew configuration (role → CLI chain → models)."""
    cafe_dir = Path(".cafe")
    crew_mgr = CrewManager(cafe_dir)

    if not crew_mgr.exists():
        console.print("[yellow]⚠ Warning: .cafe/crew.yaml does not exist. Run 'cafe init' or 'cafe crew set-primary' first.[/yellow]")
        return

    crew_data = crew_mgr.load()

    if not crew_data:
        console.print("[yellow]⚠ Warning: .cafe/crew.yaml is empty or unreadable.[/yellow]")
        return

    table = Table(title="Resolved Crew Configuration", show_header=True)
    table.add_column("Role", style="cyan")
    table.add_column("Order", style="dim")
    table.add_column("CLI", style="green")
    table.add_column("Model", style="yellow")
    table.add_column("Phase Models", style="dim")

    for role in _ROLES:
        role_config = crew_data.get(role, {})
        if not isinstance(role_config, dict):
            continue
        chain = normalize_role_config(role_config)
        if not chain:
            table.add_row(role, "-", "(none)", "", "")
            continue
        for idx, entry in enumerate(chain):
            order_label = f"{idx + 1}"
            if idx == 0:
                order_label += " (primary)"
            phase_models_str = ", ".join(
                f"{k}={v}" for k, v in sorted(entry.phase_models.items())
            ) if entry.phase_models else ""
            table.add_row(
                role if idx == 0 else "",
                order_label,
                entry.cli.value,
                entry.model or "",
                phase_models_str,
            )

    console.print(table)


# ---------------------------------------------------------------------------
# cafe crew set-primary
# ---------------------------------------------------------------------------

def _preset_has_available_clis(preset_data: Dict[str, Any], available_clis: List[str]) -> bool:
    """Return True when every role's primary CLI is in available_clis."""
    for role in _ROLES:
        role_cfg = preset_data.get(role, {})
        if not isinstance(role_cfg, dict):
            continue
        primary = role_cfg.get("cli") or (
            (role_cfg.get("clis") or [{}])[0].get("cli") if isinstance(role_cfg.get("clis"), list) else None
        )
        if primary and primary not in available_clis:
            return False
    return True


def _render_preset_preview(preset_data: Dict[str, Any]) -> str:
    """Return a human-readable preview string for a preset."""
    lines = []
    for role in _ROLES:
        role_cfg = preset_data.get(role, {})
        if not isinstance(role_cfg, dict):
            continue
        chain = normalize_role_config(role_cfg)
        if chain:
            chain_str = " → ".join(
                f"{e.cli.value}" + (f":{e.model}" if e.model else "") for e in chain
            )
            lines.append(f"  {role}: {chain_str}")
    return "\n".join(lines) if lines else "  (empty)"


def run_set_primary_interactive(cafe_dir: Path = Path(".cafe")) -> None:
    """Interactive flow: detect CLIs, pick preset, preview, confirm, apply."""
    from cafe.ui.init_helpers import check_available_clis

    available_clis = check_available_clis()
    if not available_clis:
        console.print("[red]No supported AI CLIs found. Install at least one (claude, gemini, codex, copilot, cursor-agent).[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Detected installed CLIs:[/green] {', '.join(available_clis)}")
    console.print()

    manager = PresetManager()
    all_presets = manager.list()
    if not all_presets:
        console.print("[red]No presets found. Cannot configure crew.[/red]")
        raise typer.Exit(1)

    # Filter to fully-compatible presets; fall back to all if none match
    compatible = []
    for info in all_presets:
        try:
            data = yaml.safe_load(info.path.read_text()) or {}
        except Exception:
            data = {}
        if _preset_has_available_clis(data, available_clis):
            compatible.append(info)

    display_presets = compatible if compatible else all_presets
    if not compatible:
        console.print("[yellow]⚠ No preset fully matches your installed CLIs. Showing all presets.[/yellow]")

    while True:
        choices = [f"{p.name}  [{p.source}]" for p in display_presets]
        selected_label = prompt_list(
            message="Select a preset to apply:",
            choices=choices,
        )
        if not selected_label:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

        selected_idx = choices.index(selected_label)
        selected_info = display_presets[selected_idx]

        try:
            preset_data = yaml.safe_load(selected_info.path.read_text()) or {}
        except Exception:
            preset_data = {}

        console.print()
        console.print(f"[bold cyan]Preview: {selected_info.name}[/bold cyan]")
        console.print(_render_preset_preview(preset_data))
        console.print()

        confirmed = prompt_confirm("Apply this preset to crew.yaml?", default=True)
        if confirmed:
            manager.apply(selected_info.name, cafe_dir=cafe_dir)
            console.print(f"[green]✓ Applied preset '[cyan]{selected_info.name}[/cyan]' to {cafe_dir}/crew.yaml[/green]")
            return
        # Loop back to selection


@crew_app.command(name="set-primary")
def set_primary(
    preset: Optional[str] = typer.Option(
        None,
        "--preset",
        help="Preset name to apply directly (non-interactive).",
    ),
    cli: Optional[str] = typer.Option(
        None,
        "--cli",
        help="Primary CLI for all roles (non-interactive, applies to every role).",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Default model for all roles (used with --cli).",
    ),
    phase_model: Optional[List[str]] = typer.Option(
        None,
        "--phase-model",
        help="Phase model override as role.phase=model (e.g. developer.plan=opus). Repeatable.",
    ),
) -> None:
    """Set the primary CLI for all roles via preset selection or per-role flags.

    Without flags runs an interactive flow to detect installed CLIs,
    preview matching presets, and apply the selection.

    Use --cli to set a single CLI for all roles (non-interactive).
    Use --preset to apply a named preset (non-interactive).
    """
    cafe_dir = Path(".cafe")

    if preset:
        manager = PresetManager()
        try:
            manager.apply(preset, cafe_dir=cafe_dir)
            console.print(f"[green]✓ Applied preset '[cyan]{preset}[/cyan]' to {cafe_dir}/crew.yaml[/green]")
        except PresetNotFoundError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)
        return

    if cli:
        from cafe.core.types import AgentCLI

        try:
            AgentCLI(cli)
        except ValueError:
            valid = [c.value for c in AgentCLI]
            console.print(f"[red]Error: '{cli}' is not a supported CLI. Valid: {valid}[/red]")
            raise typer.Exit(1)

        # Parse phase model overrides: role.phase=model
        phase_overrides: Dict[str, Dict[str, str]] = {}
        if phase_model:
            for spec in phase_model:
                if "=" not in spec:
                    console.print(f"[red]Error: --phase-model must be role.phase=model, got '{spec}'[/red]")
                    raise typer.Exit(1)
                key, _, val = spec.partition("=")
                if "." not in key:
                    console.print(f"[red]Error: --phase-model key must be role.phase, got '{key}'[/red]")
                    raise typer.Exit(1)
                role_key, _, phase_key = key.partition(".")
                if role_key not in _ROLES:
                    console.print(f"[red]Error: Unknown role '{role_key}'. Valid: {_ROLES}[/red]")
                    raise typer.Exit(1)
                phase_overrides.setdefault(role_key, {})[phase_key] = val

        crew_data = _load_crew_data(cafe_dir)
        for role in _ROLES:
            existing_clis = _get_role_chain_as_clis(crew_data, role)
            existing_fallback = existing_clis[1:] if len(existing_clis) > 1 else []

            new_primary: Dict[str, Any] = {"cli": cli}
            if model:
                new_primary["model"] = model
            # Merge phase model overrides for this role
            if role in phase_overrides:
                new_primary.update(phase_overrides[role])

            new_clis = [new_primary] + existing_fallback
            _update_role_clis(crew_data, role, new_clis)

        _save_crew_data(crew_data, cafe_dir)
        console.print(f"[green]✓ Set primary CLI to '[cyan]{cli}[/cyan]' for all roles in {cafe_dir}/crew.yaml[/green]")
        return

    try:
        run_set_primary_interactive(cafe_dir=cafe_dir)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        raise typer.Exit(0)


# ---------------------------------------------------------------------------
# cafe crew set-fallback
# ---------------------------------------------------------------------------

def run_set_fallback_interactive(role: str, cafe_dir: Path = Path(".cafe")) -> None:
    """Interactive editor for the fallback chain of one role."""
    from cafe.ui.init_helpers import check_available_clis

    crew_data = _load_crew_data(cafe_dir)
    clis = _get_role_chain_as_clis(crew_data, role)

    if not clis:
        console.print(f"[yellow]⚠ No primary CLI configured for {role}. Add one first via 'cafe crew set-primary'.[/yellow]")
        raise typer.Exit(1)

    available_clis = check_available_clis()

    def _display_chain(chain: List[Dict[str, Any]]) -> None:
        console.print(f"\n[bold]Current {role} fallback chain:[/bold]")
        for i, entry in enumerate(chain):
            label = " (primary)" if i == 0 else f" (fallback {i})"
            model_part = f"  model={entry['model']}" if entry.get("model") else ""
            console.print(f"  {i + 1}. {entry['cli']}{label}{model_part}")
        console.print()

    while True:
        _display_chain(clis)

        action = prompt_list(
            message="Choose action:",
            choices=["Add entry", "Remove entry", "Reorder", "Done"],
        )

        if action == "Done":
            break

        elif action == "Add entry":
            cli_choices = [c for c in available_clis if c not in {e["cli"] for e in clis}]
            if not cli_choices:
                console.print("[yellow]All installed CLIs are already in the chain.[/yellow]")
                continue
            chosen_cli = prompt_list(message="Select CLI to add:", choices=cli_choices)
            if not chosen_cli:
                continue
            model_input = prompt_text(message="Model (leave blank for default):", default="")
            new_entry: Dict[str, Any] = {"cli": chosen_cli}
            if model_input and model_input.strip():
                new_entry["model"] = model_input.strip()
            clis.append(new_entry)

        elif action == "Remove entry":
            if len(clis) <= 1:
                console.print("[yellow]Cannot remove the only (primary) entry.[/yellow]")
                continue
            removable = [f"{i + 1}. {e['cli']}" for i, e in enumerate(clis) if i > 0]
            selected = prompt_list(message="Select entry to remove:", choices=removable)
            if not selected:
                continue
            idx = int(selected.split(".")[0]) - 1
            removed = clis.pop(idx)
            console.print(f"[green]Removed {removed['cli']}[/green]")

        elif action == "Reorder":
            if len(clis) <= 1:
                console.print("[yellow]Nothing to reorder (only one entry).[/yellow]")
                continue
            entry_choices = [f"{i + 1}. {e['cli']}" for i, e in enumerate(clis)]
            selected = prompt_list(message="Select entry to move:", choices=entry_choices)
            if not selected:
                continue
            idx = int(selected.split(".")[0]) - 1
            sub_choices = []
            if idx > 0:
                sub_choices.append("Move Up")
            if idx < len(clis) - 1:
                sub_choices.append("Move Down")
            if not sub_choices:
                console.print("[yellow]Cannot move this entry further.[/yellow]")
                continue
            direction = prompt_list(message="Move direction:", choices=sub_choices)
            if direction == "Move Up":
                clis[idx], clis[idx - 1] = clis[idx - 1], clis[idx]
            elif direction == "Move Down":
                clis[idx], clis[idx + 1] = clis[idx + 1], clis[idx]

    _update_role_clis(crew_data, role, clis)
    _save_crew_data(crew_data, cafe_dir)
    console.print(f"[green]✓ Updated {role} fallback chain in crew.yaml[/green]")


@crew_app.command(name="set-fallback")
def set_fallback(
    role: Optional[str] = typer.Option(
        None,
        "--role",
        help="Role to edit: pm, developer, or reviewer.",
    ),
    add: Optional[str] = typer.Option(
        None,
        "--add",
        help="Add CLI entry: <cli> or <cli>,<model> (e.g. codex or codex,gpt-5.5).",
    ),
    remove: Optional[str] = typer.Option(
        None,
        "--remove",
        help="Remove entry by CLI name.",
    ),
) -> None:
    """Edit the fallback chain for a specific role.

    Without flags, runs an interactive editor.
    Use --add / --remove for non-interactive modification.
    """
    cafe_dir = Path(".cafe")
    non_interactive = add is not None or remove is not None

    if non_interactive and not role:
        console.print("[red]Error: --role is required when using --add or --remove.[/red]")
        raise typer.Exit(1)

    if role and role not in _ROLES:
        console.print(f"[red]Error: --role must be one of {_ROLES}.[/red]")
        raise typer.Exit(1)

    if non_interactive and add and remove:
        console.print("[red]Error: --add and --remove cannot be used together.[/red]")
        raise typer.Exit(1)

    if non_interactive:
        crew_data = _load_crew_data(cafe_dir)
        assert role is not None
        clis = _get_role_chain_as_clis(crew_data, role)

        if add:
            parts = add.split(",", 1)
            new_cli = parts[0].strip()
            new_model = parts[1].strip() if len(parts) > 1 else None

            # Validate CLI name
            from cafe.core.types import AgentCLI
            try:
                AgentCLI(new_cli)
            except ValueError:
                valid = [c.value for c in AgentCLI]
                console.print(f"[red]Error: '{new_cli}' is not a supported CLI. Valid: {valid}[/red]")
                raise typer.Exit(1)

            # Check for duplicate
            if any(e.get("cli") == new_cli for e in clis):
                console.print(f"[red]Error: '{new_cli}' is already in the {role} chain.[/red]")
                raise typer.Exit(1)

            entry: Dict[str, Any] = {"cli": new_cli}
            if new_model:
                entry["model"] = new_model
            clis.append(entry)
            _update_role_clis(crew_data, role, clis)
            _save_crew_data(crew_data, cafe_dir)
            console.print(f"[green]✓ Added '{new_cli}' to {role} fallback chain.[/green]")

        elif remove:
            match_idx = next((i for i, e in enumerate(clis) if e.get("cli") == remove), None)
            if match_idx is None:
                console.print(f"[red]Error: '{remove}' not found in {role} chain.[/red]")
                raise typer.Exit(1)
            clis.pop(match_idx)
            _update_role_clis(crew_data, role, clis)
            _save_crew_data(crew_data, cafe_dir)
            console.print(f"[green]✓ Removed '{remove}' from {role} fallback chain.[/green]")

        return

    # Interactive mode
    if not role:
        role = prompt_list(
            message="Select role to edit:",
            choices=_ROLES,
        )
        if not role:
            raise typer.Exit(0)

    try:
        run_set_fallback_interactive(role=role, cafe_dir=cafe_dir)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        raise typer.Exit(0)
