"""Unit tests for issue.yaml config helpers."""

from pathlib import Path

from cafe.utils.issue_config import (
    parse_issue_config_value,
    read_issue_config,
    read_issue_config_value,
    resolve_issue_id,
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
