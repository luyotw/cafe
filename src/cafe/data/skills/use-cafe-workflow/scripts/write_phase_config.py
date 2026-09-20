#!/usr/bin/env python3
"""Atomically install confirmed issue-owned phase execution chains."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml

from cafe.catalogs.resolver import CatalogResolver
from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides
from cafe.utils.phase_config import load_phase_step_model
from cafe.workflow_execution.phase_bindings import (
    default_agent_for_step,
    phase_config_paths_for_project,
    resolve_phase_bindings,
)


def _load_confirmed_chains(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("confirmed phase chains must be a non-empty JSON object")
    return raw


def _candidate_document(
    chains: dict[str, Any], *, playbook: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for raw_step, raw_config in chains.items():
        step = str(raw_step).strip()
        if not step or not isinstance(raw_config, dict):
            raise ValueError("each confirmed phase chain must have a step name and mapping")
        allowed = {"name", "role", "clis"}
        unknown = set(raw_config) - allowed
        if unknown:
            raise ValueError(f"unsupported fields for step '{step}': {', '.join(sorted(unknown))}")
        name = raw_config.get("name")
        if name is None and playbook is not None:
            name = default_agent_for_step(playbook=playbook, step_name=step)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"step '{step}' must include a non-empty agent name")
        clis = raw_config.get("clis")
        if not isinstance(clis, list) or not clis:
            raise ValueError(f"step '{step}' must include a primary CLI")
        document[step] = {**raw_config, "name": name.strip()}
    return document


def _project_root_from_target(target: Path) -> Path:
    resolved_target = target.resolve()
    if resolved_target.name != "phases.yaml" or resolved_target.parent.name != ".cafe":
        raise ValueError("phase-config target must be <project-root>/.cafe/phases.yaml")
    return resolved_target.parent.parent


def _load_issue_mapping(issue_config: Path) -> Mapping[str, Any]:
    if not issue_config.is_file():
        raise ValueError(f"issue config is missing: {issue_config}")
    try:
        raw = yaml.safe_load(issue_config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"issue config is unreadable: {issue_config}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError(f"issue config must contain a mapping: {issue_config}")
    return raw


def _playbook_id_from_issue_config(issue_config: Path) -> str:
    playbook_id = _load_issue_mapping(issue_config).get("playbook_id")
    if not isinstance(playbook_id, str) or not playbook_id.strip():
        raise ValueError(f"issue config has no playbook_id: {issue_config}")
    return playbook_id.strip()


def _load_effective_playbook(
    *, project_root: Path, playbook_id: str, issue_config: Path
) -> dict[str, Any]:
    configured_playbook_id = _playbook_id_from_issue_config(issue_config)
    if configured_playbook_id != playbook_id:
        raise ValueError(
            f"requested playbook_id '{playbook_id}' differs from issue config "
            f"'{configured_playbook_id}'"
        )
    playbook = PlaybookLoader(project_root=project_root).load(playbook_id)
    return apply_issue_playbook_overrides(playbook, issue_config)


def _discover_target_issue_config(project_root: Path) -> Path | None:
    issues_dir = project_root / ".cafe" / "issues"
    if not issues_dir.exists():
        return None
    if not issues_dir.is_dir():
        raise ValueError(f"issue directory is not a directory: {issues_dir}")
    try:
        candidates = sorted(
            issue_dir / "issue.yaml"
            for issue_dir in issues_dir.iterdir()
            if issue_dir.is_dir() and (issue_dir / "issue.yaml").is_file()
        )
    except OSError as exc:
        raise ValueError(f"issue directory is unreadable: {issues_dir}") from exc
    if len(candidates) > 1:
        raise ValueError(
            "phase-config target has multiple issue configs; pass --playbook-id and "
            "--issue-config explicitly"
        )
    return candidates[0] if candidates else None


def _requires_issue_context(project_root: Path) -> bool:
    """Whether a standard target belongs to a repository or CAFE workflow."""
    return (project_root / ".git").exists() or (project_root / ".cafe" / "issues").exists()


def _effective_writer_context(
    *,
    target: Path,
    playbook: Mapping[str, Any] | None,
    project_root: Path | None,
) -> tuple[Mapping[str, Any] | None, Path | None]:
    try:
        target_project_root = _project_root_from_target(target)
    except ValueError:
        target_project_root = None

    requested_project_root = Path(project_root).resolve() if project_root is not None else None
    if requested_project_root is not None and target_project_root is not None:
        if requested_project_root != target_project_root:
            raise ValueError("--project-root must match the phase-config target checkout")
    resolved_project_root = requested_project_root or target_project_root
    if playbook is not None:
        if resolved_project_root is None:
            raise ValueError("semantic phase validation requires a standard phase-config target")
        return playbook, resolved_project_root
    if resolved_project_root is None:
        return None, None

    issue_config = _discover_target_issue_config(resolved_project_root)
    if issue_config is None:
        if _requires_issue_context(resolved_project_root):
            raise ValueError(
                "phase-config target has no issue config; pass --playbook-id and "
                "--issue-config explicitly"
            )
        return None, resolved_project_root
    return (
        _load_effective_playbook(
            project_root=resolved_project_root,
            playbook_id=_playbook_id_from_issue_config(issue_config),
            issue_config=issue_config,
        ),
        resolved_project_root,
    )


def write_phase_config(
    *,
    chains_file: Path,
    target: Path,
    playbook: Mapping[str, Any] | None = None,
    project_root: Path | None = None,
    catalog_resolver: CatalogResolver | None = None,
) -> None:
    effective_playbook, resolved_project_root = _effective_writer_context(
        target=target,
        playbook=playbook,
        project_root=project_root,
    )
    document = _candidate_document(_load_confirmed_chains(chains_file), playbook=effective_playbook)
    resolver = None
    phase_paths = None
    if effective_playbook is not None:
        assert resolved_project_root is not None
        resolver = catalog_resolver or CatalogResolver(project_root=resolved_project_root)
        phase_paths = phase_config_paths_for_project(
            project_root=resolved_project_root,
            catalog_resolver=resolver,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        for step in document:
            load_phase_step_model(step_name=step, local_path=temporary)
        if effective_playbook is not None:
            assert resolver is not None
            assert phase_paths is not None
            resolve_phase_bindings(
                playbook=effective_playbook,
                step_names=document,
                local_path=temporary,
                repo_path=phase_paths.repo_path,
                catalog_resolver=resolver,
            )
            resolve_phase_bindings(
                playbook=effective_playbook,
                local_path=temporary,
                repo_path=phase_paths.repo_path,
                catalog_resolver=resolver,
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install confirmed phase chains in the active worktree."
    )
    parser.add_argument("--chains-json", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path(".cafe/phases.yaml"))
    parser.add_argument("--playbook-id")
    parser.add_argument("--issue-config", type=Path)
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    try:
        if args.playbook_id is None:
            if args.issue_config is not None or args.project_root is not None:
                raise ValueError("--issue-config and --project-root require --playbook-id")
            write_phase_config(chains_file=args.chains_json, target=args.target)
        else:
            if args.issue_config is None:
                raise ValueError("--playbook-id requires --issue-config")
            project_root = (
                args.project_root.resolve()
                if args.project_root is not None
                else _project_root_from_target(args.target)
            )
            issue_config = (
                args.issue_config
                if args.issue_config.is_absolute()
                else project_root / args.issue_config
            )
            write_phase_config(
                chains_file=args.chains_json,
                target=args.target,
                playbook=_load_effective_playbook(
                    project_root=project_root,
                    playbook_id=args.playbook_id,
                    issue_config=issue_config,
                ),
                project_root=project_root,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
