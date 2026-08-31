"""Integration journeys for declared initial-input providers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore
from cafe.core.types import AgentCLI, TokenUsage
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.ui.cli import app
from cafe.utils.phase_config import PhaseStepModelResolution

runner = CliRunner()


@pytest.fixture(autouse=True)
def _configured_test_phase_chain(monkeypatch):
    """Keep input-provider journeys focused on their public boundary."""
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


def _write_phase_chains(tmp_path: Path) -> None:
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    (cafe_dir / "phases.yaml").write_text(
        "intake:\n  name: David\n  clis:\n    - cli: codex\n      model: test-model\n"
        "spec:\n  name: David\n  clis:\n    - cli: codex\n      model: test-model\n",
        encoding="utf-8",
    )


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
        return (
            "# Result\n\nGOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001\n\n"
            "## Downstream Contract\n\n"
            "- Contract-Version: `1`\n"
            "- Artifact-Kind: `spec`\n\n"
            "### Goals\n"
            "| ID | Statement |\n| --- | --- |\n| GOAL-001 | Goal |\n\n"
            "### Non-Goals\n"
            "| ID | Statement |\n| --- | --- |\n| NONGOAL-001 | None |\n\n"
            "### Acceptance Criteria\n"
            "| ID | Priority | Statement |\n| --- | --- | --- |\n"
            "| AC-001 | must | Accepted |\n\n"
            "### Invariants\n"
            "| ID | Statement |\n| --- | --- |\n| INV-001 | Safe |\n\n"
            "### Trust Boundaries\n"
            "| ID | Statement |\n| --- | --- |\n| TRUST-001 | Local |\n",
            TokenUsage(),
            [],
            [],
            [],
            None,
        )


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


def _write_intake_playbook(tmp_path: Path) -> None:
    playbooks_dir = tmp_path / ".cafe" / "playbooks"
    playbooks_dir.mkdir(parents=True, exist_ok=True)
    (playbooks_dir / "intake.yaml").write_text(
        """
playbook:
  id: intake
roles:
  researcher:
    default_agent: Morgan
entry_point: intake
steps:
  intake:
    type: skill
    skill: intake
    role: researcher
    input_artifacts: []
    output_artifact: intake_brief
    initial_input:
      providers: [manual_text, github_issue]
      bind:
        artifact: intake_brief
        prompt_context: user_input
    hooks:
      prepare_input: [InitialInputProviderResolver]
    valid_intents: [confirmed]
    "on": {await_agent: _done}
commands:
  prepare:
    fields:
      - id: input_method
        type: enum
        label: Input method
        write: intake.input_method
        required: true
        choices:
          - value: manual
            label: Manual input
          - value: github
            label: GitHub issue
      - id: github_issue_id
        type: text
        label: GitHub issue ID
        write: intake.issue_id
        normalize: github_issue
""".lstrip(),
        encoding="utf-8",
    )


def _prepare_intake_issue(
    tmp_path: Path,
    *,
    input_method: str,
    issue_id: int | None = None,
    step_user_inputs: dict[str, str] | None = None,
) -> tuple[GenericWorkflowStepExecutor, _AgentManager, Path, dict[str, object]]:
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    _write_phase_chains(tmp_path)
    _write_skill(tmp_path / ".cafe" / "skills", "intake")
    _write_intake_playbook(tmp_path)
    config_path = tmp_path / ".cafe" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["playbook"] = "intake"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    mock_git = MagicMock()
    mock_git.get_current_branch.return_value = "main"
    mock_git.has_uncommitted_changes.return_value = False
    mock_git.branch_exists.return_value = False
    mock_git.worktree_exists.return_value = False
    command = [
        "prepare",
        "intake-journey",
        "--no-interactive",
        f"--input-method={input_method}",
    ]
    if issue_id is not None:
        command.append(f"--issue-id={issue_id}")
    with (
        patch("cafe.ui.cli.GitOperations", return_value=mock_git),
        patch("cafe.utils.git_utils.is_github_repo", return_value=True),
        patch("cafe.ui.phase_prompts.is_github_repo", return_value=True),
    ):
        result = runner.invoke(app, command)
    assert result.exit_code == 0, result.stdout

    builtin_root = tmp_path / "builtin"
    for name in ("cafe-workflow-common", "cafe-github_sync"):
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
    issue_dir = tmp_path / ".cafe" / "issues" / "intake-journey"
    playbook = PlaybookLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    ).load("intake")
    step = playbook["steps"]["intake"]
    assert (issue_dir / "intake").is_dir()
    assert not (issue_dir / "spec").exists()
    prepared_config = yaml.safe_load((issue_dir / "issue.yaml").read_text(encoding="utf-8"))
    assert prepared_config["initial_input"]["provider"] == (
        "github_issue" if input_method == "github" else "manual_text"
    )
    manager = _AgentManager()
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="intake-journey",
        playbook=playbook,
        generic_phase=generic_phase,
        agent_manager=manager,
        git_ops=_GitOps(),
        role_agent_map={"researcher": "Morgan"},
        step_user_inputs=step_user_inputs,
    )
    return executor, manager, issue_dir, step


def _prepare_builtin_issue(
    tmp_path: Path, *, playbook_id: str
) -> tuple[GenericWorkflowStepExecutor, _AgentManager, Path, dict[str, object]]:
    """Prepare one built-in workflow for its first-step compatibility journey."""
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    _write_phase_chains(tmp_path)
    config_path = tmp_path / ".cafe" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["playbook"] = playbook_id
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    mock_git = MagicMock()
    mock_git.get_current_branch.return_value = "main"
    mock_git.has_uncommitted_changes.return_value = False
    mock_git.branch_exists.return_value = False
    mock_git.worktree_exists.return_value = False
    issue_name = f"{playbook_id}-legacy-input"
    with (
        patch("cafe.ui.cli.GitOperations", return_value=mock_git),
        patch("cafe.utils.git_utils.is_github_repo", return_value=True),
        patch("cafe.ui.phase_prompts.is_github_repo", return_value=True),
    ):
        result = runner.invoke(
            app,
            ["prepare", issue_name, "--no-interactive", "--input-method=manual"],
        )
    assert result.exit_code == 0, result.stdout

    issue_dir = tmp_path / ".cafe" / "issues" / issue_name
    playbook = PlaybookLoader(project_root=tmp_path).load(playbook_id)
    step = playbook["steps"]["spec"]
    manager = _AgentManager()
    loader = SkillLoader(project_root=tmp_path)
    loader.discover()
    generic_phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name=issue_name,
        playbook=playbook,
        generic_phase=generic_phase,
        agent_manager=manager,
        git_ops=_GitOps(),
        role_agent_map={"pm": "Roger"},
    )
    return executor, manager, issue_dir, step


def test_custom_manual_intake_delivers_one_input_to_artifact_and_agent(
    tmp_path: Path, monkeypatch
) -> None:
    """I1 — custom prepare and loader deliver manual input to an intake entry step."""
    monkeypatch.chdir(tmp_path)
    content = "Collect the customer's incident timeline."
    executor, manager, issue_dir, step = _prepare_intake_issue(
        tmp_path,
        input_method="manual",
        step_user_inputs={"intake": content},
    )
    state = BlackboardStore(issue_dir).load_or_create("intake")

    executor.execute_step("intake", step, state)

    output = issue_dir / "intake" / "iteration_001" / "output.md"
    assert content in output.read_text(encoding="utf-8")
    assert "Current user input for this iteration:\n" + content in manager.prompts[0]
    assert not (issue_dir / "spec").exists()


def test_custom_github_intake_uses_trusted_host_boundary_once(tmp_path: Path, monkeypatch) -> None:
    """I2 — custom prepare and loader deliver trusted GitHub input to intake."""
    monkeypatch.chdir(tmp_path)
    content = "**Issue Title:** Intake request\n\nCollect source material."
    executor, manager, issue_dir, step = _prepare_intake_issue(
        tmp_path,
        input_method="github",
        issue_id=346,
    )
    state = BlackboardStore(issue_dir).load_or_create("intake")

    with patch(
        "cafe.core.hooks.native.InitialInputProviderResolver._fetch_github_issue",
        return_value=content,
    ) as fetch:
        executor.execute_step("intake", step, state)

    fetch.assert_called_once_with(346)
    output = issue_dir / "intake" / "iteration_001" / "output.md"
    assert content in output.read_text(encoding="utf-8")
    assert "Current user input for this iteration:\n" + content in manager.prompts[0]


def test_builtin_workflows_prepare_and_seed_their_first_spec_step(
    tmp_path: Path, monkeypatch
) -> None:
    """I3 — the shared built-in contract preserves prepare and first-step seeding."""
    monkeypatch.chdir(tmp_path)
    playbook_id = "standard"
    executor, manager, issue_dir, step = _prepare_builtin_issue(
        tmp_path, playbook_id=playbook_id
    )
    config = yaml.safe_load((issue_dir / "issue.yaml").read_text(encoding="utf-8"))
    assert config["spec"]["input_method"] == "manual"
    assert config["initial_input"] == {"provider": "manual_text"}

    state = BlackboardStore(issue_dir).load_or_create("spec")
    result = executor.execute_step("spec", step, state)

    output = issue_dir / "spec" / "iteration_001" / "output.md"
    assert result.artifacts["spec"] == str(output)
    assert output.read_text(encoding="utf-8").strip()
    assert len(manager.prompts) == 1


def test_invalid_initial_input_declaration_stops_at_cli_validation_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    """I4 — invalid declarations fail before agent or GitHub-provider execution."""
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".cafe" / "skills", "intake")
    playbooks_dir = tmp_path / ".cafe" / "playbooks"
    playbooks_dir.mkdir(parents=True, exist_ok=True)
    (playbooks_dir / "invalid-intake.yaml").write_text(
        """
playbook: {id: invalid-intake}
entry_point: intake
steps:
  intake:
    role: researcher
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [github_issue]
      bind: {artifact: ""}
    hooks:
      prepare_input: [InitialInputProviderResolver]
    "on": {await_agent: _done}
""".lstrip(),
        encoding="utf-8",
    )

    with (
        patch("cafe.core.hooks.native.InitialInputProviderResolver._fetch_github_issue") as fetch,
        patch("cafe.ui.cli.AgentManager") as agents,
    ):
        result = runner.invoke(app, ["playbook", "validate", "invalid-intake"])

    assert result.exit_code == 1
    assert "bind.artifact" in result.stdout
    fetch.assert_not_called()
    agents.assert_not_called()
