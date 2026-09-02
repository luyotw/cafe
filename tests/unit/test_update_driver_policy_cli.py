"""Explicit bounded driver-policy update command tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app


def _layout(tmp_path: Path) -> tuple[Path, Path, bytes]:
    root = tmp_path / ".cafe" / "issues" / "issue432" / "issue.yaml"
    worktree = tmp_path / ".cafe" / "worktrees" / "issue432"
    active = worktree / ".cafe" / "issues" / "issue432" / "issue.yaml"
    root.parent.mkdir(parents=True)
    active.parent.mkdir(parents=True)
    root.write_text(
        yaml.safe_dump({"issue_name": "issue432", "worktree_path": str(worktree)}),
        encoding="utf-8",
    )
    active.write_text(
        yaml.safe_dump(
            {
                "base_branch": "develop",
                "playbook_id": "standard-qa",
                "review": {"custom": True},
                "driver_execution": {"mode": "continuous", "poll_interval_seconds": 180},
            }
        ),
        encoding="utf-8",
    )
    blackboard = active.parent / "blackboard.json"
    blackboard_bytes = json.dumps({"current_step": "develop", "events": [1]}).encode()
    blackboard.write_bytes(blackboard_bytes)
    return root, active, blackboard_bytes


def test_update_command_replaces_only_active_policy_from_complete_explicit_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    root, active, blackboard_bytes = _layout(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "update-driver-policy",
            "issue432",
            "--contract-version",
            "2",
            "--driver-mode",
            "delegated",
            "--delegated-cli",
            "cursor-agent",
            "--delegated-model",
            "composer-1.5",
        ],
    )

    assert result.exit_code == 0, (result.stdout, result.exception)
    updated = yaml.safe_load(active.read_text(encoding="utf-8"))
    assert updated["review"] == {"custom": True}
    assert updated["driver"] == {
        "mode": "delegated",
        "cli": "cursor-agent",
        "model": "composer-1.5",
    }
    assert "driver_execution" not in updated
    assert (active.parent / "blackboard.json").read_bytes() == blackboard_bytes
    assert set(yaml.safe_load(root.read_text(encoding="utf-8"))) == {
        "issue_name",
        "worktree_path",
    }


def test_update_command_never_infers_missing_values_from_legacy_policy(
    tmp_path: Path, monkeypatch
) -> None:
    _, active, _ = _layout(tmp_path)
    original = active.read_bytes()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        ["update-driver-policy", "issue432", "--contract-version", "2"],
    )

    assert result.exit_code != 0
    assert active.read_bytes() == original


def test_update_command_rejects_inapplicable_mode_fields_before_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    _, active, _ = _layout(tmp_path)
    original = active.read_bytes()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "update-driver-policy",
            "issue432",
            "--contract-version",
            "2",
            "--driver-mode",
            "unattended",
            "--poll-interval-seconds",
            "10",
        ],
    )

    assert result.exit_code == 1
    assert active.read_bytes() == original


def test_update_command_rejects_issue_name_that_escapes_issue_root(
    tmp_path: Path, monkeypatch
) -> None:
    issues_root = tmp_path / ".cafe" / "issues"
    issues_root.mkdir(parents=True)
    victim = tmp_path / "victim" / "issue.yaml"
    victim.parent.mkdir(parents=True)
    victim.write_text("owner: outside\n", encoding="utf-8")
    original = victim.read_bytes()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "update-driver-policy",
            "../../victim",
            "--contract-version",
            "2",
            "--driver-mode",
            "unattended",
        ],
    )

    assert result.exit_code == 1
    assert victim.read_bytes() == original


def test_update_command_rejects_traversal_from_inventory_issue_name(
    tmp_path: Path, monkeypatch
) -> None:
    root, _, _ = _layout(tmp_path)
    inventory = yaml.safe_load(root.read_text(encoding="utf-8"))
    inventory["issue_name"] = "../../victim"
    root.write_text(yaml.safe_dump(inventory), encoding="utf-8")
    victim = Path(inventory["worktree_path"]) / "victim" / "issue.yaml"
    victim.parent.mkdir(parents=True)
    victim.write_text("owner: outside\n", encoding="utf-8")
    original = victim.read_bytes()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "update-driver-policy",
            "issue432",
            "--contract-version",
            "2",
            "--driver-mode",
            "unattended",
        ],
    )

    assert result.exit_code == 1
    assert victim.read_bytes() == original
