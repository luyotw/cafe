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


@pytest.fixture
def compact_request(tmp_path, monkeypatch):
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
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "project_root": str(root),
        "issue_name": "sample",
        "model_assessments": [
            {
                "provider": "codex",
                "model": "test",
                "version": "test-v1",
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
            "phases": [{"name": "build", "chain": [{"cli": "codex", "model": "test"}]}],
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
