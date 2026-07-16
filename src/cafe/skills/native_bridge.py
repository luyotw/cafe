"""Install resolved CAFE skills into CLI-native skills directories."""

from __future__ import annotations

import shutil
import subprocess
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

    GLOBAL_CLI_SKILL_DIRS = {
        **CLI_SKILL_DIRS,
        AgentCLI.CURSOR: ".cursor/skills",
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
        return self.home_dir / self.GLOBAL_CLI_SKILL_DIRS[cli]

    def get_installed_skill_name(self, name: str) -> str:
        """Return the installed skill folder/invocation name.

        Skills install verbatim under their resolved catalog folder name.
        Builtin workflow skills already carry the ``cafe-`` prefix in their
        folder names, so no rename happens at copy time; deprecated aliases
        (e.g. ``spec``) resolve to the prefixed folder.
        """
        try:
            return self.skill_loader.get_skill_dir(name).name
        except (SkillDiscoveryError, FileNotFoundError):
            return name

    def install_skill(
        self,
        name: str,
        cli: AgentCLI,
        context: dict[str, str] | None = None,
    ) -> Path:
        """Install one resolved skill into the project-local CLI-native directory.

        Using the worktree-local skill directory avoids cross-process races when
        multiple CAFE workflows install/update the same native skill in parallel.
        """
        source_dir = self.skill_loader.get_skill_dir(name)
        target_dir = self.get_native_skills_dir(cli) / self.get_installed_skill_name(name)
        skills_root = target_dir.parent
        # Recover from invalid roots (regular file or dangling symlink), which can
        # otherwise raise FileExistsError even with exist_ok=True.
        if skills_root.is_symlink() and not skills_root.exists():
            skills_root.unlink()
        elif skills_root.exists() and not skills_root.is_dir():
            skills_root.unlink()
        skills_root.mkdir(parents=True, exist_ok=True)
        self._ensure_cli_dir_git_excluded(cli)

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
        if context:
            skill_file = target_dir / "SKILL.md"
            rendered = skill_file.read_text(encoding="utf-8")
            for key, value in context.items():
                rendered = rendered.replace(f"{{{key}}}", str(value))
            skill_file.write_text(rendered, encoding="utf-8")
        return target_dir

    def _ensure_cli_dir_git_excluded(self, cli: AgentCLI) -> None:
        """Best-effort: add the CLI's top-level injection dir to git's local
        excludes so these CAFE-managed native-skill dirs never count as
        uncommitted changes.

        An untracked dir like ``.codex/`` otherwise makes ``git status`` dirty,
        which blocks chat-handoff consumption in the workflow runner. We write to
        ``.git/info/exclude`` (worktree-local, does not touch the tracked
        ``.gitignore``). Failures are swallowed — this is a convenience only.
        """
        top = Path(self.CLI_SKILL_DIRS[cli]).parts[0]  # e.g. ".codex"
        entry = f"/{top}/"
        try:
            result = subprocess.run(
                ["git", "-C", str(self.project_root), "rev-parse", "--git-path", "info/exclude"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return
            exclude_path = Path(result.stdout.strip())
            if not exclude_path.is_absolute():
                exclude_path = self.project_root / exclude_path
            existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
            if any(line.strip() == entry for line in existing.splitlines()):
                return
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            with exclude_path.open("a", encoding="utf-8") as handle:
                if existing and not existing.endswith("\n"):
                    handle.write("\n")
                handle.write(f"# CAFE native-skill CLI dir (auto-excluded)\n{entry}\n")
        except Exception:
            return

    def install_skills(
        self,
        names: list[str],
        cli: AgentCLI,
        context: dict[str, str] | None = None,
    ) -> list[Path]:
        """Install a list of skills for one CLI."""
        installed: list[Path] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            installed.append(self.install_skill(name, cli, context=context))
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
