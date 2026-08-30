"""Governed upstream updates for the bundled portable review skill."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - available only on Windows.
    msvcrt = None  # type: ignore[assignment]

Fetcher = Callable[[str], bytes]


class ReviewFallbackUpdateError(RuntimeError):
    """Raised when an upstream fallback update cannot be staged safely."""


@dataclass(frozen=True)
class ReviewFallbackUpdatePlan:
    """Immutable check result that can be applied after drift revalidation."""

    current_revision: str
    target_revision: str
    current_sha256: str
    target_sha256: str
    snapshot_path: Path
    target_content: bytes
    diff: str

    @property
    def changed(self) -> bool:
        return self.current_sha256 != self.target_sha256


class ReviewFallbackUpdater:
    """Check and apply a pinned update without overwriting local drift."""

    MANIFEST_RELATIVE_PATH = Path("assets/upstream.json")
    LOCK_RELATIVE_PATH = Path("assets/update.lock")
    EXPECTED_LICENSE_PATH = "references/LICENSE.md"
    EXPECTED_SNAPSHOT_PATH = "references/upstream_code_reviewer.md"
    EXPECTED_REPOSITORY = "anthropics/claude-plugins-official"
    EXPECTED_SOURCE_PATH = "plugins/pr-review-toolkit/agents/code-reviewer.md"
    MAX_DOWNLOAD_BYTES = 512 * 1024
    MAX_DIFF_LINES = 240
    MAX_DIFF_BYTES = 32 * 1024

    def __init__(self, skill_dir: Path, *, fetcher: Fetcher | None = None) -> None:
        self.skill_dir = skill_dir.expanduser().resolve()
        self.fetcher = fetcher or self._fetch

    def check(self, *, target_ref: str = "main") -> ReviewFallbackUpdatePlan:
        """Resolve upstream and return a bounded, non-mutating update plan."""
        manifest, snapshot_path, current_content = self._verified_local_state()
        current_sha = self._sha256(current_content)

        repository = self._safe_repository(self._required_string(manifest, "source_repository"))
        if repository != self.EXPECTED_REPOSITORY:
            raise ReviewFallbackUpdateError(
                f"fallback source_repository must remain {self.EXPECTED_REPOSITORY}"
            )
        target_revision = self._resolve_revision(repository, target_ref)
        source_path = self._safe_source_path(self._required_string(manifest, "source_path"))
        if source_path != self.EXPECTED_SOURCE_PATH:
            raise ReviewFallbackUpdateError(
                f"fallback source_path must remain {self.EXPECTED_SOURCE_PATH}"
            )
        target_content = self._download_source(repository, target_revision, source_path)
        self._validate_upstream_content(target_content)
        target_sha = self._sha256(target_content)
        diff = self._bounded_diff(current_content, target_content, snapshot_path.name)
        return ReviewFallbackUpdatePlan(
            current_revision=self._required_string(manifest, "pinned_revision"),
            target_revision=target_revision,
            current_sha256=current_sha,
            target_sha256=target_sha,
            snapshot_path=snapshot_path,
            target_content=target_content,
            diff=diff,
        )

    def apply(self, plan: ReviewFallbackUpdatePlan) -> None:
        """Apply a checked plan after verifying neither source file has drifted."""
        with self._exclusive_update_lock():
            manifest, snapshot_path, current_content = self._verified_local_state()
            if self._required_string(manifest, "pinned_revision") != plan.current_revision:
                raise ReviewFallbackUpdateError("fallback manifest changed after the update check")
            if snapshot_path != plan.snapshot_path:
                raise ReviewFallbackUpdateError(
                    "fallback snapshot target changed after the update check"
                )
            if self._sha256(current_content) != plan.current_sha256:
                raise ReviewFallbackUpdateError("fallback snapshot changed after the update check")
            if self._sha256(plan.target_content) != plan.target_sha256:
                raise ReviewFallbackUpdateError("fallback update plan content digest is invalid")
            self._validate_revision(plan.target_revision, field="target revision")
            self._validate_upstream_content(plan.target_content)

            next_manifest = dict(manifest)
            next_manifest["pinned_revision"] = plan.target_revision
            next_manifest["source_sha256"] = plan.target_sha256
            self._atomic_write(snapshot_path, plan.target_content)
            try:
                self._atomic_write(
                    self.skill_dir / self.MANIFEST_RELATIVE_PATH,
                    (json.dumps(next_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                )
            except Exception:
                self._atomic_write(snapshot_path, current_content)
                raise

    def verify_local(self) -> None:
        """Fail closed unless the bundled snapshot matches its pinned provenance."""
        self._verified_local_state()

    def _verified_local_state(self) -> tuple[dict[str, object], Path, bytes]:
        manifest = self._load_manifest()
        repository = self._safe_repository(self._required_string(manifest, "source_repository"))
        if repository != self.EXPECTED_REPOSITORY:
            raise ReviewFallbackUpdateError(
                f"fallback source_repository must remain {self.EXPECTED_REPOSITORY}"
            )
        source_path = self._safe_source_path(self._required_string(manifest, "source_path"))
        if source_path != self.EXPECTED_SOURCE_PATH:
            raise ReviewFallbackUpdateError(
                f"fallback source_path must remain {self.EXPECTED_SOURCE_PATH}"
            )
        self._validate_revision(
            self._required_string(manifest, "pinned_revision"),
            field="pinned_revision",
        )
        snapshot_path = self._snapshot_path(manifest)
        current_content = self._read_snapshot(snapshot_path)
        current_sha = self._sha256(current_content)
        expected_sha = self._required_string(manifest, "source_sha256")
        if current_sha != expected_sha:
            raise ReviewFallbackUpdateError(
                "bundled fallback snapshot has local drift; reconcile or restore it before update"
            )
        self._validate_upstream_content(current_content)
        return manifest, snapshot_path, current_content

    @contextmanager
    def _exclusive_update_lock(self) -> Iterator[None]:
        lock_path = self.skill_dir / self.LOCK_RELATIVE_PATH
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ReviewFallbackUpdateError("fallback update lock must be a regular file")
            with os.fdopen(descriptor, "r+") as handle:
                descriptor = -1
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        yield
                    finally:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    raise ReviewFallbackUpdateError(
                        "cross-process fallback update locking is unavailable"
                    )
        except ReviewFallbackUpdateError:
            raise
        except OSError as exc:
            raise ReviewFallbackUpdateError(f"cannot lock fallback update: {exc}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _load_manifest(self) -> dict[str, object]:
        path = self.skill_dir / self.MANIFEST_RELATIVE_PATH
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReviewFallbackUpdateError(f"invalid fallback manifest: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ReviewFallbackUpdateError("fallback manifest must use schema_version 1")
        if raw.get("license") != "Apache-2.0":
            raise ReviewFallbackUpdateError("fallback manifest must retain Apache-2.0 provenance")
        if self._required_string(raw, "license_path") != self.EXPECTED_LICENSE_PATH:
            raise ReviewFallbackUpdateError(
                f"fallback license_path must remain {self.EXPECTED_LICENSE_PATH}"
            )
        license_path = self._safe_skill_relative_path(
            self._required_string(raw, "license_path"),
            field="license_path",
        )
        if not license_path.is_file():
            raise ReviewFallbackUpdateError("fallback Apache-2.0 license copy is missing")
        return raw

    def _snapshot_path(self, manifest: Mapping[str, object]) -> Path:
        if self._required_string(manifest, "snapshot_path") != self.EXPECTED_SNAPSHOT_PATH:
            raise ReviewFallbackUpdateError(
                f"fallback snapshot_path must remain {self.EXPECTED_SNAPSHOT_PATH}"
            )
        return self._safe_skill_relative_path(
            self._required_string(manifest, "snapshot_path"),
            field="snapshot_path",
        )

    def _safe_skill_relative_path(self, value: str, *, field: str) -> Path:
        relative = Path(value)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ReviewFallbackUpdateError(f"{field} must stay inside the fallback skill")
        candidate = self.skill_dir / relative
        current = self.skill_dir
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ReviewFallbackUpdateError(f"{field} must not traverse a symlink")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.skill_dir):
            raise ReviewFallbackUpdateError(f"{field} escapes the fallback skill")
        references_dir = (self.skill_dir / "references").resolve()
        if not resolved.is_relative_to(references_dir):
            raise ReviewFallbackUpdateError(f"{field} must stay inside references")
        return resolved

    @staticmethod
    def _safe_source_path(value: str) -> str:
        path = Path(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ReviewFallbackUpdateError("source_path must be a safe repository-relative path")
        return path.as_posix()

    @staticmethod
    def _safe_repository(value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is None:
            raise ReviewFallbackUpdateError(
                "source_repository must be a GitHub owner/repository pair"
            )
        return value

    @staticmethod
    def _required_string(manifest: Mapping[str, object], field: str) -> str:
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ReviewFallbackUpdateError(f"fallback manifest field {field!r} is required")
        return value.strip()

    @staticmethod
    def _read_snapshot(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ReviewFallbackUpdateError(f"cannot read fallback snapshot: {exc}") from exc

    def _resolve_revision(self, repository: str, target_ref: str) -> str:
        normalized_ref = target_ref.strip()
        if not normalized_ref:
            raise ReviewFallbackUpdateError("target ref must not be empty")
        if len(normalized_ref) > 200 or any(ord(character) < 32 for character in normalized_ref):
            raise ReviewFallbackUpdateError("target ref is not a bounded printable value")
        encoded_ref = urllib.parse.quote(normalized_ref, safe="")
        payload = self.fetcher(f"https://api.github.com/repos/{repository}/commits/{encoded_ref}")
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewFallbackUpdateError("upstream revision response is not valid JSON") from exc
        revision = data.get("sha") if isinstance(data, dict) else None
        if not isinstance(revision, str):
            raise ReviewFallbackUpdateError("upstream did not return a full commit revision")
        self._validate_revision(revision, field="upstream revision")
        return revision.lower()

    @staticmethod
    def _validate_revision(revision: str, *, field: str) -> None:
        if re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
            raise ReviewFallbackUpdateError(f"{field} must be a full hexadecimal commit revision")

    def _download_source(self, repository: str, revision: str, source_path: str) -> bytes:
        encoded_path = "/".join(
            urllib.parse.quote(part, safe="") for part in source_path.split("/")
        )
        content = self.fetcher(
            f"https://raw.githubusercontent.com/{repository}/{revision}/{encoded_path}"
        )
        if len(content) > self.MAX_DOWNLOAD_BYTES:
            raise ReviewFallbackUpdateError("upstream review source exceeds the 512 KiB limit")
        return content

    @staticmethod
    def _validate_upstream_content(content: bytes) -> None:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewFallbackUpdateError("upstream review source must be UTF-8") from exc
        required_markers = (
            "## Review Scope",
            "## Core Review Responsibilities",
            "## Issue Confidence Scoring",
            "Only report issues with confidence ≥ 80",
            "## Output Format",
        )
        missing = [marker for marker in required_markers if marker not in text]
        if missing:
            raise ReviewFallbackUpdateError(
                "upstream review contract changed; manual adapter review is required: "
                + ", ".join(missing)
            )

    @classmethod
    def _bounded_diff(cls, before: bytes, after: bytes, filename: str) -> str:
        if before == after:
            return ""
        before_lines = before.decode("utf-8", errors="replace").splitlines()
        after_lines = after.decode("utf-8", errors="replace").splitlines()
        lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"pinned/{filename}",
                tofile=f"upstream/{filename}",
                lineterm="",
            )
        )
        selected = lines[: cls.MAX_DIFF_LINES]
        text = "\n".join(selected)
        encoded = text.encode("utf-8")
        if len(encoded) > cls.MAX_DIFF_BYTES:
            text = encoded[: cls.MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
        if len(selected) < len(lines) or len(text.encode("utf-8")) < len(encoded):
            text += "\n[CAFE truncated upstream diff]"
        return text

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def _fetch(cls, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "cafe-review-fallback-updater",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                content = bytes(response.read(cls.MAX_DOWNLOAD_BYTES + 1))
        except OSError as exc:
            raise ReviewFallbackUpdateError(f"cannot fetch upstream review source: {exc}") from exc
        if len(content) > cls.MAX_DOWNLOAD_BYTES:
            raise ReviewFallbackUpdateError("upstream response exceeds the 512 KiB limit")
        return content

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
