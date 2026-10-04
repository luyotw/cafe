"""Plan U4/U6: native parameters, not a whole-process safety proof."""

import pytest

from cafe.agents.cli import CodexCLI
from cafe.core.types import AgentCLI, AgentConfig


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
