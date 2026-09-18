"""Unit tests for issue.yaml config helpers."""

from pathlib import Path

import pytest

from cafe.utils.issue_config import (
    parse_issue_config_value,
    read_issue_config,
    read_issue_config_strict,
    read_issue_config_value,
    resolve_issue_config_path,
    resolve_issue_id,
    write_issue_config_atomic,
)


def test_reads_issue_id_from_spec_section(tmp_path: Path) -> None:
    config_path = tmp_path / "issue.yaml"
    config_path.write_text("spec:\n  issue_id: '123'\nbase_branch: main\n", encoding="utf-8")
    assert read_issue_config_value(config_path, "issue_id") is None
    assert read_issue_config_value(config_path, "spec.issue_id") == "123"
    assert resolve_issue_id(config_path) == "123"


def test_reads_issue_id_from_top_level(tmp_path: Path) -> None:
    config_path = tmp_path / "issue.yaml"
    config_path.write_text("issue_id: '456'\nbase_branch: main\n", encoding="utf-8")
    assert resolve_issue_id(config_path) == "456"


def test_issue_id_none_when_not_configured(tmp_path: Path) -> None:
    config_path = tmp_path / "issue.yaml"
    config_path.write_text("base_branch: main\n", encoding="utf-8")
    assert resolve_issue_id(config_path) is None


def test_top_level_issue_id_takes_precedence(tmp_path: Path) -> None:
    config_path = tmp_path / "issue.yaml"
    config_path.write_text(
        "issue_id: '789'\nspec:\n  issue_id: '123'\nbase_branch: main\n",
        encoding="utf-8",
    )
    assert resolve_issue_id(config_path) == "789"


def test_integer_issue_id_coerced_to_string(tmp_path: Path) -> None:
    config_path = tmp_path / "issue.yaml"
    config_path.write_text("spec:\n  issue_id: 131\nbase_branch: main\n", encoding="utf-8")
    assert resolve_issue_id(config_path) == "131"


def test_parse_issue_config_value_missing_key() -> None:
    assert parse_issue_config_value({"base_branch": "main"}, "issue_id") is None


def test_read_issue_config_missing_file(tmp_path: Path) -> None:
    assert read_issue_config(tmp_path / "missing.yaml") is None


def test_strict_issue_config_io_rejects_malformed_and_is_atomic(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "issue.yaml"
    config_path.write_text("pr: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        read_issue_config_strict(config_path)

    config_path.write_text("pr:\n  auto_create: true\n", encoding="utf-8")
    before = config_path.read_bytes()

    def fail_before_replace(*_args, **_kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr("cafe.utils.issue_config.atomic_write_bytes", fail_before_replace)
    with pytest.raises(OSError, match="interruption"):
        write_issue_config_atomic(config_path, {"pr": {"auto_create": False}})
    assert config_path.read_bytes() == before


def test_registered_authority_rejects_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "issue.yaml").write_text("playbook_id: direct\n", encoding="utf-8")
    issue = tmp_path / ".cafe" / "issues" / "demo"
    issue.parent.mkdir(parents=True)
    issue.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        "cafe.utils.issue_config._registered_worktree_paths", lambda _root: (tmp_path,)
    )

    with pytest.raises(ValueError, match="must not traverse a symlink"):
        resolve_issue_config_path(issue / "issue.yaml", require_registered_worktree=True)


def test_registered_authority_rejects_in_tree_issue_alias(tmp_path: Path, monkeypatch) -> None:
    issues = tmp_path / ".cafe" / "issues"
    victim = issues / "victim"
    victim.mkdir(parents=True)
    (victim / "issue.yaml").write_text("playbook_id: direct\n", encoding="utf-8")
    (issues / "demo").symlink_to(victim, target_is_directory=True)
    monkeypatch.setattr(
        "cafe.utils.issue_config._registered_worktree_paths", lambda _root: (tmp_path,)
    )

    with pytest.raises(ValueError, match="must not traverse a symlink"):
        resolve_issue_config_path(issues / "demo" / "issue.yaml", require_registered_worktree=True)
