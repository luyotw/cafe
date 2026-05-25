"""Playbook loader compatibility wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cafe.core.playbook import LoadedPlaybook, load_playbook_file
from cafe.skills.loader import SkillLoader
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
