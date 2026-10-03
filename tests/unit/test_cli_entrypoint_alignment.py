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


def test_build_repo_entrypoint_mismatch_message_reports_external_install(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_repo_marker(repo_root)

    external_cli = tmp_path / "site-packages" / "cafe" / "ui" / "cli.py"
    external_cli.parent.mkdir(parents=True, exist_ok=True)
    external_cli.write_text('print("external")\n', encoding="utf-8")
    venv_python = tmp_path / ".venv" / "bin" / "python"
    monkeypatch.setattr(cli.sys, "executable", str(venv_python))

    message = _build_repo_entrypoint_mismatch_message(
        cwd=repo_root,
        imported_cli_file=external_cli,
    )

    assert message is not None
    assert str(repo_root.resolve()) in message
    assert str(external_cli.resolve()) in message
    assert "pip install -e ." in message
    assert f"{venv_python.absolute()} -m pip install -e ." in message


def test_reexec_command_preserves_cli_args(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "make", "--user-input", "hello"])

    command = _build_repo_entrypoint_reexec_command(repo_root)

    assert command[1:] == ["-m", "cafe.ui.cli", "make", "--user-input", "hello"]


def test_reexec_command_preserves_virtualenv_interpreter_symlink(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "repo"
    base_python = tmp_path / "base" / "python"
    base_python.parent.mkdir()
    base_python.touch()
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)
    monkeypatch.setattr(cli.sys, "executable", str(venv_python))

    command = _build_repo_entrypoint_reexec_command(repo_root)

    assert command[0] == str(venv_python.absolute())
    assert Path(command[0]).resolve() == base_python.resolve()


def test_reexec_env_prefers_checkout_src(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    monkeypatch.setenv("PYTHONPATH", "/existing/path")

    env = _build_repo_entrypoint_reexec_env(repo_root)

    assert env["PYTHONPATH"].split(":")[0] == str((repo_root / "src").resolve())
    assert "/existing/path" in env["PYTHONPATH"].split(":")


def test_entrypoint_check_honors_explicit_internal_skip(monkeypatch) -> None:
    monkeypatch.setenv("CAFE_SKIP_ENTRYPOINT_CHECK", "1")
    monkeypatch.setattr(
        cli,
        "_resolve_repo_entrypoint_mismatch",
        lambda **_: pytest.fail("internal workflow launch must not re-exec into its worktree"),
    )

    assert cli._check_repo_entrypoint_alignment() is True


def test_main_returns_error_code_without_traceback_when_auto_reexec_fails(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CAFE_SKIP_ENTRYPOINT_CHECK", raising=False)
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


def test_public_startup_forwards_neutral_composition_to_checkout_reexec(monkeypatch, tmp_path):
    """Plan U8/I7: a caller-owned module survives the complete public main path."""
    root = tmp_path / 'repo'
    monkeypatch.setattr(cli, '_check_dependencies', lambda: None)
    monkeypatch.delenv('CAFE_SKIP_ENTRYPOINT_CHECK', raising=False)
    monkeypatch.setattr(cli, '_resolve_repo_entrypoint_mismatch', lambda **_: (root, root/'expected', root/'actual'))
    monkeypatch.setattr(cli.sys, 'argv', ['tool', 'custom', '--value', 'literal argument'])
    class Reexecuted(BaseException): pass
    calls = []
    def reexec(executable, arguments, environment):
        calls.append((executable, arguments, environment))
        raise Reexecuted
    monkeypatch.setattr(cli.os, 'execvpe', reexec)
    with pytest.raises(Reexecuted):
        cli.main(application=lambda: pytest.fail('old application started'), entry_module='example.custom_cli')
    assert calls[0][1][1:] == ['-m', 'example.custom_cli', 'custom', '--value', 'literal argument']
    assert calls[0][2]['PYTHONPATH'].split(cli.os.pathsep)[0] == str((root/'src').resolve())


def test_public_startup_uses_supplied_neutral_application(monkeypatch):
    monkeypatch.setattr(cli, '_check_dependencies', lambda: None)
    monkeypatch.setattr(cli, '_check_repo_entrypoint_alignment', lambda **_: True)
    monkeypatch.setattr(cli, '_auto_sync_global_helper_skills', lambda: None)
    calls = []
    assert cli.main(application=lambda: calls.append('custom'), entry_module='example.custom_cli') is None
    assert calls == ['custom']
