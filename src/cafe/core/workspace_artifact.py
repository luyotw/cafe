"""Declared, schema-versioned identity for a verified Git workspace."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from cafe.verification.receipt import check_verification_receipt

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


def _receipt_path(value: Path, *, root: Path) -> Path:
    """Resolve only the canonical receipt adjacent to an in-repository output."""
    candidate_input = Path(value)
    if candidate_input.is_symlink() or any(
        parent.is_symlink() for parent in candidate_input.parents if parent != Path(".")
    ):
        raise WorkspaceArtifactError("workspace receipt input must not use a symlink")
    candidate = candidate_input.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceArtifactError("workspace receipt reference is outside the repository") from exc
    if candidate.name == "verification.json":
        receipt = candidate
    elif candidate.name == "output.md":
        receipt = candidate.parent / "verification.json"
    else:
        raise WorkspaceArtifactError(
            "workspace receipt input must be an in-repository output.md or verification.json"
        )
    try:
        receipt.resolve().relative_to(root)
    except ValueError as exc:
        raise WorkspaceArtifactError("workspace verification receipt is outside the repository") from exc
    if receipt.name != "verification.json":
        raise WorkspaceArtifactError("workspace receipt must be verification.json")
    return receipt


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class WorkspaceArtifact:
    """The persisted current-contract workspace companion."""

    name: str
    version: int
    repository: str
    base_sha: str
    head_sha: str
    changed_files: tuple[dict[str, str], ...]
    receipts: tuple[dict[str, str], ...]
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
            "receipts": [dict(item) for item in self.receipts],
            "updated_at": self.updated_at,
            "producer_step": self.producer_step,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WorkspaceArtifact":
        if not isinstance(raw, Mapping):
            raise WorkspaceArtifactError("workspace record must be an object")
        version = raw.get("schema_version")
        if version != WORKSPACE_SCHEMA_VERSION:
            raise WorkspaceArtifactError(
                f"unsupported workspace schema version: {version!r}"
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
        changed = _normalize_changed_files(raw.get("changed_files"))
        receipts = _normalize_receipts(raw.get("receipts"))
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
            changed_files=changed,
            receipts=receipts,
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


def _normalize_receipts(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise WorkspaceArtifactError("workspace receipts must be a list")
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise WorkspaceArtifactError("workspace receipt entry must be an object")
        path = item.get("path")
        digest = item.get("sha256")
        scope = item.get("scope")
        head = item.get("head")
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(scope, str)
            or not scope
            or not isinstance(head, str)
            or not _SHA.fullmatch(head)
        ):
            raise WorkspaceArtifactError("workspace receipt entry is invalid")
        path_value = Path(path)
        if path_value.is_absolute() or ".." in path_value.parts or path_value.name != "verification.json":
            raise WorkspaceArtifactError(
                "workspace receipt path must be a repository-relative verification.json"
            )
        normalized.append({"path": path, "sha256": digest, "scope": scope, "head": head})
    if len({item["path"] for item in normalized}) != len(normalized):
        raise WorkspaceArtifactError("workspace receipts must be unique")
    if len({item["sha256"] for item in normalized}) != len(normalized):
        raise WorkspaceArtifactError("workspace receipts must bind distinct canonical files")
    return tuple(normalized)


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
    """Build a workspace identity exclusively from Git and verified receipts."""
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
    receipt_records: list[dict[str, str]] = []
    for output in receipt_outputs:
        output = Path(output)
        receipt_path = _receipt_path(output, root=root)
        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceArtifactError("workspace verification receipt is unreadable") from exc
        scope = payload.get("scope") if isinstance(payload, dict) else None
        if not isinstance(scope, str) or not scope:
            raise WorkspaceArtifactError("workspace verification receipt scope is missing")
        checked = check_verification_receipt(output_file=output, required_scope=scope, cwd=root)
        if not checked.valid or checked.receipt is None:
            raise WorkspaceArtifactError("workspace verification receipt is invalid or stale")
        recorded_head = checked.receipt.get("git", {}).get("head")
        if recorded_head != resolved_head:
            raise WorkspaceArtifactError("workspace verification receipt head is stale")
        relative = receipt_path.relative_to(root).as_posix()
        receipt_records.append(
            {
                "path": relative,
                "sha256": _sha256(receipt_path),
                "scope": scope,
                "head": resolved_head,
            }
        )
    return WorkspaceArtifact(
        name=name,
        version=version,
        repository=str(root),
        base_sha=resolved_base,
        head_sha=resolved_head,
        changed_files=_changed_files(root, resolved_base, resolved_head),
        receipts=_normalize_receipts(receipt_records),
        updated_at=updated_at,
        producer_step=producer_step,
    )


def verify_workspace_artifact(
    artifact: WorkspaceArtifact | Mapping[str, Any], *, repo: Path
) -> WorkspaceVerification:
    """Verify a workspace record before a current-contract consumer runs."""
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
        seen_paths: set[str] = set()
        for receipt in current.receipts:
            relative_receipt = Path(receipt["path"])
            if (
                relative_receipt.is_absolute()
                or ".." in relative_receipt.parts
                or relative_receipt.name != "verification.json"
            ):
                reasons.append(f"workspace receipt path is not canonical: {receipt['path']}")
                continue
            receipt_candidate = root / relative_receipt
            if receipt_candidate.is_symlink() or any(
                parent.is_symlink() for parent in receipt_candidate.parents if parent != root
            ):
                reasons.append(f"workspace receipt must not use a symlink: {receipt['path']}")
                continue
            receipt_path = receipt_candidate.resolve()
            try:
                receipt_path.relative_to(root)
            except ValueError:
                reasons.append(f"workspace receipt escapes repository: {receipt['path']}")
                continue
            if receipt["path"] in seen_paths:
                reasons.append("workspace receipts are duplicated")
                continue
            seen_paths.add(receipt["path"])
            if not receipt_path.is_file() or _sha256(receipt_path) != receipt["sha256"]:
                reasons.append(f"workspace receipt is missing or stale: {receipt['path']}")
                continue
            try:
                payload = json.loads(receipt_path.read_text(encoding="utf-8"))
                output_file = receipt_path.parent / "output.md"
                checked = check_verification_receipt(
                    output_file=output_file, required_scope=receipt["scope"], cwd=root
                )
            except (OSError, json.JSONDecodeError, WorkspaceArtifactError):
                checked = None
            if checked is None or not checked.valid:
                reasons.append(f"workspace receipt is invalid: {receipt['path']}")
                continue
            if receipt["head"] != head or payload.get("git", {}).get("head") != head:
                reasons.append(f"workspace receipt head is stale: {receipt['path']}")
        return WorkspaceVerification(not reasons, tuple(reasons))
    except WorkspaceArtifactError as exc:
        return WorkspaceVerification(False, (str(exc),))
