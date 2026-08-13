"""Integration coverage for a non-development skill declared entirely in metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from types import SimpleNamespace

from cafe.core.blackboard import BlackboardStore
from cafe.core.downstream_contract import ContractValidationError
from cafe.core.types import AgentCLI, TokenUsage
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.utils.phase_config import PhaseStepModelResolution


@pytest.fixture(autouse=True)
def _configured_test_phase_chain(monkeypatch):
    """Keep skill journeys focused on metadata with a valid execution chain."""
    from cafe.phases import generic_workflow_step

    real_loader = generic_workflow_step.load_phase_step_model

    def load_test_phase(*, step_name, local_path, repo_path=None):
        if any(path is not None and path.exists() for path in (local_path, repo_path)):
            return real_loader(step_name=step_name, local_path=local_path, repo_path=repo_path)
        return PhaseStepModelResolution(
            name=None,
            role=None,
            clis=(("codex", "gpt-5-test"),),
            model="gpt-5-test",
            source="test",
            chain=("test",),
            clis_source="test",
        )

    monkeypatch.setattr(generic_workflow_step, "load_phase_step_model", load_test_phase)


class _AgentManager:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.agent = SimpleNamespace(
            config=SimpleNamespace(cli=AgentCLI.CODEX, session_id=None, model=None)
        )

    def get_agent(self, _name: str):
        return self.agent

    def execute(self, _name: str, prompt: str, **_kwargs):
        self.prompts.append(prompt)
        return "confirmed", TokenUsage(), [], [], [], None


class _GitOps:
    def get_default_base_branch(self) -> str:
        return "main"

    def get_commits_between(self, *, base: str, head: str) -> str:
        return ""


def _write_skill(root: Path, name: str, body: str = "") -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n{body}", encoding="utf-8"
    )


@pytest.fixture(autouse=True)
def _configured_phase_chains(tmp_path: Path) -> None:
    """Custom workflow journeys declare every executable test step explicitly."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    steps = ("synthesis", "assemble", "spec", "plan", "develop", "review", "pr", "run")
    (cafe_dir / "phases.yaml").write_text(
        "".join(
            f"{step}:\n  name: David\n  clis:\n    - cli: codex\n      model: test-model\n"
            for step in steps
        ),
        encoding="utf-8",
    )


def test_custom_synthesis_step_uses_declared_artifact_checklist_and_template(
    tmp_path: Path, monkeypatch
) -> None:
    """A custom skill runs with its named input, feedback checklist, and selected catalog file."""
    monkeypatch.chdir(tmp_path)
    builtin_root = tmp_path / "builtin"
    for name in ("cafe-workflow-common", "cafe-github_sync"):
        _write_skill(builtin_root / "skills", name)

    skill_dir = tmp_path / ".cafe" / "skills" / "synthesis"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "assets" / "templates").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: synthesis
description: synthesis
workflow:
  prompt_inputs:
    - artifacts: [research_notes]
      placeholder: evidence_file
      required: true
    - artifacts: [editor_feedback]
      placeholder: feedback_file
      required: false
  checklist:
    variants:
      - when: {artifact_present: [editor_feedback]}
        sections: [{reference: feedback.md}, {template_catalog: true}]
      - when: {}
        sections: [{reference: first.md}, {template_catalog: true}]
    include_role_guidance: false
  output_templates:
    catalog: synthesis-report
---

Read {evidence_file} and use {template_file}.
""",
        encoding="utf-8",
    )
    (skill_dir / "references" / "first.md").write_text(
        "[ ] Read {evidence_file}\n", encoding="utf-8"
    )
    (skill_dir / "references" / "feedback.md").write_text(
        "[ ] Address {feedback_file} using {evidence_file}\n", encoding="utf-8"
    )
    selected_template = skill_dir / "assets" / "templates" / "evidence.md"
    selected_template.write_text("# Evidence report\n", encoding="utf-8")

    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    loader.discover()
    generic_phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "synthesis-test"
    (issue_dir / "issue.yaml").parent.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("synthesis:\n  template: evidence\n", encoding="utf-8")
    state = BlackboardStore(issue_dir).load_or_create("synthesis")
    research = issue_dir / "research" / "iteration_001" / "output.md"
    feedback = issue_dir / "editorial" / "iteration_001" / "output.md"
    research.parent.mkdir(parents=True)
    feedback.parent.mkdir(parents=True)
    research.write_text("evidence", encoding="utf-8")
    feedback.write_text("clarify sources", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    store.set_artifact(state, "research_notes", str(research))
    store.set_artifact(state, "editor_feedback", str(feedback))
    manager = _AgentManager()
    step = {
        "skill": "synthesis",
        "role": "researcher",
        "output_artifact": "report",
        "allowed_tools": ["Read"],
        "valid_intents": ["confirmed"],
        "on": {"await_agent": "_done"},
    }
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="synthesis-test",
        playbook={
            "playbook": {"id": "synthesis"},
            "roles": {"researcher": {"default_agent": "David"}},
            "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
            "steps": {"synthesis": step},
        },
        generic_phase=generic_phase,
        agent_manager=manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "David"},
    )

    executor.execute_step("synthesis", step, state)

    checklist = (issue_dir / "synthesis" / "iteration_001" / "checklist.md").read_text(
        encoding="utf-8"
    )
    assert "editorial/iteration_001/output.md" in checklist
    assert "research/iteration_001/output.md" in checklist
    assert "evidence.md" in checklist
    context = executor._build_context(
        step_name="synthesis",
        step_def=step,
        blackboard_state=state,
        agent_name="David",
        output_file=issue_dir / "synthesis" / "iteration_001" / "output.md",
    )
    activated = loader.activate("synthesis", context)
    assert "research/iteration_001/output.md" in activated
    assert "evidence.md" in activated


def test_custom_executor_preserves_full_inputs_and_falls_back_from_damaged_packet_source(
    tmp_path: Path, monkeypatch
) -> None:
    """IT-003/IT-004 — real custom execution keeps full authority and fails closed."""
    monkeypatch.chdir(tmp_path)
    builtin_root = tmp_path / "builtin"
    skill_dir = builtin_root / "skills" / "assembly"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: assembly
description: assembly
workflow:
  prompt_inputs:
    - artifacts: [brief_packet]
      placeholder: compact_brief
      required: true
      load_policy:
        - mode: packet
          contract_kind: spec
    - artifacts: [full_record]
      placeholder: full_record
      required: true
---

Use {compact_brief} with {full_record}.
""",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    loader.discover()
    phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "packet-journey"
    packet_source = issue_dir / "brief" / "iteration_001" / "output.md"
    full_record = issue_dir / "record" / "iteration_001" / "output.md"
    packet_source.parent.mkdir(parents=True)
    full_record.parent.mkdir(parents=True)
    packet_source.write_text(
        """# Brief

BODY-ONLY-SENTINEL GOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001

## Downstream Contract

- Contract-Version: `1`
- Artifact-Kind: `spec`

### Goals
| ID | Statement |
| --- | --- |
| GOAL-001 | Goal |
### Non-Goals
| ID | Statement |
| --- | --- |
| NONGOAL-001 | No |
### Acceptance Criteria
| ID | Priority | Statement |
| --- | --- | --- |
| AC-001 | must | Yes |
### Invariants
| ID | Statement |
| --- | --- |
| INV-001 | Safe |
### Trust Boundaries
| ID | Statement |
| --- | --- |
| TRUST-001 | Local |
""",
        encoding="utf-8",
    )
    full_record.write_text("AUTHORITATIVE-FULL-RECORD", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("assemble")
    store.set_artifact(state, "brief_packet", str(packet_source))
    store.set_artifact(state, "full_record", str(full_record))
    step = {
        "skill": "assembly",
        "role": "researcher",
        "input_artifacts": ["brief_packet", "full_record"],
        "output_artifact": "compiled_report",
        "allowed_tools": ["Read"],
        "valid_intents": ["confirmed"],
        "on": {"await_agent": "_done"},
    }
    playbook = {
        "playbook": {"id": "arbitrary-packet-journey"},
        "roles": {"researcher": {"default_agent": "David"}},
        "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
        "steps": {"assemble": step},
    }

    first_manager = _AgentManager()
    first = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="packet-journey",
        playbook=playbook,
        generic_phase=phase,
        agent_manager=first_manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "David"},
    )
    first.execute_step("assemble", step, state)

    packet_path = issue_dir / "assemble" / "iteration_001" / "context_compact_brief.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert "BODY-ONLY-SENTINEL" not in packet["contract"]["bytes"]
    assert "compact_brief=packet" in first_manager.prompts[0]
    assert "full_record=full" in first_manager.prompts[0]
    assert str(full_record) in first_manager.prompts[0]

    # A syntactically valid source revision must not reuse a packet whose
    # persisted provenance still identifies the previous source bytes.
    packet_source.write_text(
        packet_source.read_text(encoding="utf-8").replace(
            "| GOAL-001 | Goal |", "| GOAL-001 | Revised goal |"
        ),
        encoding="utf-8",
    )
    next_packet_path = packet_path.parent.parent / "iteration_002" / packet_path.name
    next_packet_path.parent.mkdir(parents=True)
    stale_packet = json.loads(packet_path.read_text(encoding="utf-8"))
    stale_packet["target"]["iteration"] = 2
    next_packet_path.write_text(
        json.dumps(stale_packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stale_source_manager = _AgentManager()
    stale_source_executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="packet-journey",
        playbook=playbook,
        generic_phase=phase,
        agent_manager=stale_source_manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "David"},
    )
    stale_source_executor.execute_step("assemble", step, state)

    assert "compact_brief=full_fallback" in stale_source_manager.prompts[0]
    assert str(packet_source) in stale_source_manager.prompts[0]

    tampered_packet_path = packet_path.parent.parent / "iteration_003" / packet_path.name
    tampered_packet_path.parent.mkdir(parents=True)
    tampered_packet_path.write_text("{tampered packet", encoding="utf-8")
    tampered_packet_manager = _AgentManager()
    tampered_packet_executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="packet-journey",
        playbook=playbook,
        generic_phase=phase,
        agent_manager=tampered_packet_manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "David"},
    )
    tampered_packet_executor.execute_step("assemble", step, state)

    assert "compact_brief=full_fallback" in tampered_packet_manager.prompts[0]
    assert str(packet_source) in tampered_packet_manager.prompts[0]

    packet_source.write_text("# legacy source without a contract\n", encoding="utf-8")
    second_manager = _AgentManager()
    second = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="packet-journey",
        playbook=playbook,
        generic_phase=phase,
        agent_manager=second_manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "David"},
    )
    with pytest.raises(ContractValidationError):
        second.execute_step("assemble", step, state)

    packet_source.write_text(
        """# Brief

GOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001

## Downstream Contract

- Contract-Version: `1`
- Artifact-Kind: `spec`

### Goals
| ID | Statement |
| --- | --- |
### Non-Goals
| ID | Statement |
| --- | --- |
| NONGOAL-001 | No |
### Acceptance Criteria
| ID | Priority | Statement |
| --- | --- | --- |
| AC-001 | must | Yes |
### Invariants
| ID | Statement |
| --- | --- |
| INV-001 | Safe |
### Trust Boundaries
| ID | Statement |
| --- | --- |
| TRUST-001 | Local |
""",
        encoding="utf-8",
    )
    empty_table_manager = _AgentManager()
    empty_table_executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="packet-journey",
        playbook=playbook,
        generic_phase=phase,
        agent_manager=empty_table_manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "David"},
    )
    with pytest.raises(ContractValidationError):
        empty_table_executor.execute_step("assemble", step, state)


def test_packaged_workflow_uses_full_then_packet_then_legacy_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """IT-001/IT-002/IT-006 — packaged stages retain full host authority."""
    monkeypatch.chdir(tmp_path)
    root = Path(__file__).resolve().parents[2]
    source_root = root / "src" / "cafe" / "data"
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=source_root,
    )
    loader.discover()
    class CapturingPhase(GenericPhase):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.host_contexts: list[dict[str, object]] = []

        def execute(self, **kwargs):
            self.host_contexts.append(dict(kwargs["hook_context"]))
            return super().execute(**kwargs)

    phase = CapturingPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )
    playbook = PlaybookLoader().load("default")
    issue_dir = tmp_path / ".cafe" / "issues" / "packaged-packet-journey"
    spec = issue_dir / "spec" / "iteration_001" / "output.md"
    plan = issue_dir / "plan" / "iteration_001" / "output.md"
    code = issue_dir / "develop" / "iteration_001" / "output.md"
    feedback = issue_dir / "review" / "iteration_001" / "output.md"
    for artifact in (spec, plan, code, feedback):
        artifact.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "BODY-ONLY-PACKAGED-SENTINEL\n"
        + (source_root / "skills/cafe-spec/assets/templates/default.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    plan.write_text(
        (source_root / "skills/cafe-plan/assets/templates/default.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    code.write_text("implementation evidence", encoding="utf-8")
    feedback.write_text("review evidence", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    for name, artifact in (("spec", spec), ("plan", plan), ("code", code)):
        store.set_artifact(state, name, str(artifact))

    def run(step_name: str) -> _AgentManager:
        manager = _AgentManager()
        GenericWorkflowStepExecutor(
            issue_dir=issue_dir,
            issue_name="packaged-packet-journey",
            playbook=playbook,
            generic_phase=phase,
            agent_manager=manager,
            git_ops=_GitOps(),
            role_agent_map={"developer": "David", "reviewer": "Richard"},
        ).execute_step(step_name, playbook["steps"][step_name], state)
        return manager

    first_develop = run("develop")
    assert "spec_file=full" in first_develop.prompts[0]
    assert "plan_file=full" in first_develop.prompts[0]

    store.set_artifact(state, "review_feedback", str(feedback))
    correction_develop = run("develop")
    review = run("review")
    pr = run("pr")
    pr_follow_up = run("pr")
    for prompt in (
        correction_develop.prompts[0],
        review.prompts[0],
        pr.prompts[0],
        pr_follow_up.prompts[0],
    ):
        assert "spec_file=packet" in prompt
        assert "plan_file=packet" in prompt
    spec_packets = list(issue_dir.glob("**/context_spec_file.json"))
    plan_packets = list(issue_dir.glob("**/context_plan_file.json"))
    assert spec_packets and plan_packets
    spec_packet = json.loads(spec_packets[-1].read_text(encoding="utf-8"))["contract"]["bytes"]
    plan_packet = json.loads(plan_packets[-1].read_text(encoding="utf-8"))["contract"]["bytes"]
    assert "BODY-ONLY-PACKAGED-SENTINEL" not in spec_packet
    for identifier in ("GOAL-001", "NONGOAL-001", "AC-001", "INV-001", "TRUST-001"):
        assert identifier in spec_packet
    assert "### Test List" in plan_packet
    assert "### Dependency ADR References" in plan_packet
    assert "ADR-001" in plan_packet
    assert "| TASK-001 | pending |" in plan_packet
    final_host_inputs = phase.host_contexts[-1]["authoritative_inputs"]
    assert Path(final_host_inputs["spec_file"]).resolve() == spec
    assert Path(final_host_inputs["plan_file"]).resolve() == plan

    spec.write_text("# Legacy confirmed artifact\n", encoding="utf-8")
    with pytest.raises(ContractValidationError):
        run("develop")


def test_workflow_replace_removes_stale_native_skills(tmp_path: Path, monkeypatch) -> None:
    """I2 — a replace declaration leaves only the current workflow environment."""
    monkeypatch.chdir(tmp_path)
    builtin_root = tmp_path / "builtin"
    for name in ("phase", "stale-support", "replacement-support"):
        _write_skill(builtin_root / "skills", name)

    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    loader.discover()
    generic_phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )
    step = {
        "skill": "phase",
        "role": "operator",
        "output_artifact": "report",
        "allowed_tools": ["Read"],
        "valid_intents": ["confirmed"],
        "on": {"await_agent": "_done"},
    }

    def execute_with(playbook: dict) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / playbook["playbook"]["id"]
        state = BlackboardStore(issue_dir).load_or_create("run")
        executor = GenericWorkflowStepExecutor(
            issue_dir=issue_dir,
            issue_name=playbook["playbook"]["id"],
            playbook=playbook,
            generic_phase=generic_phase,
            agent_manager=_AgentManager(),
            git_ops=_GitOps(),
            role_agent_map={"operator": "David"},
        )
        executor.execute_step("run", step, state)

    execute_with(
        {
            "playbook": {"id": "workflow-base"},
            "roles": {"operator": {"default_agent": "David"}},
            "skills": {"workflow": {"shared": ["stale-support"]}, "chat": {"shared": []}},
            "steps": {"run": step},
        }
    )
    assert (tmp_path / ".codex" / "skills" / "stale-support").is_dir()

    execute_with(
        {
            "playbook": {"id": "workflow-replace"},
            "roles": {"operator": {"default_agent": "David"}},
            "skills": {
                "workflow": {
                    "shared": ["stale-support"],
                    "steps": {"run": {"mode": "replace", "skills": ["replacement-support"]}},
                },
                "chat": {"shared": []},
            },
            "steps": {"run": step},
        }
    )

    native_skills = tmp_path / ".codex" / "skills"
    assert not (native_skills / "stale-support").exists()
    assert (native_skills / "replacement-support" / "SKILL.md").is_file()
    assert (native_skills / "phase" / "SKILL.md").is_file()


def test_interrupted_custom_step_uses_replaced_declared_batch_scope(
    tmp_path: Path, monkeypatch
) -> None:
    """I1 — a resumed custom step receives only its latest declared batch source."""
    monkeypatch.chdir(tmp_path)
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "synthesis")
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    loader.discover()
    generic_phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "resumed-custom-batch"
    iteration_dir = issue_dir / "synthesis" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text(
        json.dumps({"cli": "codex", "session_id": "interrupted-session"}),
        encoding="utf-8",
    )
    replacement = issue_dir / "batches" / "batch-2.md"
    replacement.parent.mkdir(parents=True)
    replacement.write_text("CURRENT_BATCH_CONTENT", encoding="utf-8")
    historical = issue_dir / "history" / "batch-1.md"
    historical.parent.mkdir(parents=True)
    historical.write_text("HISTORICAL_BATCH_CONTENT", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("synthesis")
    state.handoff_summary = "Process batch 1 again."
    store.set_artifact(state, "batch_scope", str(replacement))
    store.set_artifact(state, "historical_output", str(historical))
    step = {
        "skill": "synthesis",
        "role": "researcher",
        "input_artifacts": ["batch_scope"],
        "output_artifact": "report",
        "allowed_tools": ["Read"],
        "valid_intents": ["confirmed"],
        "on": {"await_agent": "_done"},
    }
    manager = _AgentManager()
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="resumed-custom-batch",
        playbook={
            "playbook": {"id": "synthesis"},
            "roles": {"researcher": {"default_agent": "David"}},
            "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
            "steps": {"synthesis": step},
        },
        generic_phase=generic_phase,
        agent_manager=manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "David"},
    )
    executor.step_user_inputs["synthesis"] = "[system] Resume from where you left off."

    executor.execute_step("synthesis", step, state)

    prompt = manager.prompts[0]
    assert "Current resume scope (declared step inputs):" in prompt
    scope = prompt.split("Current resume scope (declared step inputs):", maxsplit=1)[1]
    scope = scope.split("Current user input for this iteration:", maxsplit=1)[0]
    assert str(replacement) in scope
    assert str(historical) not in scope
    assert "CURRENT_BATCH_CONTENT" not in prompt
    assert "Process batch 1 again." in prompt
    assert "[system] Resume from where you left off." in prompt


def test_execution_without_declared_scope_does_not_invent_resume_scope(
    tmp_path: Path, monkeypatch
) -> None:
    """I2 — fresh and resumed custom steps omit grounding without declared inputs."""
    monkeypatch.chdir(tmp_path)
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "synthesis")
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    loader.discover()
    generic_phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )
    step = {
        "skill": "synthesis",
        "role": "researcher",
        "input_artifacts": [],
        "output_artifact": "report",
        "allowed_tools": ["Read"],
        "valid_intents": ["confirmed"],
        "on": {"await_agent": "_done"},
    }

    def execute(issue_name: str, *, interrupted: bool) -> str:
        issue_dir = tmp_path / ".cafe" / "issues" / issue_name
        if interrupted:
            iteration_dir = issue_dir / "synthesis" / "iteration_001"
            iteration_dir.mkdir(parents=True)
            (iteration_dir / "iteration.json").write_text(
                json.dumps({"cli": "codex", "session_id": "interrupted-session"}),
                encoding="utf-8",
            )
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("synthesis")
        historical = issue_dir / "history" / "prior-output.md"
        historical.parent.mkdir(parents=True)
        historical.write_text("HISTORICAL_OUTPUT", encoding="utf-8")
        store.set_artifact(state, "historical_output", str(historical))
        manager = _AgentManager()
        executor = GenericWorkflowStepExecutor(
            issue_dir=issue_dir,
            issue_name=issue_name,
            playbook={
                "playbook": {"id": "synthesis"},
                "roles": {"researcher": {"default_agent": "David"}},
                "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
                "steps": {"synthesis": step},
            },
            generic_phase=generic_phase,
            agent_manager=manager,
            git_ops=_GitOps(),
            role_agent_map={"researcher": "David"},
        )
        executor.execute_step("synthesis", step, state)
        return manager.prompts[0]

    fresh_prompt = execute("fresh-custom-step", interrupted=False)
    resumed_prompt = execute("resumed-custom-step", interrupted=True)

    assert "Current resume scope (declared step inputs):" not in fresh_prompt
    assert "Current resume scope (declared step inputs):" not in resumed_prompt
    assert "HISTORICAL_OUTPUT" not in fresh_prompt
    assert "HISTORICAL_OUTPUT" not in resumed_prompt
