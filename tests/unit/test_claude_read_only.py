"""Plan U4: fixed built-in tool selection with accepted native bypasses."""

import pytest

from cafe.agents.cli import ClaudeCLI
from cafe.core.types import AgentCLI, AgentConfig


@pytest.mark.parametrize("session", [None, "stored-writable-session"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
def test_u4_claude_available_read_tools_override_broad_approvals(session, operation):
    strategy = ClaudeCLI(
        AgentConfig(name="Ada", cli=AgentCLI.CLAUDE, model="selected-model", session_id=session)
    )
    command = (
        strategy.build_interactive_command("inspect")
        if operation == "open_interactive_session"
        else strategy.build_command("inspect", ["Bash", "Write"])
    )
    native = command.copy()
    restricted = strategy.apply_read_only(command, operation)
    assert command == native
    assert restricted[restricted.index("--tools") + 1].split(",") == ["Read", "Glob", "Grep"]
    assert restricted[restricted.index("--allowed-tools") + 1].split(",") == [
        "Read",
        "Glob",
        "Grep",
    ]
    assert restricted.count("--allowed-tools") == 1
    denied = restricted[restricted.index("--disallowed-tools") + 1].split(",")
    assert {"Bash", "Edit", "Write", "NotebookEdit"} <= set(denied)
    assert restricted[restricted.index("--permission-mode") + 1] == "plan"
    assert "selected-model" in restricted and "inspect" in restricted
    assert ("stored-writable-session" in restricted) == bool(session)
    assert "--bare" not in restricted and "--safe-mode" not in restricted
    assert not any("bypass-permissions" in x for x in restricted)
    # Equals syntax prevents variadic tool options consuming the positional prompt.
    prompt_index = restricted.index("inspect")
    if operation == "open_interactive_session":
        assert restricted[prompt_index - 1] == "--"


@pytest.mark.parametrize("prompt", ["--tools", "--allowed-tools", "--permission-mode"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
def test_u4_permission_option_text_in_prompt_is_preserved(prompt, operation):
    strategy = ClaudeCLI(AgentConfig(name="Ada", cli=AgentCLI.CLAUDE, model="selected-model"))
    native = (
        strategy.build_interactive_command(prompt)
        if operation == "open_interactive_session"
        else strategy.build_command(prompt, ["Bash"])
    )
    command = strategy.apply_read_only(native, operation)
    if operation == "run_one_shot":
        assert command[command.index("-p") + 1] == prompt
    else:
        assert command[-2:] == ["--", prompt]
    assert "--permission-mode" in command and "Read,Glob,Grep" in command
