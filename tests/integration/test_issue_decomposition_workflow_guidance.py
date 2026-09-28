"""Playbook declaration reaches arbitrary step prompts without a PR stage."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore
from cafe.core.playbook import PlaybookDefinition, resolve_step_behavior
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader

DATA_ROOT = Path(__file__).resolve().parents[2] / "src" / "cafe" / "data"


@pytest.mark.parametrize("enabled", [True, False])
def test_custom_step_decomposition_survives_context_and_execution(tmp_path, monkeypatch, enabled):
    custom_dir = tmp_path / ".cafe" / "skills" / "cafe-observe"
    custom_dir.mkdir(parents=True)
    (custom_dir / "SKILL.md").write_text(
        "---\nname: cafe-observe\ndescription: Inspect the request.\n---\n"
        "Write observations to {output_file}.\n",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path, global_root=tmp_path / "global", builtin_root=DATA_ROOT
    )
    phase = GenericPhase(loader)
    model = PlaybookDefinition.model_validate(
        {
            "playbook": {"id": "custom-observation"},
            "behavior": {"allow_issue_decomposition": True},
            "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
            "steps": {
                "observe": {
                    "role": "observer",
                    "skill": "cafe-observe",
                    "input_artifacts": [],
                    "behavior": {"allow_issue_decomposition": enabled},
                    "on": {"await_agent": "_done"},
                }
            },
        }
    )
    playbook = model.model_dump(mode="json")
    issue_dir = tmp_path / ".cafe" / "issues" / "sample"
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="sample",
        playbook=playbook,
        generic_phase=phase,
        agent_manager=Mock(),
        git_ops=Mock(),
        role_agent_map={},
    )
    monkeypatch.setattr(AgentManager, "read_agent_file", lambda *args: ("test", "Observe."))
    state = BlackboardStore(issue_dir).load_or_create("observe")
    step = playbook["steps"]["observe"]
    output = issue_dir / "observe" / "iteration_001" / "output.md"
    context = executor._build_context(
        step_name="observe",
        step_def=step,
        blackboard_state=state,
        agent_name="Observer",
        output_file=output,
    )
    prompts = []

    def capture(prompt):
        prompts.append(prompt)
        return "CAFE_CONFIRMED"

    phase.execute(
        skill_name="cafe-observe",
        skill_invocation="/cafe-observe",
        step_def=step,
        context=context,
        output_file=output,
        agent_executor=capture,
    )
    assert len(prompts) == 1
    assert context["allow_issue_decomposition"] is enabled
    assert ("# Issue Split Proposals" in prompts[0]) is enabled
    if enabled:
        guidance = loader.get_reference("cafe-workflow-common", "issue_decomposition.md").strip()
        assert guidance in prompts[0]
        # Both examples reach the agent; this does not assert an LLM's decisions.
        assert "CSV export and configurable email alerts" in prompts[0]
        assert "an API field, its UI display, and tests" in prompts[0]
    assert step["on"] == {"await_agent": "_done"}
    assert step["capability_requests"] == []
    assert not (issue_dir / "human_tasks.json").exists()


def test_bundled_playbooks_explicitly_enable_decomposition(
    tmp_path, cached_builtin_playbook_models
):
    loader = PlaybookLoader(
        project_root=tmp_path, global_root=tmp_path / "global", builtin_root=DATA_ROOT
    )
    for path in (DATA_ROOT / "playbooks").glob("*.yaml"):
        model = loader.load_model(path.stem, strict=True).model
        assert model.behavior.allow_issue_decomposition is True
        for step in model.steps:
            assert resolve_step_behavior(model, step).allow_issue_decomposition is True
