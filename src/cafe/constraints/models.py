"""Strict, mode-neutral contracts for package-owned runtime facts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from packaging.version import Version
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Tag = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]*$", min_length=1)]
CLI = Literal["codex", "claude", "gemini", "copilot"]
Platform = Literal["linux", "darwin", "win32"]
Surface = Literal["phase", "chat", "inspection"]
Operation = Literal["managed", "interactive", "event-driver"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Scope(StrictModel):
    clis: list[CLI] = Field(default_factory=list)
    providers: list[Tag] = Field(default_factory=list)
    platforms: list[Platform] = Field(default_factory=list)
    surfaces: list[Surface] = Field(default_factory=list)
    operations: list[Operation] = Field(default_factory=list)
    modes: list[Tag] = Field(default_factory=list)
    capabilities: list[Tag] = Field(default_factory=list)
    workloads: list[Tag] = Field(default_factory=list)
    consumers: list[Tag] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_values(self):
        for field in type(self).model_fields:
            values = getattr(self, field)
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate scope values: {field}")
        return self


class Context(StrictModel):
    cli: CLI = "codex"
    provider: Tag = "openai"
    platform: Platform = "linux"
    surface: Surface = "phase"
    operation: Operation = "managed"
    modes: list[Tag] = Field(default_factory=list)
    capabilities: list[Tag] = Field(default_factory=lambda: ["execute"])
    workloads: list[Tag] = Field(default_factory=list)
    consumers: list[Tag] = Field(default_factory=list)
    compatibility: str | None = None


class Quantity(StrictModel):
    name: Tag
    value: Annotated[int, Field(strict=True, gt=0)]
    unit: Literal["seconds", "bytes", "lines"]


class NumericBoundary(StrictModel):
    kind: Literal["numeric"] = "numeric"
    limits: list[Quantity] = Field(min_length=1)
    semantics: Text

    @model_validator(mode="after")
    def unique_names(self):
        if len({q.name for q in self.limits}) != len(self.limits):
            raise ValueError("duplicate quantity name")
        return self


class SemanticBoundary(StrictModel):
    kind: Literal["semantic"] = "semantic"
    rule: Text


Boundary = Annotated[NumericBoundary | SemanticBoundary, Field(discriminator="kind")]


class Variant(StrictModel):
    scope: Scope
    boundary: Boundary


class Lifecycle(StrictModel):
    introduced: str | None = None
    changed: str | None = None
    resolved: str | None = None
    historical_evidence: Text
    resolution_evidence: Text | None = None
    observed_version: str

    @field_validator("introduced", "changed", "resolved", "observed_version")
    @classmethod
    def valid_versions(cls, value):
        if value is not None:
            Version(value)
        return value

    @model_validator(mode="after")
    def ordered(self):
        versions = [
            Version(v)
            for v in (self.introduced, self.changed, self.resolved, self.observed_version)
            if v
        ]
        if versions != sorted(versions):
            raise ValueError("contradictory lifecycle order")
        return self


class Verification(StrictModel):
    source: Text
    verified_at: datetime
    expires_at: datetime | None = None
    compatibility: Text | None = None

    @model_validator(mode="after")
    def qualified(self):
        if self.verified_at.tzinfo is None or (self.expires_at and self.expires_at.tzinfo is None):
            raise ValueError("verification times require timezone")
        if not self.expires_at and not self.compatibility:
            raise ValueError("verification requires expiry or compatibility")
        if self.expires_at and self.expires_at <= self.verified_at:
            raise ValueError("expiry must follow verification")
        return self


class Entry(StrictModel):
    id: Tag
    title: Text
    status: Literal["active", "deprecated", "resolved"]
    lifecycle: Lifecycle
    enforcement: Literal["runtime", "provider-host", "policy"]
    variants: list[Variant] = Field(min_length=1)
    trigger: Text
    impact: Text
    detection: Text
    sources: list[Text] = Field(min_length=1)
    mitigation: Text
    escalation: Text
    permission_boundary: Text
    related_issues: list[Text] = Field(default_factory=list)
    replacements: list[Tag] = Field(default_factory=list)
    replacement_behavior: Text | None = None
    verification: Verification | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "resolved":
            if not self.lifecycle.resolved or not self.lifecycle.resolution_evidence:
                raise ValueError("resolved entry requires version and evidence")
        elif self.lifecycle.resolved or self.lifecycle.resolution_evidence:
            raise ValueError("unresolved entry cannot claim resolution")
        if self.enforcement == "provider-host" and self.verification is None:
            raise ValueError("external fact requires verification evidence")
        for i, left in enumerate(self.variants):
            for right in self.variants[i + 1 :]:
                # A dimension with disjoint alternatives proves non-overlap.
                if not any(
                    getattr(left.scope, f)
                    and getattr(right.scope, f)
                    and set(getattr(left.scope, f)).isdisjoint(getattr(right.scope, f))
                    for f in ("clis", "providers", "platforms", "surfaces", "operations")
                ):
                    raise ValueError(f"ambiguous variants for {self.id}")
        return self


class Registry(StrictModel):
    schema_version: Literal[1]
    entries: list[Entry] = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self):
        by_id = {e.id: e for e in self.entries}
        if len(by_id) != len(self.entries):
            raise ValueError("duplicate constraint IDs")

        def visit(identity, ancestors):
            if identity in ancestors:
                raise ValueError("replacement cycle")
            for target in by_id[identity].replacements:
                if target not in by_id:
                    raise ValueError("unknown replacement ID")
                visit(target, ancestors | {identity})

        for identity in by_id:
            visit(identity, set())
        return self
