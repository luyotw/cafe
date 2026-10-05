"""Fresh bounded evidence comparison; this module never runs preflight work."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from cafe.core.packet_io import canonical_json

from ._schema import freshness_semantic_facts
from .constraints import validate_evidence


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
    if live != expected:
        return Freshness.MATERIAL_CHANGE
    recorded = contract.get("provenance", {}).get("runtime_constraints")
    refreshed = fresh_facts.get("runtime_constraints")
    if recorded is None or refreshed is None:
        return Freshness.UNKNOWN
    try:
        validate_evidence(recorded)
        validate_evidence(refreshed)
    except ValueError:
        return Freshness.UNKNOWN
    return (Freshness.SAME_SEMANTICS if canonical_json(recorded) == canonical_json(refreshed)
            else Freshness.MATERIAL_CHANGE)
