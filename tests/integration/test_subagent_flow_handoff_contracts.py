"""Exercise persisted planning and curated PR handoffs without an agent service."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.checklist import ProjectedTodo
from cafe.core.hooks import InitialInputProviderResolver
from cafe.core.todo import parse_todo_list, plan_work_fingerprint
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.checklist_composer import compose_effective_checklist
from cafe.skills.contracts import resolve_prompt_inputs
from cafe.skills.loader import SkillLoader

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def _executor(issue_dir: Path, name: str) -> GenericWorkflowStepExecutor:
    return GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name=issue_dir.name,
        playbook=PlaybookLoader().load(name, strict=True),
        generic_phase=GenericPhase(SkillLoader()),
        agent_manager=MagicMock(),
        git_ops=MagicMock(),
        role_agent_map={"developer": "David"},
    )


def _publish_plan(executor, store, state, content: str) -> ArtifactEntry:
    executor.phase_name = "spec_plan"
    executor.phase_dir = executor.issue_dir / "spec_plan"
    executor.iteration = state.artifacts["plan"].version + 1 if "plan" in state.artifacts else 1
    output = executor._get_iteration_dir(executor.iteration) / "output.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    artifact = executor._write_artifact_record(
        blackboard_state=state,
        output_key="plan",
        output_path=str(output),
        updated_by="spec_plan",
    )
    state.artifacts["plan"] = artifact
    store.save(state)
    return artifact


@pytest.mark.parametrize("name", ["subagent-flow", "subagent-flow-qa"])
@pytest.mark.parametrize("has_detailed_baseline", [False, True])
def test_early_clarification_survives_cold_resume_and_preserves_plan_identity(
    tmp_path: Path, name: str, has_detailed_baseline: bool
) -> None:
    issue_dir = tmp_path / "issue"
    executor = _executor(issue_dir, name)
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec_plan")
    original = "Build a parser; preserve existing command output."
    step = executor.playbook["steps"]["spec_plan"]
    executor.iteration = 1
    seeded = issue_dir / "spec_plan" / "iteration_001" / "output.md"

    InitialInputProviderResolver().run(
        stage="prepare_input",
        phase=executor,
        step_name="spec_plan",
        step_def=step,
        output_file=seeded,
        context={"user_input": original},
    )
    assert seeded.read_text(encoding="utf-8").strip() == original
    # A restart of the first iteration must not replace the original with an answer.
    restarted = _executor(issue_dir, name)
    restarted.iteration = 1
    InitialInputProviderResolver().run(
        stage="prepare_input",
        phase=restarted,
        step_name="spec_plan",
        step_def=step,
        output_file=seeded,
        context={"user_input": "Only accept JSON."},
    )
    assert seeded.read_text(encoding="utf-8").strip() == original

    detailed = (
        "<!-- plan-stage: detailed-plan -->\n"
        "## Todo List\n"
        "- [ ] `PLAN-007` — Source: `plan` — Work: build parser — "
        "Closure: command output is preserved — Evidence: parser regression\n"
    )
    prior = _publish_plan(executor, store, state, detailed) if has_detailed_baseline else None
    template = executor._get_skill_loader().get_skill_dir("cafe-spec_plan")
    provisional = (template / "references" / "clarification_draft.md").read_text(encoding="utf-8")
    provisional = provisional.replace(
        "[Preserve the original request and supplied issue title verbatim.]", original
    ).replace("[Record the missing decisions and material scope or tradeoffs.]", "Which format?")

    for answer in ("Only accept JSON.", "Reject malformed JSON with the existing error format."):
        _publish_plan(executor, store, state, provisional)
        # Reconstruct both host executor and blackboard, with only the current answer.
        resumed = _executor(issue_dir, name)
        resumed.step_user_inputs = {"spec_plan": answer}
        state = BlackboardStore(issue_dir).load_or_create("spec_plan")
        selected = resumed._step_input_artifacts(step, state)
        resumed._prepare_todo_identity_input(step_def=step, input_artifacts=selected)
        composition = resumed._effective_workflow_composition(
            step_name="spec_plan", step_def=step, skill_name=step["skill"]
        )
        inputs = resolve_prompt_inputs(composition.as_declaration(), selected)
        saved = Path(inputs["prior_plan_file"]).read_text(encoding="utf-8")
        assert original in saved
        baseline = selected["plan"].todo_identity_baseline
        assert baseline is not None
        if prior is None:
            assert baseline["artifact"] is None
        else:
            assert baseline["artifact"]["path"] == prior.path
            assert baseline["artifact"]["content_sha256"] == prior.content_sha256
        provisional = saved.replace("## Known Decisions\n", f"## Known Decisions\n{answer}\n")
        executor = resumed

    _publish_plan(executor, store, state, provisional)
    assert "Only accept JSON." in Path(state.artifacts["plan"].path).read_text(encoding="utf-8")
    if has_detailed_baseline:
        with pytest.raises(ValueError, match="retain the existing ID"):
            _publish_plan(executor, store, state, detailed.replace("PLAN-007", "PLAN-008"))
    complete = _publish_plan(executor, store, state, detailed)
    assert complete.todo_identity_baseline is None
    assert complete.todo_work_identities == {plan_work_fingerprint("build parser"): "PLAN-007"}


@pytest.mark.parametrize("name", ["subagent-flow", "subagent-flow-qa"])
def test_declared_pr_curation_handoff_projects_mixed_feedback_without_relabeling(
    tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cafe.agents.manager import AgentManager

    monkeypatch.setattr(AgentManager, "read_agent_file", lambda *args: ("agent.md", ""))
    issue_dir = tmp_path / "issue"
    executor = _executor(issue_dir, name)
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    pr = executor.playbook["steps"]["pr"]
    executor.phase_name = "pr"
    executor.phase_dir = issue_dir / "pr"
    executor.iteration = 2
    catalog = json.loads(
        executor._build_route_context(step_name="pr", step_def=pr, blackboard_state=state)[
            "route_catalog"
        ]
    )
    assert not any(route.get("carries_feedback") for route in catalog["goto"])
    target = catalog["defaults"]["manual_handoff"]["to"]
    assert target == "develop"
    assert any(route["to"] == target for route in catalog["goto"])
    pr_instructions = (
        executor._get_skill_loader().get_skill_dir("cafe-pr") / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "defaults.manual_handoff" in pr_instructions

    store.update_handoff_contract(
        state,
        from_step="pr",
        to_owner=HandoffOwner.AGENT,
        to_step=target,
        intent=HandoffIntent.MANUAL_HANDOFF,
        source="test.curated_feedback",
    )
    output = issue_dir / "pr" / "iteration_002" / "output.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "## Todo List\n- [ ] Raw review text without canonical fields\n", encoding="utf-8"
    )

    def validate():
        return executor._validate_outbound_causal_todo(
            step_name="pr",
            step_def=pr,
            blackboard_state=state,
            output_file=output,
            response="",
            status_code=None,
            auto_continue=False,
        )

    passed, detail, required = validate()
    assert not passed and required
    assert "Todo List" in detail
    output.write_text(
        "## Todo List\n"
        "- [ ] `PRC-003` — Source: `pr_comment` — Work: fix parser — "
        "Closure: malformed input rejected — Evidence: parser regression\n"
        "- [ ] `WF-011` — Source: `workflow_feedback` — Work: preserve output — "
        "Closure: command output unchanged — Evidence: command regression\n",
        encoding="utf-8",
    )
    assert validate() == (True, "", True)
    curated = executor._write_artifact_record(
        blackboard_state=state,
        output_key="pr_result",
        output_path=str(output),
        updated_by="pr",
    )
    raw = issue_dir / "raw_feedback.md"
    raw.write_text("Raw historical feedback must not become the worklist.", encoding="utf-8")
    state.artifacts = {
        "pr_result": curated,
        "workflow_feedback": ArtifactEntry(
            name="workflow_feedback",
            kind=ArtifactKind.DOCUMENT,
            version=1,
            updated_by="pr",
            path=str(raw),
        ),
    }
    state.current_step = target
    store.save(state)
    state = BlackboardStore(issue_dir).load_or_create(target)
    develop = executor.playbook["steps"][target]
    composition = executor._effective_workflow_composition(
        step_name=target, step_def=develop, skill_name=develop["skill"]
    )
    artifacts, feedback = executor._checklist_inputs(
        composition, executor._step_input_artifacts(develop, state), state
    )
    assert feedback
    assert artifacts["causal_todo"] is state.artifacts["pr_result"]
    checklist = tmp_path / "checklist.md"
    ledger = tmp_path / "develop_output.md"
    context = resolve_prompt_inputs(composition.as_declaration(), artifacts)
    context["output_file"] = str(ledger)
    materialized = compose_effective_checklist(
        composition=composition,
        agent_name="David",
        role="developer",
        checklist_file_path=checklist,
        iteration=2,
        context=context,
        artifacts=artifacts,
        feedback=feedback,
        todo_ledger_path=ledger,
        skill_loader=executor._get_skill_loader(),
    )
    assert materialized.projections[0]["path"] == curated.path
    assert materialized.projections[0]["producer_ids"] == ["PRC-003", "WF-011"]
    items = parse_todo_list(output.read_text(encoding="utf-8"))
    assert [(item.item_id, item.source) for item in items] == [
        ("PRC-003", "pr_comment"),
        ("WF-011", "workflow_feedback"),
    ]
    for handle, row, item in zip(
        materialized.projections[0]["handles"], materialized.projections[0]["rows"], items
    ):
        assert handle.split("__")[0] == item.item_id
        assert row == ProjectedTodo(handle, item).checklist_row()
        assert row in checklist.read_text(encoding="utf-8")
