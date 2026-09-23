"""Current delivery policy and explicit historical Driver fixtures."""

import hashlib
from copy import deepcopy

from cafe.core.packet_io import canonical_json


def delivery_contract():
    return {
        "schema_version": 3,
        "outcome": "Readers can export a complete report.",
        "in_scope": ["Export text and citations.", "Handle an empty report."],
        "out_of_scope": ["Automatic publication."],
        "acceptance_invariants": ["Exports preserve every citation and existing format."],
        "implementation_direction": "Reuse the existing export path.",
        "permissions": ["Local files only."],
        "constraints": [
            "Keep the existing export interface.",
            "No new dependencies.",
            "Keep the existing format.",
            "Cover empty reports.",
            "No automatic publication.",
            "No paid services.",
        ],
        "closeout_plan": {"deliver": [], "cleanup": []},
    }


def legacy_delivery_contract(*, schema_version=1):
    """Return the complete historical v1/v2 shape, never a downgraded v3."""
    if schema_version not in (1, 2):
        raise ValueError("Legacy delivery fixtures support only v1 and v2")
    contract = {
        "schema_version": 1,
        "outcome": "Readers can export a complete report.",
        "motivation": "Support offline review.",
        "in_scope": ["Export text and citations.", "Handle an empty report."],
        "out_of_scope": ["Automatic publication."],
        "acceptance_invariants": ["Exports preserve every citation and existing format."],
        "required_evidence": ["Verify populated and empty exports against expected files."],
        "implementation_direction": "Reuse the existing export path.",
        "constraints": {
            "architecture": ["Keep the existing export interface."],
            "dependencies": ["No new dependencies."],
            "compatibility": ["Keep the existing format."],
            "quality": ["Cover empty reports."],
            "permissions": ["Local files only."],
            "external_side_effects": ["No automatic publication."],
            "cost": ["No paid services."],
        },
        "allowed_variations": ["Fewer files or abstractions with equivalent coverage."],
        "deviation_triggers": ["Any omitted requirement or new integration requires the user."],
    }
    if schema_version == 2:
        contract["schema_version"] = 2
        contract["closeout_plan"] = {"deliver": [], "cleanup": []}
    return contract


def legacy_driver_contract(*, schema_version, identity, driver=None):
    """Build a historically valid Driver v3/v4 document with its original digest."""
    if schema_version not in (3, 4):
        raise ValueError("Legacy Driver fixtures support only v3 and v4")
    policy = {
        "locales": {"conversation": {"value": "en", "source": "test"}},
        "confirmation_contract": {
            "user_required": ["spec", "plan"],
            "driver_confirmable": [],
            "mandatory_human_stops": ["spec", "plan"],
        },
        "reactive_user_handoffs": {
            "need_clarification": "user_required",
            "need_permission": "user_required",
            "alignment_checkpoint": "driver_resolvable_when_clear",
        },
        "mandate": {"source": "test", "boundaries": ["issue scope"]},
        "issue_assessment": {
            "nature": "feature",
            "scale": "small",
            "risks": [],
            "rationale": "Historical policy for an already confirmed workflow.",
        },
        "phases": [
            {
                "name": "develop",
                "chain": [{"cli": "codex", "model": "exact"}],
                "rationale": "The historical confirmed model chain.",
            }
        ],
        "proactive_review": {
            "phase_decisions": [
                {
                    "phase": "develop",
                    "decision": "not_required",
                    "rationale": "No review was scheduled.",
                }
            ]
        },
        "driver": deepcopy(driver) if driver is not None else {"mode": "unattended"},
        "checkout": {"kind": "current_checkout"},
    }
    if schema_version == 4:
        policy["delivery_contract"] = legacy_delivery_contract(schema_version=2)
    assumptions = {"provider": "codex", "permissions": ["local"]}
    projection = {**policy, "identity": identity, "material_assumptions": assumptions}
    return {
        "schema_version": schema_version,
        "identity": deepcopy(identity),
        "revision": {"generation": 1, "previous_contract_sha256": None},
        "provenance": {
            "kind": "initial",
            "confirmed_by": "user",
            "confirmed_at": "2026-09-06T02:00:00+00:00",
            "proposal_digest": hashlib.sha256(canonical_json(projection)).hexdigest(),
        },
        **policy,
        "preflight": {
            "semantic_facts": {"effective_policy": deepcopy(policy)},
            "material_assumptions": assumptions,
        },
    }
