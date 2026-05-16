"""Crew configuration management for CAFE."""

from pathlib import Path
from typing import Any, Dict

import yaml


class CrewManager:
    """Manages crew.yaml — the agent role-to-CLI mapping file."""

    def __init__(self, cafe_dir: Path = Path(".cafe")) -> None:
        self.cafe_dir = cafe_dir

    @property
    def crew_file(self) -> Path:
        return self.cafe_dir / "crew.yaml"

    @property
    def config_file(self) -> Path:
        return self.cafe_dir / "config.yaml"

    def exists(self) -> bool:
        return self.crew_file.exists()

    def load(self) -> Dict[str, Any]:
        """Load crew configuration.

        Priority: crew.yaml > config.yaml agents: section > empty dict.
        """
        if self.crew_file.exists():
            try:
                data = yaml.safe_load(self.crew_file.read_text())
                return data if isinstance(data, dict) else {}
            except yaml.YAMLError:
                return {}

        if self.config_file.exists():
            try:
                data = yaml.safe_load(self.config_file.read_text())
                if isinstance(data, dict):
                    agents = data.get("agents")
                    if isinstance(agents, dict):
                        return agents
            except yaml.YAMLError:
                pass

        return {}

    def save(self, agents: Dict[str, Any]) -> None:
        """Write agents config to crew.yaml."""
        self.cafe_dir.mkdir(parents=True, exist_ok=True)
        with open(self.crew_file, "w") as f:
            yaml.dump(agents, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
