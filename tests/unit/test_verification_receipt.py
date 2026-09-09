"""Tests for develop-to-review verification receipts."""

from __future__ import annotations

import hashlib
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
    reuse_verification_receipt,
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
    log_path = receipt_path.parent / payload["output_log"]["path"]
    assert log_path.read_text(encoding="utf-8") == "passed\n"
    assert payload["output_log"]["size_bytes"] == log_path.stat().st_size
    assert payload["output_log"]["sha256"] == hashlib.sha256(
        log_path.read_bytes()
    ).hexdigest()
    assert checked.valid is True
    assert checked.reasons == ()


def test_verification_captures_combined_stdout_and_stderr(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)

    _, receipt_path, _ = run_verification(
        output_file=output,
        command=[
            sys.executable,
            "-c",
            (
                "import sys; "
                "print('stdout-line', flush=True); "
                "print('stderr-line', file=sys.stderr, flush=True)"
            ),
        ],
        scope="full",
        cwd=repo,
    )

    log = (receipt_path.parent / "verification.log").read_text(encoding="utf-8")
    assert log.splitlines() == ["stdout-line", "stderr-line"]


def test_check_rejects_tampered_verification_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    _, receipt_path, _ = run_verification(
        output_file=output,
        command=[sys.executable, "-c", "print('trusted')"],
        scope="full",
        cwd=repo,
    )
    (receipt_path.parent / "verification.log").write_text(
        "tampered\n", encoding="utf-8"
    )

    checked = check_verification_receipt(output_file=output, cwd=repo)

    assert checked.valid is False
    assert any("output log" in reason for reason in checked.reasons)


def test_verification_replaces_log_symlink_without_writing_its_target(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched\n", encoding="utf-8")
    log_path = output.parent / "verification.log"
    log_path.symlink_to(victim)

    _, receipt_path, _ = run_verification(
        output_file=output,
        command=[sys.executable, "-c", "print('safe-output')"],
        scope="full",
        cwd=repo,
    )

    assert victim.read_text(encoding="utf-8") == "untouched\n"
    assert not log_path.is_symlink()
    assert log_path.read_text(encoding="utf-8") == "safe-output\n"
    log_path.unlink()
    log_path.symlink_to(victim)
    checked = check_verification_receipt(output_file=output, cwd=repo)
    assert checked.valid is False
    assert "output log is not a regular file" in checked.reasons


def test_version_one_receipt_remains_backward_readable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    _, receipt_path, _ = run_verification(
        output_file=output,
        command=[sys.executable, "-c", "pass"],
        scope="full",
        cwd=repo,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["schema_version"] = 1
    receipt.pop("output_log")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    checked = check_verification_receipt(output_file=output, cwd=repo)

    assert checked.valid is True


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


def test_reuse_materializes_a_valid_receipt_for_the_next_iteration(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source_output = _output_file(repo)
    target_output = (
        repo / ".cafe/issues/issue1/develop/iteration_002/output.md"
    )
    run_verification(
        output_file=source_output,
        command=[sys.executable, "-c", "pass"],
        scope="full",
        cwd=repo,
    )

    receipt_path, payload = reuse_verification_receipt(
        source_output_file=source_output,
        output_file=target_output,
        required_scope="full",
        cwd=repo,
    )
    checked = check_verification_receipt(output_file=target_output, cwd=repo)

    assert receipt_path == target_output.parent / "verification.json"
    assert payload["reused_from"] == str(source_output.parent / "verification.json")
    assert payload["reused_at"]
    assert (target_output.parent / "verification.log").read_bytes() == (
        source_output.parent / "verification.log"
    ).read_bytes()
    assert checked.valid is True
    assert checked.receipt == payload


def test_reuse_rejects_a_receipt_for_an_older_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source_output = _output_file(repo)
    target_output = repo / ".cafe/issues/issue1/develop/iteration_002/output.md"
    run_verification(
        output_file=source_output,
        command=[sys.executable, "-c", "pass"],
        scope="full",
        cwd=repo,
    )
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "change")

    with pytest.raises(VerificationReceiptError, match="cannot reuse an invalid receipt"):
        reuse_verification_receipt(
            source_output_file=source_output,
            output_file=target_output,
            required_scope="full",
            cwd=repo,
        )

    assert not (target_output.parent / "verification.json").exists()


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
        command=[sys.executable, "-c", "print('failed-output'); raise SystemExit(7)"],
        scope="full",
        cwd=repo,
    )
    checked = check_verification_receipt(output_file=output, cwd=repo)

    assert exit_code == 7
    assert payload["valid"] is False
    assert (
        output.parent / payload["output_log"]["path"]
    ).read_text(encoding="utf-8") == "failed-output\n"
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


def test_verification_cli_returns_only_a_bounded_output_tail(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    output = _output_file(repo)
    monkeypatch.chdir(repo)

    result = runner.invoke(
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
            "for i in range(120): print(f'line-{i:03d}-' + 'x' * 1000)",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "line-000-" not in result.stdout
    assert "line-119-" in result.stdout
    assert '"output_truncated": true' in result.stdout
    assert len(result.stdout.encode("utf-8")) < 40 * 1024
    log = output.parent / "verification.log"
    assert "line-000-" in log.read_text(encoding="utf-8")
    assert log.stat().st_size > 100 * 1024


def test_verification_cli_reuses_receipt_for_new_iteration(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    source_output = _output_file(repo)
    target_output = repo / ".cafe/issues/issue1/develop/iteration_002/output.md"
    run_verification(
        output_file=source_output,
        command=[sys.executable, "-c", "pass"],
        scope="full",
        cwd=repo,
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(
        app,
        [
            "verification",
            "reuse",
            "--source-output-file",
            str(source_output),
            "--output-file",
            str(target_output),
            "--require-scope",
            "full",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert '"reused": true' in result.stdout
    assert check_verification_receipt(output_file=target_output, cwd=repo).valid is True


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
    assert "verification command must not be empty" in result.output
