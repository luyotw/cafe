"""Plan U4/U6/U8: native options and context/default compatibility."""

import pytest

from cafe.agents.cli import CopilotCLI
from cafe.core.types import AgentCLI, AgentConfig


@pytest.mark.parametrize("session", [None, "stored-session"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
@pytest.mark.parametrize("literal", ['--available-tools', '--allow-tool', '--allow-all-tools', '--allow-all', '--yolo'])
def test_u4_copilot_native_restriction_preserves_option_like_values(session, operation, literal):
    strategy = CopilotCLI(AgentConfig(
        name="Ada", cli=AgentCLI.COPILOT, model=literal, session_id=session))
    command = (strategy.build_interactive_command(literal)
               if operation == "open_interactive_session" else strategy.build_command(literal))
    original = command.copy()
    restricted = strategy.apply_read_only(command, operation)
    assert command == original
    assert restricted[0] == 'copilot'
    assert "--available-tools=view,glob,grep" in restricted
    assert "--allow-tool=read" in restricted
    assert "--deny-tool=shell" in restricted and "--deny-tool=write" in restricted
    assert restricted[restricted.index("--model") + 1] == literal
    if operation == "run_one_shot":
        assert restricted[restricted.index("-p") + 1] == literal
    assert ("stored-session" in restricted) == bool(session)
    # Literal values are excluded when inspecting actual option slots.
    actual = []
    pairs = {"-p", "--model", "--resume", "--output-format", "--add-dir",
             "--include-directories", "--approval-mode", "--mode", "--allowed-tools"}
    i = 1
    while i < len(restricted):
        actual.append(restricted[i])
        i += 2 if restricted[i] in pairs else 1
    assert not set(actual).intersection(['--allow-all-tools', '--allow-all', '--yolo', '--allow-tool'])


def test_u6_copilot_unsupported_operation_is_identified():
    strategy = CopilotCLI(AgentConfig(name="Ada", cli=AgentCLI.COPILOT))
    with pytest.raises(ValueError) as caught:
        strategy.apply_read_only(['copilot'], "deliver_to_exact_session")
    assert 'copilot' in str(caught.value)
    assert "deliver_to_exact_session" in str(caught.value)


def test_u8_copilot_ordinary_command_remains_writable():
    strategy = CopilotCLI(AgentConfig(name="Ada", cli=AgentCLI.COPILOT, model="selected-model"))
    command = strategy.build_command("diagnose")
    assert "--allow-all-tools" in command and "--available-tools=view,glob,grep" not in command


@pytest.mark.parametrize("session", ["--force", "--yolo", "--allow-all-tools", "--approval-mode", "--mode", "--available-tools", "--allow-tool"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
def test_u4_copilot_literal_session_identity_is_preserved(session, operation):
    strategy = CopilotCLI(AgentConfig(
        name="Ada", cli=AgentCLI.COPILOT, model="selected-model", session_id=session))
    command = (strategy.build_interactive_command()
               if operation == "open_interactive_session" else strategy.build_command("diagnose"))
    original = command.copy()
    restricted = strategy.apply_read_only(command, operation)
    assert restricted[restricted.index("--resume") + 1] == session
    assert command == original


@pytest.mark.parametrize("conflict", [["--allow-all"], ["--yolo"], ["--allow-tool", "shell"], ["--allow-tool=write"], ["--available-tools", "bash,edit"], ["--available-tools=bash,edit"]])
def test_u4_copilot_actual_broad_permissions_are_replaced(conflict):
    strategy = CopilotCLI(AgentConfig(name="Ada", cli=AgentCLI.COPILOT, model="selected-model"))
    original = strategy.build_command("--allow-all-tools") + conflict
    restricted = strategy.apply_read_only(original, "run_one_shot")
    assert restricted.count("--available-tools=view,glob,grep") == 1
    assert "--allow-tool=read" in restricted
    assert "--deny-tool=shell" in restricted and "--deny-tool=write" in restricted
    assert restricted[restricted.index("-p") + 1] == "--allow-all-tools"
    assert restricted.count("--allow-all-tools") == 1
    assert not set(restricted).intersection({"--allow-all", "--yolo", "--allow-tool", "shell", "--allow-tool=write", "bash,edit", "--available-tools=bash,edit"})
    assert original[-len(conflict):] == conflict
