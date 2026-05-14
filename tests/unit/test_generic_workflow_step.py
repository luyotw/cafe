"""Tests for direct workflow step execution."""

from collections.abc import Iterator
import json
from pathlib import Path
from types import MethodType
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.hooks import HookResult
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import AgentCLI, TokenUsage
from cafe.phases.generic_phase import GenericPhaseExecution
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.agents.executor import AgentExecutionError


class FakeAgentManager:
    def __init__(self, response: str | list[str], on_execute=None) -> None:
        if isinstance(response, list):
            self._responses: Iterator[str] = iter(response)
        else:
            self._responses = iter([response])
        self.prompts: list[str] = []
        self.allowed_tools_calls: list[list[str] | None] = []
        self.preview_calls: list[list[str] | None] = []
        self.agent = SimpleNamespace(
            config=SimpleNamespace(cli=AgentCLI.CODEX, session_id="session-1", model=None)
        )
        self.on_execute = on_execute

    def get_agent(self, name: str) -> SimpleNamespace:
        return self.agent

    def preview_cli_command_args(
        self,
        agent_name: str,
        prompt: str,
        allowed_tools=None,
        allowed_directories=None,
    ) -> list[str]:
        args = ["-C", str(Path.cwd().resolve()), "-a", "never", "exec", "--json"]
        if self.agent.config.model:
            args.extend(["--model", self.agent.config.model])
        args.append(prompt)
        self.preview_calls.append(args)
        return args

    def preview_cli_environment(self, agent_name: str) -> dict[str, str]:
        return {"CODEX_HOME": str(Path.cwd().resolve() / ".codex")}

    def execute(
        self,
        agent_name: str,
        prompt: str,
        allowed_tools=None,
        allowed_directories=None,
        streaming_output_file=None,
    ):
        self.prompts.append(prompt)
        self.allowed_tools_calls.append(list(allowed_tools) if allowed_tools is not None else None)
        response = next(self._responses)
        if self.on_execute is not None:
            self.on_execute(
                prompt=prompt,
                response=response,
                streaming_output_file=streaming_output_file,
            )
        return response, TokenUsage(), [], [], [], None


class FakeGitOperations:
    def get_main_branch(self) -> str:
        return "main"

    def get_default_base_branch(self) -> str:
        return "main"

    def get_commits_between(self, base: str, head: str) -> str:
        return "abc123 test commit"


def _build_loader(tmp_path: Path) -> GenericPhase:
    skill_root = tmp_path / "builtin" / "skills"
    for name, body in {
        "spec_first": "## Role\nRead your agent file: {agent_file}\n\n## Context\n{blackboard_digest}\n",
        "plan": "Write plan to: {output_file}\n",
        "develop": "Implement the current request.\n",
        "workflow-common": "Read blackboard first.\n",
        "github_sync": "Shared GitHub sync helper.\n",
        "review": "Review the latest changes.\n",
        "pr": "Write PR content to: {output_file}\n",
    }.items():
        skill_dir = skill_root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: desc\n---\n\n{body}",
            encoding="utf-8",
        )
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(
            loader,
            project_root=tmp_path,
            home_dir=tmp_path / "home",
        ),
    )


def test_generic_workflow_step_executor_writes_iteration_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-1"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-1",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert result.response == "confirmed"
    assert "spec" in result.artifacts
    iteration_dir = issue_dir / "spec" / "iteration_001"
    assert (iteration_dir / "context.json").exists()
    assert (iteration_dir / "checklist.md").exists()
    assert (iteration_dir / "output.md").exists()
    assert (iteration_dir / "artifact.json").exists()
    status_file = issue_dir / "spec" / "status.json"
    assert not status_file.exists()
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.DONE
    assert reloaded.handoff_contract.to_step == "done"
    assert reloaded.handoff_contract.intent == HandoffIntent.WORKFLOW_COMPLETE
    assert reloaded.handoff_contract.status_code == "confirmed"
    assert reloaded.handoff_contract.source == "workflow.status_transition_adapter"


def test_generic_workflow_step_agent_written_baton_preserved(tmp_path: Path, monkeypatch) -> None:
    """When the agent writes a baton directly, the status-driven write is skipped."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-agent-baton"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    def on_execute(prompt, response, streaming_output_file=None):
        # Simulate agent writing a baton targeting "plan" instead of "_done"
        baton_path = issue_dir / "next_step.txt"
        baton_path.write_text(json.dumps({
            "version": 1,
            "from_step": "spec",
            "to_owner": "agent",
            "to_step": "plan",
            "intent": "await_agent",
            "status_code": "confirmed",
            "created_at": "2026-05-14T10:00:00+08:00",
            "source": "spec.agent",
        }), encoding="utf-8")

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-agent-baton",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed", on_execute=on_execute),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)
    assert result.response == "confirmed"

    # The agent's baton should be preserved (to_step=plan), not overwritten
    # by the status-driven transition (which would have set to_step=done)
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_step == "plan"
    assert reloaded.handoff_contract.to_owner == HandoffOwner.AGENT
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_generic_workflow_step_writes_review_pause_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review-pause"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["ready_for_review", "confirmed"],
                "on": {"confirm_output": "spec", "await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review-pause",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("ready_for_review"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert result.status_code == "ready_for_review"
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.to_step == "user"
    assert reloaded.handoff_contract.intent == HandoffIntent.CONFIRM_OUTPUT
    assert reloaded.handoff_contract.source == "workflow.status_transition_adapter"


def test_generic_workflow_step_does_not_retry_for_legacy_status_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-status-retry"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    agent_manager = FakeAgentManager(["ready_for_review", "confirmed"])
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-status-retry",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert len(agent_manager.prompts) == 1
    assert result.status_code is None


def test_generic_workflow_step_executor_uses_iteration_specific_skill_mapping(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-2"
    phase_dir = issue_dir / "spec" / "iteration_001"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / "context.json").write_text(
        '{"iteration": 1, "response": "confirmed", "end_time": "2026-04-09T00:00:00+08:00"}',
        encoding="utf-8",
    )

    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "plan"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    class RecordingPhase(GenericPhase):
        def __init__(self, loader, bridge):
            super().__init__(loader, skill_bridge=bridge)
            self.skill_names: list[str] = []

        def execute(self, **kwargs):
            self.skill_names.append(kwargs["skill_name"])
            return super().execute(**kwargs)

    state = BlackboardStore(issue_dir).load_or_create("spec")
    phase_for_loader = _build_loader(tmp_path)
    generic_phase = RecordingPhase(phase_for_loader.skill_loader, phase_for_loader.skill_bridge)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-2",
        playbook=playbook,
        generic_phase=generic_phase,
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert generic_phase.skill_names == ["plan"]


def test_generic_workflow_step_executor_installs_workflow_common_and_phase_skill(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review-skill"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"reviewer": {"default_agent": "Richard"}},
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "output_artifact": "review_feedback",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("review")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    generic_phase = _build_loader(tmp_path)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review-skill",
        playbook=playbook,
        generic_phase=generic_phase,
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"reviewer": "Richard"},
    )

    executor.execute_step("review", playbook["steps"]["review"], state)

    assert (tmp_path / "home" / ".codex" / "skills" / "cafe-workflow-common" / "SKILL.md").exists()
    assert (tmp_path / "home" / ".codex" / "skills" / "cafe-github_sync" / "SKILL.md").exists()
    assert (tmp_path / "home" / ".codex" / "skills" / "cafe-review" / "SKILL.md").exists()


def test_generic_workflow_step_prompt_includes_latest_blackboard_handoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-handoff"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    store.set_handoff_summary(
        state,
        "還要再實作 cafe skill rm，支援批次刪除、interactive 多選與 confirm。",
    )
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-handoff",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert any("Latest workflow handoff from blackboard:" in prompt for prompt in agent_manager.prompts)
    assert any("還要再實作 cafe skill rm" in prompt for prompt in agent_manager.prompts)


def test_generic_workflow_step_prompt_keeps_skill_invocations_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-skill-body"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-skill-body",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    prompt = agent_manager.prompts[-1]
    assert "Shared skills:" in prompt
    assert "Phase skill: " in prompt
    assert "Shared skill instructions:" not in prompt
    assert "Phase skill instructions:" not in prompt
    assert "Read blackboard first." not in prompt
    assert "Write PR content to:" not in prompt


def test_generic_workflow_step_pr_prompt_overrides_external_state_guardrail(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-guardrail"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    store.set_handoff_summary(state, "原本的 pr script 有問題，我把 pr 砍掉了麻煩重發一次")
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-guardrail",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    prompt = agent_manager.prompts[-1]
    assert "Do not wait for, verify, or require a remote GitHub branch/PR" in prompt
    assert "Remote PR publish happens later in the host-side publish_output hook." in prompt
    assert "Before updating the workflow baton, verify whether the requested state change has actually happened in files or external state relevant to this phase." not in prompt


def test_generic_workflow_step_writes_pr_publish_request_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-contract"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    issue_yaml = issue_dir / "issue.yaml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    issue_yaml.write_text("base_branch: v02\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-contract",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    publish_request = json.loads(
        (issue_dir / "pr" / "iteration_001" / "publish_request.json").read_text(encoding="utf-8")
    )
    assert publish_request["capability"] == "cafe.pr.publish"
    assert publish_request["args"] == {
        "output": ".cafe/issues/issue-pr-contract/pr/iteration_001/output.md",
        "base": "v02",
    }
    assert publish_request["permissions"]["network"] == ["github.com", "api.github.com"]
    assert publish_request["permissions"]["writes"] == [".git", ".cafe/issues/issue-pr-contract"]


def test_generic_workflow_step_collects_clarification_before_next_agent_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-clarify"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["need_clarification", "confirmed"],
                "hooks": {"prepare_input": ["UserInputCollector"]},
                "on": {
                    "need_clarification": "spec",
                    "await_agent": "_done",
                },
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    def _write_questions_xml(*, response: str, streaming_output_file: str | None, **_: object) -> None:
        if response != "need_clarification" or not streaming_output_file:
            return
        iteration_dir = Path(streaming_output_file).parent
        (iteration_dir / "questions.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="q1">
    <title>Which flow should we support first?</title>
    <options>
      <option>CLI only</option>
      <option>CLI and GitHub</option>
    </options>
  </question>
</questions>
""",
            encoding="utf-8",
        )

    agent_manager = FakeAgentManager(
        ["need_clarification", "confirmed"],
        on_execute=_write_questions_xml,
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-clarify",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    first_result = executor.execute_step("spec", playbook["steps"]["spec"], state)
    assert first_result.response == "need_clarification"
    assert first_result.auto_continue is False

    # Mirror the runtime which records a `step_completed` event after each step
    # so the second iteration's hooks can read the previous status from the
    # blackboard.
    store.record_event(
        state,
        "step_completed",
        {"step": "spec", "status_code": first_result.status_code},
    )

    with patch(
        "cafe.core.hooks.native.interactive_qa_flow",
        return_value="Q1: Which flow should we support first?\nA1: CLI only",
    ):
        second_result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert second_result.response == "confirmed"
    assert any(
        "Current user input for this iteration:" in prompt and "A1: CLI only" in prompt
        for prompt in agent_manager.prompts
    )


def test_initial_requirements_collection_does_not_auto_continue_clarification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-initial-input"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["need_clarification", "confirmed"],
                "hooks": {"prepare_input": ["GitHubIssueFetcher"]},
                "on": {
                    "need_clarification": "spec",
                    "await_agent": "_done",
                },
            }
        },
    }
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "spec:\n  input_method: manual\n",
        encoding="utf-8",
    )
    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-initial-input",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("need_clarification"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
        step_user_inputs={"spec": "Initial issue text"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert result.status_code == "need_clarification"
    assert result.auto_continue is False
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.to_step == "user"
    assert reloaded.handoff_contract.intent == HandoffIntent.NEED_CLARIFICATION


def test_generic_workflow_step_records_script_hook_events_to_blackboard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-script-event"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "hooks": {"before_execute": ["ScriptHook"]},
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    class ScriptHook:
        name = "ScriptHook"

        def run(self, **kwargs):
            return HookResult(
                events=[
                    {
                        "type": "script_hook",
                        "step": "develop",
                        "skill": "develop",
                        "stage": "before_execute",
                        "script": "demo.sh",
                        "status": "success",
                        "exit_code": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "validation_errors": [],
                    }
                ]
            )

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))

    base_phase = _build_loader(tmp_path)
    phase_with_hook = GenericPhase(
        base_phase.skill_loader,
        hook_registry={"ScriptHook": ScriptHook},
        skill_bridge=base_phase.skill_bridge,
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-script-event",
        playbook=playbook,
        generic_phase=phase_with_hook,
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "confirmed"
    reloaded = BlackboardStore(issue_dir).load_or_create("develop")
    script_event = next(item for item in reloaded.events if item.event_type == "script_hook")
    assert script_event.data["script"] == "demo.sh"
    assert script_event.data["status"] == "success"


def test_generic_workflow_step_auto_continues_pause_statuses_in_interactive_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review"
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    (spec_dir / "context.json").write_text(
        '{"iteration":1,"status_code":"confirmed"}',
        encoding="utf-8",
    )
    (issue_dir / "spec" / "status.json").write_text(
        '{"phase":"spec","status":"completed","status_code":"confirmed","iteration":1}',
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "plan": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "plan",
                "allowed_tools": ["Read"],
                "valid_intents": ["ready_for_review"],
                "on": {"confirm_output": "plan"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("plan")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("ready_for_review"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=True,
    )

    result = executor.execute_step("plan", playbook["steps"]["plan"], state)

    assert result.response == "ready_for_review"
    assert result.auto_continue is True


def test_generic_workflow_step_keeps_missing_status_without_continue_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-no-status"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-no-status",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager(["done without status", "confirmed"]),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert "done without status" in result.response
    assert result.status_code is None
    assert len(executor.agent_manager.prompts) == 1
    context_data = json.loads((issue_dir / "spec" / "iteration_001" / "context.json").read_text(encoding="utf-8"))
    assert "status_code" not in context_data


def test_generic_workflow_step_does_not_recover_from_unchanged_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-stale-output"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed", "no_changes_needed"],
                "on": {"await_agent": "review", "await_agent": "review"},
            }
        },
    }

    class FailingAgentManager(FakeAgentManager):
        def execute(self, *args, **kwargs):
            raise AgentExecutionError("Codex execution failed: thread/resume failed")

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-stale-output",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FailingAgentManager("unused"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    with pytest.raises(AgentExecutionError):
        executor.execute_step("develop", playbook["steps"]["develop"], state)


def test_generic_workflow_step_restores_spec_runtime_allowed_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-spec-tools"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read", "Grep", "Glob", "WebFetch", "WebSearch"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-spec-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "ls" in allowed_tools
    assert "web_fetch" in allowed_tools
    assert "web_search" in allowed_tools
    assert "edit(./.cafe/issues/issue-spec-tools/spec/iteration_001/output.md)" in allowed_tools
    assert "edit(./.cafe/issues/issue-spec-tools/spec/iteration_001/checklist.md)" in allowed_tools
    assert "edit(./.cafe/issues/issue-spec-tools/spec/iteration_001/questions.xml)" in allowed_tools


def test_generic_workflow_step_restores_develop_runtime_allowed_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-develop-tools"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read", "Edit", "Write", "Grep", "Glob", "Bash", "WebFetch", "WebSearch"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-develop-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("develop", playbook["steps"]["develop"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "edit" in allowed_tools
    assert "write" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "bash" in allowed_tools
    assert "ls" in allowed_tools
    assert "web_fetch" in allowed_tools
    assert "web_search" in allowed_tools


def test_generic_workflow_step_restores_review_runtime_allowed_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review-tools"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"reviewer": {"default_agent": "Richard"}},
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "output_artifact": "review_feedback",
                "allowed_tools": ["Read", "Grep", "Glob", "Bash(git:*)"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("review")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"reviewer": "Richard"},
    )

    executor.execute_step("review", playbook["steps"]["review"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "ls" in allowed_tools
    assert "web_fetch" in allowed_tools
    assert "web_search" in allowed_tools
    assert "bash(git:*)" in allowed_tools
    assert "bash(git log)" in allowed_tools
    assert "bash(git diff)" in allowed_tools
    assert "bash(git show)" in allowed_tools
    assert "bash(git status)" in allowed_tools
    assert "edit(./.cafe/issues/issue-review-tools/review/iteration_001/output.md)" in allowed_tools
    assert "edit(./.cafe/issues/issue-review-tools/review/iteration_001/checklist.md)" in allowed_tools


def test_generic_workflow_step_restores_pr_runtime_allowed_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-tools"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "input_artifacts": ["spec", "plan", "review_feedback"],
                "output_artifact": "pr_result",
                "allowed_tools": ["Read", "Edit", "Write", "Grep", "Glob", "Bash", "WebFetch", "WebSearch"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    review_file = issue_dir / "review" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    review_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    review_file.write_text("# Review\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    store.set_artifact(state, "review_feedback", str(review_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "edit" in allowed_tools
    assert "write" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "bash" in allowed_tools
    assert "ls" in allowed_tools
    assert "web_fetch" in allowed_tools
    assert "web_search" in allowed_tools
    assert "edit(./.cafe/issues/issue-pr-tools/pr/iteration_001/output.md)" in allowed_tools
    assert "edit(./.cafe/issues/issue-pr-tools/pr/iteration_001/checklist.md)" in allowed_tools


def test_generic_workflow_step_pr_does_not_require_status_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-statusless"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Nick"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("pr")
    state.handoff_summary = "Reopen PR and complete the local artifact before host-side publish."
    state.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path="spec/iteration_001/output.md",
    )
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/iteration_001/output.md",
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-statusless",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("local artifact updated"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "Nick"},
    )

    captured: dict[str, object] = {}

    def fake_execute_agent_iteration(self, **kwargs):
        captured["require_status_code"] = kwargs["require_status_code"]
        return "local artifact updated", None

    executor._execute_agent_iteration = MethodType(fake_execute_agent_iteration, executor)

    result = executor.execute_step("pr", playbook["steps"]["pr"], state)

    assert captured["require_status_code"] is False
    assert result.status_code is None
    assert not (issue_dir / "pr" / "status.json").exists()


def test_generic_workflow_step_pr_does_not_parse_status_from_response(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-no-parse"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Nick"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("pr")
    state.handoff_summary = "Refresh the local PR artifact only."
    state.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path="spec/iteration_001/output.md",
    )
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/iteration_001/output.md",
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-no-parse",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("unused"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "Nick"},
    )

    def fake_execute(*args, **kwargs):
        return GenericPhaseExecution(
            response="confirmed",
            status_code=None,
            goto_target=None,
            context_updates={},
            events=[],
        )

    executor.generic_phase.execute = fake_execute

    result = executor.execute_step("pr", playbook["steps"]["pr"], state)

    assert result.response == "confirmed"
    assert result.status_code is None
    assert all(event.get("type") != "handoff_intent" for event in result.events)


def test_generic_workflow_step_pr_aligns_baton_when_execute_returns_needs_changes(
    tmp_path: Path, monkeypatch
) -> None:
    """align_pr_baton_after_execution must run for pr even though require_status_code is False."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-baton-integrate"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Nick"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "on": {"manual_handoff": "develop"},
            },
            "develop": {"skill": "develop", "role": "developer", "allowed_tools": ["Read"]},
        },
    }
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "pr",
                "to_owner": "agent",
                "to_step": "pr",
                "intent": "await_agent",
                "status_code": "",
                "created_at": "2026-05-11T12:00:00+08:00",
                "source": "test",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state = BlackboardStore(issue_dir).load_or_create("pr")
    state.handoff_summary = "PR feedback requires follow-up."
    state.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path="spec/iteration_001/output.md",
    )
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/iteration_001/output.md",
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-baton-integrate",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("unused"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "Nick"},
    )

    def fake_execute(*args, **kwargs):
        return GenericPhaseExecution(
            response="needs_changes",
            status_code=PhaseStatusCode.NEEDS_CHANGES,
            goto_target=None,
            context_updates={},
            events=[],
        )

    executor.generic_phase.execute = fake_execute

    result = executor.execute_step("pr", playbook["steps"]["pr"], state)

    assert result.status_code == "needs_changes"
    reloaded = BlackboardStore(issue_dir).load_or_create("pr")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_step == "develop"
    assert reloaded.handoff_contract.to_owner == HandoffOwner.AGENT
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_generic_workflow_step_pr_prompt_uses_baton_wording(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-prompt"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Nick"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("pr")
    state.handoff_summary = "Reopen PR and complete the local artifact before host-side publish."
    state.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path="spec/iteration_001/output.md",
    )
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/iteration_001/output.md",
    )
    agent_manager = FakeAgentManager("local artifact updated")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-prompt",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "Nick"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    assert any("Before finishing this step" in prompt for prompt in agent_manager.prompts)
    assert any(
        "Do NOT finish this step until ALL checklist items are marked as [x]." in prompt
        for prompt in agent_manager.prompts
    )
    assert not any("Before returning a status code" in prompt for prompt in agent_manager.prompts)


def test_generic_workflow_step_applies_phase_specific_model_per_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-models"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}, "developer": {"default_agent": "David"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "plan",
                "allowed_tools": ["Read"],
                "input_artifacts": ["spec"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")

    def _mark_checklist_complete(*, streaming_output_file, **kwargs) -> None:
        iteration_dir = Path(streaming_output_file).parent
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("- [x] completed\n", encoding="utf-8")

    agent_manager = FakeAgentManager(
        ["confirmed", "confirmed"],
        on_execute=_mark_checklist_complete,
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-models",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger", "developer": "David"},
        role_configs={
            "pm": {"spec": {"model": "gpt-5.4"}},
            "developer": {"plan": {"model": "claude-opus-4.6"}},
        },
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    store.set_artifact(state, "spec", str(spec_file))
    executor.execute_step("plan", playbook["steps"]["plan"], state)

    assert "--model" in (agent_manager.preview_calls[0] or [])
    assert "gpt-5.4" in (agent_manager.preview_calls[0] or [])
    assert "--model" in (agent_manager.preview_calls[1] or [])
    assert "claude-opus-4.6" in (agent_manager.preview_calls[1] or [])


def test_generic_workflow_step_develop_confirmed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-develop-ready"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed", "need_clarification"],
                "on": {"await_agent": "review", "need_clarification": "develop"},
            },
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))

    def _mark_checklist_complete(*, streaming_output_file, **kwargs) -> None:
        iteration_dir = Path(streaming_output_file).parent
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("- [x] completed\n", encoding="utf-8")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-develop-ready",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager(
            "confirmed",
            on_execute=_mark_checklist_complete,
        ),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "confirmed"
