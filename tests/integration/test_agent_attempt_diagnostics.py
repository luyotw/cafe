"""Journey tests for durable CLI-attempt diagnostics."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    OperationLogPolicy,
    OperationMonitoring,
    OperationRisk,
)
from cafe.core.long_running_operation_helper import get_operation_status, run_operation_command
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


def _build_loader(
    tmp_path: Path,
    *,
    skill_name: str = "develop",
    input_artifact: str | None = None,
) -> GenericPhase:
    skill_root = tmp_path / "builtin" / "skills" / skill_name
    skill_root.mkdir(parents=True)
    prompt_inputs = (
        f"workflow:\n  prompt_inputs:\n    - artifacts: [{input_artifact}]\n"
        f"      placeholder: {input_artifact}_file\n"
        if input_artifact
        else ""
    )
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: desc\n{prompt_inputs}---\n\n"
        "Write output to: {output_file}\n",
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


def _build_executor(
    tmp_path: Path,
    manager: AgentManager,
    *,
    step_name: str = "develop",
    skill_name: str = "develop",
    input_artifact: str | None = None,
) -> GenericWorkflowStepExecutor:
    issue_dir = tmp_path / ".cafe" / "issues" / "attempt-diagnostics"
    phase_dir = issue_dir / step_name
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
            "steps": {
                step_name: {
                    "skill": skill_name,
                    "role": "developer",
                    **({"input_artifacts": [input_artifact]} if input_artifact else {}),
                }
            },
        },
        generic_phase=_build_loader(
            tmp_path, skill_name=skill_name, input_artifact=input_artifact
        ),
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


def test_cold_backup_chain_status_checks_a_running_operation_before_takeover(
    tmp_path: Path,
) -> None:
    """IT-005 — a custom workflow reuses, rather than relaunches, live work."""
    step_name = "orbit_launch"
    skill_name = "orbit_operator"
    input_artifact = "mission_brief"
    manager = AgentManager()
    manager.register_agent(
        AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            clis=[
                CliEntry(cli=AgentCLI.CLAUDE),
                CliEntry(cli=AgentCLI.GEMINI),
                CliEntry(cli=AgentCLI.CODEX),
            ],
        )
    )
    executor = _build_executor(
        tmp_path,
        manager,
        step_name=step_name,
        skill_name=skill_name,
        input_artifact=input_artifact,
    )
    executor.git_ops.run_git.return_value = "test-head"
    executor.git_ops.get_status.return_value = ""
    state = BlackboardStore(executor.issue_dir).load_or_create(step_name)
    brief_file = tmp_path / "mission-brief.md"
    brief_file.write_text("mission input", encoding="utf-8")
    state.artifacts[input_artifact] = ArtifactEntry(
        name=input_artifact,
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="orbit_intake",
        path=str(brief_file),
    )
    iteration_dir = executor.phase_dir / "iteration_001"
    output_file = iteration_dir / "output.md"
    checklist_file = iteration_dir / "checklist.md"
    release_file = tmp_path / "release-operation"
    operation_script = tmp_path / "wait-for-release.py"
    operation_script.write_text(
        "from pathlib import Path\n"
        "import sys, time\n"
        "while not Path(sys.argv[1]).exists():\n"
        "    time.sleep(0.01)\n",
        encoding="utf-8",
    )
    launched = run_operation_command(
        issue_dir=executor.issue_dir,
        step=step_name,
        iteration_dir=iteration_dir,
        command=[sys.executable, str(operation_script), str(release_file)],
        cwd=tmp_path,
        playbook=executor.playbook,
        reason="cold_backup_integration",
        risk=OperationRisk.MEDIUM,
        monitoring=OperationMonitoring.PERIODIC,
        log_policy=OperationLogPolicy.INCREMENTAL_TAIL,
        stop_condition="stop if the cold-backup fixture fails",
        recovery="inspect the same operation id before retrying",
    )
    assert launched.started is True
    primary_error = AgentExecutionError("primary rate limit", error_type="rate_limit")
    backup_error = AgentExecutionError("backup rate limit", error_type="rate_limit")
    prompts: list[str] = []

    def snapshot(error: AgentExecutionError) -> str:
        return executor._build_backup_takeover_context(
            error=error,
            step_name=step_name,
            step_def=executor.playbook["steps"][step_name],
            blackboard_state=state,
            output_file=output_file,
            checklist_file=checklist_file,
            iteration_dir=iteration_dir,
        )

    def side_effect(prompt: str, *_args, **_kwargs):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise primary_error
        if len(prompts) == 2:
            iteration_dir.mkdir(parents=True, exist_ok=True)
            output_file.write_text("partial output", encoding="utf-8")
            checklist_file.write_text("[x] partial task\n", encoding="utf-8")
            raise backup_error
        return _success("replacement output")

    try:
        with (
            patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect),
            patch(
                "cafe.phases.generic_workflow_step.get_operation_status",
                wraps=get_operation_status,
            ) as status_check,
        ):
            response, *_ = manager.execute(
                "David",
                "perform the workflow step",
                phase_name=step_name,
                backup_context_callback=snapshot,
            )

        assert status_check.call_count == 2
    finally:
        release_file.write_text("release", encoding="utf-8")

    first_takeover = json.loads(prompts[1].split("provider-neutral):\n", 1)[1])
    second_takeover = json.loads(prompts[2].split("provider-neutral):\n", 1)[1])
    assert response == "replacement output"
    assert first_takeover["reason"] != second_takeover["reason"]
    assert first_takeover["target"]["step"] == step_name
    assert first_takeover["resolved_inputs"][f"{input_artifact}_file"]["mode"] == "full"
    assert first_takeover["resolved_inputs"][f"{input_artifact}_file"]["path"] == str(
        brief_file
    )
    assert first_takeover["partial"]["output"]["state"] == "missing"
    assert second_takeover["partial"]["output"]["state"] == "file"
    assert second_takeover["partial"]["checklist"]["completed"] == 1
    assert first_takeover["operation"] == {
        "state": "running",
        "id": launched.operation.operation_id,
    }
    assert second_takeover["operation"] == first_takeover["operation"]


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
