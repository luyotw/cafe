"""CLI strategy implementations for different agent CLIs."""

from cafe.agents.cli.abstract import AbstractCLI
from cafe.agents.cli.claude import ClaudeCLI
from cafe.agents.cli.copilot import CopilotCLI
from cafe.agents.cli.cursor import CursorCLI
from cafe.agents.cli.gemini import GeminiCLI

__all__ = ["AbstractCLI", "ClaudeCLI", "CopilotCLI", "CursorCLI", "GeminiCLI"]
