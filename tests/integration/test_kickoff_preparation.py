"""I01/I06: returning user preparation renders without activating a workflow."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNIT_ROOT = PROJECT_ROOT / "tests/unit"
sys.path.insert(0, str(UNIT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/scripts"))
import kickoff_inputs
from _kickoff_test_support import load_kickoff_module

pytestmark = [pytest.mark.release_extended, pytest.mark.usefixtures("isolated_global_catalog")]


@pytest.fixture(scope="module")
def repository_catalog(tmp_path_factory):
    """Resolve the unchanged repository catalog once for input-focused CLI journeys."""
    from cafe.catalogs.resolver import CatalogResolver

    resolver = CatalogResolver(
        project_root=PROJECT_ROOT,
        global_root=tmp_path_factory.mktemp("kickoff-global") / ".cafe",
    )
    return load_kickoff_module("kickoff_catalog").discover_index(
        project_root=PROJECT_ROOT,
        global_root=resolver.global_root,
        builtin_root=resolver.builtin_root,
        cache_file=tmp_path_factory.mktemp("kickoff-catalog") / "catalog.json",
    )


@pytest.fixture(autouse=True)
def reuse_repository_catalog(monkeypatch, request, repository_catalog):
    # The compact-report case checks cold/warm catalog reuse through the public CLI.
    if request.node.originalname == "test_compact_cli_reports_preserve_selected_facts_and_full_render":
        return
    load = kickoff_inputs._load_local_module
    catalogs = {}

    def load_with_catalog(name):
        module = load(name)
        if name != "kickoff_catalog":
            return module

        def discover_index(**kwargs):
            root = Path(kwargs["project_root"]).resolve()
            overlays = (root / ".cafe" / name for name in ("playbooks", "skills", "capabilities"))
            if root == PROJECT_ROOT or not any(path.exists() for path in overlays):
                return copy.deepcopy(repository_catalog)
            key = tuple(Path(kwargs[name]).resolve() for name in ("project_root", "global_root", "builtin_root"))
            if key not in catalogs:
                catalogs[key] = module.discover_index(**kwargs)
            return copy.deepcopy(catalogs[key])

        return SimpleNamespace(discover_index=discover_index)

    monkeypatch.setattr(kickoff_inputs, "_load_local_module", load_with_catalog)


@pytest.fixture
def phase_config(tmp_path: Path) -> Path:
    """Own test configuration instead of relying on an untracked checkout file."""
    path = tmp_path / "phases.yaml"
    path.write_text(json.dumps({
        step: {"name": step, "role": role, "clis": [{"cli": "codex", "model": "fixture-model"}]}
        for step, role in (("spec", "pm"), ("plan", "developer"), ("develop", "developer"),
                           ("review", "reviewer"), ("qa", "qa"), ("pr", "developer"), ("deliver", "developer"))
    }))
    return path


def _render_with_store(values: dict, config_dir: Path) -> dict:
    """Compare complete renderings using the CLI journey's explicit store."""
    store = load_kickoff_module("kickoff_preferences").PreferenceStore(
        config_dir, repository_root=Path(values["project_root"])
    )
    return load_kickoff_module("kickoff_inputs").render_kickoff(
        values, preference_store=store
    )


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
        "cleanup": args.cleanup,
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


def _phase_config(path: Path) -> Path:
    """Provide the repository phase defaults explicitly for isolated fixtures."""
    defaults = _formatter_inputs("phase-config-fixture")["phase_chain"]
    config = {}
    for item in defaults:
        step, chain = item.split("=", 1)
        config[step] = {
            "name": step,
            "clis": [
                {"cli": cli, "model": model}
                for cli, model in (candidate.split(":", 1) for candidate in chain.split(","))
            ],
        }
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


@pytest.mark.release_smoke
def test_compact_cli_reports_preserve_selected_facts_and_full_render(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], phase_config: Path
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
        {"issue_name": issue_name, "playbook_id": "standard-qa", "project_root": str(PROJECT_ROOT),
         "phase_config": str(phase_config)}
    )
    formatter_inputs["phase_chain"].append("qa=gemini:qa-main,copilot:qa-fallback")
    formatter_inputs["proactive_review_decision"].insert(-2, "qa=not_required")
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
    compact_render = _render_with_store(compact_assembly["formatter_inputs"], config)
    assert compact_render["status"] == "rendered", compact_render
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


def test_public_draft_prefills_owner_defaults_and_renders_the_same_contract(tmp_path, capsys, phase_config):
    cli = load_kickoff_module("prepare_kickoff")
    inputs = load_kickoff_module("kickoff_inputs")
    values = _formatter_inputs("issue573-prefill-journey")
    values["phase_config"] = str(phase_config)
    for key in ("phase_chain", "effective_locale", "locale_source", "user_required", "manager_confirmable",
                "proactive_review_decision"):
        values.pop(key, None)
    expected = _render_with_store(values, tmp_path / "config")
    assert expected["status"] == "rendered", expected
    request, draft, output = [tmp_path / name for name in ("request.json", "draft.json", "proposal.md")]
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
        "issue_name": values["issue_name"], "playbook_id": values["playbook_id"], "formatter_inputs": values}))
    stores = ["--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(["assemble", "--request-file", str(request), "--summary", "--draft-output", str(draft), *stores]) == 0
    report = json.loads(capsys.readouterr().out)
    fields = json.loads(draft.read_text())["formatter_inputs"]
    assert fields["phase_chain"] and fields["proactive_review_decision"]
    assert fields["manager_confirmable"] and fields["user_required"] == []
    assert {"phase_chain", "proactive_review_decision", "effective_locale"} <= set(report["prefilled"])
    assert not {"decision_brief", "source_index", "deferred_details", "guidance"} & report.keys()
    assert cli.main(["render", "--request-file", str(draft), "--output", str(output), *stores]) == 0
    capsys.readouterr()
    assert output.read_text() == expected["output"]
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()


def test_public_draft_selection_and_in_place_overrides_render_without_duplicate_inputs(tmp_path, capsys):
    from test_kickoff_prefill import _project

    project = tmp_path / "project"
    _project(project)
    playbook = project / ".cafe/playbooks/example.yaml"
    playbook.write_text(playbook.read_text().replace(
        "conversation_locale: ja-JP",
        "conversation_locale: ja-JP, applicability: {summary: Writing, use_when: [outline], avoid_when: [deployment]}"
    ).replace("await_agent: _done", "await_agent: compose") +
        "  compose: {role: writer, assignee_type: agent, skill: custom-step, on: {await_agent: _done}}\n")
    config = project / ".cafe/phases.yaml"
    config.write_text(config.read_text() +
        "compose: {name: Writer, role: writer, clis: [{cli: codex, model: second-model}]}\n")
    cli = load_kickoff_module("prepare_kickoff")
    stores = ["--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    initial, updated, output = [tmp_path / name for name in ("draft.json", "updated.json", "proposal.md")]
    assert cli.main(["draft", "--project-root", str(project), "--issue-name", "new",
                     "--manager-cli", "codex", "--output", str(initial), *stores]) == 3
    capsys.readouterr()
    request = json.loads(initial.read_text())
    assert request["formatter_inputs"]["phase_chain"] == []
    request["playbook_id"] = "example"
    initial.write_text(json.dumps(request))
    assert cli.main(["assemble", "--request-file", str(initial), "--summary",
                     "--draft-output", str(updated), *stores]) == 3
    capsys.readouterr()
    request = json.loads(updated.read_text())
    assert "current_explicit_inputs" not in request
    fields = request["formatter_inputs"]
    assert fields["phase_chain"] == ["outline=codex:configured-model", "compose=codex:second-model"]
    assert fields["locale_source"] == "playbook:example"
    original = json.loads(json.dumps(fields))
    fields["phase_chain"][0] = "outline=claude:chosen-model"
    fields.update(effective_locale="fr-FR", locale_source="explicit")
    values = _formatter_inputs("new")
    fields["delivery_contract"] = values["delivery_contract"]
    for kind in ("update", "catalog"):
        report_file = tmp_path / f"{kind}.json"
        report_file.write_text(json.dumps(values[f"{kind}_preflight"]))
        request.setdefault("preflight_files", {})[kind] = str(report_file)
    updated.write_text(json.dumps(request))
    expected = load_kickoff_module("kickoff_inputs").render_kickoff({
        **fields, **{f"{kind}_preflight": values[f"{kind}_preflight"] for kind in ("update", "catalog")}
    })
    assert expected["status"] == "rendered", expected
    assert cli.main(["render", "--request-file", str(updated), "--output", str(output), *stores]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "rendered"
    assert output.read_text() == expected["output"]
    assert fields["phase_chain"][1] == original["phase_chain"][1]
    for key in ("cleanup", "cleanup_description", "manager_mode", "event_manager", "current_checkout"):
        assert fields[key] == original[key]
    assert json.loads(updated.read_text()) == request
    assert not (project / ".cafe/issues").exists()


def test_cached_delivery_and_issue_defaults_reach_the_complete_formatter(tmp_path, capsys, monkeypatch):
    from cafe.utils import git_utils

    monkeypatch.setattr(git_utils, "get_github_repo_name", lambda root: "example/project")
    cli = load_kickoff_module("prepare_kickoff")
    source = PROJECT_ROOT / "docs/settings-updates.md"
    evidence = tmp_path / "delivery.json"
    evidence.write_text(json.dumps({"target": "github-pr", "stable_conventions": ["Merge the reviewed PR."],
        "sources": [{"path": "docs/settings-updates.md", "fingerprint": hashlib.sha256(source.read_bytes()).hexdigest()}],
        "delivery_template": {"deliver": [["gh", "pr", "merge", "--merge"]],
                              "deliver_description": ["Merge the reviewed PR for {issue_name}."]}}))
    cache = tmp_path / "cache"
    refresh = ["evidence", "refresh", "--category", "delivery", "--project-root", str(PROJECT_ROOT),
               "--cache-dir", str(cache), "--evidence-file", str(evidence)]
    assert cli.main(refresh) == 3
    assert json.loads(capsys.readouterr().out)["diagnostic"] == "obsolete_manager_delivery_template"
    descriptive = json.loads(evidence.read_text())
    descriptive.pop("delivery_template")
    evidence.write_text(json.dumps(descriptive))
    assert cli.main(refresh) == 0
    capsys.readouterr()
    values = _formatter_inputs("issue999573")
    draft, output = [tmp_path / name for name in ("draft.json", "proposal.md")]
    stores = ["--config-dir", str(tmp_path / "config"), "--cache-dir", str(cache)]
    create = ["draft", "--project-root", str(PROJECT_ROOT), "--issue-id", "999573",
              "--playbook-id", values["playbook_id"], "--manager-cli", "codex", "--output", str(draft), *stores]
    for chain in values["phase_chain"]:
        create.extend(["--phase-chain", chain])
    assert cli.main(create) == 3  # Product decisions and reports remain to be supplied.
    report = json.loads(capsys.readouterr().out)
    request = json.loads(draft.read_text())
    fields = request["formatter_inputs"]
    assert fields["phase_chain"] == values["phase_chain"]
    assert fields["capability_choice"] == []
    assert fields["delivery_contract"]["outcome"] == ""
    original = draft.read_bytes()
    assert cli.main(create) == 2
    assert "already exists" in json.loads(capsys.readouterr().out)["message"]
    assert draft.read_bytes() == original
    assert report["delivery"]["status"] == "hit"
    assert "deliver" not in fields and "deliver_description" not in fields
    assert fields["cleanup"] == [["gh", "issue", "close", "999573", "--repo", "example/project"], ["cafe", "close", "--archive-only"]]
    assert fields["manager_mode"] == "event-driven" and fields["event_manager"] == ["codex"]
    assert fields["worktree"].endswith("/.cafe/worktrees/" + values["issue_name"])
    # Only fill the actual gaps; do not hand-copy any prefilled field.
    for key in ("delivery_contract", "capability_choice", "update_preflight", "catalog_preflight"):
        fields[key] = values[key]
    draft.write_text(json.dumps(request))
    assert cli.main(["render", "--request-file", str(draft), "--output", str(output), *stores]) == 0
    capsys.readouterr()
    assert "gh pr merge --merge" not in output.read_text()
    assert "gh issue close 999573 --repo example/project" in output.read_text()
    assert not Path(fields["worktree"]).exists()
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()


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


def test_resume_keeps_confirmed_locale_and_workflow_state_unchanged(tmp_path: Path, phase_config: Path) -> None:
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
    blackboard = issue_dir / "blackboard.json"
    before = blackboard.read_bytes()
    formatter_inputs = _formatter_inputs(issue_name)
    formatter_inputs.update(
        {
            "project_root": str(project),
            "phase_config": str(phase_config),
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
        "formatter input: delivery_contract"
    }
    assert partial["selected_graph"]["id"] == values["playbook_id"]
    assert partial["catalog"]["candidates"] == []
    assert partial["catalog"]["candidate_count"] > 1
    request["formatter_inputs"] = {key: values[key] for key in ("delivery_contract", "cleanup")}
    path.write_text(json.dumps(request))
    assert cli.main(["assemble", *common, "--summary"]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert {key: ready["formatter_inputs"][key] for key in values} == values
    defaults = load_kickoff_module("format_kickoff_contract")._parser()
    added = set(ready["formatter_inputs"]) - set(values)
    assert added == set(ready["prefilled"])
    assert all(ready["formatter_inputs"][key] == defaults.get_default(key) for key in added)
    expected = _render_with_store(values, tmp_path / "config")
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
    assert "deliver" not in template and template["cleanup"] == [["cafe", "close", "--archive-only"]]
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
    for key in ("cleanup",):
        draft_request["formatter_inputs"][key] = values[key]
    draft.write_text(json.dumps(draft_request))
    output = tmp_path / "proposal.md"
    assert cli.main(["render", *draft_common, "--output", str(output)]) == 0
    capsys.readouterr()
    expected = _render_with_store(values, tmp_path / "config")
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
            validate_closeout_plan_policy({"cleanup": [{"argv": example["argv"]}]}, allow_squash=False)
            valid = True
        except ValueError:
            valid = False
        assert example["valid_in_pr_mode"] == valid
    archive = next(e for e in schema["closeout_examples"] if "--archive-only" in e["argv"])
    assert archive["valid_in_pr_mode"]
    assert schema["input_template"]["cleanup"] is None


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
    values.update(cleanup=empty["actions"], cleanup_description=empty["descriptions"])
    assert "deliver" not in schema["formatter_fields"]
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
        "issue_name": values["issue_name"], "playbook_id": values["playbook_id"], "formatter_inputs": values}))
    args = ["--request-file", str(request), "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(["render", *args]) == 0
    rendered = json.loads(capsys.readouterr().out)["render"]
    expected = _render_with_store(values, tmp_path / "config")
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
    assert result["output"] == _render_with_store(values, tmp_path / "config")["output"]
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
    assert result["render"]["output"] == _render_with_store(values, tmp_path / "config")["output"]
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
    assert json.loads(rendered.stdout)["render"]["output"] == _render_with_store(values, tmp_path / "config")["output"]
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
    evidence["sources"][0].pop("fingerprint")
    data.write_text(json.dumps(evidence))
    assert cli.main(refresh) != 0  # Missing source and missing fingerprint are not matching evidence.
    capsys.readouterr()
    assert delivery()["status"] == "miss"


def test_parallel_report_capture_preserves_both_original_report_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U14-U16/I06: parallel producer outputs cannot lose a sibling report."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

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


@pytest.mark.parametrize("change", ["delivery_source", "model_source", "expired", "workload"])
def test_summary_reports_changed_evidence_without_assigning_models(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    """U05/U12/U14-U16/I05: a valid hit is useful evidence, never suitability or authority."""
    cli = load_kickoff_module("prepare_kickoff")
    project = tmp_path / "project"
    project.mkdir()
    source = project / "CONTRIBUTING.md"
    source.write_text("Local commits require review.\n")
    cache = tmp_path / "cache"
    delivery = tmp_path / "delivery.json"
    delivery.write_text(json.dumps({"target": "local", "stable_conventions": ["Review before commit."],
        "sources": [{"path": source.name, "fingerprint": hashlib.sha256(source.read_bytes()).hexdigest()}]}))
    common = ["--project-root", str(project), "--cache-dir", str(cache)]
    assert cli.main(["evidence", "refresh", *common, "--category", "delivery", "--evidence-file", str(delivery)]) == 0
    capsys.readouterr()
    now = datetime.now(timezone.utc).isoformat()
    model = {"provider": "fixture", "model": "exact-v1", "version": "exact-v1", "assessed_at": now,
        "workloads": ["requirements", "planning", "implementation", "review", "publication"],
        "reasoning": "high", "capability_bands": {"coding": "fixture"}, "limitations": ["Fixture only."],
        "sources": [{"url": "https://provider.invalid/exact-v1", "retrieved_at": now, "fingerprint": "v1"}]}
    model_file = tmp_path / "model.json"
    model_file.write_text(json.dumps(model))
    assert cli.main(["evidence", "refresh", *common, "--category", "models", "--evidence-file", str(model_file)]) == 0
    capsys.readouterr()
    request = tmp_path / "request.json"
    data = {"schema_version": 1, "project_root": str(project), "issue_name": "new-issue", "playbook_id": "standard-qa"}
    args = ["assemble", "--request-file", str(request), "--summary", "--config-dir", str(tmp_path / "config"), "--cache-dir", str(cache)]
    request.write_text(json.dumps(data))
    assert cli.main(args) == 3
    before = json.loads(capsys.readouterr().out)
    assert before["delivery"]["status"] == "hit"
    assert before["models"][0]["status"] == "hit"
    if change == "delivery_source":
        source.write_text("Changed delivery policy.\n")
    elif change == "model_source":
        data["current_model_sources"] = {"https://provider.invalid/exact-v1": "v2"}
    else:
        if change == "expired":
            old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
            model["assessed_at"] = old
            model["sources"][0]["retrieved_at"] = old
        else:
            model["workloads"] = ["implementation"]
        data["model_assessments"] = [model]
    request.write_text(json.dumps(data))
    assert cli.main(args) == 3
    after = json.loads(capsys.readouterr().out)
    if change == "delivery_source":
        assert after["delivery"]["status"] == "miss"
        assert after["models"][0]["status"] == "hit"
    elif change == "workload":
        assert after["models"][0]["assessment"]["workloads"] == ["implementation"]
        assert after["delivery"]["status"] == "hit"
    else:
        assert after["models"][0]["status"] == "miss"
        assert after["delivery"]["status"] == "hit"
    assert after["selected_graph"]["mandatory_confirmation_gates"] == before["selected_graph"]["mandatory_confirmation_gates"]


@pytest.mark.release_smoke
def test_summary_preserves_evidence_and_routes_missing_reports_to_same_draft(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """U14-U16/I01/I06: one decision index and a usable check/capture/render continuation."""
    import io
    cli = load_kickoff_module("prepare_kickoff")
    values = _formatter_inputs("issue573-report-continuation")
    mode_values = {"manager_mode": "unattended"}
    values.update(mode_values)
    request, draft, output = [tmp_path / name for name in ("request.json", "draft.json", "proposal.md")]
    partial = {key: value for key, value in values.items()
               if not key.endswith("_preflight") and key not in mode_values}
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
        "issue_name": values["issue_name"], "playbook_id": values["playbook_id"], "formatter_inputs": partial}))
    source = PROJECT_ROOT / "docs/settings-updates.md"
    evidence = tmp_path / "delivery.json"
    evidence.write_text(json.dumps({"target": "local", "stable_conventions": ["Respect current delivery policy."],
        "sources": [{"path": "docs/settings-updates.md", "fingerprint": hashlib.sha256(source.read_bytes()).hexdigest()}]}))
    assert cli.main(["evidence", "refresh", "--category", "delivery", "--project-root", str(PROJECT_ROOT),
        "--cache-dir", str(tmp_path / "cache"), "--evidence-file", str(evidence)]) == 0
    capsys.readouterr()
    now = datetime.now(timezone.utc).isoformat()
    model = {"provider": "fixture", "model": "exact-v1", "version": "exact-v1", "assessed_at": now,
        "workloads": ["implementation"], "reasoning": "high", "capability_bands": {"coding": "fixture"},
        "limitations": ["Fixture only."],
        "sources": [{"url": "https://provider.invalid/exact-v1", "retrieved_at": now, "fingerprint": "v1"}]}
    evidence.write_text(json.dumps(model))
    assert cli.main(["evidence", "refresh", "--category", "models", "--project-root", str(PROJECT_ROOT),
        "--cache-dir", str(tmp_path / "cache"), "--evidence-file", str(evidence)]) == 0
    capsys.readouterr()
    argv = ["assemble", "--request-file", str(request), "--summary", "--draft-output", str(draft),
            "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(argv) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["delivery"]["status"] == "hit"
    assert report["delivery"]["stable_conventions"] == ["Respect current delivery policy."]
    assert report["delivery"]["sources"][0]["fingerprint"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assessment = report["models"][0]["assessment"]
    assert report["models"][0]["status"] == "hit"
    assert assessment["workloads"] == model["workloads"] and assessment["limitations"] == model["limitations"]
    assert assessment["sources"] == model["sources"]
    if mode_values:
        data = json.loads(draft.read_text())
        # Changing modes replaces the previous mode's prefilled dependencies.
        for field in ("event_manager", "poll_interval_seconds"):
            data["formatter_inputs"].pop(field, None)
        data["formatter_inputs"].update(mode_values)
        draft.write_text(json.dumps(data))
    # Same draft, blocked before formatter execution: no misleading null-input error.
    render = [*report["render_command"][2:], "--output", str(output)]
    assert cli.main(render) == 3
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "incomplete"
    assert "formatter_inputs_must_be_an_object" not in blocked["diagnostics"]
    assert not output.exists()
    assert blocked["continuation"] == report["continuation"]
    steps = report["continuation"]["checks"]
    assert {s["kind"] for s in steps} == {"update", "catalog"}
    for step in steps:
        kind = step["kind"]
        assert step["check_argv"] == (["cafe", "update", "check", "--json"] if kind == "update" else
            [sys.executable, str(PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/scripts/catalog_version_check.py")])
        raw = values[kind + "_preflight"].copy()
        observed = raw.pop("checked_at")
        raw.pop("decision")
        raw.pop("post_change_evidence")
        if kind == "update":
            raw["token"] = raw.pop("comparison_token")
        else:
            raw = {"catalog_check": raw}
        original = json.dumps(raw) + "\n"
        # Producer I/O is the only fixture boundary; use the public capture and real validator.
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(original))
        capture = [observed if value == "<actual-checked-at>" else value for value in step["capture_argv"]]
        assert cli.main(capture[2:]) == 0
        captured = json.loads(capsys.readouterr().out)
        assert Path(captured["report_file"]).read_text() == original
        assert captured["request_file"] == str(draft)
    assert cli.main(render) != 0  # Captured facts do not decide report dispositions.
    capsys.readouterr()
    data = json.loads(draft.read_text())
    for kind in ("update", "catalog"):
        for field in ("decision", "post_change_evidence"):
            data["preflight_metadata"][kind][field] = values[kind + "_preflight"][field]
    draft.write_text(json.dumps(data))
    assert cli.main(render) == 0
    capsys.readouterr()
    assert output.read_text() == _render_with_store(values, tmp_path / "config")["output"]
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()


def test_missing_reports_block_with_actionable_continuation_not_a_null_type_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """U14/U16: a well-typed incomplete request is distinct from malformed inputs."""
    cli = load_kickoff_module("prepare_kickoff")
    values = _formatter_inputs("issue573-report-block")
    for kind in ("update", "catalog"):
        values.pop(kind + "_preflight")
    path = tmp_path / "draft.json"
    request = {"schema_version": 1, "project_root": str(PROJECT_ROOT),
        "issue_name": values["issue_name"], "playbook_id": values["playbook_id"], "formatter_inputs": values}
    path.write_text(json.dumps(request))
    assert cli.main(["render", "--request-file", str(path), "--output", str(tmp_path / "proposal.md"),
                    "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "incomplete" and not result["diagnostics"]
    assert {step["kind"] for step in result["continuation"]["checks"]} == {"update", "catalog"}
    assert json.loads(path.read_text()) == request


@pytest.mark.parametrize("mode_values,valid", [
    ({}, False),
    ({"manager_mode": "event-driven", "event_manager": ["codex"]}, True),
    ({"manager_mode": "attached", "poll_interval_seconds": 30}, True),
    ({"manager_mode": "unattended", "event_manager": ["codex"]}, False),
])
def test_operating_mode_decisions_expose_owner_types_without_changing_validation(
    tmp_path, capsys, monkeypatch, mode_values, valid
):
    """U14-U16/I06: current mode choices reveal dependencies, never authorize them."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    cli = load_kickoff_module("prepare_kickoff")
    values = _formatter_inputs("issue573-mode-types")
    values.pop("manager_mode")
    values.update(mode_values)
    request, draft, output = [tmp_path / name for name in ("request.json", "draft.json", "proposal.md")]
    request.write_text(json.dumps({"schema_version": 1, "project_root": str(PROJECT_ROOT),
        "issue_name": values["issue_name"], "playbook_id": values["playbook_id"], "formatter_inputs": values}))
    argv = ["assemble", "--request-file", str(request), "--summary", "--draft-output", str(draft),
        "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")]
    assert cli.main(argv) in (0, 3)
    view = json.loads(capsys.readouterr().out)
    types = view["input_schema"]["formatter_field_schema"]
    owner = load_kickoff_module("kickoff_inputs").request_schema()["formatter_field_schema"]
    assert types == owner  # No conditional type projection to omit dependent fields.
    for key, value in mode_values.items():
        assert json.loads(draft.read_text())["formatter_inputs"][key] == value
    before = json.loads(request.read_text())
    # Use the original supplied data: projection must not sanitize bad decisions.
    result = cli.main(["render", "--request-file", str(request), "--output", str(output),
        "--config-dir", str(tmp_path / "config"), "--cache-dir", str(tmp_path / "cache")])
    rendered = json.loads(capsys.readouterr().out)
    assert (result == 0) is valid, rendered
    assert output.exists() is valid
    assert json.loads(request.read_text()) == before
    if valid:
        assert output.read_text() == _render_with_store(values, tmp_path / "config")["output"]
    else:
        assert rendered.get("diagnostics") or rendered.get("missing") or rendered.get("missing_decisions")
    assert not (PROJECT_ROOT / ".cafe/issues" / values["issue_name"]).exists()
