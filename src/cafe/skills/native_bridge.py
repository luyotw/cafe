"""Install resolved CAFE skills into CLI-native skills directories."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cafe.catalogs.resolver import (
    MAX_CATALOG_DISCOVERY_ENTRIES,
    CatalogKind,
    CatalogResolver,
    bounded_directory_names,
    content_digest,
    global_catalog_lock,
)
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

    PROJECT_LINK_CLIS = (AgentCLI.CODEX, AgentCLI.CLAUDE, AgentCLI.COPILOT)

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
                raise SkillDiscoveryError(
                    "CLI skill directory path is not a directory; "
                    f"preserve and relocate it: {current}"
                )
        skills_root.mkdir(parents=True, exist_ok=True)
        if not skills_root.resolve().is_relative_to(self.project_root):
            raise SkillDiscoveryError(
                f"CLI skill directory escapes the project root: {skills_root}"
            )
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
        with global_catalog_lock(self.skill_loader.global_root):
            source_dir = self.skill_loader.get_skill_dir(name)
            target_dir = self._ensure_native_skills_dir(cli) / source_dir.name
            records = self._read_managed_skills(cli)
            if self.is_active_project_skill(name) and cli in self.PROJECT_LINK_CLIS:
                self._install_project_link(cli, source_dir, records)
            else:
                self._require_replaceable(target_dir, records)
                staging = Path(tempfile.mkdtemp(dir=target_dir.parent, prefix=".cafe-install-"))
                try:
                    shutil.copytree(source_dir, staging, dirs_exist_ok=True)
                    if context:
                        skill_file = staging / "SKILL.md"
                        rendered = skill_file.read_text(encoding="utf-8")
                        for key, value in context.items():
                            rendered = rendered.replace(f"{{{key}}}", str(value))
                        skill_file.write_text(rendered, encoding="utf-8")
                    self._replace_entry(cli, target_dir, staging, records, "copy", source_dir)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            return target_dir

    def is_active_project_skill(self, name: str) -> bool:
        """Distinguish active authority from canonical-checkout fallback selections."""
        try:
            source = self.skill_loader.get_skill_dir(name)
        except (SkillDiscoveryError, FileNotFoundError):
            return False
        return source.parent == self.project_root / ".cafe" / "skills"

    def reconcile_project_skills(self) -> list[str]:
        """Expose fresh active-local entries in all three named providers.

        Unrelated conflicts are reported while safe entries proceed. A selected
        conflict is rejected by install_skill before the agent can be launched.
        """
        errors: list[str] = []
        with global_catalog_lock(self.skill_loader.global_root):
            authority = self.project_root / ".cafe" / "skills"
            sources: dict[str, Path] = {}
            invalid: set[str] = set()
            resolver = CatalogResolver(
                project_root=self.project_root,
                canonical_root=self.project_root,
                global_root=self.skill_loader.global_root,
                builtin_root=self.skill_loader.builtin_root,
            )
            try:
                names = (
                    bounded_directory_names(
                        authority,
                        max_entries=MAX_CATALOG_DISCOVERY_ENTRIES,
                        limit_error=lambda: SkillDiscoveryError("Too many active project skills"),
                    )
                    if authority.exists()
                    else []
                )
                for name in names:
                    try:
                        entry = resolver.resolve(CatalogKind.PHASE, name)
                        sources[name] = entry.path
                    except (OSError, ValueError, RuntimeError, SkillDiscoveryError) as exc:
                        invalid.add(name)
                        errors.append(f"Invalid active project skill {name}: {exc}")
            except (OSError, ValueError, RuntimeError, SkillDiscoveryError) as exc:
                # Failed enumeration cannot establish deletion of old sources.
                errors.append(f"Cannot enumerate active project skills: {exc}")
                for error in errors:
                    logging.getLogger(__name__).warning(error)
                return errors
            for cli in self.PROJECT_LINK_CLIS:
                try:
                    self._ensure_native_skills_dir(cli)
                    records = self._read_managed_skills(cli)
                    for name, record in list(records.items()):
                        if (
                            record.get("kind") == "project_link"
                            and name not in sources
                            and name not in invalid
                        ):
                            self._remove_managed_skill(cli, name)
                    records = self._read_managed_skills(cli)
                    for source in sources.values():
                        try:
                            self._install_project_link(cli, source, records)
                        except (OSError, ValueError, RuntimeError, SkillDiscoveryError) as exc:
                            errors.append(f"{cli.value} project skill {source.name}: {exc}")
                except (OSError, ValueError, RuntimeError, SkillDiscoveryError) as exc:
                    errors.append(f"{cli.value} project skills: {exc}")
        for error in errors:
            logging.getLogger(__name__).warning(error)
        return errors

    def _install_project_link(self, cli: AgentCLI, source: Path, records: dict) -> Path:
        target = self._ensure_native_skills_dir(cli) / source.name
        relative = os.path.relpath(source, target.parent)
        if target.is_symlink() and os.readlink(target) == relative:
            # A correct manual link has safe semantics and can be adopted.
            records[target.name] = self._entry_record(target, "project_link", source)
            self._write_managed_skills(cli, records)
            return target
        if not (target.is_symlink() and target.resolve() == source.resolve()):
            self._require_replaceable(target, records)
        staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=".cafe-link-"))
        staging.rmdir()
        try:
            staging.symlink_to(relative, target_is_directory=True)
            self._replace_entry(cli, target, staging, records, "project_link", source)
        finally:
            if staging.is_symlink():
                staging.unlink()
        return target

    def _require_replaceable(self, target: Path, records: dict) -> None:
        if (target.exists() or target.is_symlink()) and not self._owns_entry(
            target, records.get(target.name)
        ):
            raise SkillDiscoveryError(
                f"Unmanaged or changed native skill conflict: {target}. "
                "Back up and relocate the entry, then rerun prepare or launch; "
                "legacy names or matching contents alone do not prove ownership."
            )

    def _replace_entry(
        self, cli: AgentCLI, target: Path, staging: Path, records: dict, kind: str, source: Path
    ) -> None:
        self._ensure_native_skills_dir(cli)
        correct_link = (
            kind == "project_link" and target.is_symlink() and target.resolve() == source.resolve()
        )
        if not correct_link:
            self._require_replaceable(target, records)
        backup = Path(tempfile.mkdtemp(dir=target.parent, prefix=".cafe-backup-"))
        backup.rmdir()
        had_target = target.exists() or target.is_symlink()
        previous = records.get(target.name)
        if had_target:
            target.rename(backup)
        succeeded = False
        try:
            staging.rename(target)
            records[target.name] = self._entry_record(target, kind, source)
            self._write_managed_skills(cli, records)
            succeeded = True
        except BaseException:
            if target.is_symlink():
                target.unlink()
            elif target.exists():
                shutil.rmtree(target)
            if had_target:
                backup.rename(target)
            if previous is None:
                records.pop(target.name, None)
            else:
                # Rename changes ctime; bind the restored entry to its current identity.
                records[target.name] = {**previous, "identity": self._entry_identity(target)}
            self._write_managed_skills(cli, records)
            raise
        finally:
            # If rollback itself failed, leave the backup available for recovery.
            if succeeded:
                if backup.is_symlink():
                    backup.unlink()
                elif backup.exists():
                    shutil.rmtree(backup)

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

        The selected transient environment is an exact set. Persistent project
        links are reconciled separately and are never removed by selection.
        Cleanup requires current-entry ownership, not a catalog-name match.
        """
        with global_catalog_lock(self.skill_loader.global_root):
            self._ensure_native_skills_dir(cli)
            desired = {self.skill_loader.get_skill_dir(name).name for name in names}
            records = self._read_managed_skills(cli)
            for name, record in list(records.items()):
                if record.get("kind") == "copy" and name not in desired:
                    self._remove_managed_skill(cli, name)
            # install=False never claims names that have not been installed.
            self._write_managed_skills(cli, self._read_managed_skills(cli))
            if not install:
                return []
            return self.install_skills(names, cli, context=context)

    def _managed_skills_manifest(self, cli: AgentCLI) -> Path:
        return self.get_native_skills_dir(cli) / self.MANAGED_SKILLS_MANIFEST

    @staticmethod
    def _safe_name(name: object) -> bool:
        return (
            isinstance(name, str)
            and name not in {"", ".", ".."}
            and Path(name).name == name
            and "\\" not in name
        )

    def _read_managed_skills(self, cli: AgentCLI) -> dict[str, dict]:
        manifest = self._managed_skills_manifest(cli)
        if manifest.is_symlink() or not manifest.is_file():
            return {}
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        # Legacy lists are readable but confer no destructive ownership proof.
        if isinstance(data, list):
            return {name: {"kind": "legacy"} for name in data if self._safe_name(name)}
        if not isinstance(data, dict) or data.get("version") != 2:
            return {}
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return {}
        return {
            name: record
            for name, record in entries.items()
            if self._safe_name(name) and isinstance(record, dict)
        }

    def _write_managed_skills(self, cli: AgentCLI, records: dict[str, dict]) -> None:
        manifest = self._managed_skills_manifest(cli)
        if manifest.is_symlink():
            raise SkillDiscoveryError(f"Refusing to replace native manifest symlink: {manifest}")
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=manifest.parent,
            prefix=f".{manifest.name}.",
            suffix=".tmp",
        )
        temporary_manifest = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"version": 2, "entries": records}, sort_keys=True) + "\n")
            temporary_manifest.replace(manifest)
            owned = {
                name
                for name, record in records.items()
                if self._owns_entry(manifest.parent / name, record)
            }
            self._ensure_managed_skills_git_excluded(cli, owned)
        finally:
            if temporary_manifest.exists():
                temporary_manifest.unlink()

    @staticmethod
    def _entry_identity(target: Path) -> dict:
        stat = target.lstat()
        return {"device": stat.st_dev, "inode": stat.st_ino, "ctime_ns": stat.st_ctime_ns}

    def _entry_record(self, target: Path, kind: str, source: Path) -> dict:
        record = {"kind": kind, "source": str(source), "identity": self._entry_identity(target)}
        if target.is_symlink():
            record["link_target"] = os.readlink(target)
        else:
            record["digest"] = content_digest(target)
        return record

    def _owns_entry(self, target: Path, record: dict | None) -> bool:
        if not record or record.get("kind") not in {"copy", "project_link"}:
            return False
        try:
            if self._entry_identity(target) != record.get("identity"):
                return False
            if target.is_symlink():
                return record.get("kind") == "project_link" and os.readlink(target) == record.get(
                    "link_target"
                )
            return (
                record.get("kind") == "copy"
                and target.is_dir()
                and content_digest(target) == record.get("digest")
            )
        except (OSError, ValueError, RuntimeError):
            return False

    def _remove_managed_skill(self, cli: AgentCLI, name: str) -> None:
        with global_catalog_lock(self.skill_loader.global_root):
            if not self._safe_name(name):
                return
            self._ensure_native_skills_dir(cli)
            records = self._read_managed_skills(cli)
            target = self.get_native_skills_dir(cli) / name
            if self._owns_entry(target, records.get(name)):
                if target.is_symlink():
                    target.unlink()
                else:
                    shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                logging.getLogger(__name__).warning("Preserving changed native skill: %s", target)
            records.pop(name, None)
            self._write_managed_skills(cli, records)

    def record_rendered_copy(self, cli: AgentCLI, target: Path, source: Path) -> None:
        """Bind successful transient rendering to the present entry's full contents."""
        records = self._read_managed_skills(cli)
        records[target.name] = self._entry_record(target, "copy", source)
        self._write_managed_skills(cli, records)

    def _ensure_managed_skills_git_excluded(self, cli: AgentCLI, names: set[str]) -> None:
        """Locally exclude runtime helpers, preserving visibility of user skills.

        Exclude individual managed paths: a project's .gitignore may reopen the
        native skills directory and override a top-level CLI directory rule.
        Tracked files remain subject to Git's normal dirty-worktree checks.
        """
        native = self.CLI_SKILL_DIRS[cli]
        top = Path(native).parts[0]

        # Names are literal directory names, not Git ignore patterns.
        def literal(value: str) -> str:
            return "".join("\\" + char if char in "\\*?[] " else char for char in value)

        entries = [f"/{native}/{self.MANAGED_SKILLS_MANIFEST}"]
        entries.extend(
            f"/{native}/{literal(name)}"
            + ("" if (self.get_native_skills_dir(cli) / name).is_symlink() else "/")
            for name in sorted(names)
        )
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
            # Remove only the exact legacy block written by this bridge.
            lines = existing.splitlines(keepends=True)
            kept: list[str] = []
            index = 0
            while index < len(lines):
                if (
                    lines[index].rstrip("\r\n") == "# CAFE native-skill CLI dir (auto-excluded)"
                    and index + 1 < len(lines)
                    and lines[index + 1].rstrip("\r\n") == f"/{top}/"
                ):
                    index += 2
                    continue
                if lines[index].rstrip("\r\n") == "# CAFE managed native skills (auto-excluded)":
                    index += 1
                    group = []
                    while index < len(lines) and not lines[index].startswith("#"):
                        if not lines[index].startswith(f"/{native}/"):
                            group.append(lines[index])
                        index += 1
                    if group:
                        kept.append("# CAFE managed native skills (auto-excluded)\n")
                        kept.extend(group)
                    continue
                kept.append(lines[index])
                index += 1
            updated = "".join(kept)
            missing = [entry for entry in entries if entry not in updated.splitlines()]
            if missing:
                if updated and not updated.endswith("\n"):
                    updated += "\n"
                updated += "# CAFE managed native skills (auto-excluded)\n"
                updated += "\n".join(missing) + "\n"
            if updated != existing:
                exclude_path.parent.mkdir(parents=True, exist_ok=True)
                exclude_path.write_text(updated, encoding="utf-8")
        except (OSError, subprocess.SubprocessError):
            # Best effort only; Git's clean-worktree gate remains authoritative.
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
