"""Playbook loader compatibility wrapper."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from cafe.core.playbook import LoadedPlaybook, load_playbook_file
from cafe.skills.loader import SkillLoader
from cafe.utils.config import get_global_cafe_dir


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
        unsupported = sorted(
            str(key) for key in set(step_override) - {"max_iterations"}
        )
        if unsupported:
            raise ValueError(
                f"{field_path} supports only max_iterations; unsupported field(s): "
                + ", ".join(unsupported)
            )
        if "max_iterations" not in step_override:
            continue
        max_iterations = step_override["max_iterations"]
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
            raise ValueError(f"{field_path}.max_iterations must be a positive integer")
        if max_iterations < 1:
            raise ValueError(f"{field_path}.max_iterations must be a positive integer")
        playbook_steps[step_name]["max_iterations"] = max_iterations
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
        self.project_root = project_root or self._find_project_root(Path.cwd())
        self.global_root = global_root or get_global_cafe_dir()
        self.builtin_root = builtin_root or (Path(__file__).parent.parent / "data")

    @staticmethod
    def _find_project_root(start: Path) -> Path:
        current = start.resolve()
        while current != current.parent:
            if (current / ".cafe").exists():
                return current
            current = current.parent
        return start.resolve()

    def _roots(self) -> List[Path]:
        return [
            self.builtin_root / "playbooks",
            self.global_root / "playbooks",
            self.project_root / ".cafe" / "playbooks",
        ]

    def _source_roots(self) -> List[Tuple[str, Path]]:
        return [
            ("builtin", self.builtin_root / "playbooks"),
            ("global", self.global_root / "playbooks"),
            ("project", self.project_root / ".cafe" / "playbooks"),
        ]

    def list_playbooks(self) -> List[str]:
        names = set()
        for root in self._roots():
            if not root.exists():
                continue
            for file in root.glob("*.yaml"):
                names.add(file.stem)
        return sorted(names)

    def _resolve_path(self, name: str) -> tuple[str, Path]:
        filename = f"{name}.yaml" if not name.endswith(".yaml") else name
        for source, root in reversed(self._source_roots()):
            path = root / filename
            if path.exists():
                return source, path
        raise FileNotFoundError(f"Playbook not found: {name}")

    def load_model(self, name: str, *, strict: bool = False) -> LoadedPlaybook:
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
