"""Driver contract journeys across the skill-owned entry boundary."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest

from cafe.driver import (
    ActivateConfirmedContract,
    LegacyAdoptionRequest,
    activate_confirmed_contract,
    adopt_legacy_contract,
)


PROJECT_ROOT = Path(__file__).parents[2]
ENTRY_SCRIPT = (
    PROJECT_ROOT
    / "src"
    / "cafe"
    / "data"
    / "skills"
    / "use-cafe-workflow"
    / "scripts"
    / "validate_driver_entry.py"
)


def _entry_adapter():
    spec = importlib.util.spec_from_file_location("driver_entry_adapter", ENTRY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proposal() -> dict[str, object]:
    phases = [
        {
            "name": "develop",
            "chain": [{"cli": "codex", "model": "exact"}],
            "rationale": "Confirmed implementation chain.",
        },
    ]
    proposal: dict[str, object] = {
        "locales": {
            "conversation": {"value": "en", "source": "playbook"},
        },
        "confirmation_contract": {
            "user_required": [],
            "driver_confirmable": [],
            "mandatory_human_stops": [],
        },
        "reactive_user_handoffs": {
            "need_clarification": "user_required",
            "need_permission": "user_required",
            "alignment_checkpoint": "user_required",
        },
        "mandate": {"source": "test", "boundaries": ["issue"]},
        "issue_assessment": {
            "nature": "feature",
            "scale": "small",
            "risks": [],
            "rationale": "test",
        },
        "phases": phases,
        "proactive_review": {
            "phase_decisions": [
                {"phase": "develop", "decision": "not_required", "rationale": "No schedule."},
            ]
        },
        "model_adjustment": {
            "authority": "user_approval_required",
        },
        "driver": {"mode": "unattended"},
        "checkout": {"kind": "current_checkout"},
        "semantic_facts": {},
        "material_assumptions": {"permissions": ["local"], "provider": "codex"},
    }
    proposal["semantic_facts"] = _fresh_policy_facts(proposal)
    return proposal


def _fresh_policy_facts(proposal: dict[str, object]) -> dict[str, object]:
    fields = (
        "locales",
        "confirmation_contract",
        "reactive_user_handoffs",
        "mandate",
        "issue_assessment",
        "phases",
        "proactive_review",
        "model_adjustment",
        "driver",
        "checkout",
    )
    return {"effective_policy": {name: deepcopy(proposal[name]) for name in fields}}


def _activate(issue_dir: Path) -> dict[str, object]:
    proposal = _proposal()
    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=issue_dir,
            issue_name="journey",
            workflow_id="workflow-journey",
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 6, 2, tzinfo=timezone.utc),
            proposal=proposal,
        )
    )
    return proposal


def test_resume_and_cold_takeover_reach_the_same_safe_authority_decision(tmp_path: Path) -> None:
    """Integration 2: metadata churn does not change policy for either Driver."""
    issue_dir = tmp_path / "issue"
    proposal = _activate(issue_dir)
    facts = {
        "semantic_facts": proposal["semantic_facts"],
        "material_assumptions": proposal["material_assumptions"],
        "metadata": {"cache_key": "changed", "checked_at": "later"},
    }
    adapter = _entry_adapter()

    primary = adapter.validate_entry(
        issue_dir=issue_dir, issue_name="journey", workflow_id="workflow-journey", fresh_facts=facts
    )
    backup = adapter.validate_entry(
        issue_dir=issue_dir, issue_name="journey", workflow_id="workflow-journey", fresh_facts=facts
    )
    assert primary["runtime"] == backup["runtime"]
    assert "generic_inputs" not in primary

    changed = _proposal()
    changed["phases"][0]["chain"][0]["model"] = "changed-model"
    facts["semantic_facts"] = _fresh_policy_facts(changed)
    with pytest.raises(ValueError):
        adapter.validate_entry(
            issue_dir=issue_dir,
            issue_name="journey",
            workflow_id="workflow-journey",
            fresh_facts=facts,
        )


def test_ambiguous_legacy_state_stops_for_reconfirmation_without_fallback(tmp_path: Path) -> None:
    """Integration 3: a missing complete legacy confirmation never grants authority."""
    result = adopt_legacy_contract(
        LegacyAdoptionRequest(tmp_path / "ambiguous", "journey", "workflow-journey")
    )
    assert result.adopted is False
    assert result.disposition == "reconfirmation_required"
