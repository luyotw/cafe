"""Registry for trusted native capability providers."""

from __future__ import annotations

from collections.abc import Iterable

from cafe.agents.capabilities.contracts import CapabilityProvider
from cafe.core.types import AgentCLI


class CapabilityRegistry:
    """Resolve exactly one built-in provider for a capability and agent CLI."""

    def __init__(self, providers: Iterable[CapabilityProvider] = ()) -> None:
        self._providers: dict[tuple[str, AgentCLI], CapabilityProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: CapabilityProvider) -> None:
        """Register a trusted provider and reject ambiguous routing."""
        capability_id = provider.capability_id.strip()
        provider_id = provider.provider_id.strip()
        if not capability_id or not provider_id:
            raise ValueError("capability and provider ids must not be empty")
        key = (capability_id, provider.cli)
        if key in self._providers:
            raise ValueError(
                f"duplicate provider for capability {capability_id!r} and CLI "
                f"{provider.cli.value!r}"
            )
        self._providers[key] = provider

    def resolve(self, capability_id: str, cli: AgentCLI) -> CapabilityProvider | None:
        """Return the provider registered for one capability/CLI pair."""
        return self._providers.get((capability_id, cli))

    def providers_for(self, capability_id: str) -> tuple[CapabilityProvider, ...]:
        """Return immutable provider metadata for diagnostics and tests."""
        return tuple(
            provider
            for (registered_id, _), provider in self._providers.items()
            if registered_id == capability_id
        )
