"""Tests for non-software custom playbook validation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui.cli import app


runner = CliRunner()


def _example_root() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "non_software" / "editorial"


def test_editorial_example_skills_and_playbook_validate() -> None:
    example_root = _example_root()

    skills = SkillLoader(project_root=example_root).discover()
    playbook = PlaybookLoader(project_root=example_root).load_model("editorial").model

    assert {item.name for item in skills} >= {
        "brief_first",
        "brief_revise",
        "draft",
        "editorial_review",
        "publish",
    }
    assert playbook.entry_point == "brief"
    assert list(playbook.steps.keys()) == ["brief", "draft", "review", "publish"]


def test_editorial_example_cli_validate_and_dry_run(monkeypatch) -> None:
    example_root = _example_root()
    monkeypatch.chdir(example_root)

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "editorial-issue"
        mock_git_cls.return_value = git

        validate_result = runner.invoke(app, ["playbook", "validate", "editorial"])
        dry_run_result = runner.invoke(app, ["workflow", "--playbook", "editorial", "--dry-run"])

    assert validate_result.exit_code == 0
    assert "Valid editorial" in validate_result.stdout
    assert dry_run_result.exit_code == 0
    assert "Workflow completed" in dry_run_result.stdout


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_builtin_non_software_playbooks_cli_validate(monkeypatch) -> None:
    monkeypatch.chdir(_repo_root())
    for name in ("editorial", "research", "incident"):
        result = runner.invoke(app, ["playbook", "validate", name])
        assert result.exit_code == 0, result.stdout
        assert f"Valid {name}" in result.stdout


def test_builtin_non_software_playbooks_simulate_reports_no_findings(monkeypatch) -> None:
    monkeypatch.chdir(_repo_root())
    for name in ("editorial", "research", "incident"):
        result = runner.invoke(app, ["playbook", "simulate", name])
        assert result.exit_code == 0
        assert "(no findings)" in result.stdout


def test_builtin_non_software_playbooks_workflow_dry_run(monkeypatch) -> None:
    monkeypatch.chdir(_repo_root())
    with patch("cafe.ui.commands.workflow._get_GitOperations") as mock_git_factory:
        git = MagicMock()
        git.get_current_branch.return_value = "issue253"
        mock_git_factory.return_value = git
        for playbook_name in ("editorial", "research", "incident"):
            result = runner.invoke(
                app,
                [
                    "workflow",
                    "--playbook",
                    playbook_name,
                    "--dry-run",
                    "--issue",
                    "issue253",
                ],
            )
            assert result.exit_code == 0
            assert "Workflow completed" in result.stdout


def test_builtin_non_software_playbook_load_strict(monkeypatch) -> None:
    monkeypatch.chdir(_repo_root())
    loader = PlaybookLoader()
    for name in ("editorial", "research", "incident"):
        loaded = loader.load_model(name, strict=True)
        assert loaded.model.playbook.id == name


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
