"""Authoritative issue policy persistence tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from cafe.core.issue_policy_store import (
    IssuePolicyStore,
    PrepareWouldClobberError,
)
from cafe.ui.cli import app
from cafe.utils.issue_config import read_authoritative_issue_config, resolve_issue_config_path


def _v2_policy() -> dict:
    return {
        "contract_version": 2,
        "driver": {
            "mode": "delegated",
            "cli": "cursor-agent",
            "model": "composer-1.5",
        },
    }


def _issue_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    root_config = tmp_path / ".cafe" / "issues" / "issue432" / "issue.yaml"
    worktree = tmp_path / ".cafe" / "worktrees" / "issue432"
    active_config = worktree / ".cafe" / "issues" / "issue432" / "issue.yaml"
    root_config.parent.mkdir(parents=True)
    active_config.parent.mkdir(parents=True)
    root_config.write_text(
        yaml.safe_dump({"issue_name": "issue432", "worktree_path": str(worktree)}),
        encoding="utf-8",
    )
    return root_config, active_config, worktree


def test_root_inventory_dereferences_active_worktree_policy(tmp_path: Path) -> None:
    root_config, active_config, _ = _issue_layout(tmp_path)
    active_config.write_text(
        yaml.safe_dump({"base_branch": "develop", **_v2_policy()}),
        encoding="utf-8",
    )

    assert resolve_issue_config_path(root_config) == active_config.resolve()
    loaded = read_authoritative_issue_config(root_config)
    assert loaded is not None
    assert loaded["driver"]["cli"] == "cursor-agent"
    assert "driver" not in yaml.safe_load(root_config.read_text(encoding="utf-8"))


def test_atomic_update_replaces_only_policy_and_preserves_blackboard_bytes(tmp_path: Path) -> None:
    root_config, active_config, _ = _issue_layout(tmp_path)
    original = {
        "base_branch": "develop",
        "feature_branch": "issue432",
        "playbook_id": "standard-qa",
        "driver_execution": "interactive",
        "review": {"agent": "Grace", "future": {"enabled": True}},
    }
    active_config.write_text(yaml.safe_dump(original, sort_keys=False), encoding="utf-8")
    blackboard = active_config.parent / "blackboard.json"
    blackboard_bytes = b'{"current_step":"develop","events":[1,2]}'
    blackboard.write_bytes(blackboard_bytes)

    updated = IssuePolicyStore(root_config).replace(_v2_policy())

    assert updated["base_branch"] == original["base_branch"]
    assert updated["review"] == original["review"]
    assert "driver_execution" not in updated
    assert updated["contract_version"] == 2
    assert blackboard.read_bytes() == blackboard_bytes
    assert "driver" not in yaml.safe_load(root_config.read_text(encoding="utf-8"))


def test_update_requires_complete_explicit_policy_before_mutation(tmp_path: Path) -> None:
    root_config, active_config, _ = _issue_layout(tmp_path)
    original = yaml.safe_dump({"base_branch": "main", "driver_execution": "interactive"})
    active_config.write_text(original, encoding="utf-8")

    with pytest.raises(ValidationError):
        IssuePolicyStore(root_config).replace(
            {"contract_version": 2, "driver": {"mode": "delegated", "cli": "codex"}}
        )

    assert active_config.read_text(encoding="utf-8") == original


def test_update_rejects_unreadable_yaml_authority_without_mutation(tmp_path: Path) -> None:
    _, active_config, _ = _issue_layout(tmp_path)
    original = b"base_branch: develop\nreview: [unterminated\n"
    active_config.write_bytes(original)

    with pytest.raises(ValueError):
        IssuePolicyStore(active_config).replace(_v2_policy())

    assert active_config.read_bytes() == original


def test_prepare_guard_rejects_existing_runtime_before_mutation(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue432"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PrepareWouldClobberError):
        IssuePolicyStore.ensure_prepare_target_available(issue_dir)


def test_prepare_cli_stops_before_template_or_git_mutation(tmp_path: Path, monkeypatch) -> None:
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue432"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    from unittest.mock import patch

    with (
        patch("cafe.ui.init_helpers.sync_templates") as sync_templates,
        patch("cafe.ui.commands.lifecycle.GitOperations") as git_operations,
    ):
        result = CliRunner().invoke(app, ["prepare", "issue432"])

    assert result.exit_code == 1
    assert "update-driver-policy" in result.stdout
    sync_templates.assert_not_called()
    git_operations.assert_not_called()
