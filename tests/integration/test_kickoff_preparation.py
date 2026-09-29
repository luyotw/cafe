"""I01/I06: returning user preparation renders without activating a workflow."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNIT_ROOT = PROJECT_ROOT / "tests/unit"
sys.path.insert(0, str(UNIT_ROOT))
from _kickoff_test_support import load_kickoff_module


def _formatter_inputs(issue_name: str) -> dict:
    helper_path = UNIT_ROOT / "test_use_cafe_workflow_skill.py"
    spec = importlib.util.spec_from_file_location("kickoff_preparation_formatter_fixture", helper_path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    command = helper._kickoff_formatter_command(PROJECT_ROOT / ".cafe/strategic_context.yaml", "--issue-name", issue_name)
    formatter = load_kickoff_module("format_kickoff_contract")
    args = formatter._parser().parse_args(command[2:])
    return {
        "playbook_id": args.playbook_id,
        "project_root": str(PROJECT_ROOT),
        "issue_name": issue_name,
        "delivery_contract": args.delivery_contract,
        "deliver": args.deliver,
        "cleanup": args.cleanup,
        "deliver_description": args.deliver_description,
        "cleanup_description": args.cleanup_description,
        "update_preflight": args.update_preflight,
        "catalog_preflight": args.catalog_preflight,
        "manager_mode": args.manager_mode,
        "phase_chain": args.phase_chain,
        "effective_locale": args.effective_locale,
        "locale_source": args.locale_source,
        "repository_content_locale": args.repository_content_locale,
        "capability_choice": args.capability_choice,
        "user_required": args.user_required,
        "manager_confirmable": args.manager_confirmable,
        "worktree": args.worktree,
        "proactive_review_decision": args.proactive_review_decision,
    }


def test_returning_user_renders_same_complete_proposal_with_warm_preferences(tmp_path: Path) -> None:
    inputs = load_kickoff_module("kickoff_inputs")
    preferences = load_kickoff_module("kickoff_preferences").PreferenceStore(
        tmp_path / "config", repository_root=PROJECT_ROOT
    )
    preferences.set("manager.mode", "unattended", scope="user", origin="explicit")
    request = {
        "schema_version": 1,
        "project_root": str(PROJECT_ROOT),
        "issue_name": "issue573-kickoff-returning-test",
        "playbook_id": "standard",
        "manager_decisions": {"assessment": "bounded implementation"},
        "formatter_inputs": {
            key: value
            for key, value in _formatter_inputs("issue573-kickoff-returning-test").items()
            if key != "manager_mode"
        },
    }

    cold = inputs.assemble_kickoff(request, preference_store=preferences)
    warm = inputs.assemble_kickoff(request, preference_store=preferences)
    first = inputs.render_kickoff(cold["formatter_inputs"])
    second = inputs.render_kickoff(warm["formatter_inputs"])

    assert cold["selected_playbook"] == "standard"
    assert warm["preferences"]["manager.mode"] == {
        "value": "unattended", "scope": "user", "origin": "explicit"
    }
    assert first["status"] == second["status"] == "rendered", (first, second)
    assert first["proposal"] == second["proposal"]
    assert first["output"]


def test_complete_format_render_does_not_create_contract_or_run_delivery(tmp_path: Path) -> None:
    inputs = load_kickoff_module("kickoff_inputs")
    issue_name = "issue573-kickoff-render-only-test"
    issue_dir = PROJECT_ROOT / ".cafe/issues" / issue_name
    request_values = _formatter_inputs(issue_name)
    normalized = inputs.normalize_formatter_inputs(request_values)

    rendered = inputs.render_kickoff(normalized)

    assert rendered["status"] == "rendered"
    assert rendered["proposal"]["phases"]
    assert rendered["proposal"]["delivery_contract"]
    assert isinstance(rendered["output"], str) and rendered["output"].strip()
    assert not issue_dir.exists()
    assert "activate" not in rendered["proposal"]


def test_resume_keeps_confirmed_locale_and_workflow_state_unchanged(tmp_path: Path) -> None:
    inputs = load_kickoff_module("kickoff_inputs")
    project = tmp_path / "project"
    issue_name = "issue573-resume-test"
    issue_dir = project / ".cafe/issues" / issue_name
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        '{"conversation_locale":"ja-JP","conversation_locale_source":"explicit",'
        '"workflow_id":"existing-workflow","pending_human_tasks":["confirm"]}',
        encoding="utf-8",
    )
    phase_dir = project / ".cafe"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / "phases.yaml").write_text(
        (PROJECT_ROOT / ".cafe/phases.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    blackboard = issue_dir / "blackboard.json"
    before = blackboard.read_bytes()
    formatter_inputs = _formatter_inputs(issue_name)
    formatter_inputs.update(
        {
            "project_root": str(project),
            "phase_config": str(phase_dir / "phases.yaml"),
            "effective_locale": "zh-TW",
            "locale_source": "new user preference",
        }
    )

    rendered = inputs.render_kickoff(formatter_inputs)

    assert rendered["status"] == "rendered"
    assert rendered["proposal"]["locales"]["conversation"] == {
        "value": "ja-JP", "source": "explicit"
    }
    assert blackboard.read_bytes() == before


def test_preference_and_evidence_maintenance_commands_are_scoped_and_recoverable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = load_kickoff_module("prepare_kickoff")
    config = tmp_path / "config"
    cache = tmp_path / "cache"
    project = tmp_path / "project"
    project.mkdir()
    common = ["--project-root", str(project), "--config-dir", str(config)]

    assert cli.main(["preferences", "set", *common, "--scope", "user", "--key", "manager.mode", "--value-json", '"unattended"', "--reuse"]) == 0
    user = cli.main(["preferences", "inspect", *common, "--scope", "user"])
    user_output = capsys.readouterr().out
    assert user == 0 and '"unattended"' in user_output
    assert cli.main(["preferences", "set", *common, "--scope", "repository", "--key", "manager.mode", "--value-json", '"attached"', "--reuse"]) == 0
    assert cli.main(["preferences", "clear", *common, "--scope", "repository", "--key", "manager.mode"]) == 0
    assert cli.main(["preferences", "inspect", *common, "--scope", "user"]) == 0
    assert '"unattended"' in capsys.readouterr().out

    store_module = load_kickoff_module("_kickoff_store")
    store_path = config / "preferences-v1.json"
    prior = store_path.read_bytes()
    monkeypatch.setattr(store_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))
    assert cli.main(["preferences", "set", *common, "--scope", "user", "--key", "manager.mode", "--value-json", '"attached"', "--reuse"]) == 2
    monkeypatch.undo()
    assert store_path.read_bytes() == prior

    delivery_file = tmp_path / "delivery.json"
    delivery_source = project / "docs/release.md"
    delivery_source.parent.mkdir(parents=True)
    delivery_source.write_text("Release notes source.\n", encoding="utf-8")
    source_fingerprint = hashlib.sha256(delivery_source.read_bytes()).hexdigest()
    delivery_file.write_text(
        json.dumps({
            "stable_conventions": ["Keep release notes."], "target": "local",
            "sources": [{"path": "docs/release.md", "fingerprint": source_fingerprint}],
        }),
        encoding="utf-8",
    )
    evidence_args = ["--project-root", str(project), "--cache-dir", str(cache)]
    assert cli.main(["evidence", "refresh", *evidence_args, "--category", "delivery", "--evidence-file", str(delivery_file)]) == 0
    assert cli.main(["evidence", "inspect", *evidence_args, "--category", "delivery"]) == 0
    assert "Keep release notes." in capsys.readouterr().out
    assert cli.main(["evidence", "clear", *evidence_args, "--category", "delivery"]) == 0
    assert cli.main(["evidence", "inspect", *evidence_args, "--category", "delivery"]) == 0
    assert '"evidence":{}' in capsys.readouterr().out


    request_file = tmp_path / "request.json"
    request_file.write_text(
        json.dumps({"schema_version": 1, "project_root": str(project), "issue_name": "issue573-maintenance"}),
        encoding="utf-8",
    )
    assert cli.main(["evidence", "refresh", *evidence_args, "--category", "catalog", "--request-file", str(request_file)]) == 0
    assert cli.main(["evidence", "inspect", *evidence_args, "--category", "catalog"]) == 0
    assert '"standard"' in capsys.readouterr().out
    assert cli.main(["evidence", "clear", *evidence_args, "--category", "catalog"]) == 0
    assert cli.main(["evidence", "inspect", *evidence_args, "--category", "catalog"]) == 0
    assert '"evidence":{}' in capsys.readouterr().out

    now = datetime.now(timezone.utc)
    assessment_file = tmp_path / "model.json"
    assessment_file.write_text(
        json.dumps(
            {
                "provider": "provider-a", "model": "model-x", "version": "2026-09-29",
                "assessed_at": now.isoformat(), "workloads": ["implementation"], "reasoning": "high",
                "capability_bands": {"coding": "strong"}, "limitations": ["review scope was limited"],
                "sources": [{
                    "url": "https://provider.invalid/model-x", "retrieved_at": (now - timedelta(hours=1)).isoformat(),
                    "fingerprint": "source-v1", "valid_until": (now + timedelta(days=2)).isoformat(),
                }],
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["evidence", "refresh", *evidence_args, "--category", "models", "--evidence-file", str(assessment_file)]) == 0
    assert cli.main(["evidence", "inspect", *evidence_args, "--category", "models"]) == 0
    assert '"model-x"' in capsys.readouterr().out
    assert cli.main(["evidence", "clear", *evidence_args, "--category", "models"]) == 0
    assert cli.main(["evidence", "inspect", *evidence_args, "--category", "models"]) == 0
    assert '"evidence":{}' in capsys.readouterr().out

def test_expired_model_research_stays_unresolved_despite_successful_probe(tmp_path: Path) -> None:
    inputs = load_kickoff_module("kickoff_inputs")
    now = datetime.now(timezone.utc)
    request = {
        "schema_version": 1,
        "project_root": str(PROJECT_ROOT),
        "issue_name": "issue573-expired-evidence-test",
        "model_assessments": [
            {
                "provider": "provider-a", "model": "model-x", "version": "2026-01-01",
                "available": True,
                "assessed_at": (now - timedelta(days=9)).isoformat(),
                "workloads": ["implementation"], "reasoning": "high",
                "capability_bands": {"coding": "strong"}, "limitations": ["small sample"],
                "sources": [{
                    "url": "https://provider.invalid/model-x",
                    "retrieved_at": (now - timedelta(days=10)).isoformat(),
                    "fingerprint": "source-v1",
                }],
            }
        ],
    }

    discovery = inputs.discover_kickoff(
        request, config_dir=tmp_path / "config", cache_dir=tmp_path / "cache"
    )

    assert discovery["catalog"]["candidates"]
    assert discovery["models"][0]["status"] == "miss"
    assert "assessment_expired" in discovery["models"][0]["diagnostics"]
