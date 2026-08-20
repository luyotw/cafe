"""Tests for long-running operation CLI commands."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from cafe.core.blackboard import LongRunningOperationState
from cafe.ui.cli import app
from cafe.ui.commands import operation as operation_command

runner = CliRunner()


def test_operation_load_playbook_uses_supported_loader_api() -> None:
    playbook = operation_command._load_playbook("default")

    assert isinstance(playbook, dict)
    assert "steps" in playbook


def test_operation_run_cli_loads_playbook_and_delegates(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_operation_command(**kwargs: object) -> object:
        calls.append(kwargs)
        return SimpleNamespace(
            operation=SimpleNamespace(
                operation_id="op-test",
                state=LongRunningOperationState.RUNNING,
            ),
            started=True,
            handle_path=tmp_path / "issue" / "develop" / "iteration_001" / "operation_handle.json",
        )

    monkeypatch.setattr(operation_command, "run_operation_command", fake_run_operation_command)

    issue_dir = tmp_path / "issue"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    result = runner.invoke(
        app,
        [
            "operation",
            "run",
            "--issue-dir",
            str(issue_dir),
            "--step",
            "develop",
            "--iteration-dir",
            str(iteration_dir),
            "--risk",
            "low",
            "--monitoring",
            "final-only",
            "--log-policy",
            "summary-only",
            "--stop-condition",
            "test completes",
            "--recovery",
            "rerun safely",
            "--readable-root",
            str(tmp_path),
            "--writable-root",
            str(iteration_dir),
            "--",
            "true",
        ],
    )

    assert result.exit_code == 0
    assert calls
    assert calls[0]["playbook"]["steps"]
    assert calls[0]["command"] == ["true"]
    assert calls[0]["stop_condition"] == "test completes"
    assert calls[0]["recovery"] == "rerun safely"
    assert calls[0]["readable_roots"] == [tmp_path]
    assert calls[0]["writable_roots"] == [iteration_dir]
    assert "op-test" in result.stdout


def test_operation_run_cli_requires_complete_compatible_risk_decision(
    monkeypatch, tmp_path: Path
) -> None:
    """UT-008: the public launcher rejects incomplete or mismatched policies."""
    called = False

    def fake_run_operation_command(**kwargs: object) -> object:
        nonlocal called
        called = True
        return SimpleNamespace()

    monkeypatch.setattr(operation_command, "run_operation_command", fake_run_operation_command)
    issue_dir = tmp_path / "issue"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    result = runner.invoke(
        app,
        [
            "operation", "run", "--issue-dir", str(issue_dir), "--step", "develop",
            "--iteration-dir", str(iteration_dir), "--risk", "high", "--monitoring",
            "final-only", "--log-policy", "summary-only", "--stop-condition", "halt",
            "--recovery", "restore", "--", "true",
        ],
    )

    assert result.exit_code == 2
    assert called is False


def test_operation_run_cli_rejects_an_omitted_agent_policy(monkeypatch, tmp_path: Path) -> None:
    called = False
    monkeypatch.setattr(
        operation_command,
        "run_operation_command",
        lambda **_kwargs: called,
    )

    result = runner.invoke(
        app,
        [
            "operation", "run", "--issue-dir", str(tmp_path / "issue"), "--step", "develop",
            "--iteration-dir", str(tmp_path / "iteration"), "--", "true",
        ],
    )

    assert result.exit_code == 2
    assert called is False


def test_operation_status_cli_loads_playbook_and_delegates(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_get_operation_status(**kwargs: object) -> object:
        calls.append(kwargs)
        return SimpleNamespace(
            to_dict=lambda: {
                "operation_id": "op-test",
                "state": LongRunningOperationState.RUNNING.value,
            }
        )

    monkeypatch.setattr(operation_command, "get_operation_status", fake_get_operation_status)

    issue_dir = tmp_path / "issue"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    result = runner.invoke(
        app,
        [
            "operation",
            "status",
            "--issue-dir",
            str(issue_dir),
            "--step",
            "develop",
            "--iteration-dir",
            str(iteration_dir),
        ],
    )

    assert result.exit_code == 0
    assert calls
    assert calls[0]["playbook"]["steps"]
    assert "op-test" in result.stdout
