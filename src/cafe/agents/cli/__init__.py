"""CLI strategy implementations for different agent CLIs."""

from cafe.agents.cli.abstract import AbstractCLI
from cafe.agents.cli.claude import ClaudeCLI

__all__ = ["AbstractCLI", "ClaudeCLI"]
