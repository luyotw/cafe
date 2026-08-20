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
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, arbitrary_types_allowed=True)


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
        clean = {str(k): str(v) for k, v in value.items() if k in _ENV_ALLOWLIST and not is_sensitive_name(k)}
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

    def cleanup(self) -> None:
        shutil.rmtree(self._temp_dir, ignore_errors=True)


def snapshot_script(script: Path, *, allowed_root: Path) -> ScriptSnapshot:
    """Copy a regular script through O_NOFOLLOW into a private immutable snapshot."""
    root = allowed_root.resolve(strict=True)
    candidate = script.absolute()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("script escapes its declared root") from exc
    relative = candidate.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("script path contains a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(candidate, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("script must be a regular file")
        content = b""
        while chunk := os.read(fd, 65536):
            content += chunk
    finally:
        os.close(fd)
    temp_dir = Path(tempfile.mkdtemp(prefix="cafe-script-"))
    target = temp_dir / "script"
    target.write_bytes(content)
    target.chmod(0o500)
    return ScriptSnapshot(target, hashlib.sha256(content).hexdigest(), temp_dir)
