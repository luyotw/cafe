"""Test that checklist validation preserves status code from iteration.json context."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import AgentCLI
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge


def _build_loader(tmp_path: Path) -> GenericPhase:
    skill_root = tmp_path / "builtin" / "skills"
    for name, body in {
        "plan": "Write plan to: {output_file}\n",
        "workflow-common": "Read blackboard first.\n",
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


@pytest.fixture
def plan_executor(tmp_path: Path) -> GenericWorkflowStepExecutor:
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True, exist_ok=True)
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "test-issue"
    git_ops.get_main_branch.return_value = "main"
    git_ops.get_default_base_branch.return_value = "main"
    git_ops.get_commits_between.return_value = ""

    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Roger"}},
        "steps": {
            "plan": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "plan",
                "on": {"await_agent": "develop"},
            }
        },
    }

    manager = MagicMock()
    manager.get_agent.return_value = SimpleNamespace(
        config=SimpleNamespace(cli=AgentCLI.COPILOT, session_id=None, model=None)
    )

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="test-issue",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=manager,
        git_ops=git_ops,
        role_agent_map={"developer": "Roger"},
        interactive=False,
    )
    executor.phase_dir = issue_dir / "plan"
    executor.issue_dir = issue_dir
    executor.iteration = 1
    return executor


class TestChecklistValidationPreservesStatusCode:
    def test_returns_status_code_when_context_response_has_it(
        self, plan_executor: GenericWorkflowStepExecutor, tmp_path: Path
    ) -> None:
        iteration_dir = plan_executor._get_iteration_dir(1)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "checklist.md").write_text("## Checklist\n\n[x] Task 1\n[x] Task 2\n")
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"response": "Done.\n\nneed_clarification"}),
            encoding="utf-8",
        )

        _response, status_code, passed = plan_executor._validate_and_retry_checklist_completion(
            agent_name="Roger",
            prompt="test prompt",
            user_input="",
            valid_intents=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
        )

        assert passed is True
        assert status_code == PhaseStatusCode.NEED_CLARIFICATION

    def test_returns_none_when_context_response_missing_status_code(
        self, plan_executor: GenericWorkflowStepExecutor, tmp_path: Path
    ) -> None:
        iteration_dir = plan_executor._get_iteration_dir(1)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "checklist.md").write_text("## Checklist\n\n[x] Task 1\n[x] Task 2\n")
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"response": "I'm working on the spec analysis..."}),
            encoding="utf-8",
        )

        _response, status_code, passed = plan_executor._validate_and_retry_checklist_completion(
            agent_name="Roger",
            prompt="test prompt",
            user_input="",
            valid_intents=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
        )

        assert passed is True
        assert status_code is None
