"""Typed, fail-closed boundaries for workflow-managed script execution."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class ExecutionClass(str, Enum):
    SANDBOX = "sandbox"
    LIFECYCLE = "lifecycle"
    CAPABILITY = "capability"


class TrustSource(str, Enum):
    WORKFLOW = "workflow"
    USER_DECLARATION = "user_declaration"
    PACKAGE_REGISTRY = "package_registry"


_SENSITIVE_PARTS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH", "PROXY")
_ENV_ALLOWLIST = {"PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "TZ"}


def is_sensitive_name(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in _SENSITIVE_PARTS)


def redact(value: Any, *, key: str = "") -> Any:
    if key and is_sensitive_name(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class StrictBoundaryModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, arbitrary_types_allowed=True
    )


class EffectiveBoundary(StrictBoundaryModel):
    cwd: Path
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    network_destinations: tuple[str, ...] = ()
    environment: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("readable_roots", "writable_roots", "network_destinations", mode="before")
    @classmethod
    def freeze_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("environment")
    @classmethod
    def sanitize_environment(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        clean = {
            str(k): str(v)
            for k, v in value.items()
            if k in _ENV_ALLOWLIST and not is_sensitive_name(k)
        }
        return MappingProxyType(clean)

    @field_serializer("environment")
    def serialize_environment(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class ScriptLaunchRequest(StrictBoundaryModel):
    execution_class: ExecutionClass
    trust_source: TrustSource
    script: Path
    args: tuple[str, ...] = ()
    boundary: EffectiveBoundary
    timeout_seconds: float = Field(default=60.0, gt=0, le=3600)


class ExecutionReceipt(StrictBoundaryModel):
    correlation_id: str = Field(min_length=1)
    execution_class: ExecutionClass
    trust_source: TrustSource
    outcome: str = Field(min_length=1)
    boundary: EffectiveBoundary
    canonical_identity: str | None = None
    policy_decision: str | None = None
    details: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def redact_details(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return MappingProxyType(redact(value))

    @field_serializer("details")
    def serialize_details(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return dict(value)


class ScriptSnapshot:
    def __init__(self, path: Path, digest: str, temp_dir: Path) -> None:
        self.path = path
        self.digest = digest
        self._temp_dir = temp_dir

    @property
    def root(self) -> Path:
        return self._temp_dir

    def cleanup(self) -> None:
        shutil.rmtree(self._temp_dir, ignore_errors=True)


def snapshot_script(script: Path, *, allowed_root: Path) -> ScriptSnapshot:
    """Copy a regular script using fd-relative, no-follow traversal."""
    root = Path(os.path.abspath(allowed_root))
    candidate = Path(os.path.abspath(script))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("script escapes its declared root") from exc
    if not relative.parts:
        raise ValueError("script must name a file below its declared root")

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    directory_fd = os.open(os.sep, directory_flags)
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        for part in relative.parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(relative.parts[-1], os.O_RDONLY | no_follow, dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("script must be a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(fd, 65536):
                chunks.append(chunk)
            content = b"".join(chunks)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("script path contains a symlink or invalid component") from exc
    finally:
        os.close(directory_fd)
    temp_dir = Path(tempfile.mkdtemp(prefix="cafe-script-"))
    target = temp_dir / "script"
    target.write_bytes(content)
    target.chmod(0o500)
    return ScriptSnapshot(target, hashlib.sha256(content).hexdigest(), temp_dir)


def snapshot_script_tree(script: Path, *, allowed_root: Path) -> ScriptSnapshot:
    """Copy a script and its declared runtime tree without following symlinks."""
    root = Path(os.path.abspath(allowed_root))
    candidate = Path(os.path.abspath(script))
    try:
        relative_script = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("script escapes its declared root") from exc
    if not relative_script.parts:
        raise ValueError("script must name a file below its declared root")
    if root == Path(root.anchor):
        raise ValueError("script runtime root must not be a filesystem root")

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    root_fd = os.open(os.sep, directory_flags)
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=root_fd)
            os.close(root_fd)
            root_fd = next_fd
    except OSError as exc:
        os.close(root_fd)
        raise ValueError("script runtime root contains a symlink or invalid component") from exc

    temp_dir = Path(tempfile.mkdtemp(prefix="cafe-script-tree-"))
    script_content: bytes | None = None

    def copy_directory(source_fd: int, destination: Path, relative: Path) -> None:
        nonlocal script_content
        destination.mkdir(mode=0o700, exist_ok=True)
        for name in sorted(os.listdir(source_fd)):
            source_stat = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            item_relative = relative / name
            target = destination / name
            if stat.S_ISDIR(source_stat.st_mode):
                child_fd = os.open(name, directory_flags, dir_fd=source_fd)
                try:
                    opened_stat = os.fstat(child_fd)
                    if (
                        opened_stat.st_dev != source_stat.st_dev
                        or opened_stat.st_ino != source_stat.st_ino
                    ):
                        raise ValueError("script runtime directory changed during snapshot")
                    copy_directory(child_fd, target, item_relative)
                finally:
                    os.close(child_fd)
                continue
            if stat.S_ISLNK(source_stat.st_mode):
                link_target = os.readlink(name, dir_fd=source_fd)
                if Path(link_target).is_absolute() or ".." in Path(link_target).parts:
                    raise ValueError("script runtime tree contains an escaping symlink")
                target.symlink_to(link_target)
                continue
            if not stat.S_ISREG(source_stat.st_mode):
                raise ValueError("script runtime tree contains a non-regular entry")
            file_fd = os.open(name, os.O_RDONLY | no_follow, dir_fd=source_fd)
            try:
                opened_stat = os.fstat(file_fd)
                if (
                    opened_stat.st_dev != source_stat.st_dev
                    or opened_stat.st_ino != source_stat.st_ino
                    or not stat.S_ISREG(opened_stat.st_mode)
                ):
                    raise ValueError("script runtime entry changed during snapshot")
                chunks: list[bytes] = []
                while chunk := os.read(file_fd, 65536):
                    chunks.append(chunk)
                content = b"".join(chunks)
                final_stat = os.fstat(file_fd)
                if (
                    final_stat.st_size != opened_stat.st_size
                    or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
                    or final_stat.st_ctime_ns != opened_stat.st_ctime_ns
                ):
                    raise ValueError("script runtime entry changed during snapshot")
            finally:
                os.close(file_fd)
            target.write_bytes(content)
            target.chmod(stat.S_IMODE(source_stat.st_mode))
            if item_relative == relative_script:
                script_content = content

    try:
        copy_directory(root_fd, temp_dir, Path())
        target = temp_dir / relative_script
        if script_content is None or not target.is_file():
            raise ValueError("script must be a regular file")
        target.chmod(stat.S_IMODE(target.stat().st_mode) | 0o500)
        return ScriptSnapshot(
            target,
            hashlib.sha256(script_content).hexdigest(),
            temp_dir,
        )
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    finally:
        os.close(root_fd)
