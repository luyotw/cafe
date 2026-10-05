"""Canonical constraints contracts: plan U1-U7 and U10."""

import copy
import json
from datetime import datetime, timezone

import pytest

from cafe.constraints import Context, load_registry, resolve, render_prompt, material_digest
from cafe.constraints.registry import parse_registry
from cafe.constraints.rendering import render_docs


def seed():
    return load_registry().model_dump(mode="json")


def test_inventory_is_complete_and_packaged():
    registry = load_registry()
    assert len(registry.entries) == 9
    assert {e.id for e in registry.entries} >= {
        "agent.stdout-idle",
        "callback.attempt-budget",
        "human-task.user-authority",
    }
    assert parse_registry(json.dumps(seed())) == registry


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(unexpected=True),
        lambda d: d["entries"].append(copy.deepcopy(d["entries"][0])),
        lambda d: d["entries"][0].update(mitigation=""),
        lambda d: d["entries"][0].update(sources=[]),
        lambda d: d["entries"][0]["variants"][0]["scope"].update(clis=["bogus"]),
        lambda d: d["entries"][0]["variants"].append(copy.deepcopy(d["entries"][0]["variants"][0])),
        lambda d: d["entries"][0]["variants"][0]["boundary"]["limits"][0].update(value=True),
        lambda d: d["entries"][0]["variants"][0]["boundary"]["limits"][0].update(unit="minutes"),
        lambda d: d["entries"][0]["lifecycle"].update(resolved="0.7.2"),
        lambda d: d["entries"][0].update(replacements=[d["entries"][0]["id"]]),
    ],
)
def test_invalid_registry_is_rejected(mutation):
    data = seed()
    mutation(data)
    with pytest.raises(ValueError):
        parse_registry(json.dumps(data))


def test_duplicate_object_keys_rejected():
    with pytest.raises(ValueError):
        parse_registry('{"schema_version":1,"schema_version":1,"entries":[]}')


def values(view, identity):
    return {q.name: q.value for e in view.entries if e.id == identity for q in e.boundary.limits}


def test_context_scopes_and_history():
    assert values(resolve(Context(cli="codex")), "agent.stdout-idle")["idle"] == 300
    assert values(resolve(Context(cli="gemini")), "agent.stdout-idle")["idle"] == 600
    assert "agent.stdout-idle" not in {e.id for e in resolve(Context(platform="win32")).entries}
    assert "agent.stdout-idle" not in {
        e.id for e in resolve(Context(operation="interactive")).entries
    }
    assert "agent.stdout-idle" not in {
        e.id for e in resolve(Context(workloads=["short-docs"], capabilities=[])).entries
    }
    data = seed()
    entry = data["entries"][0]
    entry.update(status="resolved")
    entry["lifecycle"].update(resolved="0.7.2", resolution_evidence="Replaced by tested behavior")
    registry = parse_registry(json.dumps(data))
    assert entry["id"] not in {e.id for e in resolve(Context(), registry=registry).entries}
    assert entry["id"] in {
        e.id for e in resolve(Context(), registry=registry, history=True).entries
    }


def external_registry():
    data = seed()
    entry = data["entries"][0]
    entry.update(
        enforcement="provider-host",
        verification={
            "source": "https://provider.example/limits",
            "verified_at": "2026-10-01T00:00:00Z",
            "expires_at": "2026-10-10T00:00:00Z",
            "compatibility": "v1",
        },
    )
    return parse_registry(json.dumps(data))


def test_external_freshness_never_claims_unverified_numeric_value():
    registry = external_registry()
    fresh = resolve(
        Context(compatibility="v1"),
        registry=registry,
        now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    stale = resolve(
        Context(compatibility="v1"),
        registry=registry,
        now=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )
    assert next(e for e in fresh.entries if e.id == "agent.stdout-idle").validity == "verified"
    assert next(e for e in stale.entries if e.id == "agent.stdout-idle").boundary is None
    assert stale.diagnostics and "unverified" in render_prompt(stale)
    assert material_digest(fresh) != material_digest(stale)
    for context, now in [
        (Context(compatibility="v2"), datetime(2026, 10, 5, tzinfo=timezone.utc)),
        (Context(compatibility="v1"), datetime(2026, 9, 5, tzinfo=timezone.utc)),
    ]:
        assert (
            next(
                e
                for e in resolve(context, registry=registry, now=now).entries
                if e.id == "agent.stdout-idle"
            ).boundary
            is None
        )
    data = registry.model_dump(mode="json")
    data["entries"][0]["verification"] = None
    with pytest.raises(ValueError):
        parse_registry(json.dumps(data))


def test_material_identity_excludes_editorial_metadata_and_unrelated_entries():
    context = Context(cli="codex")
    before = material_digest(resolve(context))
    data = seed()
    data["entries"][0].update(title="Editorial change", sources=["src/cafe/agents/executor.py:999"])
    assert material_digest(resolve(context, registry=parse_registry(json.dumps(data)))) == before
    data["entries"][0]["variants"][0]["boundary"]["limits"][0]["value"] += 1
    assert material_digest(resolve(context, registry=parse_registry(json.dumps(data)))) != before


def test_block_is_bounded_and_preserves_actions_or_fails():
    view = resolve(Context(consumers=["callback", "authority"]))
    prompt = render_prompt(view)
    assert len(prompt.encode()) <= 8192
    assert all(e.id in prompt for e in view.entries)
    assert "constraint_assistance" in prompt and "completion_evidence" in prompt
    assert "exit status" in prompt
    data = seed()
    data["entries"][0]["mitigation"] = "必要" * 5000
    with pytest.raises(ValueError, match="agent.stdout-idle"):
        render_prompt(resolve(Context(), registry=parse_registry(json.dumps(data))))


def test_documentation_is_deterministic_and_contains_sources_actions_history():
    registry = load_registry()
    docs = render_docs(registry)
    assert docs == render_docs(registry)
    for e in registry.entries:
        assert e.id in docs and e.mitigation in docs and e.detection in docs
    assert "Policy" in docs and "Runtime" in docs and "Resolved" in docs
