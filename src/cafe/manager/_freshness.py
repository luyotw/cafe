"""Fresh bounded evidence comparison; this module never runs preflight work."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from cafe.core.packet_io import canonical_json

from ._schema import freshness_semantic_facts


class Freshness(str, Enum):
    """The only continuation classifications exposed by the contract boundary."""

    SAME_SEMANTICS = "same_semantics"
    MATERIAL_CHANGE = "material_change"
    UNKNOWN = "unknown"


def compare_freshness(contract: Mapping[str, Any], fresh_facts: Mapping[str, Any]) -> Freshness:
    """Compare caller-supplied semantic facts without treating diagnostics as policy."""
    if not isinstance(fresh_facts, Mapping):
        return Freshness.UNKNOWN
    live_semantics = fresh_facts.get("semantic_facts")
    if (
        not isinstance(live_semantics, Mapping)
        or set(live_semantics) != {"effective_policy"}
        or not isinstance(live_semantics.get("effective_policy"), Mapping)
    ):
        return Freshness.UNKNOWN
    try:
        expected = canonical_json(freshness_semantic_facts(contract))
        live = canonical_json(dict(live_semantics))
    except (TypeError, ValueError):
        return Freshness.UNKNOWN
    return Freshness.SAME_SEMANTICS if live == expected else Freshness.MATERIAL_CHANGE
