"""Constraint delivery journeys: plan I1-I3/I6, U4/U6/U10."""

import json
from unittest.mock import MagicMock

import pytest

from cafe.agents.executor import AgentExecutionError, AgentExecutor
from cafe.agents.manager import AgentManager
from cafe.agents.transport import ConversationTransport
from cafe.constraints.rendering import START
from cafe.core.session import SessionManager
from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, CliEntry, TokenUsage
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.loader import SkillLoader


def phase(tmp_path, workload="implementation"):
    root = tmp_path / "builtin" / "skills" / "bespoke"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f"---\n"
        f"name: bespoke\n"
        f"description: custom\n"
        f"workflow:\n"
        f"  execution_profile:\n"
        f"    workload: content\n"
        f"    requested_workloads: [{workload}]\n"
        f"---\n"
        f"Body\n"
        f""
    )
    return GenericPhase(
        SkillLoader(
            project_root=tmp_path,
            global_root=tmp_path / "global",
            builtin_root=tmp_path / "builtin",
        )
    )


def test_custom_prompt_and_actual_fallback_retry_refresh_effective_cli(tmp_path, monkeypatch):
    prompt = phase(tmp_path).build_prompt(
        skill_name="bespoke", skill_invocation="$bespoke", context={"agent_cli": "codex"}
    )
    assert "agent.stdout-idle" in prompt and "idle=300 seconds" in prompt
    manager = AgentManager(session_manager=MagicMock(spec=SessionManager))
    manager.session_manager.load_session.return_value = None
    manager.register_agent(
        AgentConfig(
            name="Unusual",
            cli=AgentCLI.CODEX,
            clis=[CliEntry(cli=AgentCLI.CODEX), CliEntry(cli=AgentCLI.GEMINI)],
        )
    )
    captured = []

    def execute(executor, prompt, *args, **kwargs):
        captured.append((executor.config.cli, prompt))
        if executor.config.cli == AgentCLI.CODEX:
            raise AgentExecutionError("missing", error_type="cli_not_found")
        if len(captured) == 2:
            raise AgentExecutionError("busy", error_type="provider_overloaded")
        return AgentResponse(response="done", token_usage=TokenUsage(), cli=executor.config.cli)

    monkeypatch.setattr(AgentExecutor, "execute", execute)
    monkeypatch.setattr("time.sleep", lambda _: None)
    assert manager.execute("Unusual", prompt, phase_name="arbitrary")[0] == "done"
    assert len(captured) == 3
    for cli, text in captured:
        assert text.count(START) == 1
        assert ("idle=600 seconds" if cli == AgentCLI.GEMINI else "idle=300 seconds") in text


def test_explicit_short_docs_and_windows_omit_idle_noise(tmp_path, monkeypatch):
    p = phase(tmp_path, "short-docs")
    assert "agent.stdout-idle" not in p.build_prompt(
        skill_name="bespoke", skill_invocation="$bespoke"
    )
    monkeypatch.setattr("sys.platform", "win32")
    assert "agent.stdout-idle" not in phase(tmp_path / "long").build_prompt(
        skill_name="bespoke", skill_invocation="$bespoke"
    )


@pytest.mark.parametrize("cli", [AgentCLI.CODEX, AgentCLI.CLAUDE])
def test_interactive_context_with_no_initial_message_preserves_selected_session(cli, monkeypatch):
    executor = AgentExecutor(AgentConfig(name="custom", cli=cli, session_id="selected"))
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda command, **kw: calls.append(command)
        or MagicMock(returncode=0, stderr=None, stdout=None),
    )
    from cafe.ui.chat import _chat_constraint_prompt

    ConversationTransport(executor).open_interactive_session(
        _chat_constraint_prompt("", cli, "interactive"), require_initial_context=True
    )
    assert len(calls) == 1 and "selected" in calls[0]
    text = calls[0][-1]
    assert "constraint_assistance" in text
    assert "agent.stdout-idle" not in text
    assert executor.config.session_id == "selected"


@pytest.mark.parametrize("cli", [AgentCLI.GEMINI, AgentCLI.COPILOT, AgentCLI.CURSOR])
def test_unsupported_interactive_context_is_explicit_and_never_launches(cli, monkeypatch):
    launch = MagicMock()
    monkeypatch.setattr("subprocess.run", launch)
    with pytest.raises(AgentExecutionError) as caught:
        ConversationTransport(
            AgentExecutor(AgentConfig(name="custom", cli=cli))
        ).open_interactive_session(require_initial_context=True)
    assert caught.value.error_type == "unsupported_initial_context"
    launch.assert_not_called()


def test_oversized_applicable_contract_prevents_actual_attempt(tmp_path, monkeypatch):
    from cafe.constraints import load_registry, resolver
    from cafe.constraints.registry import parse_registry

    data = load_registry().model_dump(mode="json")
    data["entries"][0]["mitigation"] = "必要" * 5000
    monkeypatch.setattr(resolver, "load_registry", lambda: parse_registry(json.dumps(data)))
    manager = AgentManager(session_manager=MagicMock(spec=SessionManager))
    manager.session_manager.load_session.return_value = None
    manager.register_agent(AgentConfig(name="custom", cli=AgentCLI.CODEX))
    launch = MagicMock()
    monkeypatch.setattr(AgentExecutor, "execute", launch)
    with pytest.raises(ValueError):
        manager.execute("custom", "work")
    launch.assert_not_called()


@pytest.mark.parametrize("resume_change", [False, True])
def test_custom_step_forwards_declared_workload_to_actual_execution(
    tmp_path, monkeypatch, resume_change
):
    """I1/I2: removing declaration-to-attempt forwarding reintroduces idle noise."""
    from pathlib import Path

    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
    from cafe.skills.native_bridge import NativeSkillBridge

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_git_toplevel", lambda: tmp_path)
    issue = tmp_path / ".cafe/issues/custom"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text("feature_branch: custom\n")
    (tmp_path / ".cafe/phases.yaml").write_text(
        "scribe:\n  name: CustomAuthor\n  clis:\n    - cli: codex\n      model: test-model\n"
    )
    agent_file = tmp_path / ".cafe/agents/author/CustomAuthor.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text(
        "---\nname: CustomAuthor\ndescription: Writer\n---\nWrite documentation.\n"
    )
    generic = phase(tmp_path, "short-docs")
    generic.skill_bridge = NativeSkillBridge(
        generic.skill_loader, project_root=tmp_path, home_dir=tmp_path / "home"
    )
    playbook = {
        "playbook": {"id": "bespoke-flow"},
        "entry": "scribe",
        "steps": {
            "scribe": {
                "skill": "bespoke",
                "role": "author",
                "behavior": {"completion": "baton"},
                "allowed_tools": ["Bash"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    manager = AgentManager(issue_name="custom", stream_agent_output=False)
    manager.register_agent(AgentConfig(name="CustomAuthor", cli=AgentCLI.CODEX))
    captured = []

    def execute(executor, prompt, tools, directories, stream, **kwargs):
        captured.append(prompt)
        iteration = Path(stream).parent
        (iteration / "output.md").write_text("Written documentation.\n")
        checklist = iteration / "checklist.md"
        checklist.write_text(checklist.read_text().replace("[ ]", "[x]"))
        (issue / "next_step.txt").write_text('{"version":1,"intent":"await_agent"}')
        return AgentResponse(response="done", token_usage=TokenUsage(), cli=executor.config.cli)

    monkeypatch.setattr(AgentExecutor, "execute", execute)
    git = MagicMock()
    git.get_repo_root.return_value = tmp_path
    git.get_default_base_branch.return_value = "main"
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue,
        issue_name="custom",
        playbook=playbook,
        generic_phase=generic,
        agent_manager=manager,
        git_ops=git,
        role_agent_map={"author": "CustomAuthor"},
    )
    result = BlackboardWorkflowRuntime(
        issue_dir=issue, playbook=playbook, executor=executor.execute_step
    ).run(start_step="scribe")
    assert result.completed
    assert len(captured) == 1
    assert "agent.stdout-idle" not in captured[0]
    assert captured[0].count(START) == 1
    assert manager.get_last_constraints()["context"]["workloads"] == ["short-docs"]

    saved_path = issue / "scribe/iteration_001/iteration.json"
    saved = json.loads(saved_path.read_text())
    assert saved["runtime_constraints"]["digest"] == manager.get_last_constraints()["digest"]
    if resume_change:
        saved.pop("end_time", None)
        saved["workflow_completion_trusted"] = False
        saved_path.write_text(json.dumps(saved))
        from cafe.constraints import load_registry, resolver
        from cafe.constraints.registry import parse_registry

        registry_data = load_registry().model_dump(mode="json")
        selected = next(
            e for e in registry_data["entries"] if e["id"] == "agent.structured-completion"
        )
        selected["mitigation"] += " Collect additional evidence."
        registry = parse_registry(json.dumps(registry_data))
        monkeypatch.setattr(resolver, "load_registry", lambda: registry)
        from cafe.core.types import CriticalPhaseError

        with pytest.raises(CriticalPhaseError) as stopped:
            executor._execute_agent_iteration(
                "CustomAuthor",
                "resume",
                "workflow execute",
                [],
                require_status_code=False,
                persist_status=False,
                allowed_tools=["Bash"],
                phase_specific_data={
                    "step_name": "scribe",
                    "constraint_context": saved["constraint_context"],
                },
            )
        assert stopped.value.error_type == "constraints_changed"
        assert len(captured) == 1
        refreshed = json.loads(saved_path.read_text())
        assert refreshed["constraint_freshness"] == "material_change"


def test_custom_prompt_exposes_expired_external_fact_without_asserting_exact_limit(
    tmp_path, monkeypatch
):
    from cafe.constraints import load_registry, resolver
    from cafe.constraints.registry import parse_registry

    data = load_registry().model_dump(mode="json")
    entry = data["entries"][0]
    entry["enforcement"] = "provider-host"
    entry["verification"] = {
        "source": "https://provider.example/limits",
        "verified_at": "2000-01-01T00:00:00Z",
        "expires_at": "2001-01-01T00:00:00Z",
    }
    registry = parse_registry(json.dumps(data))
    monkeypatch.setattr(resolver, "load_registry", lambda: registry)
    prompt = phase(tmp_path).build_prompt(skill_name="bespoke", skill_invocation="$bespoke")
    line = next(line for line in prompt.splitlines() if "[agent.stdout-idle]" in line)
    assert "unverified" in line and "idle=300" not in line
    assert entry["mitigation"] in line
