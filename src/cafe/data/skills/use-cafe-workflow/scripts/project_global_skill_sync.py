#!/usr/bin/env python3
"""Compare project CAFE skills with global copies and update only after approval."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA_VERSION = 1
SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
IGNORED_NAMES = {"__pycache__", ".DS_Store"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05


class SkillSyncError(ValueError):
    """Raised when skill comparison or synchronization is unsafe."""


def _ignored(path: Path) -> bool:
    return any(part in IGNORED_NAMES for part in path.parts) or path.suffix in IGNORED_SUFFIXES


def _tree_digest(root: Path) -> str:
    if not root.is_dir():
        raise SkillSyncError(f"skill directory does not exist: {root}")
    manifest: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if _ignored(relative):
            continue
        record: dict[str, Any] = {"path": relative.as_posix()}
        if path.is_symlink():
            record.update(type="symlink", target=os.readlink(path))
        elif path.is_dir():
            record["type"] = "directory"
        elif path.is_file():
            mode = path.stat(follow_symlinks=False).st_mode & 0o777
            content_digest = hashlib.sha256()
            size = 0
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    size += len(chunk)
                    content_digest.update(chunk)
            record.update(
                type="file",
                mode=f"{mode:o}",
                size=size,
                sha256=content_digest.hexdigest(),
            )
        else:
            raise SkillSyncError(f"unsupported skill entry: {path}")
        manifest.append(record)
    serialized = json.dumps(
        manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _path_digest(path: Path) -> str | None:
    """Fingerprint any existing path, including invalid skill states."""
    if path.is_symlink():
        state: dict[str, Any] = {"type": "symlink", "target": os.readlink(path)}
    elif not path.exists():
        return None
    elif path.is_dir():
        return _tree_digest(path)
    elif path.is_file():
        stat = path.stat(follow_symlinks=False)
        content_digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                content_digest.update(chunk)
        state = {
            "type": "file",
            "mode": f"{stat.st_mode & 0o777:o}",
            "size": stat.st_size,
            "sha256": content_digest.hexdigest(),
        }
    else:
        stat = path.lstat()
        state = {
            "type": "other",
            "mode": f"{stat.st_mode:o}",
            "size": stat.st_size,
        }
    serialized = json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(serialized).hexdigest()


def _comparison_token(result: dict[str, Any]) -> str:
    approval_evidence = {
        "schema_version": result["schema_version"],
        "project_roots": result["project_roots"],
        "global_root": result["global_root"],
        "compared_count": result["compared_count"],
        "differences": result["differences"],
    }
    serialized = json.dumps(
        approval_evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _skill_version(skill_dir: Path | None) -> str | None:
    if skill_dir is None:
        return None
    skill_file = skill_dir / "SKILL.md"
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"(?m)^version:\s*([^\s#]+)", text)
    return match.group(1) if match else None


def _validate_skill(skill_dir: Path, expected_name: str) -> None:
    if not SKILL_NAME.fullmatch(expected_name):
        raise SkillSyncError(f"invalid skill name: {expected_name!r}")
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        raise SkillSyncError(f"skill is missing SKILL.md: {skill_dir}")
    text = skill_file.read_text(encoding="utf-8")
    match = re.search(r"(?m)^name:\s*([^\s#]+)", text)
    if match is None or match.group(1) != expected_name:
        raise SkillSyncError(
            f"skill frontmatter name must match directory {expected_name!r}: {skill_file}"
        )


def _git_output(cwd: Path, *args: str) -> Path | None:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    value = Path(result.stdout.strip())
    return value if value.is_absolute() else (cwd / value).resolve()


def _git_main_worktree(cwd: Path) -> Path | None:
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain", "-z"],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for field in result.stdout.split("\0"):
        if field.startswith("worktree "):
            return Path(field.removeprefix("worktree ")).resolve()
    return None


def _separate_git_worktree(common_dir: Path, current: Path) -> Path | None:
    """Resolve the primary tree whose .git file points at a separate common dir."""
    candidates = list(current.parents)
    for parent in {current.parent, common_dir.parent}:
        try:
            candidates.extend(path for path in parent.iterdir() if path.is_dir())
        except OSError:
            continue
    for candidate in candidates:
        git_file = candidate / ".git"
        try:
            marker = git_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not marker.startswith("gitdir: "):
            continue
        target = Path(marker.removeprefix("gitdir: "))
        resolved = target.resolve() if target.is_absolute() else (candidate / target).resolve()
        if resolved == common_dir.resolve():
            return candidate.resolve()
    return None


def discover_project_roots(cwd: Path) -> tuple[Path, ...]:
    """Return canonical-main then active-worktree roots, with active taking precedence."""
    current = _git_output(cwd, "rev-parse", "--show-toplevel") or cwd.resolve()
    common_dir = _git_output(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    candidates: list[Path] = []
    main = _git_main_worktree(cwd)
    if main is not None:
        candidates.append(main)
    if common_dir is not None and main is not None and main.resolve() == common_dir.resolve():
        separate = _separate_git_worktree(common_dir, current)
        if separate is None:
            raise SkillSyncError(
                "cannot resolve the primary worktree for the separate Git directory; "
                "re-run with --project-root <canonical-main-worktree>"
            )
        candidates.append(separate)
    candidates.append(current.resolve())
    roots: list[Path] = []
    for candidate in candidates:
        if candidate not in roots and (candidate / ".cafe" / "skills").is_dir():
            roots.append(candidate)
    return tuple(roots)


def _project_skills(project_roots: Sequence[Path]) -> dict[str, Path]:
    skills: dict[str, Path] = {}
    for project_root in project_roots:
        skills_root = project_root / ".cafe" / "skills"
        if not skills_root.is_dir():
            continue
        for path in sorted(skills_root.iterdir()):
            if path.is_dir() and (path / "SKILL.md").is_file():
                _validate_skill(path, path.name)
                skills[path.name] = path
    return skills


def compare_skills(*, project_roots: Sequence[Path], global_root: Path) -> dict[str, Any]:
    project = _project_skills(project_roots)
    differences: list[dict[str, Any]] = []
    for name, project_dir in sorted(project.items()):
        global_dir = global_root / name
        project_digest = _tree_digest(project_dir)
        if global_dir.is_symlink() or not global_dir.is_dir():
            reason = (
                "missing_global"
                if not global_dir.exists() and not global_dir.is_symlink()
                else "invalid_global"
            )
            global_digest = _path_digest(global_dir)
            global_version = None
        else:
            try:
                _validate_skill(global_dir, name)
                global_digest = _tree_digest(global_dir)
                reason = "content_mismatch" if global_digest != project_digest else "identical"
                global_version = _skill_version(global_dir)
            except (OSError, SkillSyncError):
                reason = "invalid_global"
                global_digest = _path_digest(global_dir)
                global_version = _skill_version(global_dir)
        if reason != "identical":
            differences.append(
                {
                    "skill": name,
                    "reason": reason,
                    "project_path": str(project_dir),
                    "global_path": str(global_dir),
                    "project_version": _skill_version(project_dir),
                    "global_version": global_version,
                    "project_digest": project_digest,
                    "global_digest": global_digest,
                }
            )
    status = "no_project_skills" if not project else ("differences" if differences else "identical")
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "project_roots": [str(path) for path in project_roots],
        "global_root": str(global_root),
        "compared_count": len(project),
        "differences": differences,
    }
    result["comparison_token"] = _comparison_token(result)
    return result


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name for name in names if name in IGNORED_NAMES or Path(name).suffix in IGNORED_SUFFIXES
    }


@contextmanager
def _sync_lock(global_root: Path) -> Iterator[None]:
    global_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(global_root / ".project-skill-sync.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SkillSyncError(f"timed out waiting for skill sync lock: {global_root}")
                time.sleep(min(LOCK_POLL_SECONDS, remaining))
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def update_skills(
    *,
    project_roots: Sequence[Path],
    global_root: Path,
    selected: Sequence[str],
    comparison_token: str,
) -> dict[str, Any]:
    if not selected:
        raise SkillSyncError("update requires at least one --skill")
    if len(set(selected)) != len(selected):
        raise SkillSyncError("duplicate --skill values are not allowed")
    if not re.fullmatch(r"[0-9a-f]{64}", comparison_token):
        raise SkillSyncError("update requires a valid --comparison-token from check")

    with _sync_lock(global_root):
        current = compare_skills(project_roots=project_roots, global_root=global_root)
        if current["comparison_token"] != comparison_token:
            raise SkillSyncError(
                "skill contents changed after approval; re-run check and ask again"
            )
        available = {item["skill"] for item in current["differences"]}
        expected_project_digests = {
            item["skill"]: item["project_digest"] for item in current["differences"]
        }
        expected_global_digests = {
            item["skill"]: item["global_digest"] for item in current["differences"]
        }
        project = _project_skills(project_roots)
        for name in selected:
            if not SKILL_NAME.fullmatch(name):
                raise SkillSyncError(f"invalid skill name: {name!r}")
            if name not in project:
                raise SkillSyncError(f"no project skill named {name!r}")
            if name not in available:
                raise SkillSyncError(f"project and global skill are already identical: {name}")
        transaction = Path(tempfile.mkdtemp(prefix=".project-skill-sync-", dir=global_root))
        staged = transaction / "staged"
        backups = transaction / "backups"
        staged.mkdir()
        backups.mkdir()
        published: list[str] = []
        backed_up: list[str] = []
        preserve_transaction = False
        try:
            for name in selected:
                destination = staged / name
                shutil.copytree(project[name], destination, symlinks=True, ignore=_copy_ignore)
                _validate_skill(destination, name)
                if _tree_digest(destination) != expected_project_digests[name]:
                    raise SkillSyncError(
                        f"skill contents changed after approval while staging: {name}"
                    )
            before_publish = compare_skills(project_roots=project_roots, global_root=global_root)
            if before_publish["comparison_token"] != comparison_token:
                raise SkillSyncError(
                    "skill contents changed after approval; re-run check and ask again"
                )
            for name in selected:
                target = global_root / name
                if target.exists() or target.is_symlink():
                    os.replace(target, backups / name)
                    backed_up.append(name)
                    if _path_digest(backups / name) != expected_global_digests[name]:
                        raise SkillSyncError(
                            f"skill contents changed after approval during publish: {name}"
                        )
                elif expected_global_digests[name] is not None:
                    raise SkillSyncError(
                        f"skill contents changed after approval during publish: {name}"
                    )
                if target.exists() or target.is_symlink():
                    raise SkillSyncError(
                        f"skill contents changed after approval during publish: {name}"
                    )
                os.replace(staged / name, target)
                published.append(name)
            after = compare_skills(project_roots=project_roots, global_root=global_root)
            remaining = {item["skill"] for item in after["differences"]}
            failed = [name for name in selected if name in remaining]
            if failed:
                raise SkillSyncError("post-update verification failed: " + ", ".join(failed))
        except Exception as original_error:
            rollback_errors: list[str] = []
            for name in reversed(published):
                target = global_root / name
                try:
                    if target.exists() or target.is_symlink():
                        if target.is_dir() and not target.is_symlink():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                except OSError as exc:
                    rollback_errors.append(f"remove {target}: {exc}")
            for name in reversed(backed_up):
                target = global_root / name
                backup = backups / name
                try:
                    if backup.exists() or backup.is_symlink():
                        if target.exists() or target.is_symlink():
                            raise OSError(f"rollback target still exists: {target}")
                        os.replace(backup, target)
                except OSError as exc:
                    rollback_errors.append(f"restore {backup} -> {target}: {exc}")
            if rollback_errors:
                preserve_transaction = True
                details = "; ".join(rollback_errors)
                raise SkillSyncError(
                    f"update failed and rollback was incomplete; recover backups from "
                    f"{backups}: {details}"
                ) from original_error
            raise
        finally:
            if not preserve_transaction:
                shutil.rmtree(transaction, ignore_errors=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "updated",
        "updated": list(selected),
        "comparison": compare_skills(project_roots=project_roots, global_root=global_root),
    }


def _resolve_project_roots(explicit: Path | None) -> tuple[Path, ...]:
    if explicit is not None:
        root = explicit.expanduser().resolve()
        if not (root / ".cafe" / "skills").is_dir():
            raise SkillSyncError(f"project has no .cafe/skills directory: {root}")
        roots = [root]
        cwd = Path.cwd()
        current = _git_output(cwd, "rev-parse", "--show-toplevel")
        root_common = _git_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        current_common = _git_output(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if (
            current is not None
            and current != root
            and current_common is not None
            and root_common == current_common
            and (current / ".cafe" / "skills").is_dir()
        ):
            roots.append(current)
        return tuple(roots)
    return discover_project_roots(Path.cwd())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare project CAFE skills with ~/.cafe/skills before workflow execution."
    )
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--global-root", type=Path, default=Path("~/.cafe/skills"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check")
    update = subparsers.add_parser("update")
    update.add_argument("--skill", action="append", default=[])
    update.add_argument("--comparison-token", required=True)
    args = parser.parse_args()
    try:
        roots = _resolve_project_roots(args.project_root)
        global_root = args.global_root.expanduser().resolve()
        result = (
            compare_skills(project_roots=roots, global_root=global_root)
            if args.command == "check"
            else update_skills(
                project_roots=roots,
                global_root=global_root,
                selected=args.skill,
                comparison_token=args.comparison_token,
            )
        )
    except (OSError, SkillSyncError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
