"""Authoritative issue policy persistence tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from cafe.orchestration.driver_policy import REJECTED_POLICY_KEYS, extract_driver_policy
from cafe.orchestration.issue_policy_store import (
    IssuePolicyStore,
    PrepareWouldClobberError,
)
from cafe.ui.cli import app
from cafe.utils.issue_config import read_authoritative_issue_config, resolve_issue_config_path

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


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
    subprocess.run(["git", "init", "-b", "develop"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "cafe-test@local.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "CAFE Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    worktree.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", "issue432", str(worktree)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
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


def test_root_inventory_public_update_writes_registered_worktree_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_config, active_config, _ = _issue_layout(tmp_path)
    active_config.write_text(
        yaml.safe_dump({"base_branch": "develop", **_v2_policy()}),
        encoding="utf-8",
    )
    root_before = root_config.read_bytes()
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

    assert result.exit_code == 0, (result.stdout, result.exception)
    assert extract_driver_policy(
        yaml.safe_load(active_config.read_text(encoding="utf-8"))
    ).driver.mode == "unattended"
    assert root_config.read_bytes() == root_before


def test_registered_worktree_authority_ignores_its_inventory_metadata(
    tmp_path: Path,
) -> None:
    _, active_config, _ = _issue_layout(tmp_path)
    active_config.write_text(
        yaml.safe_dump(
            {
                "issue_name": "issue432",
                "worktree_path": ".cafe/worktrees/issue432",
                **_v2_policy(),
            }
        ),
        encoding="utf-8",
    )
    store = IssuePolicyStore(active_config)

    assert resolve_issue_config_path(active_config) == active_config.resolve()
    assert store.config_path == active_config.resolve()
    with store.locked_policy() as policy:
        assert policy == extract_driver_policy(_v2_policy())


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


def test_atomic_update_removes_every_removed_policy_key(tmp_path: Path) -> None:
    root_config, active_config, _ = _issue_layout(tmp_path)
    active_config.write_text(
        yaml.safe_dump(
            {
                "base_branch": "develop",
                "execution": "continuous",
                "advancement": "automatic",
                "hosting": "background",
                "availability": "best-effort",
                "driver_execution": "interactive",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    updated = IssuePolicyStore(root_config).replace(_v2_policy())

    assert REJECTED_POLICY_KEYS.isdisjoint(updated)
    persisted = yaml.safe_load(active_config.read_text(encoding="utf-8"))
    assert REJECTED_POLICY_KEYS.isdisjoint(persisted)
    assert extract_driver_policy(persisted) == extract_driver_policy(_v2_policy())


def test_replace_rolls_back_when_published_policy_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cafe.orchestration.issue_policy_store as store_module
    from cafe.core.packet_io import atomic_write_bytes as real_atomic_write_bytes

    root_config, active_config, _ = _issue_layout(tmp_path)
    original = yaml.safe_dump({"base_branch": "develop"}, sort_keys=False).encode()
    active_config.write_bytes(original)
    writes = 0

    def corrupt_first_write(path: Path, content: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 1:
            path.write_bytes(b"driver: [unreadable\n")
            return
        real_atomic_write_bytes(path, content)

    monkeypatch.setattr(store_module, "atomic_write_bytes", corrupt_first_write)

    with pytest.raises(ValueError):
        IssuePolicyStore(root_config).replace(_v2_policy())

    assert active_config.read_bytes() == original


def test_policy_write_rejects_inventory_target_not_registered_to_selected_repo(
    tmp_path: Path,
) -> None:
    root_config, _, _ = _issue_layout(tmp_path)
    attacker_worktree = tmp_path / "attacker-worktree"
    attacker_config = attacker_worktree / ".cafe" / "issues" / "issue432" / "issue.yaml"
    attacker_config.parent.mkdir(parents=True)
    original = b"owner: attacker\n"
    attacker_config.write_bytes(original)
    root_config.write_text(
        yaml.safe_dump({"issue_name": "issue432", "worktree_path": str(attacker_worktree)}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        IssuePolicyStore(root_config).replace(_v2_policy())

    assert attacker_config.read_bytes() == original


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
