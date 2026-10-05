"""Mode-neutral literal path validation and change scope comparison."""

from __future__ import annotations

from pathlib import PurePosixPath, Path
from dataclasses import dataclass
import hashlib
import json
import os
import stat
import subprocess

from cafe.core.workspace_artifact import inspect_workspace


def validate_scope_paths(paths):
    if not isinstance(paths, (list, tuple)) or not paths or len(paths) > 1024:
        raise ValueError("scope paths must be a bounded nonempty file list")
    seen = set()
    for path in paths:
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or any(c in path for c in "\\\x00*?[]")
            or path.endswith("/")
            or any(p in {"", ".", ".."} for p in path.split("/"))
            or PurePosixPath(path).as_posix() != path
            or path in seen
        ):
            raise ValueError("scope approval requires distinct literal repository-relative files")
        seen.add(path)
    return list(paths)


@dataclass(frozen=True)
class ChangeCollection:
    records: tuple[dict, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ScopeResult:
    passed: bool
    findings: tuple[dict, ...] = ()


def _git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, timeout=20
    )
    if len(result.stdout) > 8 * 1024 * 1024:
        raise ValueError("change evidence exceeds its bounded inspection limit")
    return result.stdout.decode("utf-8", errors="surrogateescape")


def path_content(root: Path, path: str):
    validate_scope_paths([path])
    candidate = root / path
    # A symlink's own contents are evidence; never follow it outside the checkout.
    if not candidate.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError("scope path parent escapes the checkout")
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return "missing"
    digest = hashlib.sha256()
    digest.update(str(stat.S_IMODE(metadata.st_mode)).encode())
    if stat.S_ISLNK(metadata.st_mode):
        digest.update(os.readlink(candidate).encode(errors="surrogateescape"))
    elif stat.S_ISREG(metadata.st_mode):
        if metadata.st_size > 128 * 1024 * 1024:
            raise ValueError("scope content exceeds its bounded inspection limit")
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    else:
        raise ValueError("scope path must name a file or symlink")
    return digest.hexdigest()


def collect_changes(root: Path, baseline_commit: str) -> ChangeCollection:
    """Collect every baseline-descendant commit edge plus Git-visible dirt."""
    try:
        resolved = _git(root, "rev-parse", "--verify", baseline_commit + "^{commit}").strip()
        _git(root, "merge-base", "--is-ancestor", resolved, "HEAD")
        tokens = _git(
            root, "log", "--format=", "--name-status", "-z", "-m", "-M", resolved + "..HEAD"
        ).split("\x00")
        records = []
        index = 0
        while index < len(tokens):
            status_token = tokens[index].strip("\n")
            index += 1
            if not status_token:
                continue
            if status_token[0] not in "ACDMRTUXB" or index >= len(tokens):
                raise ValueError("malformed committed path evidence")
            if status_token[0] in "RC":
                old_path, path = tokens[index : index + 2]
                index += 2
                validate_scope_paths([old_path, path])
                records.append(
                    {
                        "origin": "committed",
                        "state": status_token,
                        "old_path": old_path,
                        "path": path,
                    }
                )
            else:
                path = tokens[index]
                index += 1
                validate_scope_paths([path])
                records.append({"origin": "committed", "state": status_token, "path": path})
        for change in inspect_workspace(root).changes:
            record = {**change, "origin": "workspace"}
            for key in ("path", "old_path"):
                if key in record:
                    record[key + "_content"] = path_content(root, record[key])
            records.append(record)
        if len(records) > 8192:
            raise ValueError("change records exceed the bounded inspection limit")
        return ChangeCollection(tuple(records))
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        return ChangeCollection(error=f"{type(exc).__name__}: {str(exc)[:500]}")


def compare_scope(changes: ChangeCollection, approved_paths, *, preexisting=()) -> ScopeResult:
    """Pure comparison; preserved unrelated workspace evidence is never approval."""
    if changes.error:
        return ScopeResult(False, ({"reason": "evidence_unavailable", "detail": changes.error},))
    try:
        approved = set(validate_scope_paths(approved_paths))
        findings = []
        for record in changes.records:
            if not isinstance(record, dict) or not record.get("path"):
                raise ValueError("malformed change record")
            for key in ("path", "old_path"):
                if key not in record:
                    continue
                path = validate_scope_paths([record[key]])[0]
                retained = record.get("origin") == "workspace" and any(
                    p.get("path") == path and p.get("content") == record.get(key + "_content")
                    for p in preexisting
                )
                if path not in approved and not retained:
                    findings.append(
                        {
                            "path": path,
                            "reason": "outside_approved_scope",
                            "origin": record.get("origin", "unknown"),
                        }
                    )
        unique = {json.dumps(f, sort_keys=True): f for f in findings}
        findings = tuple(unique[k] for k in sorted(unique))
        return ScopeResult(not findings, findings)
    except (ValueError, TypeError, AttributeError) as exc:
        return ScopeResult(False, ({"reason": "malformed_evidence", "detail": str(exc)},))


def content_snapshot(root: Path, changes: ChangeCollection, approved_paths) -> str:
    """Current relevant content, including approved files absent from Git status."""
    if changes.error:
        raise ValueError(changes.error)
    paths = set(validate_scope_paths(approved_paths))
    for record in changes.records:
        paths.add(record["path"])
        if "old_path" in record:
            paths.add(record["old_path"])
    payload = {"paths": {p: path_content(root, p) for p in sorted(paths)}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
