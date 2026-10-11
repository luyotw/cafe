"""Read-only staged validation through existing runtime catalogs and contracts."""

from __future__ import annotations

import dataclasses
import hashlib
import re
import shutil
import tempfile
from pathlib import Path

from cafe.catalogs.resolver import CatalogKind, CatalogResolver
from cafe.core.playbook import (
    PlaybookDefinition,
    _resolve_phase_step_defaults,
    _tool_requirement_satisfied,
    confirmation_gate_steps,
    load_playbook_file,
    mandatory_confirmation_gate_steps,
    normalize_playbook_yaml,
    resolve_playbook_skills,
    resolve_step_behavior,
)
from cafe.playbooks.simulate import analyze_playbook
from cafe.skills.loader import SkillLoader
from cafe.skills.selectors import skill_selector_names
from cafe.skills.workflow_composition import resolve_step_workflow_composition

from .phase import guard
from .requests import decode_request


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _public(value):
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items()}
    if isinstance(value, (set, tuple, list)):
        items = sorted(value) if isinstance(value, set) else value
        return [_public(v) for v in items]
    return value


def _authority(model):
    return {
        name: {
            k: step.model_dump(mode="json")[k]
            for k in (
                "allowed_tools",
                "capability_requests",
                "hooks",
                "behavior",
                "human_tasks",
                "assignee_type",
                "delivery",
            )
        }
        for name, step in model.steps.items()
    }


def validate(root, result, requests):
    builtin = root / "src/cafe/data" if (root / "src/cafe/core/playbook.py").is_file() else None
    resolver = CatalogResolver(project_root=root, builtin_root=builtin, read_only=True)
    entries = resolver.entries([CatalogKind.PHASE, CatalogKind.PLAYBOOK])
    phase_entries = [e for e in entries if e.kind == CatalogKind.PHASE]
    playbook_entries = [e for e in entries if e.kind == CatalogKind.PLAYBOOK]
    with tempfile.TemporaryDirectory(prefix="cafe-author-view-") as temporary:
        staged = Path(temporary)
        (staged / ".cafe").mkdir()
        source_by_name = {}
        for entry in phase_entries:
            dest = staged / ".cafe/skills" / entry.key
            shutil.copytree(entry.path, dest, symlinks=False)
            source_by_name[entry.key] = entry.path
            for file in entry.path.rglob("*"):
                if file.is_file():
                    result.dependencies[str(file)] = digest(file)
        candidates = {}
        for entry in playbook_entries:
            path = entry.path
            result.dependencies[str(path)] = digest(path)
            relative = None
            try:
                relative = str(path.relative_to(root))
            except ValueError:
                pass
            candidates[relative or str(path)] = path.read_text()
        changed_names = set()
        for relative, content in result.files.items():
            path = Path(relative)
            if "skills" in path.parts:
                index = path.parts.index("skills")
                name = path.parts[index + 1]
                changed_names.add(name)
                selected = source_by_name.get(name)
                actual = root / Path(*path.parts[: index + 2])
                if selected and selected.resolve() != actual.resolve():
                    # A builtin patch must not overwrite the selected project/global override.
                    if relative.startswith("src/"):
                        result.diagnose(
                            "ineffective_shadow",
                            f"Selected source is {selected}",
                            target=relative,
                            skill=name,
                            remedy=(
                                "Explicitly target the effective source "
                                "or remove the shadow separately"
                            ),
                        )
                        continue
                dest = staged / ".cafe/skills" / name / Path(*path.parts[index + 2 :])
            else:
                candidates[relative] = content
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        loader = SkillLoader(
            project_root=staged,
            global_root=staged / "global",
            builtin_root=staged / "builtin",
            read_only=True,
            resolve_presentation=False,
        )
        loader.discover(strict=True)
        for name in sorted(changed_names):
            skill_dir = loader.get_skill_dir(name)
            resources = {
                str(p.relative_to(skill_dir)): p.read_text()
                for p in skill_dir.rglob("*.md")
                if p.name != "SKILL.md"
            }
            declaration = guard((skill_dir / "SKILL.md").read_text(), name, resources)
            loader.validate_workflow_declaration_resources(skill_dir, declaration)
        changed_books = {r.target for r in requests if "playbooks" in Path(r.target).parts}
        for relative, content in sorted(candidates.items()):
            # Existing unaffected playbooks need not revalidate legacy warning contracts.
            if relative not in changed_books and not any(name in content for name in changed_names):
                continue
            candidate = staged / "playbooks" / Path(relative).name
            candidate.parent.mkdir(exist_ok=True)
            candidate.write_text(content)
            try:
                raw = decode_request(content)
                preliminary = PlaybookDefinition.model_validate(
                    _resolve_phase_step_defaults(normalize_playbook_yaml(raw), loader)
                )
                graph = _public(dataclasses.asdict(analyze_playbook(preliminary)))
                _bindings(preliminary, loader, result, relative, graph)
                loaded = load_playbook_file(
                    candidate, source="project", skill_loader=loader, strict=True
                )
                model = loaded.model
                graph = _public(dataclasses.asdict(analyze_playbook(model)))
                result.simulation[relative] = graph
                result.transition_summary[relative] = graph["edges"]
                for field in ("unreachable_steps", "dead_end_steps", "missing_intent_handlers"):
                    if graph[field]:
                        result.diagnose(
                            "graph_invalid", str(graph[field]), target=relative, field=field
                        )
                gates = {
                    "assignable": list(confirmation_gate_steps(model)),
                    "mandatory": list(mandatory_confirmation_gate_steps(model)),
                }
                before = {"assignable": [], "mandatory": []}
                before_authority = {}
                source = root / relative
                if source.is_file():
                    original_loader = SkillLoader(
                        project_root=root,
                        builtin_root=builtin,
                        read_only=True,
                        resolve_presentation=False,
                    )
                    original = load_playbook_file(
                        source, source="project", skill_loader=original_loader
                    ).model
                    before = {
                        "assignable": list(confirmation_gate_steps(original)),
                        "mandatory": list(mandatory_confirmation_gate_steps(original)),
                    }
                    before_authority = _authority(original)
                result.confirmation_gates[relative] = {
                    "before": before,
                    "after": gates,
                    "stale_stop_contracts": before != gates,
                    "authority_before": before_authority,
                    "authority_after": _authority(model),
                }
                _explicit_authority(model, raw, before_authority, requests, result, relative)
            except Exception as error:
                message = str(error).replace(str(staged), "<candidate>")
                step = re.search(r"[Ss]tep ['\"]([^'\"]+)", message)
                skill = re.search(r"contributor ['\"]([^'\"]+)", message)
                result.diagnose(
                    "contract_invalid",
                    message,
                    target=relative,
                    step=step.group(1) if step else None,
                    skill=skill.group(1) if skill else None,
                    field="runtime_contract",
                )


def _bindings(model, loader, result, target, graph):
    producers = {}
    consumers = {}
    for name, step in model.steps.items():
        for artifact in (step.output_artifact, step.workspace_artifact):
            if artifact:
                producers.setdefault(artifact, []).append(name)
        for artifact in [
            *(step.input_artifacts or []),
            step.workspace_input_artifact,
            step.todo_identity_input_artifact,
        ]:
            if artifact is None:
                continue
            consumers.setdefault(artifact, []).append(name)
        behavior = resolve_step_behavior(model, name)
        for receiver, route in (behavior.feedback_routes or {}).items():
            producers.setdefault(route.artifact, []).append(name)
        for binding in step.human_tasks:
            if binding.feedback_delivery:
                producers.setdefault(binding.feedback_delivery.artifact, []).append(name)
    result.artifact_summary[target] = {
        "producers": producers,
        "consumers": consumers,
        "contributors": {},
    }
    adjacency = {}
    for source, _, dest in graph["edges"]:
        adjacency.setdefault(source, set()).add(dest)
    for source, dest in graph["discretionary_edges"]:
        adjacency.setdefault(source, set()).add(dest)

    def reaches(source, destination):
        pending, seen = list(adjacency.get(source, ())), set()
        while pending:
            node = pending.pop()
            if node == destination:
                return True
            if node not in seen:
                seen.add(node)
                pending.extend(adjacency.get(node, ()))
        return False

    for name, step in model.steps.items():
        workflow = resolve_playbook_skills(
            model, channel="workflow", role=step.role, step_name=name
        )
        for primary in skill_selector_names(step.skill):
            composition = resolve_step_workflow_composition(
                loader, primary_skill=primary, step_name=name, workflow_skills=workflow
            )
            result.artifact_summary[target]["contributors"][f"{name}:{primary}"] = list(
                composition.skill_names
            )
            for contributor in composition.contributors:
                _todos(model, loader, name, contributor, producers, result, target)
                for tool in contributor.declaration.required_tools:
                    if not _tool_requirement_satisfied(tool, step.allowed_tools):
                        result.diagnose(
                            "missing_required_tool",
                            f"Explicitly declare required tool {tool}",
                            target=target,
                            step=name,
                            skill=contributor.source.skill_identity,
                            field="allowed_tools",
                        )
                        result.proposals.append(
                            {
                                "target": target,
                                "step": name,
                                "skill": contributor.source.skill_identity,
                                "operation": {
                                    "op": "upsert",
                                    "path": ["steps", name, "allowed_tools"],
                                    "value": tool,
                                },
                            }
                        )
                for mapping in contributor.declaration.prompt_inputs:
                    if not mapping.required:
                        continue
                    candidates = set(mapping.artifacts) & set(step.input_artifacts or ())
                    available = any(
                        any(
                            owner != name and reaches(owner, name) for owner in producers.get(a, ())
                        )
                        or (step.initial_input and a in {step.initial_input.bind.artifact})
                        for a in candidates
                    )
                    if not available:
                        field = f"workflow.prompt_inputs.{mapping.placeholder}"
                        result.diagnose(
                            "missing_producer",
                            f"Required artifact candidates: {list(mapping.artifacts)}",
                            target=target,
                            step=name,
                            skill=contributor.source.skill_identity,
                            field=field,
                            remedy=(
                                "Declare an explicit input binding and reachable producer "
                                "or initial input"
                            ),
                        )
                        result.proposals.append(
                            {
                                "target": target,
                                "step": name,
                                "skill": contributor.source.skill_identity,
                                "field": field,
                                "required_artifacts": list(mapping.artifacts),
                                "decision": "select_producer",
                            }
                        )
    for artifact, owners in producers.items():
        if artifact not in consumers:
            result.diagnose(
                "terminal_report",
                f"Unconsumed output {artifact} from {owners}",
                target=target,
                field=artifact,
                severity="info",
                remedy="No action required for an intentional terminal report",
            )


def _explicit_authority(model, raw, before, requests, result, target):
    """Effective defaults/selection changes require explicit authority field intent."""
    current = _authority(model)
    for name, fields in current.items():
        old = before.get(name, {})
        for field, value in fields.items():
            if value == old.get(field):
                continue
            # Empty/non-authority defaults do not widen a contract.
            if name not in before and (
                value in ([], {}, None, "agent")
                or field in {"behavior", "hooks"}
                and not any(value.values())
            ):
                continue
            explicit = False
            for request in requests:
                if "playbooks" in Path(request.target).parts and request.target == target:
                    if request.mode == "create":
                        explicit = field in (request.declaration.get("steps", {}).get(name, {}))
                    else:
                        explicit = any(
                            op.path[:3] == ["steps", name, field]
                            or op.path == ["steps", name]
                            and field in op.value
                            for op in request.operations
                        )
                elif request.target.endswith("/SKILL.md"):
                    contributing = set(skill_selector_names(model.steps[name].skill))
                    contributing.update(
                        resolve_playbook_skills(
                            model, channel="workflow", role=model.steps[name].role, step_name=name
                        )
                    )
                    if Path(request.target).parent.name not in contributing:
                        continue
                    if request.mode == "create":
                        workflow = request.declaration.get("workflow", {})
                        explicit = (
                            field in workflow.get("step_defaults", {}).get("values", {})
                            or field == "human_tasks"
                            and field in workflow
                        )
                    else:
                        explicit = any(
                            op.path[:4] == ["metadata", "workflow", "step_defaults", "values"]
                            and (
                                len(op.path) > 4
                                and op.path[4] == field
                                or isinstance(op.value, dict)
                                and field in op.value
                            )
                            or field == "human_tasks"
                            and op.path[:3] == ["metadata", "workflow", "human_tasks"]
                            for op in request.operations
                        )
                if explicit:
                    break
            if not explicit:
                result.diagnose(
                    "implicit_authority",
                    "Effective authority changed without explicit field intent",
                    target=target,
                    step=name,
                    field=field,
                    remedy=(
                        "Explicitly declare the effective authority field in the request; "
                        "this does not grant host permission"
                    ),
                )


def _todos(model, loader, step_name, contributor, producers, result, target):
    declaration = contributor.declaration
    for checklist in (declaration.checklist, declaration.checklist_overlay):
        if not checklist:
            continue
        for variant in checklist.variants:
            for section in variant.sections:
                projection = section.todo_projection
                if not projection:
                    continue
                owners = producers.get(projection.artifact, [])
                if projection.causal:
                    owners = [
                        name
                        for name in model.steps
                        if step_name in (resolve_step_behavior(model, name).feedback_routes or {})
                    ]
                    if not owners:
                        result.diagnose(
                            "missing_causal_route",
                            "Causal Todo projection has no declared correction route",
                            target=target,
                            skill=contributor.source.skill_identity,
                            step=step_name,
                            field="workflow.checklist.todo_projection",
                        )
                    continue
                if not owners:
                    result.diagnose(
                        "missing_todo_producer",
                        "Todo projection needs a declared producer",
                        target=target,
                        skill=contributor.source.skill_identity,
                        step=step_name,
                        field="workflow.checklist.todo_projection",
                    )
                for owner in owners:
                    if owner == step_name:
                        continue  # Serial bridges read the incoming version, not their own output.
                    for primary in skill_selector_names(model.steps[owner].skill):
                        directory = loader.get_skill_dir(primary)
                        corpus = "\n".join(p.read_text() for p in directory.rglob("*.md"))
                        if not all(
                            token in corpus
                            for token in (
                                "## Todo List",
                                "Source:",
                                "Work:",
                                "Closure:",
                                "Evidence:",
                            )
                        ):
                            result.diagnose(
                                "incomplete_todo_contract",
                                "Producer must declare the complete runtime Todo output contract",
                                target=target,
                                skill=primary,
                                step=owner,
                                field="Output",
                                remedy=(
                                    "Supply an explicit output template/instruction with Todo List "
                                    "and Source/Work/Closure/Evidence fields"
                                ),
                            )
