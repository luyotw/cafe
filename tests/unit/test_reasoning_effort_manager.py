"""Issue #408 U8/U9/U10: exact preflight identity and confirmed tuple projection."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _kickoff_test_support import load_kickoff_module
from test_reasoning_effort_adapters import provider_clients


def test_phase_selection_accepts_lossless_json_and_rejects_new_default_only_chain(provider_clients):
    owner = load_kickoff_module("format_kickoff_contract")
    entries = [
        {"cli": "codex", "model": "gpt-5.5", "reasoning_effort": "high"},
        {"cli": "claude", "model": "claude-opus-4-6", "reasoning_effort": "low"},
    ]
    selected = owner._parse_phase_chains(["bespoke=" + json.dumps(entries)], step_names={"bespoke"})
    assert [entry.as_dict() for entry in selected["bespoke"]] == entries
    with pytest.raises(ValueError):
        owner._parse_phase_chains(["bespoke=codex:gpt-5.5"], step_names={"bespoke"})


def test_confirmation_and_execution_projection_preserve_per_entry_effort(provider_clients):
    from cafe.manager._schema import _validate_phases
    from cafe.manager._execution_projection import execution_inputs

    phases = [
        {
            "name": "bespoke",
            "chain": [{"cli": "codex", "model": "gpt-5.5", "reasoning_effort": "high"}],
            "rationale": {
                "band": "balanced",
                "model": "bounded implementation",
                "effort": "edge cases require checking",
            },
        }
    ]
    normalized = _validate_phases(phases)
    assert normalized == phases
    policy = {
        "file_scope": {"paths": ["app.py"], "baseline_commit": "abc", "preexisting": []},
        "phases": normalized,
        "review_configuration": None,
        "delivery_contract": {},
    }
    result = execution_inputs(
        policy,
        identity={},
        revision=1,
        digest="d",
        root="r",
        review_policy=None,
        checkpoint_command=[],
    )
    assert result["phase_chains"]["bespoke"] == phases[0]["chain"]


def test_preflight_cache_separates_effort_omission_and_control_changes(provider_clients):
    cache = load_kickoff_module("preflight_cache")
    path = provider_clients / "preflight.json"
    cache.candidate_record(
        cache_file=path,
        cli="codex",
        model="gpt-5.5",
        reasoning_effort="high",
        resolved_model="gpt-5.5",
        now=100,
        reasoning_evidence={"applied": "high", "verification": "invocation-derived"},
    )

    def check(effort):
        return cache.candidate_check(
            cache_file=path,
            cli="codex",
            model="gpt-5.5",
            reasoning_effort=effort,
            max_age_seconds=60,
            now=101,
        )[0]

    assert check("high")
    assert not check("low")
    assert not check(None)
    (provider_clients / "codex" / "config.toml").write_text('model_reasoning_effort="low"\n')
    assert not check("high")
    cache.candidate_invalidate(
        cache_file=path, cli="codex", model="gpt-5.5", reasoning_effort="high"
    )
    assert not check("high")


def test_reassessment_consumes_each_completed_phase_in_order_without_granting_mutation():
    owner = load_kickoff_module("kickoff_models")
    completed = [
        {"phase": "build", "iteration": 1, "completed_at": "2026-10-11T10:00:00Z"},
        {"phase": "verify", "iteration": 1, "completed_at": "2026-10-11T10:01:00Z"},
    ]
    result = owner.reassess_completed_phases(
        completed, prior_assessments=[], started_phases={"build", "verify"}, recommendations={}
    )
    assert [row["phase"] for row in result] == ["build", "verify"]
    assert all(
        row["outcome"] == "no_adjustment" and row["eligible_future_phases"] == [] for row in result
    )
    assert (
        owner.reassess_completed_phases(
            completed,
            prior_assessments=result,
            started_phases={"build", "verify"},
            recommendations={},
        )
        == []
    )
    proposed = {
        "future": {
            "band": "efficiency",
            "chain": [{"cli": "claude", "model": "claude-opus-4-6", "reasoning_effort": "low"}],
            "model_rationale": "uncertainty reduced",
            "effort_rationale": "routine bounded remaining work",
        }
    }
    result = owner.reassess_completed_phases(
        completed,
        prior_assessments=[],
        started_phases={"build", "verify"},
        recommendations=proposed,
    )
    assert all(row["outcome"] == "recommendation_only" for row in result)
    assert all(row["eligible_future_phases"] == ["future"] for row in result)
    assert all(row["authorized_mutation"] is False for row in result)
    assert proposed["future"]["chain"][0]["reasoning_effort"] == "low"


def test_unsupported_adapter_or_unknown_client_never_admits_explicit_effort(provider_clients):
    from cafe.agents.cli.effort import validate_effort

    for cli in ("cursor-agent", "gemini", "copilot"):
        with pytest.raises(ValueError):
            validate_effort(cli, "unknown-model", "high", explicit=True)
    binary = provider_clients / "bin" / "claude"
    binary.write_text("#!/bin/sh\necho 1.0.0\n")
    with pytest.raises(ValueError):
        validate_effort("claude", "claude-opus-4-6", "high", explicit=True)


def test_copilot_verified_subset_projects_effort_on_resume(provider_clients):
    from cafe.agents.cli.copilot import CopilotCLI
    from cafe.core.types import AgentConfig, AgentCLI

    strategy = CopilotCLI(
        AgentConfig(
            name="David",
            cli=AgentCLI.COPILOT,
            model="gpt-5.2",
            reasoning_effort="high",
            session_id="existing",
        )
    )
    command = strategy.build_command("bounded")
    assert command[command.index("--effort") + 1] == "high"
    assert command[command.index("--resume") + 1] == "existing"


def test_atomic_writer_and_ordered_smoke_preserve_each_effort(provider_clients):
    import yaml

    writer = load_kickoff_module("write_phase_config")
    cache = load_kickoff_module("preflight_cache")
    entries = [
        {"cli": "codex", "model": "gpt-5.5", "reasoning_effort": "high"},
        {"cli": "claude", "model": "claude-opus-4-6", "reasoning_effort": "low"},
    ]
    target = provider_clients / "custom/phases.yaml"
    target.parent.mkdir()
    target.write_text(
        yaml.safe_dump({"old": {"name": "Runner", "clis": [{"cli": "claude", "model": "legacy"}]}})
    )
    prior = yaml.safe_load(target.read_text())["old"]
    chains = provider_clients / "chains.json"
    chains.write_text(json.dumps({"custom": {"name": "David", "clis": entries}}))
    writer.write_phase_config(chains_file=chains, target=target)
    written = yaml.safe_load(target.read_text())
    assert written["custom"]["clis"] == entries
    assert written["old"] == prior
    original = target.read_bytes()
    entries[0]["reasoning_effort"] = "unsupported"
    chains.write_text(json.dumps({"custom": {"name": "David", "clis": entries}}))
    with pytest.raises(ValueError):
        writer.write_phase_config(chains_file=chains, target=target)
    assert target.read_bytes() == original
    entries[0]["reasoning_effort"] = "high"
    path = provider_clients / "smoke.json"

    def smoke():
        return cache.fallback_smoke(
            cache_file=path, raw_entries=entries, max_age_seconds=60, force=False, now=100
        )

    assert smoke()["status"] == "fresh"
    assert smoke()["status"] == "hit"
    entries[1]["reasoning_effort"] = "high"
    assert smoke()["status"] == "fresh"
