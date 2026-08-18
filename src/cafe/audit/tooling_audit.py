"""Validate shipped builtin skills and playbooks for tooling consistency."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple, Union

import cafe
from cafe.core.blackboard import HandoffIntent
from cafe.core.hooks import BUILTIN_HOOKS
from cafe.core.playbook import iter_declared_playbook_skills, load_playbook_file
from cafe.core.status_codes import PLAYBOOK_INTENT_KEYS
from cafe.skills.loader import SkillLoader, read_skill_frontmatter


@dataclass(frozen=True)
class AuditLine:
    """One checklist row for ``cafe audit``."""

    ok: bool
    message: str


def cafe_builtin_data_dir() -> Path:
    """Return ``src/cafe/data`` (builtin templates, agents, skills, playbooks)."""
    return Path(cafe.__file__).resolve().parent / "data"


def _isolated_loader_roots() -> Tuple[Path, Path, Path]:
    """Project/global roots that do not shadow builtin discovery."""
    isolated = Path(tempfile.gettempdir()) / "cafe-engine-audit-empty-project"
    isolated.mkdir(exist_ok=True)
    return isolated, isolated, cafe_builtin_data_dir()


def _skill_markdown_bundle(skill_dir: Path) -> str:
    """SKILL.md body plus all ``references/*.md`` (matches runtime expectations)."""
    parts: List[str] = []
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        text = skill_md.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                text = text[end + len("\n---") :].lstrip()
        parts.append(text)
    ref_dir = skill_dir / "references"
    if ref_dir.is_dir():
        for ref in sorted(ref_dir.glob("*.md")):
            parts.append(ref.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _iter_skill_selectors(skill_field: Union[str, Dict[str, str]]) -> List[str]:
    if isinstance(skill_field, str):
        return [skill_field.strip()]
    return [str(v).strip() for v in skill_field.values() if str(v).strip()]


def _resolve_script_path(*, skill_dir: Path, script: str) -> Path:
    script_path = Path(script.strip())
    if script_path.is_absolute():
        raise ValueError("absolute script path")
    parts = list(script_path.parts)
    if any(p == ".." for p in parts):
        raise ValueError("parent traversal in script path")
    if parts and parts[0] == "scripts":
        parts = parts[1:]
    scripts_dir = (skill_dir / "scripts").resolve()
    if not parts:
        raise ValueError("empty script path")
    return (scripts_dir / Path(*parts)).resolve()


def _executable_ok(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _scan_template_path_literals(text: str, *, templates_root: Path) -> List[str]:
    """Return unresolved template path literals like ``templates/spec/foo.md``."""
    issues: List[str] = []
    marker = "templates/"
    start = 0
    while True:
        idx = text.find(marker, start)
        if idx == -1:
            break
        tail = text[idx:]
        end_chars = set("`\n\r\t '\"")
        end = len(tail)
        for j, ch in enumerate(tail):
            if j > 0 and ch in end_chars:
                end = j
                break
        rel = tail[:end]
        if not rel.endswith(".md"):
            start = idx + len(marker)
            continue
        candidate = templates_root / Path(rel[len(marker) :])
        if not candidate.is_file():
            issues.append(f"Missing template file referenced as {rel!r}")
        start = idx + len(marker)
    return issues


def run_builtin_tooling_audit() -> List[AuditLine]:
    """Run all builtin checks; each line is one checklist item."""
    lines: List[AuditLine] = []
    data = cafe_builtin_data_dir()
    agents_root = data / "agents"
    templates_root = data / "templates"
    playbooks_dir = data / "playbooks"
    skills_root = data / "skills"

    intent_ok = PLAYBOOK_INTENT_KEYS <= {e.value for e in HandoffIntent}
    lines.append(
        AuditLine(
            intent_ok,
            "Playbook transition keys are a subset of HandoffIntent enum values",
        )
    )

    project_root, global_root, builtin_root = _isolated_loader_roots()
    skill_loader = SkillLoader(
        project_root=project_root,
        global_root=global_root,
        builtin_root=builtin_root,
    )
    skill_loader.discover(strict=True)

    playbook_skill_refs: Dict[str, List[Tuple[str, str, str, str]]] = {}
    support_skill_refs: set[str] = set()
    skill_needs_output: Dict[str, bool] = {}

    if not playbooks_dir.is_dir():
        lines.append(AuditLine(False, f"Builtin playbooks directory missing: {playbooks_dir}"))
        return lines

    for pb_path in sorted(playbooks_dir.glob("*.yaml")):
        pb_id = pb_path.stem
        try:
            loaded = load_playbook_file(
                pb_path,
                source="builtin",
                skill_loader=skill_loader,
                strict=True,
            )
        except Exception as exc:
            lines.append(AuditLine(False, f"Playbook {pb_id!r} failed to load: {exc}"))
            continue

        lines.append(AuditLine(True, f"Playbook {pb_id!r} loads (strict) with no warnings"))
        model = loaded.model
        roles = model.roles
        support_skill_refs.update(
            skill_name for _, skill_name in iter_declared_playbook_skills(model)
        )

        for step_name, step in model.steps.items():
            role = step.role
            role_cfg = roles.get(role)
            agent_name = str(role_cfg.default_agent) if role_cfg and role_cfg.default_agent else ""
            if not agent_name:
                lines.append(
                    AuditLine(
                        False,
                        f"Playbook {pb_id} step {step_name!r}: role {role!r} has no default_agent",
                    )
                )
                continue

            agent_path = agents_root / role / f"{agent_name}.md"
            ok_agent = agent_path.is_file()
            lines.append(
                AuditLine(
                    ok_agent,
                    f"Playbook {pb_id} step {step_name!r}: agent file exists "
                    f"({agent_path.relative_to(data)})",
                )
            )

            skill_names = _iter_skill_selectors(step.skill)
            has_output = bool(step.output_artifact)
            for skill_name in skill_names:
                playbook_skill_refs.setdefault(skill_name, []).append(
                    (pb_id, step_name, role, agent_name)
                )
                skill_needs_output[skill_name] = skill_needs_output.get(skill_name, False) or has_output

            hooks_dump = step.hooks.model_dump()
            for stage, entries in hooks_dump.items():
                for entry in entries:
                    if isinstance(entry, str):
                        hook_name = str(entry)
                        ok_hook = hook_name in BUILTIN_HOOKS
                        lines.append(
                            AuditLine(
                                ok_hook,
                                f"Playbook {pb_id} step {step_name!r} hook stage {stage}: "
                                f"native hook {hook_name!r} is registered",
                            )
                        )
                    elif isinstance(entry, dict) and "script" in entry:
                        script = str(entry.get("script", "")).strip()
                        for skill_for_script in skill_names:
                            try:
                                sdir = skill_loader.get_skill_dir(skill_for_script)
                                resolved = _resolve_script_path(skill_dir=sdir, script=script)
                            except Exception as exc:
                                lines.append(
                                    AuditLine(
                                        False,
                                        f"Playbook {pb_id} step {step_name!r}: script hook {script!r} "
                                        f"({skill_for_script}): {exc}",
                                    )
                                )
                                continue
                            exists = resolved.is_file()
                            lines.append(
                                AuditLine(
                                    exists,
                                    f"Playbook {pb_id} step {step_name!r}: script hook file exists "
                                    f"({resolved.relative_to(data)} via skill {skill_for_script})",
                                )
                            )
                            if exists:
                                xok = _executable_ok(resolved)
                                lines.append(
                                    AuditLine(
                                        xok,
                                        f"Playbook {pb_id} step {step_name!r}: script hook is executable "
                                        f"({resolved.name}, skill {skill_for_script})",
                                    )
                                )

    skill_dirs = [p for p in skills_root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()]
    for sdir in sorted(skill_dirs, key=lambda p: p.name):
        fm_ok = True
        try:
            fm = read_skill_frontmatter(sdir / "SKILL.md")
            name = str(fm.get("name", sdir.name))
            if name != sdir.name:
                lines.append(
                    AuditLine(
                        False,
                        f"Skill {sdir.name}: frontmatter name {name!r} must match folder name",
                    )
                )
                fm_ok = False
        except Exception as exc:
            lines.append(AuditLine(False, f"Skill {sdir.name}: invalid frontmatter: {exc}"))
            fm_ok = False
        if fm_ok:
            lines.append(AuditLine(True, f"Skill {sdir.name}: SKILL.md frontmatter is valid"))

    referenced = set(playbook_skill_refs.keys()) | support_skill_refs
    all_skill_names = {p.name for p in skill_dirs}

    orphan = sorted(all_skill_names - referenced)
    lines.append(
        AuditLine(
            True,
            "Optional skills not referenced by builtin playbooks: "
            + (", ".join(orphan) if orphan else "(none)"),
        )
    )

    unknown_ref = sorted(referenced - all_skill_names)
    for missing in unknown_ref:
        lines.append(
            AuditLine(
                False,
                f"Playbook references unknown builtin skill {missing!r}",
            )
        )

    for skill_name in sorted(referenced):
        sdir = skills_root / skill_name
        bundle = _skill_markdown_bundle(sdir)
        if skill_name in playbook_skill_refs:
            if "{agent_file}" not in bundle:
                lines.append(
                    AuditLine(
                        False,
                        f"Skill {skill_name}: markdown bundle must mention placeholder {{agent_file}} "
                        "(playbook-bound)",
                    )
                )
            else:
                lines.append(
                    AuditLine(
                        True,
                        f"Skill {skill_name}: bundle documents {{agent_file}}",
                    )
                )

        if skill_name in playbook_skill_refs and skill_needs_output.get(skill_name):
            if "{output_file}" not in bundle:
                lines.append(
                    AuditLine(
                        False,
                        f"Skill {skill_name}: markdown bundle must mention {{output_file}} "
                        "because a referencing step declares output_artifact",
                    )
                )
            else:
                lines.append(
                    AuditLine(
                        True,
                        f"Skill {skill_name}: bundle documents {{output_file}}",
                    )
                )

        tmpl_issues = _scan_template_path_literals(bundle, templates_root=templates_root)
        if tmpl_issues:
            for msg in tmpl_issues:
                lines.append(AuditLine(False, f"Skill {skill_name}: {msg}"))
        else:
            lines.append(
                AuditLine(
                    True,
                    f"Skill {skill_name}: no broken builtin template path literals",
                )
            )

    return lines


def format_audit_markdown(lines: Sequence[AuditLine]) -> str:
    """Render checklist markdown."""
    out_lines = ["# CAFE builtin tooling audit", ""]
    for row in lines:
        mark = "[x]" if row.ok else "[ ]"
        out_lines.append(f"- {mark} {row.message}")
    return "\n".join(out_lines).strip() + "\n"
