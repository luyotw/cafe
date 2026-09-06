"""Public invariants for the issue-scoped Driver contract application."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from cafe.driver import (
    ActivateConfirmedContract,
    DriverEntryRequest,
    Freshness,
    LegacyAdoptionRequest,
    ReplaceConfirmedContract,
    activate_confirmed_contract,
    adopt_legacy_contract,
    evaluate_driver_entry,
    replace_confirmed_contract,
)


def _proposal() -> dict[str, object]:
    """Return a complete, PR-capable confirmed policy without runtime progress."""
    return {
        "playbook": {
            "id": "standard",
            "source": "builtin:standard",
            "selection_rationale": "Matches the confirmed development journey.",
            "semantic_fingerprint": {"steps": ["spec", "develop", "review", "pr"]},
            "capability_requests": ["cafe.pr.publish"],
        },
        "locales": {
            "conversation": {"value": "en", "source": "playbook:standard"},
            "repository_content": {"value": "en", "source": "user_confirmation"},
        },
        "confirmation_contract": {
            "user_required": ["spec", "plan"],
            "driver_confirmable": [],
            "mandatory_human_stops": ["spec", "plan"],
            "pr_auto_create": True,
        },
        "reactive_user_handoffs": {
            "need_clarification": "user_required",
            "need_permission": "user_required",
            "alignment_checkpoint": "driver_resolvable_when_clear",
        },
        "mandate": {"source": "strategic_context", "boundaries": ["issue scope"]},
        "issue_assessment": {
            "nature": "feature",
            "scale": "medium",
            "risks": ["integration"],
            "rationale": "Durable authority is required for takeover.",
        },
        "phases": [
            {
                "name": "develop",
                "assignee_type": "agent",
                "role": "developer",
                "skill": "cafe-develop",
                "execution_profile": "implementation",
                "chain": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                "rationale": "The confirmed implementation chain.",
                "capabilities": [],
            },
            {
                "name": "publish",
                "assignee_type": "agent",
                "role": "publisher",
                "skill": "cafe-pr",
                "execution_profile": "publication",
                "chain": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                "rationale": "The generic publication phase.",
                "capabilities": ["cafe.pr.publish"],
            },
        ],
        "proactive_review": {
            "phase_decisions": [
                {
                    "phase": "develop",
                    "decision": "not_required",
                    "rationale": "No confirmed proactive review is needed for this phase.",
                },
                {
                    "phase": "publish",
                    "decision": "not_required",
                    "rationale": "Publication is governed by the generic PR contract.",
                },
            ]
        },
        "model_adjustment": {
            "authority": "user_approval_required",
            "confirmed_by": "user",
            "confirmed_at": "2026-09-06T02:00:00+00:00",
        },
        "driver": {"mode": "unattended"},
        "checkout": {"kind": "current_checkout"},
        "pr": {"auto_create": True, "post_todo_list": []},
        "semantic_facts": {
            "effective_graph": ["spec", "develop", "review", "publish"],
            "assignees": {"develop": "agent", "publish": "agent"},
        },
        "material_assumptions": {"provider": "codex", "permissions": ["local"]},
    }


def _activation(issue_dir: Path, proposal: dict[str, object] | None = None) -> ActivateConfirmedContract:
    return ActivateConfirmedContract(
        issue_dir=issue_dir,
        issue_name="issue474",
        workflow_id="workflow-474",
        confirmed_by="user",
        confirmed_at=datetime(2026, 9, 6, 2, tzinfo=timezone.utc),
        proposal=proposal or _proposal(),
    )


def test_public_application_contract_persists_only_a_complete_valid_policy(tmp_path: Path) -> None:
    """Test List 1: activation exposes a bounded result, not a raw document API."""
    result = activate_confirmed_contract(_activation(tmp_path / "issue"))

    assert result.created is True
    assert result.revision == 1
    assert (tmp_path / "issue" / "driver" / "contract.json").is_file()

    invalid = _proposal()
    invalid["pr"] = {"auto_create": False}
    with pytest.raises(ValueError):
        activate_confirmed_contract(_activation(tmp_path / "invalid", invalid))

    runtime_state = _proposal()
    runtime_state["session"] = {"provider_session": "must-not-persist"}
    with pytest.raises(ValueError):
        activate_confirmed_contract(_activation(tmp_path / "runtime", runtime_state))


def test_semantic_freshness_ignores_metadata_but_fails_closed_for_unknown_or_material(
    tmp_path: Path,
) -> None:
    """Test List 2: only fresh semantic and material evidence controls continuation."""
    issue_dir = tmp_path / "issue"
    activate_confirmed_contract(_activation(issue_dir))
    proposal = _proposal()
    facts = {
        "semantic_facts": deepcopy(proposal["semantic_facts"]),
        "material_assumptions": deepcopy(proposal["material_assumptions"]),
        "metadata": {"raw_digest": "new-source-copy", "checked_at": "later"},
    }
    same = evaluate_driver_entry(
        DriverEntryRequest(
            issue_dir=issue_dir,
            issue_name="issue474",
            workflow_id="workflow-474",
            fresh_facts=facts,
        )
    )
    assert same.freshness is Freshness.SAME_SEMANTICS
    assert same.generic_inputs["pr_auto_create"] is True
    assert same.proactive_review[0]["phase"] == "develop"

    changed = deepcopy(facts)
    changed["semantic_facts"]["effective_graph"].append("release")
    assert (
        evaluate_driver_entry(
            DriverEntryRequest(issue_dir, "issue474", "workflow-474", changed)
        ).freshness
        is Freshness.MATERIAL_CHANGE
    )
    assert (
        evaluate_driver_entry(
            DriverEntryRequest(issue_dir, "issue474", "workflow-474", {"metadata": {}})
        ).freshness
        is Freshness.UNKNOWN
    )


def test_replacement_is_compare_and_swap_and_delegation_cannot_change_policy(
    tmp_path: Path,
) -> None:
    """Test List 3: readers retain valid authority when a replacement is rejected."""
    issue_dir = tmp_path / "issue"
    activated = activate_confirmed_contract(_activation(issue_dir))
    before = (issue_dir / "driver" / "contract.json").read_bytes()
    proposal = _proposal()
    proposal["proactive_review"]["phase_decisions"][0]["decision"] = "required"
    with pytest.raises(ValueError):
        replace_confirmed_contract(
            ReplaceConfirmedContract(
                issue_dir,
                "issue474",
                "workflow-474",
                "driver",
                datetime(2026, 9, 6, 3, tzinfo=timezone.utc),
                proposal,
                activated.contract_sha256,
                "delegated_change",
                {"authority_field": "model_adjustment"},
            )
        )
    assert (issue_dir / "driver" / "contract.json").read_bytes() == before

    reconfirmed = _proposal()
    reconfirmed["pr"] = {"auto_create": False, "post_todo_list": []}
    reconfirmed["confirmation_contract"]["pr_auto_create"] = False
    replacement = replace_confirmed_contract(
        ReplaceConfirmedContract(
            issue_dir,
            "issue474",
            "workflow-474",
            "user",
            datetime(2026, 9, 6, 3, tzinfo=timezone.utc),
            reconfirmed,
            activated.contract_sha256,
            "user_reconfirmation",
        )
    )
    assert replacement.revision == 2
    with pytest.raises(ValueError):
        replace_confirmed_contract(
            ReplaceConfirmedContract(
                issue_dir,
                "issue474",
                "workflow-474",
                "user",
                datetime(2026, 9, 6, 4, tzinfo=timezone.utc),
                _proposal(),
                activated.contract_sha256,
                "user_reconfirmation",
            )
        )


def test_legacy_adoption_requires_complete_identity_bound_confirmation(tmp_path: Path) -> None:
    """Test List 4: only deterministic legacy evidence can become sole authority."""
    issue_dir = tmp_path / "legacy"
    confirmation = issue_dir / "driver" / "legacy_confirmation.json"
    confirmation.parent.mkdir(parents=True)
    confirmation.write_text(
        json.dumps(
            {
                "identity": {"issue_name": "issue474", "workflow_id": "workflow-474"},
                "confirmed_by": "user",
                "confirmed_at": "2026-09-06T02:00:00+00:00",
                "proposal": _proposal(),
            }
        ),
        encoding="utf-8",
    )
    adopted = adopt_legacy_contract(LegacyAdoptionRequest(issue_dir, "issue474", "workflow-474"))
    assert adopted.adopted is True
    assert adopted.revision == 1

    unresolved = adopt_legacy_contract(
        LegacyAdoptionRequest(tmp_path / "ambiguous", "issue474", "workflow-474")
    )
    assert unresolved.adopted is False
    assert unresolved.disposition == "reconfirmation_required"
