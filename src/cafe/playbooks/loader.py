"""Playbook loader and schema validation."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml

from cafe.utils.config import get_global_cafe_dir


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

    def list_playbooks(self) -> List[str]:
        names = set()
        for root in self._roots():
            if not root.exists():
                continue
            for file in root.glob("*.yaml"):
                names.add(file.stem)
        return sorted(names)

    def _resolve_path(self, name: str) -> Path:
        filename = f"{name}.yaml" if not name.endswith(".yaml") else name
        for root in reversed(self._roots()):
            path = root / filename
            if path.exists():
                return path
        raise FileNotFoundError(f"Playbook not found: {name}")

    @staticmethod
    def _normalize_step_keys(data: Dict) -> Dict:
        """Normalize YAML 1.1 bool-converted keys like `on` -> True."""
        steps = data.get("steps")
        if not isinstance(steps, dict):
            return data

        for step in steps.values():
            if not isinstance(step, dict):
                continue
            if "on" not in step and True in step and isinstance(step[True], dict):
                step["on"] = step.pop(True)
        return data

    @staticmethod
    def _validate_schema(data: Dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("Playbook must be a mapping")

        playbook_meta = data.get("playbook")
        if not isinstance(playbook_meta, dict):
            raise ValueError("Missing playbook metadata")
        if not playbook_meta.get("id"):
            raise ValueError("playbook.id is required")

        steps = data.get("steps")
        if not isinstance(steps, dict) or not steps:
            raise ValueError("steps must be a non-empty mapping")

        for step_name, step in steps.items():
            if not isinstance(step, dict):
                raise ValueError(f"Step '{step_name}' must be a mapping")
            if "role" not in step:
                raise ValueError(f"Step '{step_name}' missing role")
            if "skill" not in step:
                raise ValueError(f"Step '{step_name}' missing skill")
            if "valid_status_codes" not in step or not isinstance(step["valid_status_codes"], list):
                raise ValueError(f"Step '{step_name}' missing valid_status_codes list")
            if "on" not in step or not isinstance(step["on"], dict):
                raise ValueError(f"Step '{step_name}' missing on transition map")

    def load(self, name: str) -> Dict:
        path = self._resolve_path(name)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data is None:
            raise ValueError(f"Playbook is empty: {path}")
        data = self._normalize_step_keys(data)
        self._validate_schema(data)
        return data
