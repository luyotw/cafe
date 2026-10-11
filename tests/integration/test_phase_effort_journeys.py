"""Issue #408 I1/I3/I5: public phase setup reaches a real provider process."""

import json
import os
from pathlib import Path

import pytest
import yaml

from cafe.core.session_continuation import SessionContinuation
from cafe.core.types import AgentCLI
from cafe.ui.cli_shared import setup_agents
from cafe.utils.config import ConfigManager


@pytest.fixture
def phase_process(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cafe = tmp_path / ".cafe"
    cafe.mkdir()
    binary = tmp_path / "claude"
    binary.write_text("""#!/usr/bin/env python3
import json, os, sys
if '--version' in sys.argv:
 print('2.1.289'); sys.exit(0)
with open(os.environ['PROCESS_CAPTURE'], 'a') as f:
 f.write(json.dumps({'argv': sys.argv[1:], 'effort_env': os.environ.get('CLAUDE_CODE_EFFORT_LEVEL')})+'\\n')
effort = sys.argv[sys.argv.index('--effort')+1] if '--effort' in sys.argv else None
print(json.dumps({'type':'system','subtype':'init','session_id':'session-408','model':'claude-opus-4-6','effortLevel':effort}))
print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'EFFORT_OK'}]}}))
print(json.dumps({'type':'result','subtype':'success','result':'EFFORT_OK','session_id':'session-408'}))
""")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PROCESS_CAPTURE", str(tmp_path / "launches.jsonl"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-settings"))
    return tmp_path, cafe


def configure(cafe, effort):
    entry = {"cli": "claude", "model": "claude-opus-4-6"}
    if effort is not None:
        entry["reasoning_effort"] = effort
    (cafe / "phases.yaml").write_text(
        yaml.safe_dump({"bespoke-build": {"name": "David", "clis": [entry]}})
    )
    return entry


def test_custom_phase_effort_survives_public_setup_launch_and_exact_resume(phase_process):
    root, cafe = phase_process
    configure(cafe, "high")
    manager = setup_agents(
        ConfigManager(config_dir=cafe),
        "issue408-test",
        "bespoke-build",
        cafe_dir=cafe,
        stream_agent_output=False,
    )
    assert manager.execute("David", "prompt", phase_name="bespoke-build")[0] == "EFFORT_OK"
    manager.execute(
        "David",
        "continue",
        phase_name="bespoke-build",
        continuation=SessionContinuation.resume_exact(AgentCLI.CLAUDE, "session-408"),
    )
    records = [json.loads(line) for line in (root / "launches.jsonl").read_text().splitlines()]
    assert len(records) == 2
    for record in records:
        assert record["argv"][record["argv"].index("--effort") + 1] == "high"
        assert record["effort_env"] == "high"
    assert records[1]["argv"][records[1]["argv"].index("--resume") + 1] == "session-408"
    evidence = manager.get_last_reasoning_evidence()
    assert evidence["configured"] == evidence["applied"] == evidence["reported"] == "high"
    assert evidence["verification"] == "provider-confirmed"


def test_changed_effort_rejects_stale_projection_before_any_launch(phase_process):
    root, cafe = phase_process
    old = configure(cafe, "high")
    configure(cafe, "low")
    with pytest.raises(ValueError):
        setup_agents(
            ConfigManager(config_dir=cafe),
            "issue408-test",
            "bespoke-build",
            cafe_dir=cafe,
            execution_chain=[old],
        )
    assert not (root / "launches.jsonl").exists()


def test_default_phase_resume_preserves_omission_and_explicit_unknown_audit(phase_process):
    root, cafe = phase_process
    configure(cafe, None)
    manager = setup_agents(
        ConfigManager(config_dir=cafe),
        "issue408-test",
        "bespoke-build",
        cafe_dir=cafe,
        stream_agent_output=False,
    )
    manager.execute("David", "prompt", phase_name="bespoke-build")
    manager.execute("David", "continue", phase_name="bespoke-build")
    records = [json.loads(line) for line in (root / "launches.jsonl").read_text().splitlines()]
    assert all("--effort" not in record["argv"] for record in records)
    assert manager.get_last_reasoning_evidence()["verification"] == "unknown/CLI-default"


def test_provider_effort_mismatch_cannot_return_success_or_change_effort(phase_process):
    from cafe.agents.executor import AgentExecutionError

    root, cafe = phase_process
    configure(cafe, "high")
    binary = root / "claude"
    binary.write_text(binary.read_text().replace("'effortLevel':effort", "'effortLevel':'low'"))
    manager = setup_agents(
        ConfigManager(config_dir=cafe),
        "issue408-test",
        "bespoke-build",
        cafe_dir=cafe,
        stream_agent_output=False,
    )
    with pytest.raises(AgentExecutionError) as error:
        manager.execute("David", "prompt", phase_name="bespoke-build")
    assert error.value.error_type == "reasoning_effort_mismatch"
    assert manager.get_failed_attempts()[0]["reasoning_evidence"]["verification"] == "mismatch"
    assert len((root / "launches.jsonl").read_text().splitlines()) == 1


def test_retry_and_fallback_keep_distinct_effort_and_each_attempt_evidence(
    phase_process, monkeypatch
):
    root, cafe = phase_process
    primary = root / "claude"
    primary.write_text(
        primary.read_text().replace(
            "effort = sys.argv",
            "print('socket connection was closed unexpectedly', file=sys.stderr)\nsys.exit(1)\neffort = sys.argv",
        )
    )
    fallback = root / "codex"
    fallback.write_text("""#!/usr/bin/env python3
import json, os, sys
if '--version' in sys.argv:
 print('0.159.3'); sys.exit(0)
with open(os.environ['PROCESS_CAPTURE'], 'a') as f:
 f.write(json.dumps({'argv':sys.argv[1:]})+'\\n')
print(json.dumps({'type':'thread.started','thread_id':'fallback-session'}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'EFFORT_OK'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
""")
    fallback.chmod(0o755)
    cache = root / "codex-home" / "models_cache.json"
    cache.parent.mkdir()
    cache.write_text(
        json.dumps(
            {
                "client_version": "0.159.3",
                "models": [{"slug": "gpt-5.5", "supported_reasoning_levels": [{"effort": "low"}]}],
            }
        )
    )
    monkeypatch.setenv("CODEX_HOME", str(cache.parent))
    monkeypatch.setattr("cafe.agents.manager.time.sleep", lambda _seconds: None)
    entries = [
        {"cli": "claude", "model": "claude-opus-4-6", "reasoning_effort": "high"},
        {"cli": "codex", "model": "gpt-5.5", "reasoning_effort": "low"},
    ]
    (cafe / "phases.yaml").write_text(
        yaml.safe_dump({"bespoke-build": {"name": "David", "clis": entries}})
    )
    manager = setup_agents(
        ConfigManager(config_dir=cafe),
        "issue408-test",
        "bespoke-build",
        cafe_dir=cafe,
        stream_agent_output=False,
    )
    result = manager.execute("David", "prompt", phase_name="bespoke-build")
    assert result[0] == "EFFORT_OK"
    attempts = manager.get_execution_attempts()
    assert len(attempts) >= 3
    assert all(
        a["cli"] == "claude"
        and a["reasoning_evidence"]["applied"] == "high"
        and a["status"] == "failed"
        for a in attempts[:-1]
    )
    assert attempts[-1]["cli"] == "codex" and attempts[-1]["reasoning_evidence"]["applied"] == "low"
    assert manager.get_last_reasoning_evidence()["configured"] == "low"


def test_primary_only_failure_never_adds_backup_or_changes_effort(phase_process):
    from cafe.agents.executor import AgentExecutionError

    root, cafe = phase_process
    configure(cafe, "high")
    binary = root / "claude"
    binary.write_text(
        binary.read_text().replace(
            "print(json.dumps({'type':'result'", "sys.exit(1)\nprint(json.dumps({'type':'result'"
        )
    )
    manager = setup_agents(
        ConfigManager(config_dir=cafe),
        "issue408-test",
        "bespoke-build",
        cafe_dir=cafe,
        stream_agent_output=False,
    )
    with pytest.raises(AgentExecutionError):
        manager.execute("David", "prompt", phase_name="bespoke-build")
    assert len(manager.get_execution_attempts()) == 1
    assert all(entry["cli"] == "claude" for entry in manager.get_execution_attempts())


def test_candidate_probe_launches_exact_effort_and_reuses_only_matching_receipt(phase_process):
    from tests.unit._kickoff_test_support import load_kickoff_module

    root, _ = phase_process
    cache = load_kickoff_module("preflight_cache")
    binary = root / "claude"
    binary.write_text(binary.read_text().replace("EFFORT_OK", cache.PROBE_MARKER))
    path = root / "preflight.json"

    def probe(effort):
        return cache.candidate_probe(
            cache_file=path,
            cli="claude",
            model="claude-opus-4-6",
            reasoning_effort=effort,
            max_age_seconds=60,
            now=100,
        )

    for effort in ("high", "low", None):
        receipt = probe(effort)
        assert receipt["status"] == "fresh"
        assert receipt["reasoning_evidence"]["configured"] == effort
        assert probe(effort)["status"] == "hit"
    records = [json.loads(line) for line in (root / "launches.jsonl").read_text().splitlines()]
    assert len(records) == 3
    assert [record["effort_env"] for record in records] == ["high", "low", None]
    binary.write_text(binary.read_text().replace("'effortLevel':effort", "'effortLevel':'low'"))
    with pytest.raises(cache.PreflightCacheError):
        probe("high")


def test_effort_only_change_invalidates_sticky_chain_and_recovery_retains_tuple(phase_process):
    root, cafe = phase_process
    entry = configure(cafe, "high")
    active = cafe / "issues/issue408-test/active_clis.json"
    active.parent.mkdir(parents=True)
    active.write_text(
        json.dumps(
            {
                "David": {
                    "cli": "claude",
                    "model": entry["model"],
                    "configured_primary": "claude",
                    "chain": [entry],
                }
            }
        )
    )
    configure(cafe, "low")
    manager = setup_agents(
        ConfigManager(config_dir=cafe),
        "issue408-test",
        "bespoke-build",
        cafe_dir=cafe,
        stream_agent_output=False,
    )
    order = manager.resolve_invocation_order("David", "bespoke-build")
    assert order.sticky_disposition == "stale"
    assert order.entries[0].reasoning_effort == "low"
    restarted = manager.resolve_invocation_order("David", "bespoke-build", configured_order=True)
    manager.execute("David", "retry", phase_name="bespoke-build", invocation_order=restarted)
    assert manager.get_execution_attempts()[-1]["reasoning_evidence"]["applied"] == "low"


def test_interrupted_preflight_without_terminal_event_never_caches_success(phase_process):
    from tests.unit._kickoff_test_support import load_kickoff_module

    root, _ = phase_process
    cache = load_kickoff_module("preflight_cache")
    binary = root / "claude"
    source = binary.read_text().replace("EFFORT_OK", cache.PROBE_MARKER)
    binary.write_text(source[: source.index("print(json.dumps({'type':'result'")])
    path = root / "preflight.json"
    with pytest.raises(cache.PreflightCacheError):
        cache.candidate_probe(
            cache_file=path,
            cli="claude",
            model="claude-opus-4-6",
            reasoning_effort="high",
            max_age_seconds=60,
            now=100,
        )
    assert not path.exists()
