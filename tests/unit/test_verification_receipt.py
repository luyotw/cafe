"""Tests for develop-to-review verification receipts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app
from cafe.verification import (
    VerificationReceiptError,
    check_verification_receipt,
    run_focused_verification,
    run_verification,
)

runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / ".gitignore").write_text(
        ".cafe/\n.pytest_cache/\n__pycache__/\n",
        encoding="utf-8",
    )
    (repo / "tracked.txt").write_text("stable\n", encoding="utf-8")
    (repo / "test_sample.py").write_text(
        """\
def test_sample() -> None:
    assert True
""",
        encoding="utf-8",
    )
    _git(repo, "add", ".gitignore", "tracked.txt", "test_sample.py")
    _git(repo, "commit", "-m", "initial")
    return repo


def _output_file(repo: Path) -> Path:
    output = repo / ".cafe/issues/issue1/develop/iteration_001/output.md"
    output.parent.mkdir(parents=True)
    return output


def test_run_and_check_verification_receipt_for_clean_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)

    exit_code, receipt_path, payload = run_verification(
        output_file=output,
        command=[sys.executable, "-c", "print('passed')"],
        scope="full",
        cwd=repo,
    )
    checked = check_verification_receipt(output_file=output, cwd=repo)

    assert exit_code == 0
    assert receipt_path == output.parent / "verification.json"
    assert payload["valid"] is True
    assert payload["git"]["clean_before"] is True
    assert payload["git"]["clean_after"] is True
    assert payload["git"]["cwd_relative_to_root"] == "."
    assert checked.valid is True
    assert checked.reasons == ()


def test_receipt_becomes_stale_when_head_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    run_verification(
        output_file=output,
        command=[sys.executable, "-c", "pass"],
        scope="full",
        cwd=repo,
    )
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "change")

    checked = check_verification_receipt(output_file=output, cwd=repo)

    assert checked.valid is False
    assert "current HEAD does not match the verified HEAD" in checked.reasons


def test_final_verification_refuses_dirty_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(VerificationReceiptError, match="worktree must be clean"):
        run_verification(
            output_file=output,
            command=[sys.executable, "-c", "pass"],
            scope="full",
            cwd=repo,
        )


@pytest.mark.parametrize("scope", ["full", "targeted"])
def test_verification_refuses_nested_working_directory(
    tmp_path: Path, scope: str
) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    nested = repo / "tests/unit"
    nested.mkdir(parents=True)

    with pytest.raises(VerificationReceiptError, match="worktree root"):
        run_verification(
            output_file=output,
            command=[sys.executable, "-c", "pass"],
            scope=scope,
            cwd=nested,
        )


def test_verification_wraps_missing_executable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)

    with pytest.raises(VerificationReceiptError, match="cannot start verification command"):
        run_verification(
            output_file=output,
            command=["definitely-not-a-real-test-command"],
            scope="full",
            cwd=repo,
        )


def test_failed_verification_writes_non_reusable_receipt(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)

    exit_code, _, payload = run_verification(
        output_file=output,
        command=[sys.executable, "-c", "raise SystemExit(7)"],
        scope="full",
        cwd=repo,
    )
    checked = check_verification_receipt(output_file=output, cwd=repo)

    assert exit_code == 7
    assert payload["valid"] is False
    assert checked.valid is False
    assert "recorded verification did not pass" in checked.reasons


def test_check_rejects_receipt_without_recorded_command(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    _, receipt_path, _ = run_verification(
        output_file=output,
        command=[sys.executable, "-c", "pass"],
        scope="full",
        cwd=repo,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["command"] = []
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    checked = check_verification_receipt(output_file=output, cwd=repo)

    assert checked.valid is False
    assert "recorded verification command is missing or invalid" in checked.reasons


def test_focus_replays_verified_command_with_selectors(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    run_verification(
        output_file=output,
        command=["pytest", "-q"],
        scope="full",
        cwd=repo,
    )

    exit_code, command = run_focused_verification(
        output_file=output,
        selectors=["test_sample.py::test_sample"],
        cwd=repo,
    )

    assert exit_code == 0
    assert command[-1] == "test_sample.py::test_sample"


def test_focus_requires_at_least_one_selector(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)

    with pytest.raises(VerificationReceiptError, match="at least one selector"):
        run_focused_verification(output_file=output, selectors=[], cwd=repo)


def test_focus_rejects_non_pytest_receipt_and_option_like_selector(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    run_verification(
        output_file=output,
        command=[sys.executable, "-c", "pass"],
        scope="full",
        cwd=repo,
    )
    with pytest.raises(VerificationReceiptError, match="only supports"):
        run_focused_verification(
            output_file=output,
            selectors=["test_sample.py"],
            cwd=repo,
        )

    run_verification(
        output_file=output,
        command=["pytest", "-q"],
        scope="full",
        cwd=repo,
    )
    with pytest.raises(VerificationReceiptError, match="relative pytest file paths"):
        run_focused_verification(output_file=output, selectors=["-k"], cwd=repo)


def test_verification_cli_runs_and_checks_receipt(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    monkeypatch.chdir(repo)

    run_result = runner.invoke(
        app,
        [
            "verification",
            "run",
            "--output-file",
            str(output),
            "--scope",
            "full",
            "--",
            sys.executable,
            "-c",
            "pass",
        ],
    )
    check_result = runner.invoke(
        app,
        ["verification", "check", "--output-file", str(output)],
    )

    assert run_result.exit_code == 0, run_result.stdout
    assert '"valid": true' in run_result.stdout
    assert check_result.exit_code == 0, check_result.stdout
    assert '"reasons": []' in check_result.stdout
    assert '"command":' in check_result.stdout


def test_verification_cli_runs_focused_selector(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    run_verification(
        output_file=output,
        command=["pytest", "-q"],
        scope="full",
        cwd=repo,
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(
        app,
        [
            "verification",
            "focus",
            "--output-file",
            str(output),
            "--",
            "test_sample.py::test_sample",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert '"focused": true' in result.stdout


def test_verification_cli_requires_a_command(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    monkeypatch.chdir(repo)

    result = runner.invoke(
        app,
        ["verification", "run", "--output-file", str(output), "--"],
    )

    assert result.exit_code == 2
    assert "verification command must not be empty" in result.stderr
