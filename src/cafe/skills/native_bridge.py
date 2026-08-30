"""Install resolved CAFE skills into CLI-native skills directories."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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

    MANAGED_SKILLS_MANIFEST = ".cafe-managed-skills.json"

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

    def _ensure_native_skills_dir(self, cli: AgentCLI) -> Path:
        """Return a usable project-local native skill directory for one CLI."""
        skills_root = self.get_native_skills_dir(cli)
        relative = skills_root.relative_to(self.project_root)
        current = self.project_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                if current == skills_root and not current.exists():
                    current.unlink()
                    continue
                raise SkillDiscoveryError(
                    f"Refusing to traverse project-local CLI skill symlink: {current}"
                )
            if current.exists() and not current.is_dir():
                if current != skills_root:
                    raise SkillDiscoveryError(
                        f"CLI skill directory ancestor is not a directory: {current}"
                    )
                current.unlink()
        skills_root.mkdir(parents=True, exist_ok=True)
        if not skills_root.resolve().is_relative_to(self.project_root):
            raise SkillDiscoveryError(
                f"CLI skill directory escapes the project root: {skills_root}"
            )
        self._ensure_cli_dir_git_excluded(cli)
        return skills_root

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
        target_dir = self._ensure_native_skills_dir(cli) / self.get_installed_skill_name(name)

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
        if context:
            skill_file = target_dir / "SKILL.md"
            rendered = skill_file.read_text(encoding="utf-8")
            for key, value in context.items():
                rendered = rendered.replace(f"{{{key}}}", str(value))
            skill_file.write_text(rendered, encoding="utf-8")
        self._record_managed_skill(cli, target_dir.name)
        return target_dir

    @staticmethod
    def provider_aware_invocation(invocations: Mapping[AgentCLI, str]) -> str:
        """Describe one installed skill across the effective CLI fallback chain."""
        grouped: dict[str, list[str]] = {}
        for cli, invocation in invocations.items():
            grouped.setdefault(invocation, []).append(cli.value)
        if not grouped:
            raise ValueError("at least one CLI skill invocation is required")
        if len(grouped) == 1:
            return next(iter(grouped))
        choices = "; ".join(
            f"{invocation} for {', '.join(cli_names)}" for invocation, cli_names in grouped.items()
        )
        return f"select the invocation for the CLI executing this prompt: {choices}"

    def synchronize_skills(
        self,
        names: list[str],
        cli: AgentCLI,
        context: dict[str, str] | None = None,
        *,
        install: bool = True,
    ) -> list[Path]:
        """Make the CLI-native CAFE skill directory match one resolved environment.

        A workflow or chat environment is an exact set, not an additive install.
        The manifest lets the bridge remove CAFE-managed entries without touching
        unrelated native skills. On first use, existing catalog-backed entries
        are treated as legacy CAFE installs so an upgrade also clears stale skills.
        """
        desired: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name in names:
            installed_name = self.skill_loader.get_skill_dir(name).name
            if installed_name in seen:
                continue
            seen.add(installed_name)
            desired.append((name, installed_name))

        self._ensure_native_skills_dir(cli)
        managed = self._read_managed_skills(cli)
        if managed is None:
            managed = self._legacy_managed_skills(cli)
        desired_names = {installed_name for _, installed_name in desired}
        for stale_name in managed - desired_names:
            self._remove_managed_skill(cli, stale_name)
        self._write_managed_skills(cli, desired_names)
        if not install:
            return []
        return [self.install_skill(name, cli, context=context) for name, _ in desired]

    def _managed_skills_manifest(self, cli: AgentCLI) -> Path:
        return self.get_native_skills_dir(cli) / self.MANAGED_SKILLS_MANIFEST

    def _read_managed_skills(self, cli: AgentCLI) -> set[str] | None:
        manifest = self._managed_skills_manifest(cli)
        if not manifest.is_file():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, list):
            return None
        return {name for name in data if isinstance(name, str) and Path(name).name == name}

    def _write_managed_skills(self, cli: AgentCLI, names: set[str]) -> None:
        manifest = self._managed_skills_manifest(cli)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=manifest.parent,
            prefix=f".{manifest.name}.",
            suffix=".tmp",
        )
        temporary_manifest = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
                temporary_file.write(json.dumps(sorted(names)) + "\n")
            temporary_manifest.replace(manifest)
        finally:
            if temporary_manifest.exists():
                temporary_manifest.unlink()

    def _legacy_managed_skills(self, cli: AgentCLI) -> set[str]:
        """Recognize pre-manifest CAFE installs without claiming arbitrary skills."""
        skills_root = self.get_native_skills_dir(cli)
        if not skills_root.is_dir():
            return set()
        managed: set[str] = set()
        for candidate in skills_root.iterdir():
            if not candidate.is_dir():
                continue
            try:
                if self.skill_loader.get_skill_dir(candidate.name).name == candidate.name:
                    managed.add(candidate.name)
            except (SkillDiscoveryError, FileNotFoundError):
                continue
        return managed

    def _remove_managed_skill(self, cli: AgentCLI, name: str) -> None:
        if Path(name).name != name:
            return
        target = self.get_native_skills_dir(cli) / name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)

    def _record_managed_skill(self, cli: AgentCLI, name: str) -> None:
        managed = self._read_managed_skills(cli)
        if managed is None:
            managed = self._legacy_managed_skills(cli)
        managed.add(name)
        self._write_managed_skills(cli, managed)

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
