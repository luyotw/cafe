"""U2/I9: selected compact preparation across public helper entrypoints."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module, SCRIPT_ROOT
from test_reasoning_effort_adapters import install_provider_clients


@pytest.fixture
def compact_request(tmp_path, monkeypatch):
    provider_clients = install_provider_clients(tmp_path, monkeypatch)
    from cafe.utils import config

    monkeypatch.setattr(config, "get_global_cafe_dir", lambda **kwargs: tmp_path / "global")
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "--allow-empty",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    (root / ".gitignore").write_text(".cafe/\n")
    subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c",
                    "user.email=test@example.org", "commit", "-qm", "ignore workflow data"], check=True)
    subprocess.run(["git", "-C", str(root), "checkout", "-qb", "feature"], check=True)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(remote)], check=True)
    skills = root / ".cafe/skills/plain"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: plain\ndescription: Test\n---\n")
    playbooks = root / ".cafe/playbooks"
    playbooks.mkdir(parents=True)
    (playbooks / "selected.yaml").write_text(
        yaml.safe_dump(
            {
                "playbook": {
                    "id": "selected",
                    "applicability": {
                        "summary": "Clear change",
                        "use_when": ["small"],
                        "avoid_when": ["research"],
                    },
                },
                "contract": {"mode": "compact"},
                "roles": {"operator": {}},
                "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
                "commands": {
                    "prepare": {
                        "fields": [
                            {
                                "id": "input_method",
                                "type": "text",
                                "label": "Input",
                                "write": "build.input_method",
                            }
                        ]
                    }
                },
                "steps": {
                    "build": {"role": "operator", "skill": "plain", "on": {"await_agent": "_done"}}
                },
            }
        )
    )
    cache = provider_clients / 'codex' / 'models_cache.json'
    metadata = json.loads(cache.read_text())
    metadata['models'].append({'slug':'test', 'supported_reasoning_levels':[{'effort':'high'},{'effort':'low'}]})
    cache.write_text(json.dumps(metadata))
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "project_root": str(root),
        "issue_name": "sample",
        "manager_cli": "codex",
        "model_assessments": [
            {
                "provider": "codex",
                "model": "test",
                "version": "test-v1",
                "reasoning_effort": "high",
                "assessed_at": now,
                "sources": [
                    {
                        "url": "https://example.org/provider",
                        "retrieved_at": now,
                        "fingerprint": "fixture",
                    }
                ],
                "workloads": ["implementation"],
                "reasoning": "standard",
                "capability_bands": {"implementation": "strong"},
                "limitations": ["Test fixture"],
            }
        ],
        "playbook_id": "selected",
        "compact_inputs": {
            "files": ["app.py", "tests/test_app.py"],
            "phases": [{"name": "build", "chain": [{"cli": "codex", "model": "test", "reasoning_effort": "high"}], "rationale": {"band":"balanced", "model":"bounded implementation", "effort":"verify edge cases"}}],
            "review_configuration": {
                "cli": "codex",
                "model": "test",
                "provider_version": "test",
                "read_only": True,
                "model_behavior": "inherits_parent",
                "checkpoint_interface": "parent_command",
            },
            "delivery_contract": {
                "schema_version": 4,
                "route": "pr",
                "remote": "origin",
                "source_branch": "feature",
                "target_branch": "main",
                "effects": ["create_pr"],
            },
        },
    }


def test_compact_public_discovery_assembly_render_only_three_groups(compact_request, tmp_path):
    owner = load_kickoff_module("kickoff_inputs")
    discovered = owner.discover_kickoff(
        compact_request, config_dir=tmp_path / "config", cache_dir=tmp_path / "cache"
    )
    assert discovered["contract_mode"] == "compact"
    assert [c["id"] for c in discovered["catalog"]["candidates"]] == ["selected"]
    assembled = owner.assemble_kickoff(compact_request, discovery=discovered)
    assert assembled["status"] == "ready", assembled
    rendered = owner.render_kickoff(assembled["formatter_inputs"])
    assert rendered["status"] == "rendered", rendered
    assert set(rendered["decision_groups"]) == {"files", "execution", "delivery"}
    assert "沒有我的同意禁止修改上面列出來的檔案" in rendered["output"]
    assert "outcome" not in rendered["proposal"]["delivery_contract"]
    assert rendered["proposal"]["manager"] == {"mode": "event-driven", "clis": [{"cli": "codex"}]}
    assert rendered["decision_groups"]["execution"]["manager"] == rendered["proposal"]["manager"]


@pytest.mark.parametrize("mode,settings", [
    ("unattended", {}), ("attached", {"poll_interval_seconds": 17}),
    ("event-driven", {"event_manager": ["claude", "codex:test"]}),
])
@pytest.mark.parametrize("saved", [False, True], ids=["explicit", "persisted"])
def test_compact_public_modes_use_the_selected_settings(compact_request, tmp_path, mode, settings, saved):
    owner = load_kickoff_module("kickoff_inputs")
    config = tmp_path / "config"
    if saved:
        prefs = load_kickoff_module("kickoff_preferences").PreferenceStore(
            config, repository_root=Path(compact_request["project_root"]))
        prefs.set("manager.mode", mode, scope="user", origin="explicit")
        for key, value in settings.items():
            prefs.set("manager." + key, value, scope="user", origin="explicit")
    else:
        compact_request["formatter_inputs"] = {"manager_mode": mode, **settings}
    discovered = owner.discover_kickoff(compact_request, config_dir=config, cache_dir=tmp_path / "cache")
    assembled = owner.assemble_kickoff(compact_request, discovery=discovered)
    assert assembled["status"] == "ready", assembled
    rendered = owner.render_kickoff(assembled["formatter_inputs"])
    manager = rendered["proposal"]["manager"]
    assert manager["mode"] == mode
    if mode == "attached":
        assert manager["poll_interval_seconds"] == 17
    elif mode == "event-driven":
        assert manager["clis"] == [{"cli": "claude"}, {"cli": "codex", "model": "test"}]
    assert rendered["decision_groups"]["execution"]["manager"] == manager


def test_compact_missing_values_produce_focused_gaps(compact_request, tmp_path):
    compact_request["compact_inputs"].pop("files")
    owner = load_kickoff_module("kickoff_inputs")
    report = owner.discover_kickoff(
        compact_request, config_dir=tmp_path / "config", cache_dir=tmp_path / "cache"
    )
    assembly = owner.assemble_kickoff(compact_request, discovery=report)
    assert assembly["status"] == "incomplete"
    assert {gap["requirement"] for gap in assembly["missing_decisions"]} == {"files"}
    assert not any("strategy" in gap["requirement"] for gap in assembly["missing_decisions"])


def test_staged_render_creates_complete_compact_proposal(compact_request, tmp_path):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(compact_request))
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_ROOT / "prepare_kickoff.py"),
            "render",
            "--request-file",
            str(request_path),
            "--config-dir",
            str(tmp_path / "config"),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--output",
            str(tmp_path / "proposal.md"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "rendered"
    assert (tmp_path / "proposal.md").is_file()


@pytest.mark.parametrize("explicit", [False, True])
def test_saved_effort_preferences_remain_hints_until_phase_selection_is_complete(compact_request, tmp_path, explicit):
    owner = load_kickoff_module("kickoff_inputs")
    prefs = load_kickoff_module("kickoff_preferences")
    root = Path(compact_request["project_root"])
    compact_request["compact_inputs"].pop("phases")
    config = tmp_path / "saved-prefs"
    store = prefs.PreferenceStore(config, repository_root=root)
    chain = [{"cli":"codex", "model":"test", "reasoning_effort":"high"}] if explicit else ["codex:test"]
    store.set("phase.chains", {"steps":{"build":chain}}, scope="repository", origin="explicit")
    discovered = owner.discover_kickoff(compact_request, config_dir=config, cache_dir=tmp_path / "cache")
    assembled = owner.assemble_kickoff(compact_request, discovery=discovered)
    assert assembled["status"] == "incomplete"
    requirements = {gap["requirement"] for gap in assembled["missing_decisions"]}
    assert "model_effort_rationale:build" in requirements
    assert ("explicit_effort:build" in requirements) == (not explicit)
    selected = discovered["inputs"]["phases"][0]["chain"][0]
    assert selected.get("reasoning_effort") == ("high" if explicit else None)
    values = {"playbook_id":"selected", "issue_name":"new-task"}
    report, missing, sources = {}, [], {}
    owner._prefill_saved_inputs(values, store=store, request=compact_request, report=report, missing=missing, sources=sources)
    if explicit:
        entries = load_kickoff_module("format_kickoff_contract")._parse_phase_chains(values["phase_chain"], step_names={"build"})
        assert entries["build"][0].reasoning_effort == "high"
    else:
        assert "phase_chain" not in values
        assert any("reasoning_effort" in gap["requirement"] for gap in missing)
