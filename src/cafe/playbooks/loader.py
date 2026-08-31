"""Playbook loader compatibility wrapper."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from cafe.catalogs.resolver import CatalogKind, CatalogResolver, global_catalog_lock
from cafe.core.playbook import LoadedPlaybook, load_playbook_file
from cafe.skills.loader import SkillLoader


def apply_issue_playbook_overrides(
    playbook: Dict[str, Any], issue_config_path: Path
) -> Dict[str, Any]:
    """Apply the deliberately narrow per-issue playbook override contract."""
    if not issue_config_path.is_file():
        return playbook
    try:
        loaded_issue_config = yaml.safe_load(
            issue_config_path.read_text(encoding="utf-8")
        )
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"issue.yaml is unreadable: {exc}") from exc
    issue_config = {} if loaded_issue_config is None else loaded_issue_config
    if not isinstance(issue_config, dict):
        raise ValueError("issue.yaml must contain a mapping")
    overrides = issue_config.get("playbook_overrides")
    if overrides is None:
        return playbook
    if not isinstance(overrides, dict):
        raise ValueError("playbook_overrides must be a mapping")
    unsupported_root = sorted(str(key) for key in set(overrides) - {"steps"})
    if unsupported_root:
        raise ValueError(
            "playbook_overrides supports only 'steps'; unsupported field(s): "
            + ", ".join(unsupported_root)
        )
    step_overrides = overrides.get("steps", {})
    if not isinstance(step_overrides, dict):
        raise ValueError("playbook_overrides.steps must be a mapping")

    resolved = deepcopy(playbook)
    playbook_steps = resolved.get("steps")
    if not isinstance(playbook_steps, dict):
        raise ValueError("playbook steps must be a mapping")
    for step_name, step_override in step_overrides.items():
        field_path = f"playbook_overrides.steps.{step_name}"
        if step_name not in playbook_steps:
            raise ValueError(f"{field_path} names unknown playbook step '{step_name}'")
        if not isinstance(step_override, dict):
            raise ValueError(f"{field_path} must be a mapping")
        supported_attempt_limits = {"max_attempts_per_cycle", "max_iterations"}
        unsupported = sorted(str(key) for key in set(step_override) - supported_attempt_limits)
        if unsupported:
            raise ValueError(
                f"{field_path} supports only max_attempts_per_cycle; unsupported field(s): "
                + ", ".join(unsupported)
            )
        declared_attempt_limits = supported_attempt_limits.intersection(step_override)
        if len(declared_attempt_limits) > 1:
            raise ValueError(
                f"{field_path} cannot declare both max_attempts_per_cycle and "
                "legacy max_iterations"
            )
        if not declared_attempt_limits:
            continue
        attempt_limit_field = declared_attempt_limits.pop()
        max_attempts_per_cycle = step_override[attempt_limit_field]
        if isinstance(max_attempts_per_cycle, bool) or not isinstance(
            max_attempts_per_cycle, int
        ):
            raise ValueError(
                f"{field_path}.max_attempts_per_cycle must be a positive integer"
            )
        if max_attempts_per_cycle < 1:
            raise ValueError(
                f"{field_path}.max_attempts_per_cycle must be a positive integer"
            )
        playbook_steps[step_name].pop("max_iterations", None)
        playbook_steps[step_name]["max_attempts_per_cycle"] = max_attempts_per_cycle
    return resolved


class PlaybookLoader:
    """Load playbooks with project/global/builtin override precedence."""

    def __init__(
        self,
        *,
        project_root: Optional[Path] = None,
        global_root: Optional[Path] = None,
        builtin_root: Optional[Path] = None,
    ) -> None:
        self.resolver = CatalogResolver(
            project_root=project_root,
            global_root=global_root,
            builtin_root=builtin_root,
        )
        self.project_root = self.resolver.project_root
        self.global_root = self.resolver.global_root
        self.builtin_root = self.resolver.builtin_root

    @staticmethod
    def _find_project_root(start: Path) -> Path:
        current = start.resolve()
        while current != current.parent:
            if (current / ".cafe").exists():
                return current
            current = current.parent
        return start.resolve()

    def _roots(self) -> List[Path]:
        return [root for _source, root, _layer in self.resolver.catalog_roots(CatalogKind.PLAYBOOK)]

    def _source_roots(self) -> List[Tuple[str, Path]]:
        return [
            (source, root)
            for source, root, _layer in self.resolver.catalog_roots(CatalogKind.PLAYBOOK)
        ]

    def list_playbooks(self) -> List[str]:
        return self.resolver.keys(CatalogKind.PLAYBOOK)

    def _resolve_path(self, name: str) -> tuple[str, Path]:
        entry = self.resolver.resolve(CatalogKind.PLAYBOOK, name)
        return entry.source, entry.path

    def load_model(self, name: str, *, strict: bool = False) -> LoadedPlaybook:
        with global_catalog_lock(self.global_root):
            source, path = self._resolve_path(name)
            skill_loader = SkillLoader(
                project_root=self.project_root,
                global_root=self.global_root,
                builtin_root=self.builtin_root,
            )
            skill_loader.discover(strict=strict)
            return load_playbook_file(
                path,
                source=source,
                skill_loader=skill_loader,
                strict=strict,
            )

    def load(self, name: str, *, strict: bool = False) -> Dict:
        return self.load_model(name, strict=strict).as_dict()
