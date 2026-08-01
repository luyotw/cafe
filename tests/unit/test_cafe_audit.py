"""Tests for ``cafe audit`` builtin tooling checks."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cafe.agents.manager import AgentManager
from cafe.audit.tooling_audit import (
    AuditLine,
    cafe_builtin_data_dir,
    format_audit_markdown,
    run_builtin_tooling_audit,
)
from cafe.core.blackboard import BlackboardState
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor


def test_run_builtin_tooling_audit_all_pass() -> None:
    lines = run_builtin_tooling_audit()
    assert lines, "expected non-empty audit checklist"
    assert all(row.ok for row in lines), format_audit_markdown(lines)


def test_builtin_audit_treats_declared_support_skills_as_referenced() -> None:
    """I1 — support skills are audited for reachability, not phase placeholders."""
    lines = run_builtin_tooling_audit()
    messages = [row.message for row in lines]

    assert any("cafe-workflow-common" in message for message in messages)
    assert not any(
        "Skill cafe-workflow-common: markdown bundle must mention placeholder" in message
        for message in messages
    )


def test_format_audit_markdown_includes_checkbox() -> None:
    text = format_audit_markdown([AuditLine(True, "ok")])
    assert "[x]" in text


def test_build_context_uses_playbook_role_for_agent_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Editorial and research roles must map to matching agent directories."""
    recorded: list[tuple[str, str]] = []

    @classmethod
    def fake_get(
        cls: type[AgentManager],
        agent_name: str,
        role: str,
        cafe_dir: str | None = None,
    ) -> str:
        recorded.append((agent_name, role))
        return f"agents/{role}/{agent_name}.md"

    monkeypatch.setattr(AgentManager, "get_agent_file_path", fake_get)

    executor = GenericWorkflowStepExecutor.__new__(GenericWorkflowStepExecutor)
    executor.issue_dir = Path("/tmp/issue")
    executor.iteration = 1
    state = BlackboardState(current_step="draft")
    ctx = GenericWorkflowStepExecutor._build_context(
        executor,
        step_name="draft",
        step_def={"role": "writer", "skill": "draft", "input_artifacts": []},
        blackboard_state=state,
        agent_name="David",
        output_file=Path("/tmp/out.md"),
    )
    assert ctx["agent_file"] == "agents/writer/David.md"
    assert recorded == [("David", "writer")]


def test_run_builtin_tooling_audit_injected_gap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing an agent file from the builtin tree produces a failing row."""
    import cafe.audit.tooling_audit as audit_mod

    real_data = cafe_builtin_data_dir()
    fake_data = (tmp_path / "data").resolve()
    shutil.copytree(real_data, fake_data)

    # Delete the PM agent file so the spec step's agent-exists check fails.
    pm_agent = fake_data / "agents" / "pm" / "Roger.md"
    pm_agent.unlink()

    # Patch the module-level function so all callers in the module use the fake tree.
    monkeypatch.setattr(audit_mod, "cafe_builtin_data_dir", lambda: fake_data)

    lines = run_builtin_tooling_audit()
    failing = [row for row in lines if not row.ok]
    assert failing, "expected at least one failing row after removing an agent file"
    report = format_audit_markdown(lines)
    assert "[ ]" in report


def test_builtin_agents_layout_covers_playbook_roles() -> None:
    """Sanity: every role directory used in builtin playbooks exists under data/agents."""
    data = cafe_builtin_data_dir()
    roles = {"pm", "developer", "reviewer", "researcher", "ops", "editor", "writer"}
    for role in roles:
        assert (data / "agents" / role).is_dir(), f"missing agents/{role}"
