"""Tests for direct workflow step execution."""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cafe.core.blackboard import BlackboardStore
from cafe.core.types import AgentCLI, TokenUsage
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge


class FakeAgentManager:
    def __init__(self, response: str | list[str], on_execute=None) -> None:
        if isinstance(response, list):
            self._responses: Iterator[str] = iter(response)
        else:
            self._responses = iter([response])
        self.prompts: list[str] = []
        self.on_execute = on_execute

    def get_agent(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(config=SimpleNamespace(cli=AgentCLI.CODEX, session_id="session-1"))

    def execute(
        self,
        agent_name: str,
        prompt: str,
        allowed_tools=None,
        allowed_directories=None,
        streaming_output_file=None,
    ):
        self.prompts.append(prompt)
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

    def get_commits_between(self, base: str, head: str) -> str:
        return "abc123 test commit"


def _build_loader(tmp_path: Path) -> GenericPhase:
    skill_root = tmp_path / "builtin" / "skills"
    for name, body in {
        "spec_first": "## Role\nRead your agent file: {agent_file}\n\n## Context\n{blackboard_digest}\n",
        "plan": "Write plan to: {output_file}\n\n{status_code_instruction}\n",
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
    return GenericPhase(loader, skill_bridge=NativeSkillBridge(loader, home_dir=tmp_path / "home"))


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
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
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
        agent_manager=FakeAgentManager("CAFE_CONFIRMED"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert result.response == "CAFE_CONFIRMED"
    assert "spec" in result.artifacts
    iteration_dir = issue_dir / "spec" / "iteration_001"
    assert (iteration_dir / "context.json").exists()
    assert (iteration_dir / "checklist.md").exists()
    assert (iteration_dir / "output.md").exists()
    assert (iteration_dir / "artifact.json").exists()
    assert (issue_dir / "spec" / "status.json").exists()


def test_generic_workflow_step_executor_uses_iteration_specific_skill_mapping(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-2"
    phase_dir = issue_dir / "spec" / "iteration_001"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / "context.json").write_text('{"iteration": 1, "status_code": "CAFE_CONFIRMED"}', encoding="utf-8")
    (issue_dir / "spec" / "status.json").write_text(
        '{"phase":"spec","status":"completed","status_code":"CAFE_CONFIRMED","timestamp":"2026-04-09T00:00:00+08:00","iteration":1}',
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
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            }
        },
    }

    class RecordingPhase(GenericPhase):
        def __init__(self, loader):
            super().__init__(loader)
            self.skill_names: list[str] = []

        def execute(self, **kwargs):
            self.skill_names.append(kwargs["skill_name"])
            return super().execute(**kwargs)

    state = BlackboardStore(issue_dir).load_or_create("spec")
    generic_phase = RecordingPhase(_build_loader(tmp_path).skill_loader)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-2",
        playbook=playbook,
        generic_phase=generic_phase,
        agent_manager=FakeAgentManager("CAFE_CONFIRMED"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert generic_phase.skill_names == ["plan"]


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
                "valid_status_codes": ["CAFE_NEED_CLARIFICATION", "CAFE_CONFIRMED"],
                "hooks": {"prepare_input": ["UserInputCollector"]},
                "on": {
                    "CAFE_NEED_CLARIFICATION": "spec",
                    "CAFE_CONFIRMED": "_done",
                },
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    def _write_questions_xml(*, response: str, streaming_output_file: str | None, **_: object) -> None:
        if response != "CAFE_NEED_CLARIFICATION" or not streaming_output_file:
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
        ["CAFE_NEED_CLARIFICATION", "CAFE_CONFIRMED"],
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
    assert first_result.response == "CAFE_NEED_CLARIFICATION"
    assert first_result.auto_continue is False

    with patch(
        "cafe.core.hooks.native.interactive_qa_flow",
        return_value="Q1: Which flow should we support first?\nA1: CLI only",
    ):
        second_result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert second_result.response == "CAFE_CONFIRMED"
    assert any(
        "Current user input for this iteration:" in prompt and "A1: CLI only" in prompt
        for prompt in agent_manager.prompts
    )
    user_input_file = issue_dir / "spec" / "iteration_002" / "user_input.md"
    assert user_input_file.read_text(encoding="utf-8") == (
        "Q1: Which flow should we support first?\nA1: CLI only"
    )


def test_generic_workflow_step_auto_continues_pause_statuses_in_interactive_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review"
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    (spec_dir / "context.json").write_text(
        '{"iteration":1,"status_code":"CAFE_CONFIRMED"}',
        encoding="utf-8",
    )
    (issue_dir / "spec" / "status.json").write_text(
        '{"phase":"spec","status":"completed","status_code":"CAFE_CONFIRMED","iteration":1}',
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
                "valid_status_codes": ["CAFE_READY_FOR_REVIEW"],
                "on": {"CAFE_READY_FOR_REVIEW": "plan"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("plan")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("CAFE_READY_FOR_REVIEW"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=True,
    )

    result = executor.execute_step("plan", playbook["steps"]["plan"], state)

    assert result.response == "CAFE_READY_FOR_REVIEW"
    assert result.auto_continue is True
