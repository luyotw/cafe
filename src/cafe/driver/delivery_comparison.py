"""Pure structural checks shared by Driver delivery comparison consumers."""

from __future__ import annotations

import hashlib
from typing import Any

from cafe.core.packet_io import canonical_json


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def obligations(delivery: dict[str, Any]) -> dict[str, str]:
    """Enumerate required coverage; other contract facts inform deviation review."""
    return {
        f"{key}[{index}]": text
        for key in ("in_scope", "acceptance_invariants")
        for index, text in enumerate(delivery[key])
    }


def decide(packet: dict[str, Any], assessment: Any) -> dict[str, Any]:
    """Fail closed on incomplete grounded comparison evidence without mutation."""
    data = packet["data"]
    result = {"decision": "user_handoff", "reason": "incomplete_or_uncertain_evidence"}
    if digest(data) != packet["snapshot_sha256"]:
        return {"decision": "user_handoff", "reason": "stale_evidence"}
    if not data["scheduled"] and data["boundary"]["active"] is False:
        return {"decision": "no_gate", "reason": "no_existing_confirmation_boundary"}
    if not data["eligible"]:
        return {"decision": "user_handoff", "reason": "user_owned_decision"}
    if data["missing_artifacts"] or not data["artifacts"]:
        return result
    if not isinstance(assessment, dict) or set(assessment) != {
        "snapshot_sha256",
        "coverage",
        "deviation",
    }:
        return result
    if assessment["snapshot_sha256"] != packet["snapshot_sha256"]:
        return {"decision": "user_handoff", "reason": "stale_evidence"}
    coverage = assessment["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != set(data["obligations"]):
        return result

    def grounded(record: Any, state: str, fields: set[str]) -> bool:
        if not isinstance(record, dict) or set(record) != fields:
            return False
        if record["status"] != state:
            return False
        if not all(isinstance(record[key], str) and record[key].strip() for key in fields):
            return False
        source = data["artifacts"].get(record["source"])
        return source is not None and record["quote"] in source

    common = {"status", "source", "quote", "reason"}
    for name, record in coverage.items():
        fields = common | (
            {"implementation", "verification"}
            if name.startswith("acceptance_invariants[")
            else set()
        )
        if not grounded(record, "preserved", fields):
            return result
    if not grounded(assessment["deviation"], "clear", common):
        return {"decision": "user_handoff", "reason": "material_or_uncertain_deviation"}
    return {
        "decision": "accept",
        "reason": "requirement_equivalence_evidenced",
        "snapshot_sha256": packet["snapshot_sha256"],
    }
