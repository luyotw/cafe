"""Plan U4/U6/U8: native options and context/default compatibility."""

import pytest

from cafe.agents.cli import GeminiCLI
from cafe.core.types import AgentCLI, AgentConfig


@pytest.mark.parametrize("session", [None, "stored-session"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
@pytest.mark.parametrize("literal", ['--approval-mode', '--yolo', '-y'])
def test_u4_gemini_native_restriction_preserves_option_like_values(session, operation, literal):
    strategy = GeminiCLI(AgentConfig(
        name="Ada", cli=AgentCLI.GEMINI, model=literal, session_id=session))
    command = (strategy.build_interactive_command(literal)
               if operation == "open_interactive_session" else strategy.build_command(literal))
    original = command.copy()
    restricted = strategy.apply_read_only(command, operation)
    assert command == original
    assert restricted[0] == 'gemini'
    assert restricted[restricted.index('--approval-mode') + 1] == 'plan'
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
    assert not set(actual).intersection(['--yolo', '-y'])


def test_u6_gemini_unsupported_operation_is_identified():
    strategy = GeminiCLI(AgentConfig(name="Ada", cli=AgentCLI.GEMINI))
    with pytest.raises(ValueError) as caught:
        strategy.apply_read_only(['gemini'], "deliver_to_exact_session")
    assert 'gemini' in str(caught.value)
    assert "deliver_to_exact_session" in str(caught.value)


def test_u8_gemini_ordinary_command_remains_writable():
    strategy = GeminiCLI(AgentConfig(name="Ada", cli=AgentCLI.GEMINI, model="selected-model"))
    command = strategy.build_command("diagnose")
    assert "--approval-mode" not in command


@pytest.mark.parametrize("session", ["--force", "--yolo", "--allow-all-tools", "--approval-mode", "--mode", "--available-tools", "--allow-tool"])
@pytest.mark.parametrize("operation", ["open_interactive_session", "run_one_shot"])
def test_u4_gemini_literal_session_identity_is_preserved(session, operation):
    strategy = GeminiCLI(AgentConfig(
        name="Ada", cli=AgentCLI.GEMINI, model="selected-model", session_id=session))
    command = (strategy.build_interactive_command()
               if operation == "open_interactive_session" else strategy.build_command("diagnose"))
    original = command.copy()
    restricted = strategy.apply_read_only(command, operation)
    assert restricted[restricted.index("--resume") + 1] == session
    assert command == original


@pytest.mark.parametrize("conflict", [["--approval-mode", "yolo"], ["--approval-mode=yolo"], ["--yolo"], ["-y"]])
def test_u4_gemini_actual_approval_conflicts_are_replaced(conflict):
    strategy = GeminiCLI(AgentConfig(name="Ada", cli=AgentCLI.GEMINI, model="selected-model"))
    original = strategy.build_command("--yolo") + conflict
    restricted = strategy.apply_read_only(original, "run_one_shot")
    assert restricted[restricted.index("--approval-mode") + 1] == "plan"
    assert restricted.count("--approval-mode") == 1
    assert restricted[restricted.index("-p") + 1] == "--yolo"
    assert "yolo" not in restricted and "--approval-mode=yolo" not in restricted
    assert original[-len(conflict):] == conflict
