"""Effective playbook index used during staged kickoff preparation."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import yaml
from _kickoff_store import VersionedJsonStore

from cafe.catalogs.resolver import CatalogKind, CatalogResolver, content_digest
from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
from cafe.core.playbook import (
    PlaybookDefinition,
    confirmation_gate_steps,
    iter_declared_playbook_skills,
    mandatory_confirmation_gate_steps,
    normalize_playbook_yaml,
    resolve_playbook_skills,
)
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.exceptions import SkillDiscoveryError
from cafe.skills.execution_profile import resolve_execution_profile
from cafe.skills.loader import SkillLoader
from cafe.skills.selectors import skill_selector_names
from cafe.skills.workflow_composition import resolve_step_workflow_composition
from cafe.utils.yaml_utils import safe_load

SCHEMA_VERSION = 3
_DEPENDENCY_FILES = (
    "src/cafe/utils/yaml_utils.py",
    "src/cafe/catalogs/resolver.py",
    "src/cafe/playbooks/loader.py",
    "src/cafe/skills/loader.py",
    "src/cafe/skills/execution_profile.py",
    "src/cafe/skills/selectors.py",
    "src/cafe/skills/workflow_composition.py",
    "src/cafe/core/playbook.py",
    "src/cafe/core/capabilities.py",
)


def _digest(parts: list[tuple[str, str]]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            ("on" if key is True else "off" if key is False else str(key)): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _dependency_closure(
    playbook: PlaybookDefinition, skill_loader: SkillLoader,
    skill_dependencies: dict[str, list[tuple[str, str]]],
) -> list[tuple[str, str]]:
    dependencies: list[tuple[str, str]] = []
    # Include declarations hidden by a replace overlay: the loader still
    # validates these catalog references even when execution does not use them.
    names = {
        name for field, name in iter_declared_playbook_skills(playbook)
        if field.startswith("skills.workflow.")
    }
    for step in playbook.steps.values():
        names.update(skill_selector_names(step.skill))
    for name in sorted(names):
        if name in skill_dependencies:
            dependencies.extend(skill_dependencies[name])
            continue
        rows: list[tuple[str, str]] = []
        try:
            entry = skill_loader.get_skill_entry(name)
            rows.append((
                f"skill:{name}:effective_identity",
                json.dumps([entry.name, entry.source, str(entry.directory)]),
            ))
            rows.append((f"skill:{name}:effective_digest", content_digest(entry.directory)))
            paths = sorted(entry.directory.rglob("*"))
            for path in paths:
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(entry.directory)
                rows.append((f"skill:{name}/{relative.as_posix()}", hashlib.sha256(path.read_bytes()).hexdigest()))
        except (OSError, ValueError, SkillDiscoveryError) as exc:
            rows.append((f"skill:{name}:diagnostic", type(exc).__name__))
        skill_dependencies[name] = rows
        dependencies.extend(rows)
    return dependencies


def _candidate_details(
    candidate_id: str, path: Path, source: str, project_root: Path,
    validated_model: PlaybookDefinition, skill_loader: SkillLoader,
) -> dict[str, Any]:
    raw = safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Playbook root must be a mapping")
    playbook = raw.get("playbook", {})
    steps = raw.get("steps", {})
    if not isinstance(playbook, dict) or not isinstance(steps, dict):
        raise ValueError("Playbook metadata and steps must be mappings")
    profiles: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, str]] = []
    for step_name, step in validated_model.steps.items():
        selector = step.skill
        try:
            workflow_skills = resolve_playbook_skills(
                validated_model, channel="workflow", role=step.role, step_name=step_name,
            )
            profile = resolve_execution_profile(
                skill_loader,
                selector,
                workflow_skills=workflow_skills,
                step_name=str(step_name),
            )
            required_tools = list(
                dict.fromkeys(
                    tool
                    for name in skill_selector_names(selector)
                    for tool in resolve_step_workflow_composition(
                        skill_loader, primary_skill=name, workflow_skills=workflow_skills,
                        step_name=str(step_name),
                    ).required_tools
                )
            )
            profiles[str(step_name)] = {
                "skills": list(profile.skill_names),
                "required_tools": required_tools,
                "workloads": list(profile.workloads),
                "reasoning": profile.reasoning,
                "risk_domains": list(profile.risk_domains),
                "fallback_strength": profile.fallback_strength,
                "uses_default": profile.uses_default,
            }
        except (ValueError, KeyError, TypeError) as exc:
            diagnostics.append({"step": str(step_name), "status": "incomplete", "reason": type(exc).__name__})
    try:
        declared_gates = confirmation_gate_steps(validated_model)
        mandatory_gates = mandatory_confirmation_gate_steps(validated_model)
    except (ValueError, KeyError, TypeError):
        declared_gates, mandatory_gates = [], []
    requested_capabilities = sorted(
        {
            capability
            for step in validated_model.steps.values()
            for capability in step.capability_requests
        }
    )
    capability_details: dict[str, Any] = {}
    try:
        registry = load_capability_registry(default_capability_definition_dirs(project_root))
        for capability in requested_capabilities:
            manifest = registry.get(capability)
            if manifest is None:
                diagnostics.append({"capability": capability, "status": "incomplete", "reason": "manifest_missing"})
                continue
            capability_details[capability] = {
                "setup_questions": [question.model_dump(mode="json") for question in manifest.setup_questions],
                "approval": manifest.approval,
                "risk": manifest.risk,
            }
    except (OSError, ValueError) as exc:
        diagnostics.append({"status": "incomplete", "reason": f"capability_registry:{type(exc).__name__}"})
    applicability = playbook.get("applicability")
    eligible = (
        isinstance(applicability, dict)
        and bool(str(applicability.get("summary", "")).strip())
        and not diagnostics
    )
    return {
        "id": candidate_id,
        "contract_mode": validated_model.contract.mode,
        "source": source,
        "path": str(path),
        "applicability": applicability if isinstance(applicability, dict) else None,
        "eligible": eligible,
        "roles": raw.get("roles", {}),
        "behavior": raw.get("behavior", {}),
        "skills": raw.get("skills", {}),
        "steps": steps,
        "profiles": profiles,
        "native_subagent_steps": [
            name
            for name, profile in profiles.items()
            if any(
                tool.split("(", 1)[0].casefold() == "agent"
                for tool in profile["required_tools"]
            )
        ],
        "confirmation_gates": list(declared_gates),
        "mandatory_confirmation_gates": list(mandatory_gates),
        "capability_requirements": requested_capabilities,
        "capability_setup": capability_details,
        "diagnostics": diagnostics,
    }

def discover_index(
    *, project_root: Path, global_root: Path, builtin_root: Path, cache_file: Path,
    lightweight: bool = False, selected_id: str | None = None,
) -> dict[str, Any]:
    """Discover effective candidate facts and report per-candidate diagnostics."""
    resolver = CatalogResolver(
        project_root=project_root,
        global_root=global_root,
        builtin_root=builtin_root,
    )
    if lightweight:
        # Resolve effective YAML metadata only. Skill composition, templates,
        # model probes and capabilities belong to selected preparation.
        candidates, diagnostics = [], []
        names = [selected_id] if selected_id else resolver.keys(CatalogKind.PLAYBOOK)
        for candidate_id in names:
            try:
                entry = resolver.resolve(CatalogKind.PLAYBOOK, candidate_id)
                model = PlaybookDefinition.model_validate(normalize_playbook_yaml(
                    safe_load(entry.path.read_text(encoding="utf-8"))))
                applicability = model.playbook.applicability
                candidates.append({
                    "id": candidate_id, "source": entry.source, "path": str(entry.path),
                    "contract_mode": model.contract.mode,
                    "applicability": applicability.model_dump() if applicability else None,
                    "eligible": applicability is not None, "fingerprint": entry.digest,
                })
            except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
                diagnostics.append({"id": candidate_id, "status": "invalid",
                                    "reason": type(exc).__name__, "detail": str(exc)[:250]})
        return {"candidates": candidates, "diagnostics": diagnostics, "reuse": {}}
    loader = PlaybookLoader(
        project_root=project_root, global_root=global_root, builtin_root=builtin_root
    )
    skill_loader = SkillLoader(
        project_root=project_root, global_root=global_root, builtin_root=builtin_root
    )
    skill_dependencies: dict[str, list[tuple[str, str]]] = {}
    skill_discovered = False
    skill_discovery_error: Exception | None = None
    cache = VersionedJsonStore(cache_file, schema_version=SCHEMA_VERSION, collection="candidates")
    previous = cache.read()
    candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []
    reuse: dict[str, bool] = {}
    try:
        names = [selected_id] if selected_id else resolver.keys(CatalogKind.PLAYBOOK)
    except (OSError, ValueError) as exc:
        return {
            "candidates": [],
            "diagnostics": [{"status": "incomplete", "reason": type(exc).__name__}],
            "reuse": {},
        }
    dependency_code: list[tuple[str, str]] = []
    dependencies_available = True
    for relative in _DEPENDENCY_FILES:
        try:
            module_name = relative.removeprefix("src/").removesuffix(".py").replace("/", ".")
            module = importlib.import_module(module_name)
            path = Path(module.__file__)
            dependency_code.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
        except (OSError, ImportError, TypeError):
            dependencies_available = False
            dependency_code.append((relative, "missing"))
            diagnostics.append({"status": "dependency_unavailable", "path": relative})
    for directory in default_capability_definition_dirs(project_root):
        try:
            for path in sorted(directory.glob("*")):
                if path.is_file():
                    dependency_code.append((f"capability:{path.name}", hashlib.sha256(path.read_bytes()).hexdigest()))
        except OSError:
            dependency_code.append((f"capability-root:{directory}", "unreadable"))
    for candidate_id in names:
        try:
            entry = resolver.resolve(CatalogKind.PLAYBOOK, candidate_id)
            raw = safe_load(entry.path.read_text(encoding="utf-8"))
            model = PlaybookDefinition.model_validate(normalize_playbook_yaml(raw))
            dependencies = _dependency_closure(model, skill_loader, skill_dependencies)
            fingerprint = _digest(
                [("entry", entry.digest), ("source", entry.source), *dependencies, *dependency_code]
            )
            cached = previous.get(candidate_id)
            if dependencies_available and isinstance(cached, dict) and cached.get("fingerprint") == fingerprint and isinstance(cached.get("candidate"), dict):
                candidate = cached["candidate"]
                reuse[candidate_id] = True
            else:
                if not skill_discovered:
                    try:
                        skill_loader.discover(strict=False)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        skill_discovery_error = exc
                    skill_discovered = True
                if skill_discovery_error is not None:
                    raise skill_discovery_error
                loaded = loader.load_model(candidate_id, strict=False)
                candidate = _candidate_details(
                    candidate_id, entry.path, entry.source, project_root,
                    loaded.model, skill_loader,
                )
                candidate["fingerprint"] = fingerprint
                candidate = _json_safe(candidate)
                reuse[candidate_id] = False
            candidates.append(candidate)
            cache_record = {"fingerprint": fingerprint, "candidate": candidate}
            previous[candidate_id] = cache_record
        except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
            diagnostics.append({"id": candidate_id, "status": "invalid", "reason": type(exc).__name__, "detail": str(exc)[:250]})
            reuse[candidate_id] = False
    try:
        cache.write(previous)
    except OSError as exc:
        diagnostics.append({"status": "cache_write_failed", "reason": type(exc).__name__})
    return {"candidates": candidates, "diagnostics": diagnostics, "reuse": reuse}
