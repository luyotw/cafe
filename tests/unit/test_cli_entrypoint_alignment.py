from __future__ import annotations

from pathlib import Path

import pytest

from cafe.ui import cli
from cafe.ui.cli import _build_repo_entrypoint_mismatch_message, _find_repo_checkout_root


def _write_repo_marker(repo_root: Path) -> None:
    (repo_root / "pyproject.toml").write_text('[project]\nname = "cafe-engine"\n', encoding="utf-8")
    cli_file = repo_root / "src" / "cafe" / "ui" / "cli.py"
    cli_file.parent.mkdir(parents=True, exist_ok=True)
    cli_file.write_text('print("stub")\n', encoding="utf-8")


def test_find_repo_checkout_root_returns_none_outside_repo(tmp_path: Path) -> None:
    assert _find_repo_checkout_root(tmp_path) is None


def test_find_repo_checkout_root_detects_cafe_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_repo_marker(repo_root)

    nested = repo_root / "nested" / "deeper"
    nested.mkdir(parents=True)

    assert _find_repo_checkout_root(nested) == repo_root.resolve()


def test_build_repo_entrypoint_mismatch_message_returns_none_when_import_matches_repo(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_repo_marker(repo_root)

    message = _build_repo_entrypoint_mismatch_message(
        cwd=repo_root,
        imported_cli_file=repo_root / "src" / "cafe" / "ui" / "cli.py",
    )

    assert message is None


def test_build_repo_entrypoint_mismatch_message_reports_external_install(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_repo_marker(repo_root)

    external_cli = tmp_path / "site-packages" / "cafe" / "ui" / "cli.py"
    external_cli.parent.mkdir(parents=True, exist_ok=True)
    external_cli.write_text('print("external")\n', encoding="utf-8")

    message = _build_repo_entrypoint_mismatch_message(
        cwd=repo_root,
        imported_cli_file=external_cli,
    )

    assert message is not None
    assert str(repo_root.resolve()) in message
    assert str(external_cli.resolve()) in message
    assert "pip install -e ." in message


def test_main_returns_error_code_without_traceback_for_entrypoint_mismatch(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "_check_dependencies", lambda: None)
    monkeypatch.setattr(
        cli,
        "_build_repo_entrypoint_mismatch_message",
        lambda: "Error: `cafe` is running from a different installation than this checkout.",
    )
    monkeypatch.setattr(
        cli,
        "_check_for_updates",
        lambda: pytest.fail("update check should not run on entrypoint mismatch"),
    )
    monkeypatch.setattr(
        cli,
        "app",
        lambda: pytest.fail("app should not run on entrypoint mismatch"),
    )

    result = cli.main()

    captured = capsys.readouterr()
    assert result == 1
    assert "different installation than this checkout" in captured.out
    assert "Traceback" not in captured.out
