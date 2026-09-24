"""Declared, schema-versioned identity for a current Git workspace."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

WORKSPACE_SCHEMA_VERSION = 1
_SHA = re.compile(r"^[0-9a-f]{40}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_STATUSES = frozenset({"A", "D", "M", "R"})


class WorkspaceArtifactError(ValueError):
    """Raised when a workspace identity cannot be safely constructed or read."""


@dataclass(frozen=True)
class WorkspaceVerification:
    """Bounded result of checking a workspace identity against repository facts."""

    valid: bool
    reasons: tuple[str, ...] = ()


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise WorkspaceArtifactError(f"Git workspace lookup failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise WorkspaceArtifactError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _repo_root(repo: Path) -> Path:
    root = Path(_git(repo.resolve(), "rev-parse", "--show-toplevel")).resolve()
    if root != repo.resolve():
        raise WorkspaceArtifactError("workspace repository must be the active worktree root")
    return root


def _resolve_commit(repo: Path, value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceArtifactError(f"workspace {field} SHA is missing")
    try:
        resolved = _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    except WorkspaceArtifactError as exc:
        raise WorkspaceArtifactError(f"workspace {field} SHA is not resolvable") from exc
    if not _SHA.fullmatch(resolved):
        raise WorkspaceArtifactError(f"workspace {field} SHA has an invalid form")
    return resolved


def _changed_files(repo: Path, base_sha: str, head_sha: str) -> tuple[dict[str, str], ...]:
    raw = _git(
        repo,
        "diff",
        "--name-status",
        "--find-renames",
        "-z",
        base_sha,
        head_sha,
    )
    tokens = raw.split("\x00") if raw else []
    records: list[dict[str, str]] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]
        index += 1
        if status.startswith("R"):
            if index + 1 >= len(tokens) or not tokens[index] or not tokens[index + 1]:
                raise WorkspaceArtifactError("Git changed-file output contains an invalid rename")
            records.append({"status": "R", "old_path": tokens[index], "path": tokens[index + 1]})
            index += 2
            continue
        if status[:1] not in _STATUSES or index >= len(tokens) or not tokens[index]:
            raise WorkspaceArtifactError("Git changed-file output contains an invalid status")
        records.append({"status": status[:1], "path": tokens[index]})
        index += 1
    return tuple(sorted(records, key=lambda item: (item["path"], item.get("old_path", ""))))


@dataclass(frozen=True)
class WorkspaceArtifact:
    """The persisted current Git-workspace companion.

    Version 1 snapshots previously carried verification-receipt metadata. That
    metadata is intentionally ignored on read so old records remain refreshable
    without treating a receipt as a workflow gate.
    """

    name: str
    version: int
    repository: str
    base_sha: str
    head_sha: str
    changed_files: tuple[dict[str, str], ...]
    schema_version: int = WORKSPACE_SCHEMA_VERSION
    updated_at: str = ""
    producer_step: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "repository": self.repository,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "changed_files": [dict(item) for item in self.changed_files],
            "updated_at": self.updated_at,
            "producer_step": self.producer_step,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WorkspaceArtifact":
        if not isinstance(raw, Mapping):
            raise WorkspaceArtifactError("workspace record must be an object")
        schema_version = raw.get("schema_version")
        if schema_version != WORKSPACE_SCHEMA_VERSION:
            raise WorkspaceArtifactError(
                f"unsupported workspace schema version: {schema_version!r}"
            )
        name = raw.get("name")
        record_version = raw.get("version")
        repository = raw.get("repository")
        base_sha = raw.get("base_sha")
        head_sha = raw.get("head_sha")
        if not isinstance(name, str) or not _NAME.fullmatch(name.strip()):
            raise WorkspaceArtifactError("workspace name is missing")
        if (
            isinstance(record_version, bool)
            or not isinstance(record_version, int)
            or record_version < 1
        ):
            raise WorkspaceArtifactError("workspace version must be a positive integer")
        if not isinstance(repository, str) or not repository.strip():
            raise WorkspaceArtifactError("workspace repository is missing")
        if not isinstance(base_sha, str) or not _SHA.fullmatch(base_sha):
            raise WorkspaceArtifactError("workspace base_sha has an invalid form")
        if not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
            raise WorkspaceArtifactError("workspace head_sha has an invalid form")
        updated_at = raw.get("updated_at", "")
        producer_step = raw.get("producer_step", "")
        if not isinstance(updated_at, str) or not isinstance(producer_step, str):
            raise WorkspaceArtifactError("workspace ordering metadata is invalid")
        return cls(
            name=name,
            version=record_version,
            repository=repository,
            base_sha=base_sha,
            head_sha=head_sha,
            changed_files=_normalize_changed_files(raw.get("changed_files")),
            updated_at=updated_at,
            producer_step=producer_step,
        )


def _normalize_changed_files(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise WorkspaceArtifactError("workspace changed_files must be a list")
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise WorkspaceArtifactError("workspace changed-file entry must be an object")
        status = item.get("status")
        path = item.get("path")
        if status not in _STATUSES or not isinstance(path, str) or not path:
            raise WorkspaceArtifactError("workspace changed-file entry is invalid")
        record = {"status": status, "path": path}
        if status == "R":
            old_path = item.get("old_path")
            if not isinstance(old_path, str) or not old_path:
                raise WorkspaceArtifactError("workspace rename entry is incomplete")
            record["old_path"] = old_path
        normalized.append(record)
    canonical = tuple(
        sorted(normalized, key=lambda entry: (entry["path"], entry.get("old_path", "")))
    )
    if len({json.dumps(item, sort_keys=True) for item in canonical}) != len(canonical):
        raise WorkspaceArtifactError("workspace changed-file entries must be unique")
    return canonical


def build_workspace_artifact(
    *,
    repo: Path,
    name: str,
    version: int,
    base_sha: str,
    head_sha: str,
    receipt_outputs: Sequence[Path] = (),
    updated_at: str = "",
    producer_step: str = "",
) -> WorkspaceArtifact:
    """Build a current workspace snapshot from Git facts.

    ``receipt_outputs`` remains an ignored compatibility argument for callers
    using the former receipt-backed API. A snapshot is refreshed from Git, not
    authenticated against a verification receipt.
    """
    del receipt_outputs
    root = _repo_root(Path(repo))
    resolved_base = _resolve_commit(root, base_sha, field="base")
    resolved_head = _resolve_commit(root, head_sha, field="head")
    if resolved_base != resolved_head:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", resolved_base, resolved_head],
            cwd=root,
            check=False,
        )
        if result.returncode != 0:
            raise WorkspaceArtifactError("workspace base must be an ancestor of head")
    if not isinstance(name, str) or not name.strip():
        raise WorkspaceArtifactError("workspace name is missing")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise WorkspaceArtifactError("workspace version must be a positive integer")
    if _git(root, "rev-parse", "HEAD") != resolved_head:
        raise WorkspaceArtifactError("workspace head changed before snapshot creation")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise WorkspaceArtifactError("workspace worktree is dirty")
    return WorkspaceArtifact(
        name=name,
        version=version,
        repository=str(root),
        base_sha=resolved_base,
        head_sha=resolved_head,
        changed_files=_changed_files(root, resolved_base, resolved_head),
        updated_at=updated_at,
        producer_step=producer_step,
    )


def verify_workspace_artifact(
    artifact: WorkspaceArtifact | Mapping[str, Any], *, repo: Path
) -> WorkspaceVerification:
    """Verify a workspace snapshot before a current-contract consumer runs."""
    try:
        current = (
            artifact
            if isinstance(artifact, WorkspaceArtifact)
            else WorkspaceArtifact.from_dict(artifact)
        )
        root = _repo_root(Path(repo))
        reasons: list[str] = []
        if current.repository != str(root):
            reasons.append("workspace repository does not match the active worktree")
        base = _resolve_commit(root, current.base_sha, field="base")
        head = _resolve_commit(root, current.head_sha, field="head")
        if base != current.base_sha or head != current.head_sha:
            reasons.append("workspace commit identity is not canonical")
        if base != head:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", base, head], cwd=root, check=False
            )
            if result.returncode != 0:
                reasons.append("workspace base is not an ancestor of head")
        actual_head = _git(root, "rev-parse", "HEAD")
        if actual_head != head:
            reasons.append("workspace head is stale")
        if _git(root, "status", "--porcelain", "--untracked-files=all"):
            reasons.append("workspace worktree is dirty")
        if tuple(current.changed_files) != _changed_files(root, base, head):
            reasons.append("workspace changed-file set does not match Git comparison")
        return WorkspaceVerification(not reasons, tuple(reasons))
    except WorkspaceArtifactError as exc:
        return WorkspaceVerification(False, (str(exc),))
