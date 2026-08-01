"""Integration journeys for declared initial-input providers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


def _write_skill(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n", encoding="utf-8"
    )


def _intake_executor(
    tmp_path: Path,
    *,
    initial_input: dict[str, object],
    step_user_inputs: dict[str, str] | None = None,
) -> tuple[GenericWorkflowStepExecutor, _AgentManager, Path, dict[str, object]]:
    builtin_root = tmp_path / "builtin"
    for name in ("cafe-workflow-common", "cafe-github_sync"):
        _write_skill(builtin_root / "skills", name)
    _write_skill(tmp_path / ".cafe" / "skills", "intake")
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
    issue_dir = tmp_path / ".cafe" / "issues" / "intake-journey"
    issue_dir.mkdir(parents=True)
    step: dict[str, object] = {
        "skill": "intake",
        "role": "researcher",
        "input_artifacts": [],
        "output_artifact": "intake_brief",
        "initial_input": {
            "providers": ["manual_text", "github_issue"],
            "bind": {"artifact": "intake_brief", "prompt_context": "user_input"},
        },
        "hooks": {"prepare_input": ["InitialInputProviderResolver"]},
        "valid_intents": ["confirmed"],
        "on": {"await_agent": "_done"},
    }
    (issue_dir / "issue.yaml").write_text(
        "initial_input:\n"
        + "\n".join(f"  {key}: {value}" for key, value in initial_input.items())
        + "\n",
        encoding="utf-8",
    )
    manager = _AgentManager()
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="intake-journey",
        playbook={
            "playbook": {"id": "intake"},
            "roles": {"researcher": {"default_agent": "David"}},
            "entry_point": "intake",
            "steps": {"intake": step},
        },
        generic_phase=generic_phase,
        agent_manager=manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "David"},
        step_user_inputs=step_user_inputs,
    )
    return executor, manager, issue_dir, step


def test_custom_manual_intake_delivers_one_input_to_artifact_and_agent(tmp_path: Path) -> None:
    """I1 — a non-development entry step receives declared manual input."""
    content = "Collect the customer's incident timeline."
    executor, manager, issue_dir, step = _intake_executor(
        tmp_path,
        initial_input={"provider": "manual_text"},
        step_user_inputs={"intake": content},
    )
    state = BlackboardStore(issue_dir).load_or_create("intake")

    executor.execute_step("intake", step, state)

    output = issue_dir / "intake" / "iteration_001" / "output.md"
    assert content in output.read_text(encoding="utf-8")
    assert "Current user input for this iteration:\n" + content in manager.prompts[0]
    assert not (issue_dir / "spec").exists()


def test_custom_github_intake_uses_trusted_host_boundary_once(tmp_path: Path) -> None:
    """I2 — a non-development entry step receives host-resolved GitHub input."""
    content = "**Issue Title:** Intake request\n\nCollect source material."
    executor, manager, issue_dir, step = _intake_executor(
        tmp_path,
        initial_input={"provider": "github_issue", "issue_id": 346},
    )
    state = BlackboardStore(issue_dir).load_or_create("intake")

    with patch(
        "cafe.core.hooks.native.GitHubIssueFetcher._fetch_github_issue", return_value=content
    ) as fetch:
        executor.execute_step("intake", step, state)

    fetch.assert_called_once_with(346)
    output = issue_dir / "intake" / "iteration_001" / "output.md"
    assert content in output.read_text(encoding="utf-8")
    assert "Current user input for this iteration:\n" + content in manager.prompts[0]
