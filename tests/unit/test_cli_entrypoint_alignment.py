from __future__ import annotations

from pathlib import Path

import pytest

from cafe.ui import cli
from cafe.ui.cli import (
    _build_repo_entrypoint_mismatch_message,
    _build_repo_entrypoint_reexec_command,
    _build_repo_entrypoint_reexec_env,
    _find_repo_checkout_root,
)


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


def test_reexec_command_preserves_cli_args(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "make", "--user-input", "hello"])

    command = _build_repo_entrypoint_reexec_command(repo_root)

    assert command[1:] == ["-m", "cafe.ui.cli", "make", "--user-input", "hello"]


def test_reexec_env_prefers_checkout_src(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    monkeypatch.setenv("PYTHONPATH", "/existing/path")

    env = _build_repo_entrypoint_reexec_env(repo_root)

    assert env["PYTHONPATH"].split(":")[0] == str((repo_root / "src").resolve())
    assert "/existing/path" in env["PYTHONPATH"].split(":")


def test_main_returns_error_code_without_traceback_when_auto_reexec_fails(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    expected_cli = repo_root / "src" / "cafe" / "ui" / "cli.py"
    actual_cli = tmp_path / "global" / "cafe" / "ui" / "cli.py"
    monkeypatch.setattr(cli, "_check_dependencies", lambda: None)
    monkeypatch.setattr(
        cli,
        "_resolve_repo_entrypoint_mismatch",
        lambda **_: (repo_root, expected_cli, actual_cli),
    )
    monkeypatch.setattr(
        cli,
        "_reexec_repo_entrypoint",
        lambda repo_root: (_ for _ in ()).throw(OSError("exec failed")),
    )
    monkeypatch.setattr(
        cli,
        "app",
        lambda: pytest.fail("app should not run when re-exec fails"),
    )

    result = cli.main()

    captured = capsys.readouterr()
    assert result == 1
    assert "different installation than this checkout" in captured.out
    assert "Traceback" not in captured.out
