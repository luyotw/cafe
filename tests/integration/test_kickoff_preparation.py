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


def test_compact_cli_reports_preserve_selected_facts_and_full_render(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = load_kickoff_module("prepare_kickoff")
    issue_name = "issue573-compact-report-test"
    config = tmp_path / "config"
    cache = tmp_path / "cache"
    evidence_args = ["--project-root", str(PROJECT_ROOT), "--cache-dir", str(cache)]

    delivery_source = PROJECT_ROOT / "docs/settings-updates.md"
    delivery_file = tmp_path / "delivery.json"
    delivery_file.write_text(
        json.dumps(
            {
                "target": "local",
                "stable_conventions": ["Use the current repository delivery policy."],
                "sources": [
                    {
                        "path": "docs/settings-updates.md",
                        "fingerprint": hashlib.sha256(delivery_source.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "evidence",
                "refresh",
                *evidence_args,
                "--category",
                "delivery",
                "--evidence-file",
                str(delivery_file),
            ]
        )
        == 0
    )

    now = datetime.now(timezone.utc)
    model_record = {
        "provider": "provider.test",
        "model": "fixture-model",
        "version": "2026-09-30",
        "assessed_at": now.isoformat(),
        "workloads": ["implementation"],
        "reasoning": "high",
        "capability_bands": {"coding": "fixture evidence"},
        "limitations": ["Deterministic integration fixture only."],
        "sources": [
            {
                "url": "https://provider.invalid/fixture-model",
                "retrieved_at": now.isoformat(),
                "fingerprint": "fixture-source-v1",
            }
        ],
    }
    model_file = tmp_path / "model.json"
    model_file.write_text(json.dumps(model_record), encoding="utf-8")
    assert (
        cli.main(
            [
                "evidence",
                "refresh",
                *evidence_args,
                "--category",
                "models",
                "--evidence-file",
                str(model_file),
            ]
        )
        == 0
    )
    capsys.readouterr()

    formatter_inputs = _formatter_inputs(issue_name)
    formatter_inputs.update(
        {"issue_name": issue_name, "playbook_id": "standard-qa", "project_root": str(PROJECT_ROOT)}
    )
    request = {
        "schema_version": 1,
        "project_root": str(PROJECT_ROOT),
        "issue_name": issue_name,
        "playbook_id": "standard-qa",
        "manager_decisions": {"assessment": "bounded preference feature"},
        "formatter_inputs": formatter_inputs,
    }
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    common = [
        "--request-file",
        str(request_file),
        "--config-dir",
        str(config),
        "--cache-dir",
        str(cache),
    ]

    assert cli.main(["discover", *common]) == 0
    full_discovery_text = capsys.readouterr().out
    full_discovery = json.loads(full_discovery_text)
    assert cli.main(["discover", *common, "--summary"]) == 0
    compact_discovery_text = capsys.readouterr().out
    compact_discovery = json.loads(compact_discovery_text)

    full_candidates = full_discovery["catalog"]["candidates"]
    compact_candidates = compact_discovery["catalog"]["candidates"]
    assert compact_discovery["catalog"]["candidate_count"] == len(full_candidates)
    assert {candidate["id"] for candidate in compact_candidates} == {
        candidate["id"] for candidate in full_candidates
    }
    assert compact_discovery["catalog"]["invalid_candidate_count"] == sum(
        not candidate["eligible"] for candidate in full_candidates
    )
    assert compact_discovery["catalog"]["ineligible_candidate_diagnostics"] == [
        {"id": candidate["id"], "diagnostics": candidate.get("diagnostics", [])}
        for candidate in full_candidates
        if not candidate["eligible"]
    ]
    assert compact_discovery["catalog"]["diagnostics"] == full_discovery["catalog"]["diagnostics"]
    compact_selected = next(candidate for candidate in compact_candidates if candidate["id"] == "standard-qa")
    full_selected = next(candidate for candidate in full_candidates if candidate["id"] == "standard-qa")
    assert compact_selected["profiles"] == full_selected["profiles"]
    assert compact_selected["confirmation_gates"] == full_selected["confirmation_gates"]
    assert compact_selected["mandatory_confirmation_gates"] == full_selected["mandatory_confirmation_gates"]
    assert compact_selected["capability_requirements"] == full_selected["capability_requirements"]
    assert set(compact_selected["steps"]) == set(full_selected["steps"])
    for step_name, step in compact_selected["steps"].items():
        assert step["role"] == full_selected["steps"][step_name]["role"]
        assert step["on"] == full_selected["steps"][step_name]["on"]
    assert compact_discovery["delivery"]["status"] == "hit"
    assert compact_discovery["delivery"]["sources"] == json.loads(delivery_file.read_text())["sources"]
    assert compact_discovery["models"][0]["assessment"]["capability_bands"] == model_record["capability_bands"]
    assert compact_discovery["models"][0]["assessment"]["limitations"] == model_record["limitations"]
    assert compact_discovery["delivery"]["manifest"]["sources"]
    assert compact_discovery["models"][0]["status"] == "hit"
    assert compact_discovery["models"][0]["provenance"]["sources"][0]["fingerprint"] == "fixture-source-v1"
    assert compact_discovery["models"][0]["provenance"]["age_seconds"] >= 0
    assert compact_discovery["catalog"]["inspect_reference"]
    assert len(compact_discovery_text.encode()) < len(full_discovery_text.encode())

    assert cli.main(["assemble", *common]) == 0
    full_assembly = json.loads(capsys.readouterr().out)
    assert cli.main(["assemble", *common, "--summary"]) == 0
    compact_assembly_text = capsys.readouterr().out
    compact_assembly = json.loads(compact_assembly_text)
    assert compact_assembly["status"] == full_assembly["status"] == "ready"
    assert compact_assembly["selected_playbook"] == full_assembly["selected_playbook"] == "standard-qa"
    assert compact_assembly["formatter_inputs"] == full_assembly["formatter_inputs"]
    assert compact_assembly["selected_graph"]["profiles"] == full_assembly["selected_candidate"]["profiles"]
    assert compact_assembly["missing_decisions"] == full_assembly["missing_decisions"] == []
    assert compact_assembly["catalog"]["candidate_count"] == len(full_candidates)
    assert compact_assembly["catalog"]["selected_candidate_count"] == 1
    assert compact_assembly["catalog"]["ineligible_candidate_diagnostics"] == (
        compact_discovery["catalog"]["ineligible_candidate_diagnostics"]
    )
    assert compact_assembly["catalog"]["inspect_reference"]
    assert len(compact_assembly_text.encode()) < len(json.dumps(full_assembly, separators=(",", ":")).encode())

    inputs = load_kickoff_module("kickoff_inputs")
    compact_render = inputs.render_kickoff(compact_assembly["formatter_inputs"])
    assert compact_render["status"] == "rendered"
    assert compact_render["proposal"]
    issue_dir = PROJECT_ROOT / ".cafe/issues" / issue_name
    assert not issue_dir.exists()


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


def test_confirmed_inputs_fill_selected_journey_without_copying_full_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U14-U16/I01/I06: public caller fills only real gaps and renders an equivalent contract."""
    cli = load_kickoff_module("prepare_kickoff")
    values = _formatter_inputs("issue573-isolated-journey")
    assert cli.main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert set(values) <= set(schema["formatter_fields"])
    request = {"schema_version": 1, **{key: values[key] for key in ("project_root", "issue_name", "playbook_id")}}
    explicit = {key: value for key, value in values.items() if key not in request and key not in {"delivery_contract", "deliver", "cleanup"}}
    request["current_explicit_inputs"] = explicit
    path = tmp_path / "request.json"
    common = ["--request-file", str(path), "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    path.write_text(json.dumps(request))
    assert cli.main(["assemble", *common, "--summary"]) == 3
    partial_text = capsys.readouterr().out
    partial = json.loads(partial_text)
    assert partial["formatter_draft"]["issue_name"] == values["issue_name"]
    assert partial["formatter_draft"]["phase_chain"] == values["phase_chain"]
    assert {item["requirement"] for item in partial["missing_decisions"]} == {
        "formatter input: delivery_contract", "formatter input: deliver", "formatter input: cleanup"
    }
    assert partial["selected_graph"]["id"] == values["playbook_id"]
    assert partial["catalog"]["candidates"] == []
    assert partial["catalog"]["candidate_count"] > 1
    request["formatter_inputs"] = {key: values[key] for key in ("delivery_contract", "deliver", "cleanup")}
    path.write_text(json.dumps(request))
    assert cli.main(["assemble", *common, "--summary"]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["formatter_inputs"] == values
    expected = load_kickoff_module("kickoff_inputs").render_kickoff(values)
    output = tmp_path / "proposal.md"
    assert cli.main(["render", *common, "--output", str(output)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert output.read_text() == expected["output"]
    assert receipt["status"] == "rendered"
    assert "assembly" not in receipt and "proposal" not in receipt
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()
    # Explicit activation metadata cannot enter through the prefill channel.
    request["current_explicit_inputs"]["activate_confirmed"] = True
    path.write_text(json.dumps(request))
    assert cli.main(["assemble", *common, "--summary"]) != 0
    assert json.loads(capsys.readouterr().out)["assembly_diagnostics"]


def test_invalid_preflight_reports_actionable_gap_without_repeating_assembly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U14-U16/I06: a caller can repair invalid input without inspecting code or losing output."""
    cli = load_kickoff_module("prepare_kickoff")
    values = _formatter_inputs("issue573-invalid-preflight")
    values["update_preflight"].pop("comparison_token")
    request = {"schema_version": 1, "project_root": str(PROJECT_ROOT),
               "issue_name": values["issue_name"], "playbook_id": values["playbook_id"],
               "formatter_inputs": values}
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request))
    output = tmp_path / "proposal.md"
    output.write_text("Preserve the last proposal until a valid replacement is ready.")
    assert cli.main(["render", "--request-file", str(request_file), "--output", str(output),
                     "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]) == 3
    report = json.loads(capsys.readouterr().out)
    assert "comparison_token" in report["validation_error"]
    assert report["status"] == "invalid"
    assert "assembly" not in report and "selected_candidate" not in report
    assert output.read_text() == "Preserve the last proposal until a valid replacement is ready."


def test_public_schema_example_preserves_exact_model_identity_through_render(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U12/U15/I06: following the public example must not alter the selected model ID."""
    cli = load_kickoff_module("prepare_kickoff")
    assert cli.main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    values = _formatter_inputs("issue573-schema-model-identity")
    selected = "fixture-exact-model"
    example = schema["decision_examples"]["phase_chain"][0].replace("<exact-model>", selected)
    values["phase_chain"] = [entry for entry in values["phase_chain"] if not entry.startswith("develop=")] + [example]
    request = {"schema_version": 1, "project_root": str(PROJECT_ROOT), "issue_name": values["issue_name"],
               "playbook_id": values["playbook_id"], "formatter_inputs": values}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    assert cli.main(["render", "--request-file", str(path), "--config-dir", str(tmp_path / "config"),
                     "--cache-dir", str(tmp_path / "cache")]) == 0
    phases = json.loads(capsys.readouterr().out)["render"]["proposal"]["phases"]
    assert next(phase["chain"] for phase in phases if phase["name"] == "develop") == [{"cli": "codex", "model": selected}]
