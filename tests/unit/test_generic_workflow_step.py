"""Tests for direct workflow step execution."""

from pathlib import Path
from types import SimpleNamespace

from cafe.core.blackboard import BlackboardStore
from cafe.core.types import TokenUsage
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader


class FakeAgentManager:
    def __init__(self, response: str) -> None:
        self.response = response

    def get_agent(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(config=SimpleNamespace(cli=SimpleNamespace(value="codex"), session_id="session-1"))

    def execute(
        self,
        agent_name: str,
        prompt: str,
        allowed_tools=None,
        allowed_directories=None,
        streaming_output_file=None,
    ):
        return self.response, TokenUsage(), [], [], [], None


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
    return GenericPhase(loader)


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

    response, artifacts = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert response == "CAFE_CONFIRMED"
    assert "spec" in artifacts
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
