"""Plan U4/U6/U8: native options and context/default compatibility."""

import pytest

from cafe.agents.cli import CursorCLI
from cafe.core.types import AgentCLI, AgentConfig


@pytest.mark.parametrize("session", [None, "stored-session"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
@pytest.mark.parametrize("literal", ['--mode', '--force', '-f', '--yolo'])
def test_u4_cursor_native_restriction_preserves_option_like_values(session, operation, literal):
    strategy = CursorCLI(AgentConfig(
        name="Ada", cli=AgentCLI.CURSOR, model=literal, session_id=session))
    command = (strategy.build_interactive_command(literal)
               if operation == "open_interactive_session" else strategy.build_command(literal))
    original = command.copy()
    restricted = strategy.apply_read_only(command, operation)
    assert command == original
    assert restricted[0] == 'cursor-agent'
    assert restricted[restricted.index('--mode') + 1] == 'ask'
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
    assert not set(actual).intersection(['--force', '-f', '--yolo'])


def test_u6_cursor_unsupported_operation_is_identified():
    strategy = CursorCLI(AgentConfig(name="Ada", cli=AgentCLI.CURSOR))
    with pytest.raises(ValueError) as caught:
        strategy.apply_read_only(['cursor-agent'], "deliver_to_exact_session")
    assert 'cursor-agent' in str(caught.value)
    assert "deliver_to_exact_session" in str(caught.value)


def test_u8_cursor_ordinary_command_remains_writable():
    strategy = CursorCLI(AgentConfig(name="Ada", cli=AgentCLI.CURSOR, model="selected-model"))
    command = strategy.build_command("diagnose")
    assert "--force" in command and "--mode" not in command


@pytest.mark.parametrize("session", ["--force", "--yolo", "--allow-all-tools", "--approval-mode", "--mode", "--available-tools", "--allow-tool"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
def test_u4_cursor_literal_session_identity_is_preserved(session, operation):
    strategy = CursorCLI(AgentConfig(
        name="Ada", cli=AgentCLI.CURSOR, model="selected-model", session_id=session))
    command = (strategy.build_interactive_command()
               if operation == "open_interactive_session" else strategy.build_command("diagnose"))
    original = command.copy()
    restricted = strategy.apply_read_only(command, operation)
    assert restricted[restricted.index("--resume") + 1] == session
    assert command == original


@pytest.mark.parametrize("conflict", [["--mode", "plan"], ["--mode=plan"], ["-f"], ["--yolo"]])
def test_u4_cursor_actual_writable_conflicts_are_removed(conflict):
    strategy = CursorCLI(AgentConfig(name="Ada", cli=AgentCLI.CURSOR, model="selected-model"))
    original = strategy.build_command("--force") + conflict
    restricted = strategy.apply_read_only(original, "run_one_shot")
    assert restricted[restricted.index("--mode") + 1] == "ask"
    assert restricted.count("--mode") == 1
    assert restricted[restricted.index("-p") + 1] == "--force"
    assert restricted.count("--force") == 1  # Only the literal prompt remains.
    assert not set(restricted).intersection({"-f", "--yolo", "--mode=plan", "plan"})
    assert original[-len(conflict):] == conflict
