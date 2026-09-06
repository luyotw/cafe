"""Fresh bounded evidence comparison; this module never runs preflight work."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from cafe.core.packet_io import canonical_json


class Freshness(str, Enum):
    """The only continuation classifications exposed by the contract boundary."""

    SAME_SEMANTICS = "same_semantics"
    MATERIAL_CHANGE = "material_change"
    UNKNOWN = "unknown"


def compare_freshness(contract: Mapping[str, Any], fresh_facts: Mapping[str, Any]) -> Freshness:
    """Compare caller-supplied semantic facts without treating diagnostics as policy."""
    if not isinstance(fresh_facts, Mapping):
        return Freshness.UNKNOWN
    current = contract.get("preflight")
    if not isinstance(current, Mapping):
        return Freshness.UNKNOWN
    expected_semantics = current.get("semantic_facts")
    expected_assumptions = current.get("material_assumptions")
    live_semantics = fresh_facts.get("semantic_facts")
    live_assumptions = fresh_facts.get("material_assumptions")
    if not all(
        isinstance(item, Mapping)
        for item in (expected_semantics, expected_assumptions, live_semantics, live_assumptions)
    ):
        return Freshness.UNKNOWN
    try:
        expected = canonical_json(
            {
                "semantic_facts": dict(expected_semantics),
                "material_assumptions": dict(expected_assumptions),
            }
        )
        live = canonical_json(
            {
                "semantic_facts": dict(live_semantics),
                "material_assumptions": dict(live_assumptions),
            }
        )
    except (TypeError, ValueError):
        return Freshness.UNKNOWN
    return Freshness.SAME_SEMANTICS if live == expected else Freshness.MATERIAL_CHANGE
