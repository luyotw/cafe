"""Install resolved CAFE skills into CLI-native skills directories."""

from __future__ import annotations

import shutil
from pathlib import Path

from cafe.core.types import AgentCLI
from cafe.skills.loader import SkillLoader


class NativeSkillBridge:
    """Bridge resolved CAFE skills into each CLI's native skill directory."""

    SKILL_NAME_PREFIX = "cafe-"

    CLI_PREFIXES = {
        AgentCLI.CODEX: "$",
        AgentCLI.CLAUDE: "/",
        AgentCLI.GEMINI: "/",
        AgentCLI.CURSOR: "/",
        AgentCLI.COPILOT: "/",
    }

    CLI_SKILL_DIRS = {
        AgentCLI.CODEX: ".codex/skills",
        AgentCLI.CLAUDE: ".claude/skills",
        AgentCLI.GEMINI: ".gemini/skills",
        AgentCLI.CURSOR: ".cursor-agent/skills",
        AgentCLI.COPILOT: ".copilot/skills",
    }

    def __init__(
        self,
        skill_loader: SkillLoader,
        *,
        project_root: Path | None = None,
        home_dir: Path | None = None,
    ) -> None:
        self.skill_loader = skill_loader
        self.project_root = (project_root or skill_loader.project_root).resolve()
        self.home_dir = (home_dir or Path.home()).resolve()

    def get_native_skills_dir(self, cli: AgentCLI) -> Path:
        """Return the native skills directory for one CLI."""
        return self.project_root / self.CLI_SKILL_DIRS[cli]

    def get_global_native_skills_dir(self, cli: AgentCLI) -> Path:
        """Return the user-level native skills directory for one CLI."""
        return self.home_dir / self.CLI_SKILL_DIRS[cli]

    def get_installed_skill_name(self, name: str) -> str:
        """Return the installed skill folder/invocation name."""
        return f"{self.SKILL_NAME_PREFIX}{name}"

    def install_skill(self, name: str, cli: AgentCLI) -> Path:
        """Install one resolved skill into the target CLI-native directory."""
        source_dir = self.skill_loader.get_skill_dir(name)
        target_dir = self.get_native_skills_dir(cli) / self.get_installed_skill_name(name)
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
        return target_dir

    def install_skills(self, names: list[str], cli: AgentCLI) -> list[Path]:
        """Install a list of skills for one CLI."""
        installed: list[Path] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            installed.append(self.install_skill(name, cli))
        return installed

    def get_invocation(self, name: str, cli: AgentCLI) -> str:
        """Return the CLI-native invocation syntax for one skill."""
        prefix = self.CLI_PREFIXES[cli]
        return f"{prefix}{self.get_installed_skill_name(name)}"
