"""CLI tests for cafe playbook validate with prepare metadata."""

from pathlib import Path

from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


def test_playbook_validate_succeeds_for_builtin_prepare_playbooks() -> None:
    for name in (
        "default",
        "direct",
        "simple",
        "standard",
        "standard-qa",
        "tdd",
        "tdd-qa",
        "hotfix",
    ):
        result = runner.invoke(app, ["playbook", "validate", name])
        assert result.exit_code == 0, result.stdout
        assert "Valid" in result.stdout
        assert name in result.stdout


def test_playbook_validate_accepts_project_override_without_prepare(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    cafe_dir = project_root / ".cafe" / "playbooks"
    cafe_dir.mkdir(parents=True)
    (cafe_dir / "custom.yaml").write_text(
        """
playbook: {id: custom}
steps:
  spec:
    role: pm
    skill: cafe-spec
    on:
      await_agent: _done
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["playbook", "validate", "custom"])

    assert result.exit_code == 0, result.stdout
    assert "Valid custom" in result.stdout
