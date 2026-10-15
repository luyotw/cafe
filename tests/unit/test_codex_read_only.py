"""Plan U4/U6: native parameters, not a whole-process safety proof."""

from unittest.mock import patch

import pytest

from cafe.agents.cli import CodexCLI
from cafe.agents.executor import AgentExecutionControl, AgentExecutor
from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, TokenUsage


@pytest.mark.parametrize("session", [None, "stored-writable-session"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
def test_u4_codex_native_options_preserve_context(session, operation):
    strategy = CodexCLI(
        AgentConfig(name="Ada", cli=AgentCLI.CODEX, model="selected-model", session_id=session)
    )
    command = (
        strategy.build_interactive_command("inspect")
        if operation == "open_interactive_session"
        else strategy.build_command("inspect")
    )
    native = command.copy()
    restricted = strategy.apply_read_only(command, operation)
    assert command == native
    assert restricted[0] == "codex"
    assert restricted[restricted.index("--sandbox") + 1] == "read-only"
    approval = "-a" if "-a" in restricted else "--ask-for-approval"
    assert restricted[restricted.index(approval) + 1] == "never"
    assert restricted.count(approval) == 1
    assert "selected-model" in restricted and "inspect" in restricted
    assert ("stored-writable-session" in restricted) == bool(session)
    assert "sandbox" not in restricted
    assert not any("dangerously" in arg or arg == "--full-auto" for arg in restricted)
    # Global options precede exec/resume, including exec resume grammar.
    if session or operation == "run_one_shot":
        assert restricted.index("--sandbox") < min(
            restricted.index(x) for x in ["exec", "resume"] if x in restricted
        )


def test_u6_unintegrated_provider_or_operation_rejects_before_launch():
    for strategy, operation in [
        (CodexCLI(AgentConfig(name="Ada", cli=AgentCLI.CODEX)), "deliver_to_exact_session"),
    ]:
        with pytest.raises(ValueError) as caught:
            strategy.apply_read_only([strategy.config.cli.value], operation)
        assert strategy.config.cli.value in str(caught.value)
        assert operation in str(caught.value)


def test_u4_prompt_text_cannot_omit_interactive_approval_policy():
    strategy = CodexCLI(AgentConfig(name="Ada", cli=AgentCLI.CODEX))
    command = strategy.apply_read_only(
        strategy.build_interactive_command("-a"), "open_interactive_session"
    )
    assert command[command.index("--ask-for-approval") + 1] == "never"
    assert command[-1] == "-a"


@pytest.mark.parametrize("session", [None, "stored-session"])
def test_read_only_empty_scope_launches_with_one_sandbox_option(tmp_path, session):
    executor = AgentExecutor(
        AgentConfig(name="Probe", cli=AgentCLI.CODEX, session_id=session),
        stream_output=False,
    )
    response = AgentResponse(response="OK", token_usage=TokenUsage())
    with patch.object(executor, "_execute_with_streaming", return_value=response) as run:
        executor.execute(
            "inspect",
            allowed_tools=[],
            allowed_directories=[],
            read_only=True,
            execution_control=AgentExecutionControl(working_directory=tmp_path),
        )

    command = run.call_args.kwargs["cmd"]
    assert command.count("--sandbox") == 1
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command.index("--sandbox") < command.index("exec")
    assert command[command.index("-a") + 1] == "never"
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--strict-config" in command
    for feature in ("shell_tool", "unified_exec", "apps", "plugins", "multi_agent"):
        assert command[command.index(feature) - 1] == "--disable"


def test_read_only_replaces_existing_global_sandbox_without_changing_prompt():
    strategy = CodexCLI(AgentConfig(name="Probe", cli=AgentCLI.CODEX))
    command = ["codex", "--sandbox", "workspace-write", "exec", "--sandbox"]
    original = command.copy()

    restricted = strategy.apply_read_only(command, "run_one_shot")

    assert restricted == ["codex", "--sandbox", "read-only", "exec", "--sandbox"]
    assert command == original
