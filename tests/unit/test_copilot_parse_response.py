"""Test that CopilotCLI.parse_response correctly parses both old and new formats."""
import pytest
from cafe.agents.cli.copilot import CopilotCLI
from cafe.core.types import AgentConfig, AgentCLI


class TestCopilotParseResponse:
    """Verify CopilotCLI parses both old and new usage formats."""

    @pytest.fixture
    def copilot_cli(self):
        """Create CopilotCLI instance."""
        config = AgentConfig(name="test-agent", cli=AgentCLI.COPILOT)
        return CopilotCLI(config)

    def test_parse_new_format(self, copilot_cli):
        """Test parsing new 'Breakdown by AI model:' format."""
        stderr_output = """
Total usage est:        1 Premium request
API time spent:         9s
Total session time:     15s
Total code changes:     +0 -0
Breakdown by AI model:
 claude-sonnet-4.5       16.0k in, 74 out, 0 cached (Est. 1 Premium request)
"""
        output_lines = ["HI\n"]

        response, token_usage, denials, model = copilot_cli.parse_response(
            output_lines, stderr_output=stderr_output
        )

        assert response == "HI\n"
        assert model == "claude-sonnet-4.5"
        assert token_usage.input_tokens == 16000
        assert token_usage.output_tokens == 74
        assert token_usage.cache_read_input_tokens == 0

    def test_parse_old_format(self, copilot_cli):
        """Test parsing old 'Usage by model:' format for backward compatibility."""
        stderr_output = """

Total usage est:        1 Premium request
Usage by model:
    claude-sonnet-4.5    14.3k input, 39 output, 10.2k cache read (Est. 1 Premium request)
"""
        output_lines = ["Hello\n"]

        response, token_usage, denials, model = copilot_cli.parse_response(
            output_lines, stderr_output=stderr_output
        )

        assert response == "Hello\n\n"
        assert model == "claude-sonnet-4.5"
        assert token_usage.input_tokens == 14300
        assert token_usage.output_tokens == 39
        assert token_usage.cache_read_input_tokens == 10200

    def test_parse_without_cache(self, copilot_cli):
        """Test parsing without cache read tokens."""
        stderr_output = """

Total usage est:        1 request
Breakdown by AI model:
 gpt-4       5.0k in, 100 out (Est. 1 request)
"""
        output_lines = ["Test\n"]

        response, token_usage, denials, model = copilot_cli.parse_response(
            output_lines, stderr_output=stderr_output
        )

        assert response == "Test\n\n"
        assert model == "gpt-4"
        assert token_usage.input_tokens == 5000
        assert token_usage.output_tokens == 100
        assert token_usage.cache_read_input_tokens == 0

    def test_parse_no_usage_info(self, copilot_cli):
        """Test parsing when no usage information is present."""
        stderr_output = ""
        output_lines = ["Response\n"]

        response, token_usage, denials, model = copilot_cli.parse_response(
            output_lines, stderr_output=stderr_output
        )

        assert response == "Response\n"
        assert model is None
        assert token_usage.input_tokens == 0
        assert token_usage.output_tokens == 0
