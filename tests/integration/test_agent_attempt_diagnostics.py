"""Journey tests for durable CLI-attempt diagnostics."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.types import (
    AgentCLI,
    AgentConfig,
    AgentResponse,
    CliEntry,
    CriticalPhaseError,
    TokenUsage,
)
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge


def _build_loader(tmp_path: Path) -> GenericPhase:
    skill_root = tmp_path / "builtin" / "skills" / "develop"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: develop\ndescription: desc\n---\n\nWrite output to: {output_file}\n",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(
            loader,
            project_root=tmp_path,
            home_dir=tmp_path / "home",
        ),
    )


def _build_executor(tmp_path: Path, manager: AgentManager) -> GenericWorkflowStepExecutor:
    issue_dir = tmp_path / ".cafe" / "issues" / "attempt-diagnostics"
    phase_dir = issue_dir / "develop"
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Initial Requirements\n", encoding="utf-8")
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "attempt-diagnostics"
    git_ops.get_main_branch.return_value = "main"
    git_ops.get_default_base_branch.return_value = "main"
    git_ops.get_commits_between.return_value = ""
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="attempt-diagnostics",
        playbook={
            "playbook": {"id": "default"},
            "roles": {"developer": {"default_agent": "David"}},
            "steps": {"develop": {"skill": "develop", "role": "developer"}},
        },
        generic_phase=_build_loader(tmp_path),
        agent_manager=manager,
        git_ops=git_ops,
        role_agent_map={"developer": "David"},
        interactive=False,
    )
    executor.phase_dir = phase_dir
    executor.iteration = 1
    return executor


def _manager() -> AgentManager:
    manager = AgentManager()
    manager.register_agent(
        AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            clis=[CliEntry(cli=AgentCLI.CLAUDE), CliEntry(cli=AgentCLI.GEMINI)],
        )
    )
    return manager


def _stream_error_process(line: str) -> MagicMock:
    process = MagicMock()
    process.stdout.readline.side_effect = [line, ""]
    process.stderr.read.return_value = ""
    process.wait.return_value = 1
    process.terminate.return_value = None
    return process


def _stream_success_process(text: str) -> MagicMock:
    process = MagicMock()
    process.stdout.readline.side_effect = [json.dumps({"content": text}) + "\n", ""]
    process.stderr.read.return_value = ""
    process.wait.return_value = 0
    return process


def _success(text: str) -> AgentResponse:
    return AgentResponse(response=text, token_usage=TokenUsage())


def _run_iteration(executor: GenericWorkflowStepExecutor) -> None:
    executor._execute_agent_iteration(
        agent_name="David",
        prompt="perform the workflow step",
        user_input="",
        valid_intents=[],
        require_status_code=False,
        allowed_tools=[],
        phase_specific_data={"step_name": "develop"},
    )


def test_fallback_success_preserves_primary_attempts_in_iteration_record(tmp_path: Path) -> None:
    """A successful fallback does not erase retried primary failure history."""
    executor = _build_executor(tmp_path, _manager())
    transient = AgentExecutionError(
        "socket connection was closed unexpectedly; token=raw-primary-secret",
        error_type="cli_unavailable",
    )

    with patch(
        "cafe.agents.executor.AgentExecutor.execute",
        side_effect=[transient, transient, _success("fallback output")],
    ):
        _run_iteration(executor)

    iteration_path = executor.phase_dir / "iteration_001" / "iteration.json"
    record = json.loads(iteration_path.read_text(encoding="utf-8"))
    assert record["response"] == "fallback output"
    assert record["cli"] == "gemini"
    assert [(item["cli"], item["attempt"]) for item in record["failed_attempts"]] == [
        ("claude", 1),
        ("claude", 2),
    ]
    assert "raw-primary-secret" not in iteration_path.read_text(encoding="utf-8")


def test_all_failed_journey_persists_sanitized_history_without_raw_secrets(tmp_path: Path) -> None:
    """Terminal failures leave a complete, redacted durable attempt history."""
    executor = _build_executor(tmp_path, _manager())
    primary_error = AgentExecutionError(
        "socket connection was closed unexpectedly; bearer raw-primary-secret",
        error_type="cli_unavailable",
    )
    fallback_error = AgentExecutionError(
        "rate limit; password=raw-fallback-secret",
        error_type="rate_limit",
    )

    with patch(
        "cafe.agents.executor.AgentExecutor.execute",
        side_effect=[primary_error, primary_error, fallback_error],
    ), pytest.raises(CriticalPhaseError):
        _run_iteration(executor)

    iteration_path = executor.phase_dir / "iteration_001" / "iteration.json"
    error_path = executor.phase_dir / "iteration_001" / "error.json"
    iteration_record = json.loads(iteration_path.read_text(encoding="utf-8"))
    assert [(item["cli"], item["attempt"]) for item in iteration_record["failed_attempts"]] == [
        ("claude", 1),
        ("claude", 2),
        ("gemini", 1),
    ]
    assert error_path.exists()
    persisted_text = (
        iteration_path.read_text(encoding="utf-8")
        + error_path.read_text(encoding="utf-8")
    )
    assert "raw-primary-secret" not in persisted_text
    assert "raw-fallback-secret" not in persisted_text


def test_real_stream_error_path_redacts_durable_streaming_log(tmp_path: Path) -> None:
    """CLI process error events never leave their raw credentials in streaming.jsonl."""
    manager = AgentManager()
    manager.register_agent(
        AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            clis=[CliEntry(cli=AgentCLI.CLAUDE), CliEntry(cli=AgentCLI.CODEX)],
        )
    )
    executor = _build_executor(tmp_path, manager)
    primary_process = _stream_error_process(
        '{"type":"assistant","error":"Failed to authenticate: HTTP 403; '
        'socket connection was closed unexpectedly; token=raw-primary-secret",'
        '"message":{"content":[{"type":"text",'
        '"text":"Failed to authenticate: HTTP 403; socket connection was closed unexpectedly; '
        'token=raw-primary-secret"}]}}\n'
    )
    fallback_process = _stream_error_process(
        '{"type":"error","message":"rate limit; token=raw-fallback-secret"}\n'
    )

    with patch(
        "subprocess.Popen",
        side_effect=[primary_process, fallback_process],
    ), patch("sys.platform", "win32"), pytest.raises(CriticalPhaseError):
        _run_iteration(executor)

    streaming_path = executor.phase_dir / "iteration_001" / "streaming.jsonl"
    streaming_text = streaming_path.read_text(encoding="utf-8")
    assert "raw-primary-secret" not in streaming_text
    assert "raw-fallback-secret" not in streaming_text
    assert "error_excerpt" in streaming_text


def test_real_stream_socket_close_retries_primary_before_fallback(tmp_path: Path) -> None:
    """A pure classified disconnect retries Claude instead of consuming Gemini."""
    executor = _build_executor(tmp_path, _manager())
    disconnected_process = _stream_error_process(
        '{"type":"assistant","error":"socket connection was closed unexpectedly",'
        '"message":{"content":[{"type":"text",'
        '"text":"socket connection was closed unexpectedly"}]}}\n'
    )
    retry_process = _stream_success_process("retry output")

    with patch(
        "subprocess.Popen",
        side_effect=[disconnected_process, retry_process],
    ) as popen, patch("sys.platform", "win32"):
        _run_iteration(executor)

    iteration_path = executor.phase_dir / "iteration_001" / "iteration.json"
    record = json.loads(iteration_path.read_text(encoding="utf-8"))
    assert popen.call_count == 2
    assert record["response"] == "retry output"
    assert record["cli"] == "claude"
    assert [(item["cli"], item["attempt"]) for item in record["failed_attempts"]] == [
        ("claude", 1),
    ]
