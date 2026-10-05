"""Read-only canonical constraints inspection and deterministic doc generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

import typer
import yaml

from cafe.constraints import Context, load_registry, material_digest, resolve
from cafe.constraints.context import context_for_tools
from cafe.constraints.rendering import render_docs
from cafe.constraints.resolver import PROVIDERS
from cafe.core.playbook import resolve_playbook_skills, resolve_step_behavior
from cafe.core.workflow_tools import normalize_allowed_tools, runtime_granted_tools
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.execution_profile import resolve_execution_profile
from cafe.skills.loader import SkillLoader
from cafe.utils.phase_config import load_phase_step_model

constraints_app = typer.Typer(
    help="Inspect package-owned runtime constraints; no authority is granted."
)
docs_app = typer.Typer(help="Render or check deterministic registry documentation.")
constraints_app.add_typer(docs_app, name="docs")


def _bounded_mapping(path: Path, *, yaml_format=False):
    if path.stat().st_size > 1_048_576:
        raise ValueError("Issue context exceeds bounded inspection capacity")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if yaml_format else json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Issue context must be a mapping")
    return data


def issue_context(issue: str, step: str | None, cli: str | None = None) -> Context:
    """Existing declarative composition without initializing any workflow state."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", issue) or issue in {".", ".."}:
        raise ValueError("Invalid issue selector")
    root = Path.cwd()
    issue_dir = root / ".cafe/issues" / issue
    config = _bounded_mapping(issue_dir / "issue.yaml", yaml_format=True)
    snapshot = {}
    if (issue_dir / "blackboard.json").exists():
        snapshot = _bounded_mapping(issue_dir / "blackboard.json")
    step = step or snapshot.get("current_step")
    if not step:
        raise ValueError("Issue has no current step; specify --step")
    playbook_id = (
        snapshot.get("playbook_id")
        or config.get("playbook_id")
        or config.get("playbook")
        or "standard"
    )
    playbook = PlaybookLoader(project_root=root, read_only=True).load(str(playbook_id))
    definition = playbook["steps"].get(step)
    if not definition:
        raise ValueError("Unknown issue step")
    loader = SkillLoader(project_root=root, read_only=True)
    injected = resolve_playbook_skills(
        playbook, channel="workflow", role=definition.get("role"), step_name=step
    )
    # Inspect the current iteration if available; filenames are bounded selectors.
    iterations = sorted((issue_dir / step).glob("iteration_[0-9][0-9][0-9]"))
    iteration = int(iterations[-1].name.rsplit("_", 1)[1]) if iterations else 1
    profile = resolve_execution_profile(
        loader, definition["skill"], iteration=iteration, workflow_skills=injected, step_name=step
    )
    phase = load_phase_step_model(
        step_name=step, local_path=root / ".cafe/phases.yaml", repo_path=root / ".cafe/phases.yaml"
    )
    effective_cli = cli or phase.clis[0][0]
    consumers = ["authority"]
    if len(phase.clis) == 1:
        consumers.append("single-chain")
    return context_for_tools(
        effective_cli,
        allowed_tools=[
            *normalize_allowed_tools(definition.get("allowed_tools", [])),
            *runtime_granted_tools(resolve_step_behavior(playbook, step).runtime_tool_grants),
        ],
        workloads=profile.workloads,
        capabilities=profile.capabilities,
        structured=True,
        consumers=consumers,
    )


@constraints_app.command(name="list")
@constraints_app.command(name="show")
def inspect_constraints(
    ctx: typer.Context,
    identity: Optional[str] = typer.Argument(None),
    json_output: bool = typer.Option(False, "--json"),
    cli: Optional[str] = typer.Option(None, "--cli"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    platform: Optional[str] = typer.Option(None, "--platform"),
    surface: Optional[str] = typer.Option(None, "--surface"),
    operation: Optional[str] = typer.Option(None, "--operation"),
    mode: List[str] = typer.Option(None, "--mode"),
    workload: List[str] = typer.Option(None, "--workload"),
    capability: List[str] = typer.Option(None, "--capability"),
    consumer: List[str] = typer.Option(None, "--consumer"),
    compatibility: Optional[str] = typer.Option(None, "--compatibility"),
    issue: Optional[str] = typer.Option(None, "--issue"),
    step: Optional[str] = typer.Option(None, "--step"),
    include_history: bool = typer.Option(False, "--include-history"),
):
    try:
        registry = load_registry()
        if ctx.info_name == "show" and not identity:
            raise ValueError("show requires a constraint ID")
        if identity and identity not in {e.id for e in registry.entries}:
            raise ValueError("Unknown constraint ID")
        selectors = any(
            [
                cli,
                provider,
                platform,
                surface,
                operation,
                mode,
                workload,
                capability,
                consumer,
                compatibility,
                issue,
            ]
        )
        if step and not issue:
            raise ValueError("--step requires --issue")
        if selectors:
            base = (
                issue_context(issue, step, cli).model_dump(mode="json")
                if issue
                else Context().model_dump(mode="json")
            )
            for name, value in [
                ("cli", cli),
                ("provider", provider),
                ("platform", platform),
                ("surface", surface),
                ("operation", operation),
                ("compatibility", compatibility),
            ]:
                if value is not None:
                    base[name] = value
            if cli and not provider:
                base["provider"] = PROVIDERS.get(cli, "unknown")
            for name, value in [
                ("modes", mode),
                ("workloads", workload),
                ("capabilities", capability),
                ("consumers", consumer),
            ]:
                if value:
                    base[name] = value
            view = resolve(Context.model_validate(base), registry=registry, history=include_history)
            entries = [
                e.model_dump(mode="json")
                for e in view.entries
                if identity is None or e.id == identity
            ]
            payload = {
                "schema_version": 1,
                "context": view.context.model_dump(mode="json"),
                "entries": entries,
                "diagnostics": view.diagnostics,
                "digest": material_digest(view),
            }
        else:
            payload = {
                "schema_version": 1,
                "context": None,
                "entries": [
                    e.model_dump(mode="json")
                    for e in registry.entries
                    if (identity is None or e.id == identity)
                    and (include_history or e.status == "active" or identity is not None)
                ],
                "diagnostics": [],
                "digest": None,
            }
        if json_output:
            typer.echo(json.dumps(payload, sort_keys=True))
        else:
            for entry in payload["entries"]:
                boundary = entry.get("boundary")
                typer.echo(
                    f"{entry['id']} [{entry['status']}, {entry['enforcement']}] "
                    + (json.dumps(boundary) if "boundary" in entry else entry["title"])
                )
            for diagnostic in payload["diagnostics"]:
                typer.echo(diagnostic)
    except (ValueError, OSError, KeyError) as error:
        typer.echo(f"Constraints inspection failed: {str(error)[:512]}", err=True)
        raise typer.Exit(2)


@docs_app.command()
def render(output: Optional[Path] = typer.Option(None, "--output")):
    content = render_docs(load_registry())
    if output:
        output.write_text(content, encoding="utf-8")
    else:
        typer.echo(content, nl=False)


@docs_app.command()
def check(path: Path = typer.Argument(Path("docs/known-constraints.md"))):
    try:
        if path.stat().st_size > 1_048_576 or path.read_text(encoding="utf-8") != render_docs(
            load_registry()
        ):
            raise ValueError("Generated constraints documentation has drifted")
    except (OSError, ValueError) as error:
        typer.echo(str(error)[:512], err=True)
        raise typer.Exit(1)
