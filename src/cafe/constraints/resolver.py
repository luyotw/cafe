"""Deterministic applicability and clock-sensitive external validity."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pydantic import Field

from .models import Boundary, Context, Entry, Registry, Scope, StrictModel
from .registry import load_registry

PROVIDERS = {"codex": "openai", "claude": "anthropic", "gemini": "google", "copilot": "github"}


class EffectiveEntry(StrictModel):
    id: str
    status: str
    enforcement: str
    scope: Scope
    boundary: Boundary | None
    validity: str
    record: Entry


class View(StrictModel):
    context: Context
    entries: list[EffectiveEntry]
    diagnostics: list[str] = Field(default_factory=list)


def execution_context(cli="codex", **kwargs) -> Context:
    cli = getattr(cli, "value", cli)
    return Context(cli=cli, provider=PROVIDERS[cli], platform=sys.platform, **kwargs)


def effective_context(context: Context) -> Context:
    capabilities = set(context.capabilities)
    if "execute" in capabilities and (
        not context.workloads or set(context.workloads) != {"short-docs"}
    ):
        capabilities.add("long-command")
    return context.model_copy(update={"capabilities": sorted(capabilities)})


def matches(scope: Scope, context: Context) -> bool:
    for plural, singular in (
        ("clis", "cli"),
        ("providers", "provider"),
        ("platforms", "platform"),
        ("surfaces", "surface"),
        ("operations", "operation"),
    ):
        allowed = getattr(scope, plural)
        if allowed and getattr(context, singular) not in allowed:
            return False
    for field in ("modes", "capabilities", "workloads", "consumers"):
        required = getattr(scope, field)
        if required and not set(required).intersection(getattr(context, field)):
            return False
    return True


def resolve(
    context: Context,
    *,
    registry: Registry | None = None,
    now: datetime | None = None,
    history: bool = False,
) -> View:
    context = effective_context(context)
    now = now or datetime.now(timezone.utc)
    selected, diagnostics = [], []
    for entry in (registry or load_registry()).entries:
        if entry.status != "active" and not history:
            continue
        for variant in entry.variants:
            if not matches(variant.scope, context):
                continue
            validity = "code-backed"
            if entry.verification:
                proof = entry.verification
                verified = (
                    proof.verified_at <= now
                    and (not proof.expires_at or now < proof.expires_at)
                    and (not proof.compatibility or proof.compatibility == context.compatibility)
                )
                validity = "verified" if verified else "unverified"
                if not verified:
                    diagnostics.append(
                        f"{entry.id}: unverified external fact; reverify source or request assistance"
                    )
            selected.append(
                EffectiveEntry(
                    id=entry.id,
                    status=entry.status,
                    enforcement=entry.enforcement,
                    scope=variant.scope,
                    boundary=variant.boundary if validity != "unverified" else None,
                    validity=validity,
                    record=entry,
                )
            )
    return View(
        context=context, entries=sorted(selected, key=lambda e: e.id), diagnostics=diagnostics
    )


def numeric_limit(identity: str, quantity: str, context: Context) -> int:
    for entry in resolve(context).entries:
        if entry.id == identity and entry.boundary and entry.boundary.kind == "numeric":
            for limit in entry.boundary.limits:
                if limit.name == quantity:
                    return limit.value
    raise ValueError(f"No applicable numeric limit: {identity}/{quantity}")
