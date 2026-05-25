"""Custom exceptions for skill discovery and loading."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cafe.core.types import AgentCLI


class SkillDiscoveryError(LookupError):
    """Raised when a skill cannot be found in the catalog."""

    def __init__(self, name: str, *, cli: "AgentCLI | None" = None) -> None:
        self.skill_name = name
        self.cli = cli
        context = f" (cli={cli.value})" if cli else ""
        super().__init__(f"Skill not found: {name}{context}")
