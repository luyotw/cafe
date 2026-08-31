"""I1-I5 user journeys through catalog CLI and filesystem boundaries."""

import json
import shutil
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from cafe.catalogs.resolver import CatalogKind, CatalogResolver
from cafe.catalogs.sync import CatalogSyncService
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


def test_linked_worktree_preview_and_sync_use_the_effective_project_overlay(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = tmp_path / "canonical"
    linked = tmp_path / "linked"
    global_root = tmp_path / "global"
    canonical.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=canonical, check=True, capture_output=True
    )
    _write_project_catalog(canonical)
    subprocess.run(["git", "add", "."], cwd=canonical, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "seed catalogs",
        ],
        cwd=canonical,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "-b", "issue", str(linked)],
        cwd=canonical,
        check=True,
        capture_output=True,
    )
    active_phase = linked / ".cafe" / "skills" / "develop" / "SKILL.md"
    active_phase.write_text(
        "---\nname: develop\ndescription: active overlay\n---\n\nDevelop\n",
        encoding="utf-8",
    )
    canonical_only = canonical / ".cafe" / "playbooks" / "canonical-only.yaml"
    canonical_only.write_text(
        "playbook: {id: canonical-only}\nsteps: {}\n", encoding="utf-8"
    )
    command_directory = linked / "nested" / "command-directory"
    command_directory.mkdir(parents=True)
    monkeypatch.chdir(command_directory)
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)

    preview = runner.invoke(app, ["catalog", "check", "--json"])
    payload = json.loads(preview.stdout)
    entries = {item["entry_id"]: item for item in payload["entries"]}

    assert preview.exit_code == 0
    assert payload["project_roots"] == [str(canonical), str(linked)]
    assert entries["playbook:canonical-only"]["project_path"].startswith(
        str(canonical)
    )
    assert entries["phase:develop"]["project_path"].startswith(str(linked))

    synced = runner.invoke(
        app,
        [
            "catalog",
            "sync-global",
            "--token",
            payload["comparison_token"],
            "--approve",
            "phase:develop",
            "--json",
        ],
    )

    assert synced.exit_code == 0, synced.stdout
    assert (global_root / "skills" / "develop" / "SKILL.md").read_bytes() == (
        active_phase.read_bytes()
    )
    assert "description: project" in (
        canonical / ".cafe" / "skills" / "develop" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_mixed_catalog_cli_failure_rolls_back_the_complete_selection(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    project.mkdir()
    _write_project_catalog(project)
    old_playbook = global_root / "playbooks" / "standard.yaml"
    old_playbook.parent.mkdir(parents=True)
    old_playbook.write_text(
        "playbook: {id: standard}\nsteps: {}\nmarker: old\n", encoding="utf-8"
    )
    old_phase = global_root / "skills" / "develop" / "SKILL.md"
    old_phase.parent.mkdir(parents=True)
    old_phase.write_text(
        "---\nname: develop\ndescription: old\n---\n\nOld\n", encoding="utf-8"
    )
    expected = (old_playbook.read_bytes(), old_phase.read_bytes())

    def fail(boundary: str, entry_id: str | None) -> None:
        if boundary == "published" and entry_id == "phase:develop":
            raise OSError("injected mixed-catalog failure")

    service = CatalogSyncService(
        CatalogResolver(
            project_root=project,
            canonical_root=project,
            global_root=global_root,
        ),
        failure_injector=fail,
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        "cafe.ui.commands.catalog._build_catalog_service", lambda: service
    )
    preview = runner.invoke(app, ["catalog", "check", "--json"])
    token = json.loads(preview.stdout)["comparison_token"]

    result = runner.invoke(
        app,
        [
            "catalog",
            "sync-global",
            "--token",
            token,
            "--approve",
            "playbook:standard",
            "--approve",
            "phase:develop",
        ],
    )

    assert result.exit_code == 1
    assert (old_playbook.read_bytes(), old_phase.read_bytes()) == expected
    receipts = list((global_root / ".catalog-transactions").glob("*/recovery.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["status"] == "rolled_back"


def test_existing_project_agents_resolve_compare_and_publish_without_prerequisite(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    project.mkdir()
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
    )
    builtin = resolver.resolve(CatalogKind.AGENT, "developer/David")
    matching = project / ".cafe" / "agents" / "developer" / "David.md"
    matching.parent.mkdir(parents=True)
    shutil.copy2(builtin.path, matching)
    customized = project / ".cafe" / "agents" / "pm" / "Roger.md"
    customized.parent.mkdir(parents=True)
    customized.write_text(
        "---\nname: Roger\ndescription: custom\n---\n\nCustom\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)

    assert resolver.resolve(CatalogKind.AGENT, "developer/David").path == matching
    assert resolver.resolve(CatalogKind.AGENT, "pm/Roger").path == customized

    preview = runner.invoke(app, ["catalog", "check", "--kind", "agent", "--json"])
    payload = json.loads(preview.stdout)
    entries = {
        item["entry_id"]: item["project_path"] for item in payload["entries"]
    }
    assert entries == {
        "agent:developer/David": str(matching),
        "agent:pm/Roger": str(customized),
    }

    synced = runner.invoke(
        app,
        [
            "catalog",
            "sync-global",
            "--kind",
            "agent",
            "--token",
            payload["comparison_token"],
            "--approve",
            "agent:developer/David",
            "--approve",
            "agent:pm/Roger",
            "--json",
        ],
    )

    assert synced.exit_code == 0, synced.stdout
    assert (global_root / "agents" / "developer" / "David.md").read_bytes() == (
        matching.read_bytes()
    )
    assert (global_root / "agents" / "pm" / "Roger.md").read_bytes() == (
        customized.read_bytes()
    )
    assert matching.is_file()
    assert customized.is_file()
