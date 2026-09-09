"""Tests for non-software playbook loading and lightweight command coverage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.core.workflow_models import PlaybookRunResult
from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook
from cafe.skills.loader import SkillLoader
from cafe.ui.cli import app

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

runner = CliRunner()


def _example_root() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "non_software" / "editorial"


def test_editorial_example_skills_and_playbook_validate() -> None:
    example_root = _example_root()

    skills = SkillLoader(project_root=example_root).discover()
    playbook = PlaybookLoader(project_root=example_root).load_model("editorial").model

    assert {item.name for item in skills} >= {
        "cafe-brief_first",
        "cafe-brief_revise",
        "cafe-draft",
        "cafe-editorial_review",
        "cafe-publish",
    }
    assert playbook.entry_point == "brief"
    assert list(playbook.steps.keys()) == ["brief", "draft", "review", "publish"]


def test_editorial_example_playbook_loads_and_simulates(monkeypatch) -> None:
    example_root = _example_root()
    monkeypatch.chdir(example_root)
    loaded = PlaybookLoader(project_root=example_root).load_model("editorial", strict=True)
    analysis = analyze_playbook(loaded.model)

    assert loaded.model.playbook.id == "editorial"
    assert analysis.unreachable_steps == ()
    assert analysis.dead_end_steps == ()
    assert analysis.missing_intent_handlers == ()
    assert analysis.entry_point == "brief"
    assert loaded.model.playbook.applicability is not None
    assert loaded.automatic_selection_eligible is True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_builtin_non_software_playbooks_cli_validate(monkeypatch) -> None:
    monkeypatch.chdir(_repo_root())
    loader = PlaybookLoader()
    for name in ("editorial", "research", "incident"):
        loaded = loader.load_model(name, strict=True)
        assert loaded.model.playbook.id == name


def test_builtin_non_software_playbooks_simulate_reports_no_findings(monkeypatch) -> None:
    monkeypatch.chdir(_repo_root())
    loader = PlaybookLoader()
    for name in ("editorial", "research", "incident"):
        result = analyze_playbook(loader.load_model(name).model)
        assert result.unreachable_steps == ()
        assert result.dead_end_steps == ()
        assert result.missing_intent_handlers == ()


def test_builtin_non_software_workflow_dry_run_accepts_editorial(monkeypatch) -> None:
    monkeypatch.chdir(_repo_root())
    blackboard_path = _repo_root() / ".cafe" / "issues" / "issue253" / "blackboard.json"
    original_blackboard = (
        blackboard_path.read_bytes() if blackboard_path.exists() else None
    )
    with (
        patch("cafe.ui.commands.workflow._get_GitOperations") as mock_git_factory,
        patch("cafe.ui.commands.workflow.BlackboardWorkflowRuntime") as mock_runtime_cls,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue253"
        mock_git_factory.return_value = git
        mock_runtime_cls.return_value.run.return_value = PlaybookRunResult(
            final_step="publish",
            final_status_code="confirmed",
            completed=True,
        )
        result = runner.invoke(
            app,
            ["workflow", "--playbook", "editorial", "--dry-run", "--issue", "issue253"],
        )

    assert result.exit_code == 0
    assert "Ownership plan (read-only)" in result.stdout
    assert (
        blackboard_path.read_bytes() if blackboard_path.exists() else None
    ) == original_blackboard


def test_builtin_non_software_agent_defaults_on_disk() -> None:
    root = _repo_root()
    expected = [
        root / "src/cafe/data/agents/editor/Roger.md",
        root / "src/cafe/data/agents/writer/David.md",
        root / "src/cafe/data/agents/researcher/Morgan.md",
        root / "src/cafe/data/agents/ops/Casey.md",
    ]
    for path in expected:
        assert path.is_file(), f"missing agent file: {path}"
