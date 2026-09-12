"""Immutable, issue-local artifact revisions used by correction transactions."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cafe.core.packet_io import atomic_write_bytes, canonical_json, sha256_bytes

_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ArtifactRevisionError(ValueError):
    """A requested immutable revision cannot be safely applied."""


class StaleArtifactRevision(ArtifactRevisionError):
    """The request was based on a no-longer-current revision."""


@dataclass(frozen=True)
class ArtifactRevision:
    artifact: str
    sha256: str
    path: str
    operation_id: str


class ArtifactRevisionStore:
    """A small CAS store that never overwrites an accepted revision.

    The current-pointer file is the only mutable record.  Revision content is
    addressed by its digest, making duplicate operations naturally idempotent.
    A later transaction journal can use this store without turning generated
    workflow artifacts into its authority surface.
    """

    _locks: dict[Path, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = issue_dir
        self.root = issue_dir / "artifact_revisions"
        self.index_path = self.root / "index.json"
        with self._locks_guard:
            self._lock = self._locks.setdefault(self.index_path.resolve(), threading.RLock())

    def replace(
        self, artifact: str, *, base_hash: str | None, content: str, operation_id: str
    ) -> ArtifactRevision:
        name = self._artifact_name(artifact)
        if not isinstance(content, str):
            raise ArtifactRevisionError("revision content must be text")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ArtifactRevisionError("operation_id must not be empty")
        content_bytes = content.encode("utf-8")
        digest = sha256_bytes(content_bytes)
        with self._lock:
            index = self._load_index()
            prior_operation = index["operations"].get(operation_id)
            requested = {"artifact": name, "base_hash": base_hash, "sha256": digest}
            if prior_operation is not None:
                if prior_operation != requested:
                    raise ArtifactRevisionError("operation_id was already used for another revision")
                return self._revision(name, digest, operation_id)
            current = index["current"].get(name)
            current_hash = current["sha256"] if current else None
            if base_hash != current_hash:
                raise StaleArtifactRevision("base revision is not current")
            revision_path = self._revision_path(name, digest)
            if revision_path.exists() and revision_path.read_bytes() != content_bytes:
                raise ArtifactRevisionError("revision hash collides with different content")
            if not revision_path.exists():
                atomic_write_bytes(revision_path, content_bytes)
            index["operations"][operation_id] = requested
            index["current"][name] = {"sha256": digest, "path": str(revision_path.relative_to(self.issue_dir))}
            atomic_write_bytes(self.index_path, canonical_json(index))
            return self._revision(name, digest, operation_id)

    def read(self, path: str) -> str:
        candidate = (self.issue_dir / path).resolve()
        if self.issue_dir.resolve() not in candidate.parents:
            raise ArtifactRevisionError("revision path escapes issue directory")
        return candidate.read_text(encoding="utf-8")

    def prepare(self, operation_id: str, manifest: list[dict[str, Any]]) -> dict[str, Any]:
        """Durably freeze one typed invalidation manifest before mutation."""
        if not operation_id.strip() or not manifest:
            raise ArtifactRevisionError("operation and manifest are required")
        journal_path = self.root / "journals" / f"{operation_id}.json"
        with self._lock:
            if journal_path.exists():
                return self._load_journal(journal_path)
            normalized = sorted(
                ({"kind": str(item["kind"]), "id": str(item["id"])} for item in manifest),
                key=lambda item: (item["kind"], item["id"]),
            )
            if len({(item["kind"], item["id"]) for item in normalized}) != len(normalized):
                raise ArtifactRevisionError("manifest entries must be unique")
            journal = {"version": 1, "operation_id": operation_id, "state": "prepared", "manifest": normalized, "receipts": []}
            atomic_write_bytes(journal_path, canonical_json(journal))
            return journal

    def receipt(self, operation_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Record an idempotent receipt for an entry from the frozen manifest."""
        journal_path = self.root / "journals" / f"{operation_id}.json"
        with self._lock:
            journal = self._load_journal(journal_path)
            normalized = {"kind": str(entry["kind"]), "id": str(entry["id"])}
            if normalized not in journal["manifest"]:
                raise ArtifactRevisionError("receipt is not in the correction manifest")
            if normalized not in journal["receipts"]:
                journal["receipts"].append(normalized)
                journal["state"] = "applying"
                atomic_write_bytes(journal_path, canonical_json(journal))
            return normalized

    def commit(self, operation_id: str) -> dict[str, Any]:
        """Commit only once every planned invalidation receipt is durable."""
        journal_path = self.root / "journals" / f"{operation_id}.json"
        with self._lock:
            journal = self._load_journal(journal_path)
            if set(map(lambda item: (item["kind"], item["id"]), journal["receipts"])) != set(
                map(lambda item: (item["kind"], item["id"]), journal["manifest"])
            ):
                raise ArtifactRevisionError("correction journal has missing receipts")
            journal["state"] = "committed"
            atomic_write_bytes(journal_path, canonical_json(journal))
            return journal

    def _revision(self, artifact: str, digest: str, operation_id: str) -> ArtifactRevision:
        path = self._revision_path(artifact, digest)
        return ArtifactRevision(artifact, digest, str(path.relative_to(self.issue_dir)), operation_id)

    def _revision_path(self, artifact: str, digest: str) -> Path:
        return self.root / artifact / f"{digest}.txt"

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"version": 1, "current": {}, "operations": {}}
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactRevisionError("revision index is invalid") from exc
        if set(value) != {"version", "current", "operations"} or value["version"] != 1:
            raise ArtifactRevisionError("revision index has an unsupported schema")
        if not isinstance(value["current"], dict) or not isinstance(value["operations"], dict):
            raise ArtifactRevisionError("revision index is invalid")
        return value

    @staticmethod
    def _load_journal(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactRevisionError("correction journal is invalid") from exc
        if set(value) != {"version", "operation_id", "state", "manifest", "receipts"}:
            raise ArtifactRevisionError("correction journal has an unsupported schema")
        return value

    @staticmethod
    def _artifact_name(value: str) -> str:
        if not isinstance(value, str) or not _ARTIFACT_NAME.fullmatch(value):
            raise ArtifactRevisionError("artifact name is not a safe identifier")
        return value
