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
    overview = compact_assembly["catalog"]["candidate_overview"]
    assert {item["id"] for item in overview} == {item["id"] for item in full_candidates}
    for candidate in full_candidates:
        item = next(row for row in overview if row["id"] == candidate["id"])
        assert item["applicability"] == candidate["applicability"]
        assert item["steps"] == list(candidate["steps"])
        assert item["source"] == candidate["source"]
        assert item["fingerprint"] == candidate["fingerprint"]
    # A caller can select another effective candidate from this same report,
    # then assemble its actual graph without a per-candidate show/source read.
    alternative = next(item for item in overview if item["id"] == "direct-qa")
    alternative_request = json.loads(request_file.read_text())
    alternative_request["playbook_id"] = alternative["id"]
    alternative_request.pop("formatter_inputs")
    alternate_file = tmp_path / "alternative.json"
    alternate_file.write_text(json.dumps(alternative_request))
    assert cli.main(["assemble", "--request-file", str(alternate_file), "--summary",
                     "--config-dir", str(config), "--cache-dir", str(cache)]) == 3
    alternate = json.loads(capsys.readouterr().out)
    assert set(alternate["selected_graph"]["steps"]) == set(alternative["steps"])
    assert all(alternate["catalog"]["reuse"].values())
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
    assert cli.main(compact_assembly["render_command"][2:]) == 0
    continued = json.loads(capsys.readouterr().out)
    assert continued["render"]["output"] == compact_render["output"]
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


def test_selected_draft_supplies_owner_typed_contract_and_renders_without_repairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U14-U16/I01/I06: fill decisions in a public draft, not guessed nested types."""
    from cafe.manager.delivery import DeliveryContractV3

    cli = load_kickoff_module("prepare_kickoff")
    values = _formatter_inputs("issue573-typed-draft-journey")
    request = {"schema_version": 1, "request_text": "Prepare the specified outcome.",
               **{k: values[k] for k in ("project_root", "issue_name", "playbook_id")}}
    request["current_explicit_inputs"] = {
        k: v for k, v in values.items()
        if k not in request and k not in {"delivery_contract", "deliver", "cleanup", "update_preflight", "catalog_preflight"}
    }
    request["preflight_files"] = {}
    for name in ("update", "catalog"):
        report = tmp_path / f"{name}.json"
        report.write_text(json.dumps(values[f"{name}_preflight"]))
        request["preflight_files"][name] = str(report)
    path = tmp_path / "request.json"
    draft = tmp_path / "draft.json"
    path.write_text(json.dumps(request))
    common = ["--request-file", str(path), "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(["assemble", *common, "--summary"]) == 3
    summary = json.loads(capsys.readouterr().out)
    template = summary["input_template"]
    schema = summary["input_schema"]["delivery_contract"]
    owner = DeliveryContractV3.model_json_schema()
    assert schema["properties"] == {k: v for k, v in owner["properties"].items() if k != "closeout_plan"}
    assert isinstance(template["delivery_contract"]["implementation_direction"], str)
    assert set(template["delivery_contract"]) == set(values["delivery_contract"])
    assert template["deliver"] is None and template["cleanup"] is None
    assert summary["decision_brief"]["request_text"] == request["request_text"]
    assert summary["decision_brief"]["graph_reference"] == "#/selected_graph"
    assert summary["decision_brief"]["evidence_references"] == {"delivery": "#/delivery", "models": "#/models"}
    assert cli.main(["assemble", *common, "--summary", "--draft-output", str(draft)]) == 3
    capsys.readouterr()
    draft_request = json.loads(draft.read_text())
    assert draft_request["preflight_files"] == request["preflight_files"]
    assert "update_preflight" not in draft_request["formatter_inputs"]
    assert "catalog_preflight" not in draft_request["formatter_inputs"]
    # The undecided template cannot accidentally render or turn absent actions into [].
    draft_common = ["--request-file", str(draft), *common[2:]]
    assert cli.main(["render", *draft_common]) == 3
    capsys.readouterr()
    for key, decision in values["delivery_contract"].items():
        draft_request["formatter_inputs"]["delivery_contract"][key] = decision
    for key in ("deliver", "cleanup"):
        draft_request["formatter_inputs"][key] = values[key]
    draft.write_text(json.dumps(draft_request))
    output = tmp_path / "proposal.md"
    assert cli.main(["render", *draft_common, "--output", str(output)]) == 0
    capsys.readouterr()
    expected = load_kickoff_module("kickoff_inputs").render_kickoff(values)
    assert output.read_text() == expected["output"]
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()


def test_public_closeout_examples_follow_existing_policy_without_authority(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """U15/I06: discover invalid lifecycle forms before constructing a proposal."""
    from cafe.manager.delivery import validate_closeout_plan_policy

    cli = load_kickoff_module("prepare_kickoff")
    assert cli.main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    for example in schema["closeout_examples"]:
        try:
            validate_closeout_plan_policy({"deliver": [], "cleanup": [{"argv": example["argv"]}]}, allow_squash=False)
            valid = True
        except ValueError:
            valid = False
        assert example["valid_in_pr_mode"] == valid
    archive = next(e for e in schema["closeout_examples"] if "--archive-only" in e["argv"])
    assert not archive["valid_in_pr_mode"]
    assert schema["input_template"]["cleanup"] is None


def test_public_draft_loads_current_owner_guidance_once_without_execution_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U14/U16/I01: first assembly supplies inspectable policy with the actual decision inputs."""
    cli = load_kickoff_module("prepare_kickoff")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
                                   "issue_name": "issue573-guidance-journey", "playbook_id": "standard-qa"}))
    args = ["assemble", "--request-file", str(request), "--summary",
            "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(args) == 3
    plain = json.loads(capsys.readouterr().out)
    assert cli.main([*args, "--with-guidance"]) == 3
    guided = json.loads(capsys.readouterr().out)
    assert guided["missing_decisions"] == plain["missing_decisions"]
    refs = PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/references"
    assert guided["guidance"]
    for block in guided["guidance"]:
        source = (refs / block["file"]).read_bytes()
        assert block["sha256"] == hashlib.sha256(source).hexdigest()
        assert block["text"] in source.decode()
        assert block["text"].startswith(block["heading"] + "\n")
    assert not plain.get("guidance")
    assert any(b["file"] == "strategic_context.md" for b in guided["guidance"])
    assert any(b["file"] == "model_selection.md" for b in guided["guidance"])
    assert not any(b["heading"] == "## Durable Manager authority" for b in guided["guidance"])


def test_public_guidance_file_and_preflight_examples_avoid_report_reprinting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U14-U16/I06: caller reads disjoint owner sections and uses complete report shapes."""
    cli = load_kickoff_module("prepare_kickoff")
    values = _formatter_inputs("issue573-guidance-file")
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
                                "issue_name": values["issue_name"], "playbook_id": values["playbook_id"]}))
    args = ["assemble", "--request-file", str(path), "--summary", "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(args) == 3
    report = json.loads(capsys.readouterr().out)
    examples = report["input_schema"]["preflight_report_examples"]
    # Bind observed values to the public shape; the existing formatter validates it.
    reports = {name: {key: values[f"{name}_preflight"][key] for key in example}
               for name, example in examples.items()}
    for name, data in reports.items():
        values[f"{name}_preflight"] = data
    assert load_kickoff_module("kickoff_inputs").render_kickoff(values)["status"] == "rendered"
    guide = tmp_path / "guide.md"
    assert cli.main([*args, "--guidance-output", str(guide)]) == 3
    report = json.loads(capsys.readouterr().out)
    assert "guidance" not in report
    lines = guide.read_text().splitlines(keepends=True)
    prior_end = 0
    for section in report["guidance_index"]:
        assert section["start_line"] > prior_end
        text = "".join(lines[section["start_line"]-1:section["end_line"]])
        assert text.startswith(section["heading"] + "\n")
        owner = PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/references" / section["file"]
        assert text in owner.read_text()
        prior_end = section["end_line"]
    assert len(json.dumps(report).encode()) < len(guide.read_bytes())


def test_normal_caller_keeps_evidence_stores_when_proposal_output_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """I01/I04/I05/I06: emitted argv retains context without overriding isolation."""
    cli = load_kickoff_module("prepare_kickoff")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "evidence"))
    project = tmp_path / "project"
    project.mkdir()
    source = project / "CONTRIBUTING.md"
    source.write_text("Local commits require reviewed changes.\n")
    delivery = tmp_path / "delivery.json"
    delivery.write_text(json.dumps({"target": "local", "stable_conventions": ["Review before commit."],
        "sources": [{"path": source.name, "fingerprint": hashlib.sha256(source.read_bytes()).hexdigest()}]}))
    assert cli.main(["evidence", "refresh", "--category", "delivery", "--project-root", str(project), "--evidence-file", str(delivery)]) == 0
    capsys.readouterr()
    now = datetime.now(timezone.utc).isoformat()
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"provider": "fixture", "model": "exact-v1", "version": "exact-v1",
        "assessed_at": now, "workloads": ["implementation"], "reasoning": "high",
        "capability_bands": {"coding": "fixture"}, "limitations": ["Test evidence only."],
        "sources": [{"url": "https://provider.invalid/exact-v1", "retrieved_at": now, "fingerprint": "fixture-v1"}]}))
    assert cli.main(["evidence", "refresh", "--category", "models", "--evidence-file", str(model)]) == 0
    capsys.readouterr()
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(project), "issue_name": "new-issue"}))
    assert cli.main(["stores", "--request-file", str(request)]) == 0
    location = json.loads(capsys.readouterr().out)
    command = location["next_command"]
    # The output directory may be isolated without moving the evidence source.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "scratch-settings"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "scratch-cache"))
    assert cli.main(command[2:]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["delivery"]["status"] == "hit"
    assert report["models"][0]["assessment"]["workloads"] == ["implementation"]
    assert report["storage"]["repository_identity"] == location["storage"]["repository_identity"]
    assert not (tmp_path / "scratch-cache").exists()
    # A deliberately isolated request must not fall back to the original store.
    assert cli.main(["stores", "--request-file", str(request), "--cache-dir", str(tmp_path / "empty")]) == 0
    isolated = json.loads(capsys.readouterr().out)
    assert cli.main(isolated["next_command"][2:]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["delivery"]["status"] != "hit"
    assert not missing["models"]
    # Reusing pinned locations never bypasses material-source validation.
    source.write_text("Different delivery policy.\n")
    assert cli.main(command[2:]) == 0
    changed = json.loads(capsys.readouterr().out)
    assert changed["delivery"]["status"] != "hit"
    assert changed["models"][0]["status"] == "hit"
    other = tmp_path / "other-project"
    other.mkdir()
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(other), "issue_name": "new-issue"}))
    assert cli.main(command[2:]) == 0
    distinct = json.loads(capsys.readouterr().out)
    assert distinct["delivery"]["status"] != "hit"
    assert distinct["storage"]["repository_identity"] != location["storage"]["repository_identity"]


def test_public_action_description_shapes_render_without_type_or_count_repairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U15/I06: public examples cover described actions and intentionally empty cleanup."""
    cli = load_kickoff_module("prepare_kickoff")
    assert cli.main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    # A caller takes the public shape, supplies its decisions, and renders once.
    examples = schema["action_input_examples"]
    described = examples["described_action"]
    empty = examples["no_actions"]
    values = _formatter_inputs("issue573-action-description-journey")
    values.update(deliver=described["actions"], deliver_description=described["descriptions"],
                  cleanup=empty["actions"], cleanup_description=empty["descriptions"])
    values["deliver"][0] = ["git", "commit", "-m", "Implement chosen outcome"]
    values["deliver_description"][0] = "Commit the reviewed implementation locally."
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
        "issue_name": values["issue_name"], "playbook_id": values["playbook_id"], "formatter_inputs": values}))
    args = ["--request-file", str(request), "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(["render", *args]) == 0
    rendered = json.loads(capsys.readouterr().out)["render"]
    expected = load_kickoff_module("kickoff_inputs").render_kickoff(values)
    assert rendered["output"] == expected["output"]
    # Neither shape support nor early validation silently repairs an invalid decision.
    values["cleanup_description"] = ["Keep resources."]
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
        "issue_name": values["issue_name"], "playbook_id": values["playbook_id"], "formatter_inputs": values}))
    assert cli.main(["assemble", *args, "--summary"]) != 0
    mismatch = json.loads(capsys.readouterr().out)
    assert mismatch["status"] != "ready"
    assert mismatch["assembly_diagnostics"]
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()


def test_public_formatter_field_shapes_cover_real_decisions_without_source_lookup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U15/I06: advertised choices/types can construct a complete real proposal."""
    cli = load_kickoff_module("prepare_kickoff")
    assert cli.main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    fields = schema["formatter_field_schema"]
    assert set(fields) == set(schema["formatter_fields"])
    values = _formatter_inputs("issue573-field-shapes")
    # Exercise both choices and list/scalar boundaries exposed by the public schema.
    values["manager_mode"] = next(v for v in fields["manager_mode"]["enum"] if v == "event-driven")
    values["need_clarification"] = next(v for v in fields["need_clarification"]["enum"] if v == "user_required")
    values["event_manager"] = ["codex"]
    values["current_checkout"] = True
    values.pop("worktree", None)
    for key, value in values.items():
        kind = fields[key]["type"]
        assert isinstance(value, {"string": str, "array": list, "object": dict, "boolean": bool, "integer": int}[kind])
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
        "issue_name": values["issue_name"], "playbook_id": values["playbook_id"], "formatter_inputs": values}))
    assert cli.main(["render", "--request-file", str(request), "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]) == 0
    result = json.loads(capsys.readouterr().out)["render"]
    assert result["output"] == load_kickoff_module("kickoff_inputs").render_kickoff(values)["output"]
    formatter = load_kickoff_module("format_kickoff_contract")
    owner = {action.dest: action for action in formatter._parser()._actions}
    for field, shape in fields.items():
        if "enum" in shape:
            assert shape["enum"] == list(owner[field].choices)
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()


def test_raw_preflight_files_preserve_source_evidence_and_require_current_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U14-U16/I06: normal check files feed the owner without manual report copies."""
    cli = load_kickoff_module("prepare_kickoff")
    values = _formatter_inputs("issue573-raw-preflight-files")
    request = {"schema_version": 1, "project_root": str(PROJECT_ROOT), "issue_name": values["issue_name"],
               "playbook_id": values["playbook_id"], "formatter_inputs": values.copy(),
               "preflight_files": {}, "preflight_metadata": {}}
    for kind in ("update", "catalog"):
        full = values[kind + "_preflight"].copy()
        metadata = {k: full.pop(k) for k in ("checked_at", "decision", "post_change_evidence")}
        if kind == "update":
            full["token"] = full.pop("comparison_token")
            raw = {**full, "additional_source_diagnostic": "retained"}
        else:
            raw = {"catalog_check": full, "content_mismatch_entry_ids": [], "additional_source_diagnostic": "retained"}
        path = tmp_path / (kind + ".json")
        path.write_text(json.dumps(raw))
        request["preflight_files"][kind] = str(path)
        request["preflight_metadata"][kind] = metadata
        request["formatter_inputs"].pop(kind + "_preflight")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    args = ["--request-file", str(path), "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(["render", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["render"]["output"] == load_kickoff_module("kickoff_inputs").render_kickoff(values)["output"]
    for kind in ("update", "catalog"):
        report = result["assembly"]["formatter_inputs"][kind + "_preflight"]
        assert report["additional_source_diagnostic"] == "retained"
        assert report["comparison_token"] == values[kind + "_preflight"]["comparison_token"]
        assert report["checked_at"] == request["preflight_metadata"][kind]["checked_at"]
    # Metadata cannot manufacture a source token/status; unavailable evidence stays unavailable.
    request["preflight_metadata"]["update"]["comparison_token"] = "invented"
    path.write_text(json.dumps(request))
    assert cli.main(["render", *args]) != 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["render"]["status"] != "rendered"
    request["preflight_metadata"]["update"].pop("comparison_token")
    request["preflight_metadata"]["update"].pop("checked_at")
    path.write_text(json.dumps(request))
    assert cli.main(["render", *args]) != 0
    capsys.readouterr()
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()


def test_capture_report_retains_first_check_for_complete_public_render(tmp_path: Path) -> None:
    """U14-U16/I06: capture original check output once, decide later, render once."""
    import subprocess
    import sys

    inputs = load_kickoff_module("kickoff_inputs")
    values = _formatter_inputs("issue573-capture-report")
    # The formatter accepts explicit absent post-change evidence when no change occurred.
    # Capture must not force invented evidence text or an unnecessary render repair.
    for kind in ("update", "catalog"):
        values[kind + "_preflight"]["post_change_evidence"] = None
    request = {"schema_version": 1, "project_root": str(PROJECT_ROOT),
               "issue_name": values["issue_name"], "playbook_id": values["playbook_id"],
               "formatter_inputs": values.copy()}
    path = tmp_path / "draft.json"
    for kind in ("update", "catalog"):
        request["formatter_inputs"].pop(kind + "_preflight")
    path.write_text(json.dumps(request))
    script = PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/scripts/prepare_kickoff.py"
    for kind in ("update", "catalog"):
        raw = values[kind + "_preflight"].copy()
        actual_time = raw.pop("checked_at")
        raw.pop("decision")
        raw.pop("post_change_evidence")
        if kind == "update":
            raw["token"] = raw.pop("comparison_token")
        else:
            raw = {"catalog_check": raw}
        original = json.dumps(raw, indent=2) + "\n"
        output = tmp_path / (kind + ".json")
        argv = [sys.executable, str(script), "capture-report", "--request-file", str(path),
                "--kind", kind, "--report-output", str(output), "--checked-at", actual_time]
        captured = subprocess.run(argv, input=original, text=True, capture_output=True)
        assert captured.returncode == 0, captured.stderr
        assert output.read_text() == original
        updated = json.loads(path.read_text())
        assert updated["preflight_files"][kind] == str(output)
        assert updated["preflight_metadata"][kind] == {
            "checked_at": actual_time, "decision": None, "post_change_evidence": None}
        assert kind + "_preflight" not in updated["formatter_inputs"]
        before = path.read_bytes()
        rejected = subprocess.run(argv, input="not JSON", text=True, capture_output=True)
        assert rejected.returncode != 0
        assert output.read_text() == original and path.read_bytes() == before
    # Capture conveys no decision or authority; render cannot succeed until actual decisions exist.
    argv = [sys.executable, str(script), "render", "--request-file", str(path),
            "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    unresolved = subprocess.run(argv, text=True, capture_output=True)
    assert unresolved.returncode != 0
    request = json.loads(path.read_text())
    for kind in ("update", "catalog"):
        for field in ("decision", "post_change_evidence"):
            request["preflight_metadata"][kind][field] = values[kind + "_preflight"][field]
    path.write_text(json.dumps(request))
    rendered = subprocess.run(argv, text=True, capture_output=True)
    assert rendered.returncode == 0, rendered.stderr
    assert json.loads(rendered.stdout)["render"]["output"] == inputs.render_kickoff(values)["output"]
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()


def test_delivery_evidence_reuses_explicit_repository_sources_beyond_discovery_patterns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U10-U11/I01/I05: referenced lifecycle/hook facts remain verifiable dependencies."""
    cli = load_kickoff_module("prepare_kickoff")
    project = tmp_path / "project"
    source = project / "src/closeout.py"
    source.parent.mkdir(parents=True)
    source.write_text("# closeout requires explicit target and authorization\n")
    import subprocess
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    unrelated = project / "src/product.py"
    unrelated.write_text("version = 1\n")
    evidence = {"target": "repository-delivery", "stable_conventions": ["Closeout requires explicit target and authorization."],
                "sources": [{"path": "src/closeout.py", "fingerprint": hashlib.sha256(source.read_bytes()).hexdigest()}],
                "observations": []}
    data = tmp_path / "evidence.json"
    data.write_text(json.dumps(evidence))
    cache = tmp_path / "cache"
    refresh = ["evidence", "refresh", "--category", "delivery", "--project-root", str(project),
               "--cache-dir", str(cache), "--evidence-file", str(data)]
    assert cli.main(refresh) == 0
    capsys.readouterr()
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(project), "issue_name": "new-issue"}))
    discover = ["discover", "--summary", "--request-file", str(request),
                "--config-dir", str(tmp_path / "config"), "--cache-dir", str(cache)]
    def delivery():
        cli.main(discover)
        return json.loads(capsys.readouterr().out)["delivery"]
    assert delivery()["status"] == "hit"
    unrelated.write_text("version = 2\n")
    assert delivery()["stable_conventions"] == evidence["stable_conventions"]
    source.write_text("# changed closeout semantics\n")
    assert delivery()["status"] == "miss"
    assert cli.main(refresh) != 0  # stale fingerprint cannot refresh the record
    capsys.readouterr()
    # An existing inventory entry pointing outside the repo is not a valid source.
    outside = tmp_path / "outside.py"
    outside.write_text("outside evidence\n")
    source.unlink()
    source.symlink_to(outside)
    evidence["sources"][0]["fingerprint"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    data.write_text(json.dumps(evidence))
    assert cli.main(refresh) != 0
    capsys.readouterr()
    assert delivery()["status"] == "miss"


def test_parallel_report_capture_preserves_both_original_report_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U14-U16/I06: parallel producer outputs cannot lose a sibling report."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import threading

    cli = load_kickoff_module("prepare_kickoff")
    stores = load_kickoff_module("_kickoff_store")
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({"schema_version": 1, "formatter_inputs": {}}))
    first_read = threading.Event()
    release_first = threading.Event()
    local = threading.local()
    read = cli._read_json
    def staged_read(path):
        data = read(path)
        if local.kind == "update":
            first_read.set()
            assert release_first.wait(5), "second capture did not reach the coordinated boundary"
        return data
    @contextmanager
    def coordinated_lock(path):
        if local.kind == "catalog":
            release_first.set()  # First writer may finish while the second waits for its lock.
        with stores._lock(path):
            yield
    class Input:
        def read(self):
            return json.dumps({"producer": local.kind}) + "\n"
    monkeypatch.setattr(cli, "_read_json", staged_read)
    monkeypatch.setattr(cli, "_lock", coordinated_lock, raising=False)
    monkeypatch.setattr(cli.sys, "stdin", Input())
    monkeypatch.setattr(cli, "_json", lambda value: None)
    def capture(kind):
        local.kind = kind
        try:
            return cli.main(["capture-report", "--request-file", str(draft), "--kind", kind,
                "--report-output", str(tmp_path / (kind + ".json")), "--checked-at", "2026-09-30T00:00:00Z"])
        finally:
            if kind == "catalog":
                release_first.set()  # Reproduce the unlocked overwrite after the second writer finishes.
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(capture, "update")
        assert first_read.wait(5)
        second = pool.submit(capture, "catalog")
        assert first.result(timeout=10) == second.result(timeout=10) == 0
    request = json.loads(draft.read_text())
    assert set(request["preflight_files"]) == {"update", "catalog"}
    assert set(request["preflight_metadata"]) == {"update", "catalog"}
    for kind, path in request["preflight_files"].items():
        assert json.loads(Path(path).read_text()) == {"producer": kind}
        assert request["preflight_metadata"][kind]["decision"] is None
