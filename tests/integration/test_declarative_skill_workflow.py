"""Integration coverage for a non-development skill declared entirely in metadata."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cafe.core.blackboard import BlackboardStore
from cafe.core.types import AgentCLI, TokenUsage
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge


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
