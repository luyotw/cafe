"""Mode-scoped production delivery and reuse: U1/U2/U5/U9/U10, I1-I4."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from cafe.agents.executor import AgentExecutionError, AgentExecutor
from cafe.agents.manager import AgentManager
from cafe.constraints import Context, load_registry, resolver
from cafe.constraints.evidence import compare_snapshot, snapshot
from cafe.constraints.registry import parse_registry
from cafe.constraints.rendering import START, render_prompt
from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, CliEntry, TokenUsage
from cafe.ui.cli import app
from tests.integration import test_chat_read_only as chat_fixtures
from tests.integration.test_chat_read_only import configure_provider
from tests.integration.test_runtime_constraints_journeys import phase

diagnostic_workspace = chat_fixtures.diagnostic_workspace
native_io = chat_fixtures.native_io


@pytest.fixture
def mode_registry(monkeypatch):
    data = load_registry().model_dump(mode="json")
    source = next(e for e in data["entries"] if e["id"] == "human-task.user-authority")
    for mode in ("bespoke-mode", "read-only", "event-driven"):
        entry = copy.deepcopy(source)
        entry["id"] = "fixture." + mode
        entry["variants"][0]["scope"]["modes"] = [mode]
        data["entries"].append(entry)

    def current():
        return parse_registry(json.dumps(data))

    monkeypatch.setattr(resolver, "load_registry", current)
    monkeypatch.setattr("cafe.ui.commands.constraints.load_registry", current)
    return data


def inspect(context):
    args = ["constraints", "list", "--json"]
    for field in ("cli", "provider", "platform", "surface", "operation"):
        args += ["--" + field, getattr(context, field)]
    for field, flag in [
        ("modes", "mode"),
        ("capabilities", "capability"),
        ("workloads", "workload"),
        ("consumers", "consumer"),
    ]:
        for value in getattr(context, field):
            args += ["--" + flag, value]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["bespoke-mode", "other-mode"])
def test_custom_phase_modes_survive_actual_fallback_retry_inspection_and_resume(
    tmp_path, monkeypatch, mode_registry, mode
):
    from cafe.core.types import CriticalPhaseError
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
    from cafe.skills.native_bridge import NativeSkillBridge

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_git_toplevel", lambda: tmp_path)
    issue = tmp_path / ".cafe/issues/custom"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text("playbook: bespoke-flow\nfeature_branch: custom\n")
    (tmp_path / ".cafe/phases.yaml").write_text(
        "scribe:\n  name: CustomAuthor\n  clis:\n"
        "    - {cli: codex, model: fixture}\n    - {cli: gemini, model: fixture}\n"
    )
    agent = tmp_path / ".cafe/agents/author/CustomAuthor.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\nname: CustomAuthor\ndescription: Writer\n---\nWrite docs.\n")
    generic = phase(tmp_path, "short-docs", modes=[mode])
    # Public issue inspection discovers the same installed custom declaration.
    skill = tmp_path / ".cafe/skills/bespoke/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text((tmp_path / "builtin/skills/bespoke/SKILL.md").read_text())
    generic.skill_bridge = NativeSkillBridge(
        generic.skill_loader, project_root=tmp_path, home_dir=tmp_path / "home"
    )
    playbook = {
        "playbook": {"id": "bespoke-flow"},
        "entry": "scribe",
        "roles": {"author": {"default_agent": "CustomAuthor"}},
        "steps": {
            "scribe": {
                "skill": "bespoke",
                "role": "author",
                "behavior": {"completion": "baton"},
                "allowed_tools": ["Bash"],
                "on": {"workflow_complete": "_done"},
            }
        },
    }
    declared = tmp_path / ".cafe/playbooks/bespoke-flow.yaml"
    declared.parent.mkdir(parents=True)
    declared.write_text(yaml.safe_dump({k: v for k, v in playbook.items() if k != "entry"}))
    manager = AgentManager(issue_name="custom", stream_agent_output=False)
    manager.register_agent(
        AgentConfig(
            name="CustomAuthor",
            cli=AgentCLI.CODEX,
            clis=[CliEntry(cli=AgentCLI.CODEX), CliEntry(cli=AgentCLI.GEMINI)],
        )
    )
    captured = []

    def execute(executor, prompt, tools, directories, stream, **kwargs):
        captured.append(prompt)
        if len(captured) == 1:
            raise AgentExecutionError("missing", error_type="cli_not_found")
        if len(captured) == 2:
            raise AgentExecutionError("busy", error_type="provider_overloaded")
        iteration = Path(stream).parent
        (iteration / "output.md").write_text("Written documentation.\n")
        checklist = iteration / "checklist.md"
        checklist.write_text(checklist.read_text().replace("[ ]", "[x]"))
        (issue / "next_step.txt").write_text(
            '{"version":1,"to_owner":"done","to_step":"done","intent":"workflow_complete"}'
        )
        return AgentResponse(response="done", token_usage=TokenUsage(), cli=executor.config.cli)

    monkeypatch.setattr(AgentExecutor, "execute", execute)
    monkeypatch.setattr("time.sleep", lambda _: None)
    git = MagicMock()
    git.get_repo_root.return_value = tmp_path
    git.get_default_base_branch.return_value = "main"
    git.run_git.return_value = "fixture-head"
    git.get_status.return_value = ""
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue,
        issue_name="custom",
        playbook=playbook,
        generic_phase=generic,
        agent_manager=manager,
        git_ops=git,
        role_agent_map={"author": "CustomAuthor"},
    )
    assert (
        BlackboardWorkflowRuntime(
            issue_dir=issue, playbook=playbook, executor=executor.execute_step
        )
        .run(start_step="scribe")
        .completed
    )
    assert len(captured) == 3
    for prompt in captured:
        assert prompt.count(START) == 1
        assert ("[fixture.bespoke-mode]" in prompt) is (mode == "bespoke-mode")
        assert "[fixture.read-only]" not in prompt
    actual = manager.get_last_constraints()
    assert actual["context"]["modes"] == [mode]
    inspected = CliRunner().invoke(
        app,
        [
            "constraints",
            "list",
            "--issue",
            "custom",
            "--step",
            "scribe",
            "--cli",
            "gemini",
            "--json",
        ],
    )
    assert inspected.exit_code == 0, inspected.output
    payload = json.loads(inspected.stdout)
    assert payload["context"] == actual["context"]
    assert payload["digest"] == actual["digest"]
    record = issue / "scribe/iteration_001/iteration.json"
    saved = json.loads(record.read_text())
    assert saved["runtime_constraints"] == actual
    saved.pop("end_time", None)
    saved["workflow_completion_trusted"] = False
    record.write_text(json.dumps(saved))
    context = saved["constraint_context"] | {
        "modes": ["other-mode" if mode == "bespoke-mode" else "bespoke-mode"]
    }
    with pytest.raises(CriticalPhaseError) as stopped:
        executor._execute_agent_iteration(
            "CustomAuthor",
            "resume",
            "workflow execute",
            [],
            require_status_code=False,
            persist_status=False,
            allowed_tools=["Bash"],
            phase_specific_data={"step_name": "scribe", "constraint_context": context},
        )
    assert stopped.value.error_type == "constraints_changed"
    assert len(captured) == 3


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("operation", ["managed", "interactive"])
@pytest.mark.parametrize("chat_mode", [None, "bespoke-mode"])
def test_public_chat_modes_match_inspection_without_session_or_authority_changes(
    diagnostic_workspace, native_io, mode_registry, read_only, operation, chat_mode
):
    from cafe.constraints.context import context_for_tools
    from cafe.ui.chat import launch_chat_session
    from tests.unit.test_chat_read_only import inventory

    repo, _, _ = diagnostic_workspace
    configure_provider(repo, "codex", True)
    before = inventory(repo.parent)
    if chat_mode:
        result = launch_chat_session(
            "analyst",
            "issue520",
            phase_name="inspect",
            chat_mode=chat_mode,
            read_only=read_only,
            prompt="inspect" if operation == "managed" else None,
        )
    else:
        args = ["chat", "analyst", "--phase", "inspect"]
        if read_only:
            args += ["--read-only"]
        if operation == "managed":
            args += ["-p", "inspect"]
        result = CliRunner().invoke(app, args).exit_code
    assert result == 0
    assert len(native_io[0]) == 1
    command = native_io[0][0][0]
    modes = ([chat_mode] if chat_mode else []) + (["read-only"] if read_only else [])
    context = context_for_tools(
        "codex", surface="chat", operation=operation, consumers=["authority"], modes=modes
    )
    expected = resolver.resolve(context)
    guidance = "\n".join(command)
    assert render_prompt(expected) in guidance
    assert ("[fixture.bespoke-mode]" in guidance) is bool(chat_mode)
    assert ("[fixture.read-only]" in guidance) is read_only
    assert "[fixture.event-driven]" not in guidance
    assert "stored-session" in command
    assert inspect(expected.context)["digest"] == snapshot(context)["digest"]
    if read_only:
        assert inventory(repo.parent) == before


def test_public_manager_callback_mode_refresh_matches_inspection_and_material_evidence(
    tmp_path, monkeypatch, mode_registry
):
    from cafe.manager import (
        ActivateConfirmedContract,
        ManagerEntryRequest,
        activate_confirmed_contract,
        evaluate_manager_entry,
    )
    from cafe.manager._freshness import Freshness, compare_freshness
    from cafe.manager._schema import freshness_semantic_facts
    from cafe.manager._store import load_contract
    from cafe.manager.constraints import refresh_constraints
    from tests.unit.test_manager_contract_application import _manager_proposal

    monkeypatch.chdir(tmp_path)
    proposal = _manager_proposal("event-driven")
    proposal["manager"]["clis"] = [{"cli": "codex"}, {"cli": "gemini", "model": "fixture"}]
    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=tmp_path,
            issue_name="custom",
            workflow_id="w",
            confirmed_by="user",
            confirmed_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
            proposal=proposal,
        )
    )
    contract, _ = load_contract(tmp_path)
    for cli in ("codex", "gemini"):
        evidence = contract["provenance"]["runtime_constraints"]["entries"]["callback/" + cli]
        context = Context.model_validate(evidence["context"])
        assert context.modes == ["event-driven"]
        payload = inspect(context)
        assert payload["digest"] == evidence["digest"]
        assert "fixture.event-driven" in {e["id"] for e in payload["entries"]}
        assert "fixture.read-only" not in {e["id"] for e in payload["entries"]}
    facts = {"semantic_facts": freshness_semantic_facts(contract)}
    request = ManagerEntryRequest(tmp_path, "custom", "w", facts)
    assert evaluate_manager_entry(request).freshness == Freshness.SAME_SEMANTICS
    # Existing evidence can predate mode forwarding. Refresh from owning policy,
    # without mutating or automatically confirming the stored contract.
    legacy = copy.deepcopy(contract)
    for name, value in legacy["provenance"]["runtime_constraints"]["entries"].items():
        if name.startswith("callback/"):
            legacy["provenance"]["runtime_constraints"]["entries"][name] = snapshot(
                Context.model_validate(value["context"]).model_copy(update={"modes": []})
            )
    refreshed = refresh_constraints(legacy)
    assert refreshed["entries"]["callback/codex"]["context"]["modes"] == ["event-driven"]
    assert (
        compare_freshness(legacy, facts | {"runtime_constraints": refreshed})
        == Freshness.MATERIAL_CHANGE
    )
    entry = next(e for e in mode_registry["entries"] if e["id"] == "fixture.event-driven")
    entry["title"] += " editorial"
    assert evaluate_manager_entry(request).freshness == Freshness.SAME_SEMANTICS
    entry["mitigation"] += " Verify additional completion evidence."
    assert evaluate_manager_entry(request).freshness == Freshness.MATERIAL_CHANGE
    assert contract["manager"] == proposal["manager"]
    assert contract["reactive_user_handoffs"]["need_permission"] == "user_required"


def test_mode_identity_tracks_context_changes_and_normalizes_tag_order(mode_registry):
    from cafe.constraints.context import context_for_tools

    def current(modes):
        return snapshot(context_for_tools(modes=modes, consumers=["authority"]))

    before = current(["bespoke-mode"])
    assert compare_snapshot(before, current(["other-mode"])) == "material_change"
    ordered = current(["bespoke-mode", "irrelevant-mode"])
    assert (
        compare_snapshot(ordered, current(["irrelevant-mode", "bespoke-mode"])) == "same_semantics"
    )


@pytest.mark.parametrize("mode", ["bespoke-mode", "other-mode"])
def test_standalone_custom_phase_prompt_resolves_opaque_declared_mode(
    tmp_path, mode_registry, mode
):
    prompt = phase(tmp_path, modes=[mode]).build_prompt(
        skill_name="bespoke", skill_invocation="$bespoke"
    )
    assert ("[fixture.bespoke-mode]" in prompt) is (mode == "bespoke-mode")
    assert "[fixture.event-driven]" not in prompt
