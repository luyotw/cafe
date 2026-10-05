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
    baseline_commit: str | None = None


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


def git_content_entries(root: Path, revision: str | None = None):
    """Read bounded Git modes/blob identities, rejecting unresolved index stages."""
    data = (_git(root, "ls-tree", "-r", "-z", revision) if revision else
            _git(root, "ls-files", "--stage", "-z"))
    result = {}
    for item in data.split("\x00"):
        if not item:
            continue
        metadata, path = item.split("\t", 1)
        mode, middle, last = metadata.split()
        if revision:
            oid = last
        else:
            oid = middle
            if last != "0":
                raise ValueError("unresolved index cannot establish content identity")
        result[path] = (mode, oid)
    return result


def workspace_content(root: Path, path: str, *, index_entries=None):
    """Retained user work binds both worktree bytes and the exact staged blob."""
    entries = git_content_entries(root) if index_entries is None else index_entries
    value = {"working": path_content(root, path), "index": entries.get(path)}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def working_git_entry(root: Path, path: str):
    """Identity Git would stage, including mode, symlinks and clean filters."""
    candidate = root / path
    path_content(root, path)  # Apply the same file/size/containment bounds.
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        value = subprocess.run(["git", "-C", str(root), "hash-object", "--stdin"],
            input=os.readlink(candidate).encode(errors="surrogateescape"),
            capture_output=True, check=True, timeout=20).stdout.decode().strip()
        return ("120000", value)
    return ("100755" if metadata.st_mode & 0o111 else "100644",
            _git(root, "hash-object", "--path=" + path, "--", path).strip())


def require_committed_content(root: Path, paths):
    """The delivered tree must contain the implementation actually reviewed."""
    head = git_content_entries(root, "HEAD")
    for path in validate_scope_paths(paths):
        if head.get(path) != working_git_entry(root, path):
            raise ValueError("committed content differs from reviewed working content: " + path)


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
        entries = git_content_entries(root)
        for change in inspect_workspace(root).changes:
            record = {**change, "origin": "workspace"}
            for key in ("path", "old_path"):
                if key in record:
                    record[key + "_content"] = workspace_content(root, record[key], index_entries=entries)
            records.append(record)
        if len(records) > 8192:
            raise ValueError("change records exceed the bounded inspection limit")
        return ChangeCollection(tuple(records), baseline_commit=resolved)
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
    index = git_content_entries(root)
    head = git_content_entries(root, "HEAD")
    baseline = git_content_entries(root, changes.baseline_commit) if changes.baseline_commit else {}
    content = {}
    approved = set(approved_paths)
    for path in sorted(paths):
        value = {"working": path_content(root, path)}
        if path in approved:
            working = working_git_entry(root, path)
            # Normal staging/commit of reviewed bytes is stable. Divergent new
            # staged/committed blobs are additional content requiring review.
            if index.get(path) not in (working, head.get(path), baseline.get(path)):
                value["index"] = index.get(path)
            if head.get(path) not in (working, baseline.get(path)):
                value["head"] = head.get(path)
        else:
            value["index"] = index.get(path)
        content[path] = value
    return hashlib.sha256(json.dumps({"paths": content}, sort_keys=True).encode()).hexdigest()
