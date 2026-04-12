"""Tests for CodexCLI implementation."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.agents.cli.codex import CodexCLI
from cafe.core.types import AgentCLI, AgentConfig, TokenUsage


@pytest.fixture
def codex_config():
    """Create a Codex config."""
    return AgentConfig(name="test_codex", cli=AgentCLI.CODEX)


@pytest.fixture
def codex_config_with_session():
    """Create a Codex config with session ID."""
    return AgentConfig(name="test_codex", cli=AgentCLI.CODEX, session_id="session-123")


@pytest.fixture
def codex_config_with_model():
    """Create a Codex config with model."""
    return AgentConfig(name="test_codex", cli=AgentCLI.CODEX, model="gpt-5-codex")


class TestCodexCLIBuildCommand:
    """Test build_command()."""

    def test_build_basic_command(self, codex_config):
        cli = CodexCLI(codex_config)
        cmd = cli.build_command("test prompt")

        assert cmd[:6] == ["codex", "-C", str(Path.cwd().resolve()), "-a", "never", "exec"]
        assert "--json" in cmd
        assert cmd[-1] == "test prompt"

    def test_build_command_with_session(self, codex_config_with_session):
        cli = CodexCLI(codex_config_with_session)
        cmd = cli.build_command("test prompt")

        assert cmd[:7] == ["codex", "-C", str(Path.cwd().resolve()), "-a", "never", "exec", "resume"]
        assert "--json" in cmd
        assert cmd[-2] == "session-123"
        assert cmd[-1] == "test prompt"

    def test_build_command_with_model(self, codex_config_with_model):
        cli = CodexCLI(codex_config_with_model)
        cmd = cli.build_command("test prompt")

        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "gpt-5-codex"

    def test_build_command_adds_directories(self, codex_config):
        cli = CodexCLI(codex_config)
        cmd = cli.build_command("test prompt", allowed_directories=["src", ".cafe"])

        add_dir_values = [
            cmd[index + 1]
            for index, token in enumerate(cmd)
            if token == "--add-dir"
        ]
        assert "src" in add_dir_values
        assert ".cafe" in add_dir_values

    def test_build_command_ignores_directories_when_resuming(self, codex_config_with_session):
        cli = CodexCLI(codex_config_with_session)
        cmd = cli.build_command("test prompt", allowed_directories=["src"])

        assert "--add-dir" not in cmd

    def test_build_command_adds_worktree_git_dir_on_fresh_exec(self, codex_config):
        cli = CodexCLI(codex_config)
        worktree_git_dir = Path("/tmp/main-repo/.git/worktrees/issue187")

        with patch("cafe.agents.cli.codex.get_git_dir", return_value=worktree_git_dir):
            cmd = cli.build_command("test prompt", allowed_directories=[".cafe"])

        add_dir_values = [
            cmd[index + 1]
            for index, token in enumerate(cmd)
            if token == "--add-dir"
        ]
        assert ".cafe" in add_dir_values
        assert str(worktree_git_dir) in add_dir_values

    def test_build_environment_uses_repo_local_codex_home(self, codex_config):
        cli = CodexCLI(codex_config)

        env = cli.build_environment()

        assert env["CODEX_HOME"] == str(Path.cwd().resolve() / ".codex")


class TestCodexCLITranslateAllowedTools:
    """Test translate_allowed_tools()."""

    def test_translate_returns_empty_list(self, codex_config):
        cli = CodexCLI(codex_config)
        assert cli.translate_allowed_tools(["read", "write(/tmp/x)"]) == []


class TestCodexCLIParseResponse:
    """Test parse_response()."""

    def test_parse_response_extracts_final_message_and_usage(self, codex_config):
        cli = CodexCLI(codex_config)
        output_lines = [
            json.dumps({"type": "thread.started", "thread_id": "thread-123"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message", "text": "First"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_2", "type": "agent_message", "text": "Final"},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "output_tokens": 30,
                    },
                }
            ),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "Final"
        assert isinstance(token_usage, TokenUsage)
        assert token_usage.input_tokens == 100
        assert token_usage.cache_read_input_tokens == 20
        assert token_usage.output_tokens == 30
        assert token_usage.turn_usages == [
            {
                "turn": 1,
                "input_tokens": 100,
                "output_tokens": 30,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 20,
            }
        ]
        assert permission_denials == []

    def test_parse_response_extracts_all_turn_usages(self, codex_config):
        cli = CodexCLI(codex_config)
        output_lines = [
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 80,
                        "cached_input_tokens": 10,
                        "output_tokens": 20,
                    },
                }
            ),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_2", "type": "agent_message", "text": "Final"},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 120,
                        "cached_input_tokens": 30,
                        "output_tokens": 40,
                        "cache_creation_input_tokens": 5,
                    },
                }
            ),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "Final"
        assert token_usage.input_tokens == 120
        assert token_usage.output_tokens == 40
        assert token_usage.cache_read_input_tokens == 30
        assert token_usage.cache_creation_input_tokens == 5
        assert token_usage.turn_usages == [
            {
                "turn": 1,
                "input_tokens": 80,
                "output_tokens": 20,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 10,
            },
            {
                "turn": 2,
                "input_tokens": 120,
                "output_tokens": 40,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 30,
            },
        ]
        assert permission_denials == []

    def test_parse_response_ignores_invalid_json(self, codex_config):
        cli = CodexCLI(codex_config)
        output_lines = [
            "not json",
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Final"},
                }
            ),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "Final"
        assert token_usage.input_tokens == 0
        assert permission_denials == []


class TestCodexCLIExtractSessionId:
    """Test extract_session_id()."""

    def test_extract_thread_id(self, codex_config):
        cli = CodexCLI(codex_config)
        session_id = cli.extract_session_id(
            [json.dumps({"type": "thread.started", "thread_id": "thread-123"})]
        )

        assert session_id == "thread-123"

    def test_extract_session_id_returns_none_when_missing(self, codex_config):
        cli = CodexCLI(codex_config)
        assert cli.extract_session_id([json.dumps({"type": "turn.started"})]) is None


class TestCodexCLICreateSession:
    """Test create_session()."""

    def test_create_session_is_noop_for_codex(self, codex_config_with_model):
        cli = CodexCLI(codex_config_with_model)

        with patch("subprocess.run") as mock_run:
            session_id = cli.create_session()

        assert session_id == ""
        mock_run.assert_not_called()
