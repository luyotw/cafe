"""Preset management for CAFE crew configurations."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import importlib.resources
import shutil
import yaml


class PresetNotFoundError(Exception):
    """Raised when a requested preset cannot be found."""
    pass


class PresetAlreadyExistsError(Exception):
    """Raised when a preset already exists and overwrite is not requested."""
    pass


@dataclass
class PresetInfo:
    name: str
    path: Path
    source: str  # "built-in" | "global" | "project"


class PresetManager:
    """Manages crew preset files across project, global, and built-in layers."""

    def list(self) -> List[PresetInfo]:
        """Return all available presets, ordered: project, global, built-in (deduplicated by name)."""
        seen: dict[str, PresetInfo] = {}

        for info in self._project_presets():
            if info.name not in seen:
                seen[info.name] = info

        for info in self._global_presets():
            if info.name not in seen:
                seen[info.name] = info

        for info in self._built_in_presets():
            if info.name not in seen:
                seen[info.name] = info

        return list(seen.values())

    def find(self, name: str) -> Optional[PresetInfo]:
        """Find a preset by name, respecting the priority order."""
        for info in self.list():
            if info.name == name:
                return info
        return None

    def apply(self, name: str, cafe_dir: Path = Path(".cafe")) -> None:
        """Copy the named preset to .cafe/crew.yaml."""
        info = self.find(name)
        if info is None:
            available = [p.name for p in self.list()]
            raise PresetNotFoundError(
                f"Preset '{name}' not found. Available: {available}"
            )
        crew_yaml = cafe_dir / "crew.yaml"
        shutil.copy2(info.path, crew_yaml)

    def save(self, name: str, *, local: bool = False, overwrite: bool = False,
             cafe_dir: Path = Path(".cafe")) -> Path:
        """Save the current crew.yaml as a preset.

        Args:
            name: Preset name (without .yaml extension)
            local: If True, save to project-level .cafe/presets/; otherwise ~/.cafe/presets/
            overwrite: If True, overwrite existing preset without raising
            cafe_dir: Project .cafe directory

        Returns:
            Path where the preset was saved
        """
        crew_yaml = cafe_dir / "crew.yaml"
        if not crew_yaml.exists():
            raise FileNotFoundError(f"No crew.yaml found at {crew_yaml}")

        if local:
            dest_dir = cafe_dir / "presets"
        else:
            dest_dir = Path.home() / ".cafe" / "presets"

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.yaml"

        if dest.exists() and not overwrite:
            raise PresetAlreadyExistsError(
                f"Preset '{name}' already exists at {dest}. Use overwrite=True to replace."
            )

        shutil.copy2(crew_yaml, dest)
        return dest

    def _project_presets(self) -> List[PresetInfo]:
        presets_dir = Path.cwd() / ".cafe" / "presets"
        return self._scan_dir(presets_dir, "project")

    def _global_presets(self) -> List[PresetInfo]:
        presets_dir = Path.home() / ".cafe" / "presets"
        return self._scan_dir(presets_dir, "global")

    def _built_in_presets(self) -> List[PresetInfo]:
        try:
            pkg = importlib.resources.files("cafe.data.presets")
            result = []
            for item in pkg.iterdir():
                if item.name.endswith(".yaml"):
                    name = item.name[:-5]
                    # Materialize path for consistent access
                    with importlib.resources.as_file(item) as p:
                        result.append(PresetInfo(name=name, path=Path(p), source="built-in"))
            return result
        except Exception:
            # Fallback: resolve via filesystem path (development mode)
            built_in_dir = Path(__file__).parent.parent / "data" / "presets"
            return self._scan_dir(built_in_dir, "built-in")

    @staticmethod
    def _scan_dir(directory: Path, source: str) -> List[PresetInfo]:
        if not directory.exists():
            return []
        return [
            PresetInfo(name=f.stem, path=f, source=source)
            for f in sorted(directory.glob("*.yaml"))
        ]
