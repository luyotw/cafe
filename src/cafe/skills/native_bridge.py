"""Install resolved CAFE skills into CLI-native skills directories."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cafe.core.types import AgentCLI
from cafe.skills.exceptions import SkillDiscoveryError
from cafe.skills.loader import SkillLoader


@dataclass(frozen=True)
class SkillValidationResult:
    """Result of validating skill availability for a given CLI."""

    cli: AgentCLI
    available: list[str]
    missing: list[str]

    @property
    def valid(self) -> bool:
        """Return True when every requested skill is available."""
        return not self.missing


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
        """Install one resolved skill into the user-level CLI-native directory."""
        source_dir = self.skill_loader.get_skill_dir(name)
        target_dir = self.get_global_native_skills_dir(cli) / self.get_installed_skill_name(name)
        skills_root = target_dir.parent
        # Recover from invalid roots (regular file or dangling symlink), which can
        # otherwise raise FileExistsError even with exist_ok=True.
        if skills_root.is_symlink() and not skills_root.exists():
            skills_root.unlink()
        elif skills_root.exists() and not skills_root.is_dir():
            skills_root.unlink()
        skills_root.mkdir(parents=True, exist_ok=True)

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

    def validate_skills(self, names: list[str], cli: AgentCLI) -> SkillValidationResult:
        """Check which skills are discoverable for a given CLI without installing anything."""
        available: list[str] = []
        missing: list[str] = []
        for name in names:
            try:
                self.skill_loader.get_skill_dir(name)
                available.append(name)
            except (SkillDiscoveryError, FileNotFoundError):
                missing.append(name)
        return SkillValidationResult(cli=cli, available=available, missing=missing)
