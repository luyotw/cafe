"""User-owned lifecycle trust declarations and constrained dispatch."""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from cafe.core.execution_boundary import EffectiveBoundary, ExecutionClass, ScriptLaunchRequest, TrustSource, snapshot_script
from cafe.core.sandbox_execution import SandboxExecutor, SandboxRunResult


class LifecycleDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: str = Field(min_length=1)
    script: Path
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    stages: tuple[str, ...]
    cwd: Path
    writable_roots: tuple[Path, ...]

    @field_validator("script", "cwd", mode="before")
    @classmethod
    def parse_paths(cls, value: object) -> Path:
        return Path(str(value))

    @field_validator("stages", mode="before")
    @classmethod
    def freeze_stages(cls, value: object) -> tuple[str, ...]:
        return tuple(value) if isinstance(value, list) else value  # type: ignore[return-value]

    @field_validator("writable_roots", mode="before")
    @classmethod
    def parse_roots(cls, value: object) -> tuple[Path, ...]:
        return tuple(Path(str(item)) for item in value)  # type: ignore[arg-type]

    @field_validator("id")
    @classmethod
    def separate_namespace(cls, value: str) -> str:
        if value.startswith("cafe."):
            raise ValueError("lifecycle declarations cannot use capability identifiers")
        return value

    @field_validator("stages")
    @classmethod
    def validate_stages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or not set(value).issubset({"prepare", "close"}):
            raise ValueError("lifecycle stages must be prepare and/or close")
        return value


class LifecycleTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".cafe" / "lifecycle-trust.yaml"

    def list(self) -> tuple[LifecycleDeclaration, ...]:
        if not self.path.exists():
            return ()
        payload = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return tuple(LifecycleDeclaration.model_validate(item) for item in payload.get("declarations", []))

    def get(self, declaration_id: str) -> LifecycleDeclaration | None:
        return next((item for item in self.list() if item.id == declaration_id), None)

    def put(self, declaration: LifecycleDeclaration) -> None:
        declarations = [item for item in self.list() if item.id != declaration.id] + [declaration]
        self._write(declarations)

    def revoke(self, declaration_id: str) -> bool:
        before = list(self.list())
        after = [item for item in before if item.id != declaration_id]
        if len(after) == len(before):
            return False
        self._write(after)
        return True

    def validate_identity(self, declaration: LifecycleDeclaration) -> bool:
        try:
            snap = snapshot_script(declaration.script, allowed_root=declaration.script.parent)
        except (OSError, ValueError):
            return False
        try:
            return snap.digest == declaration.digest
        finally:
            snap.cleanup()

    def _write(self, declarations: list[LifecycleDeclaration]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, raw_path = tempfile.mkstemp(prefix="lifecycle-trust-", dir=self.path.parent)
        tmp = Path(raw_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump({"version": 1, "declarations": [item.model_dump(mode="json") for item in declarations]}, handle, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)


def declare_lifecycle_trust(store: LifecycleTrustStore, *, script: Path, stage: str, cwd: Path, writable_roots: tuple[Path, ...], declaration_id: str | None = None) -> LifecycleDeclaration:
    snap = snapshot_script(script, allowed_root=script.parent)
    try:
        declaration = LifecycleDeclaration(id=declaration_id or f"lifecycle-{uuid.uuid4().hex[:12]}", script=script.resolve(), digest=snap.digest, stages=(stage,), cwd=cwd.resolve(), writable_roots=tuple(root.resolve() for root in writable_roots))
    finally:
        snap.cleanup()
    store.put(declaration)
    return declaration


def run_lifecycle(store: LifecycleTrustStore, declaration_id: str, *, stage: str, executor: SandboxExecutor | None = None) -> SandboxRunResult:
    declaration = store.get(declaration_id)
    if declaration is None or stage not in declaration.stages or not store.validate_identity(declaration):
        raise ValueError("lifecycle declaration missing, out of scope, or changed")
    boundary = EffectiveBoundary(cwd=declaration.cwd, readable_roots=(declaration.cwd, declaration.script.parent), writable_roots=declaration.writable_roots, network_destinations=(), environment={"PATH": os.environ.get("PATH", "")})
    request = ScriptLaunchRequest(execution_class=ExecutionClass.LIFECYCLE, trust_source=TrustSource.USER_DECLARATION, script=declaration.script, boundary=boundary)
    return (executor or SandboxExecutor()).run(request)
