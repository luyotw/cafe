"""I1-I5 user journeys through catalog CLI and filesystem boundaries."""

import json
from pathlib import Path

from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


def _write_project_catalog(project: Path) -> None:
    playbook = project / ".cafe" / "playbooks" / "standard.yaml"
    playbook.parent.mkdir(parents=True, exist_ok=True)
    playbook.write_text("playbook: {id: standard}\nsteps: {}\n", encoding="utf-8")
    skill = project / ".cafe" / "skills" / "develop" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\nname: develop\ndescription: project\n---\n\nDevelop\n",
        encoding="utf-8",
    )
    agent = project / ".cafe" / "agents" / "developer" / "David.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(
        "---\nname: David\ndescription: project\n---\n\nDevelop\n",
        encoding="utf-8",
    )


def test_manual_subset_approval_publishes_only_previewed_project_content(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    _write_project_catalog(project)
    preview = runner.invoke(app, ["catalog", "check", "--json"])
    payload = json.loads(preview.stdout)

    result = runner.invoke(
        app,
        [
            "catalog",
            "sync-global",
            "--token",
            payload["comparison_token"],
            "--approve",
            "playbook:standard",
            "--approve",
            "agent:developer/David",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (global_root / "playbooks" / "standard.yaml").read_bytes() == (
        project / ".cafe" / "playbooks" / "standard.yaml"
    ).read_bytes()
    assert (global_root / "agents" / "developer" / "David.md").is_file()
    assert not (global_root / "skills" / "develop").exists()


def test_changed_project_after_preview_requires_a_fresh_cli_comparison(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    _write_project_catalog(project)
    preview = runner.invoke(app, ["catalog", "check", "--json"])
    token = json.loads(preview.stdout)["comparison_token"]
    playbook = project / ".cafe" / "playbooks" / "standard.yaml"
    playbook.write_text(playbook.read_text(encoding="utf-8") + "changed: true\n")

    result = runner.invoke(
        app,
        [
            "catalog",
            "sync-global",
            "--token",
            token,
            "--approve",
            "playbook:standard",
        ],
    )

    assert result.exit_code == 1
    assert not (global_root / "playbooks" / "standard.yaml").exists()
