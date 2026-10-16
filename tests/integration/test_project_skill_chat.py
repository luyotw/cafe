"""I2/I3/U7: real chat preparation reaches both public transport paths."""

# ruff: noqa: F811 -- imported pytest fixtures are requested by parameter name

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.types import AgentCLI
from cafe.ui import chat
from cafe.ui.chat import _prepare_chat_environment as real_prepare
from tests.integration.test_conversation_transport_journeys import phase_chat  # noqa: F401
from tests.unit.test_conversation_transport import init, provider_process  # noqa: F401


@pytest.mark.parametrize("interactive", [False, True])
def test_chat_launch_forwards_current_project_instructions_and_keeps_all_links(
    phase_chat, provider_process, monkeypatch, interactive
):
    issue, _metadata = phase_chat
    authority = Path.cwd() / ".cafe/skills"
    for name in ("chosen", "unselected"):
        directory = authority / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n{name}: {{issue_dir}} {{current_step}}\n"
        )
    source = authority / "chosen/SKILL.md"
    before = source.read_bytes()
    monkeypatch.setattr(chat, "_prepare_chat_environment", real_prepare)
    monkeypatch.setattr(
        chat.PlaybookLoader,
        "load",
        lambda *args: {
            "entry": "implementation",
            "steps": {"implementation": {"role": "developer"}},
            "skills": {"chat": {"shared": ["chosen"]}},
        },
    )
    if interactive:
        original_run = __import__("subprocess").run
        launch = MagicMock(return_value=__import__("subprocess").CompletedProcess([], 0))
        monkeypatch.setattr(
            "subprocess.run",
            lambda command, **kwargs: (
                launch(command, **kwargs)
                if command[0] == "claude"
                else original_run(command, **kwargs)
            ),
        )
        kwargs = {"initial_prompt": "current-message"}
    else:
        launch = provider_process([init("fresh"), dict(type="result", content="reply")])
        kwargs = {"prompt": "current-message"}
    assert chat.launch_chat_session("developer", "x", phase_name="implementation", **kwargs) == 0
    command = launch.call_args.args[0]
    prompt = " ".join(command)
    assert "current-message" in prompt
    assert f"chosen: {issue.resolve()} implementation" in prompt
    assert "unselected:" not in prompt and "{issue_dir}" not in prompt
    assert source.read_bytes() == before
    for cli in (AgentCLI.CODEX, AgentCLI.CLAUDE, AgentCLI.COPILOT):
        root = (
            Path.cwd()
            / {AgentCLI.CODEX: ".codex", AgentCLI.CLAUDE: ".claude", AgentCLI.COPILOT: ".copilot"}[
                cli
            ]
            / "skills"
        )
        assert (root / "chosen").resolve() == source.parent
        assert (root / "unselected").is_symlink()


def test_requested_chat_refreshes_old_copy_and_launches_agent(
    phase_chat, provider_process, monkeypatch
):
    """An old same-name copy cannot block the resolved skill's agent launch."""
    source = Path.cwd() / ".cafe/skills/chosen"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: chosen\ndescription: project\n---\n")
    collision = Path.cwd() / ".claude/skills/chosen"
    collision.mkdir(parents=True)
    (collision / "SKILL.md").write_text("user-owned")
    monkeypatch.setattr(chat, "_prepare_chat_environment", real_prepare)
    monkeypatch.setattr(
        chat.PlaybookLoader,
        "load",
        lambda *args: {
            "entry": "implementation",
            "steps": {"implementation": {"role": "developer"}},
            "skills": {"chat": {"shared": ["chosen"]}},
        },
    )
    launch = provider_process([init("fresh"), dict(type="result", content="reply")])
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 0
    )
    launch.assert_called_once()
    assert collision.is_symlink() and collision.resolve() == source
    assert "project" in (collision / "SKILL.md").read_text()
    assert "user-owned" not in " ".join(launch.call_args.args[0])
