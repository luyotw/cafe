"""Public invariants for the issue-scoped Driver contract application."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
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
    """Return a complete Driver-owned policy without generic configuration."""
    proposal: dict[str, object] = {
        "locales": {
            "conversation": {"value": "en", "source": "playbook:standard"},
        },
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
                "chain": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                "rationale": "The confirmed implementation chain.",
            },
        ],
        "proactive_review": {
            "phase_decisions": [
                {
                    "phase": "develop",
                    "decision": "not_required",
                    "rationale": "No confirmed proactive review is needed for this phase.",
                },
            ]
        },
        "model_adjustment": {
            "authority": "user_approval_required",
        },
        "driver": {"mode": "unattended"},
        "checkout": {"kind": "current_checkout"},
        "semantic_facts": {},
        "material_assumptions": {"provider": "codex", "permissions": ["local"]},
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


def _activation(
    issue_dir: Path,
    proposal: dict[str, object] | None = None,
    *,
    workflow_id: str = "workflow-474",
) -> ActivateConfirmedContract:
    return ActivateConfirmedContract(
        issue_dir=issue_dir,
        issue_name="issue474",
        workflow_id=workflow_id,
        confirmed_by="user",
        confirmed_at=datetime(2026, 9, 6, 2, tzinfo=timezone.utc),
        proposal=proposal or _proposal(),
    )


def _callback_module():
    path = (
        Path(__file__).parents[2]
        / "src/cafe/data/skills/use-cafe-workflow/scripts/workflow_event_callback.py"
    )
    spec = importlib.util.spec_from_file_location("issue474_contract_callback_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_application_contract_persists_only_a_complete_valid_policy(tmp_path: Path) -> None:
    """Test List 1: activation exposes a bounded result, not a raw document API."""
    result = activate_confirmed_contract(_activation(tmp_path / "issue"))

    assert result.created is True
    assert result.revision == 1
    assert (tmp_path / "issue" / "driver" / "contract.json").is_file()

    for field, value in (
        ("pr", {"auto_create": False}),
        ("playbook", {"id": "standard"}),
        ("generic_inputs", {"playbook_id": "standard"}),
    ):
        invalid = _proposal()
        invalid[field] = value
        with pytest.raises(ValueError):
            activate_confirmed_contract(_activation(tmp_path / f"invalid-{field}", invalid))

    partial_facts = _proposal()
    partial_facts["semantic_facts"] = {"effective_graph": ["develop"]}
    with pytest.raises(ValueError, match="complete effective policy"):
        activate_confirmed_contract(_activation(tmp_path / "partial", partial_facts))

    runtime_state = _proposal()
    runtime_state["session"] = {"provider_session": "must-not-persist"}
    with pytest.raises(ValueError):
        activate_confirmed_contract(_activation(tmp_path / "runtime", runtime_state))


def test_contract_accepts_legacy_model_adjustment_confirmation_evidence(tmp_path: Path) -> None:
    """Existing durable contracts retain their recorded confirmation provenance."""
    proposal = _proposal()
    proposal["model_adjustment"] = {
        "authority": "user_approval_required",
        "confirmed_by": "user",
        "confirmed_at": "2026-09-06T02:00:00+00:00",
    }
    proposal["semantic_facts"] = _fresh_policy_facts(proposal)

    result = activate_confirmed_contract(_activation(tmp_path / "legacy", proposal))

    assert result.created is True


def test_legacy_model_adjustment_evidence_has_current_semantic_freshness(
    tmp_path: Path,
) -> None:
    """Compatibility-only confirmation fields do not make unchanged policy stale."""
    issue_dir = tmp_path / "legacy"
    legacy_proposal = _proposal()
    legacy_proposal["model_adjustment"] = {
        "authority": "user_approval_required",
        "confirmed_by": "user",
        "confirmed_at": "2026-09-06T02:00:00+00:00",
    }
    legacy_proposal["semantic_facts"] = _fresh_policy_facts(legacy_proposal)
    activate_confirmed_contract(_activation(issue_dir, legacy_proposal))
    current_proposal = _proposal()

    result = evaluate_driver_entry(
        DriverEntryRequest(
            issue_dir,
            "issue474",
            "workflow-474",
            {
                "semantic_facts": current_proposal["semantic_facts"],
                "material_assumptions": current_proposal["material_assumptions"],
            },
        )
    )

    assert result.freshness is Freshness.SAME_SEMANTICS


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
    assert not hasattr(same, "generic_inputs")
    assert same.proactive_review[0]["phase"] == "develop"

    changed = deepcopy(facts)
    changed["semantic_facts"]["effective_policy"]["phases"][0]["chain"][0][
        "model"
    ] = "changed-model"
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


def test_full_policy_freshness_detects_changed_model_chain(tmp_path: Path) -> None:
    """Test List 2: fresh evidence contains every behavior-changing phase choice."""
    issue_dir = tmp_path / "issue"
    proposal = _proposal()
    proposal["semantic_facts"] = _fresh_policy_facts(proposal)
    activate_confirmed_contract(_activation(issue_dir, proposal))

    live = deepcopy(proposal)
    live["phases"][0]["chain"][0]["model"] = "new-exact-model"
    live["semantic_facts"] = _fresh_policy_facts(live)
    assert (
        evaluate_driver_entry(
            DriverEntryRequest(
                issue_dir,
                "issue474",
                "workflow-474",
                {
                    "semantic_facts": live["semantic_facts"],
                    "material_assumptions": live["material_assumptions"],
                },
            )
        ).freshness
        is Freshness.MATERIAL_CHANGE
    )


def test_contract_only_event_callback_derives_and_digest_binds_runtime_state(
    tmp_path: Path, monkeypatch
) -> None:
    """Test List 2/5: automatic callback derives no competing transport policy."""
    from cafe.core.blackboard import BlackboardStore

    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue474"
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec")
    proposal = _proposal()
    proposal["driver"] = {
        "mode": "event-driven",
        "clis": [{"cli": "claude", "model": "exact"}],
    }
    proposal["semantic_facts"] = _fresh_policy_facts(proposal)
    activation = activate_confirmed_contract(
        _activation(issue_dir, proposal, workflow_id=blackboard.workflow_id)  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="do not write legacy config"):
        callback.write_config(issue_dir, clis=[("claude", "exact")])
    event = store.prepare_workflow_callback_event(
        blackboard,
        {
            "workflow_id": blackboard.workflow_id,
            "issue": "issue474",
            "event_type": "phase_terminal",
            "step": "develop",
            "status_code": "ok",
        },
    )
    observed: dict[str, object] = {}

    def record_callback(_driver_dir, state, _event, **_kwargs):
        observed["state"] = state
        return state

    monkeypatch.setattr(callback, "_run_v3_callback", record_callback)
    callback.run_callback(event, repository_root=tmp_path)

    driver_dir = issue_dir / "driver"
    assert not (driver_dir / "config.yaml").exists()
    assert observed["state"]["contract_sha256"] == activation.contract_sha256
    persisted = json.loads((driver_dir / "dispatch_state.json").read_text(encoding="utf-8"))
    assert set(persisted) == {
        "schema_version",
        "workflow_id",
        "contract_sha256",
        "active_index",
        "entries",
        "events",
        "updated_at",
    }
    assert persisted["schema_version"] == 2
    assert persisted["entries"] == [{"index": 0, "session": None}]

    replacement = deepcopy(proposal)
    replacement["driver"]["clis"][0]["model"] = "reconfirmed-model"
    replacement["semantic_facts"] = _fresh_policy_facts(replacement)
    replace_confirmed_contract(
        ReplaceConfirmedContract(
            issue_dir,
            "issue474",
            blackboard.workflow_id,
            "user",
            datetime(2026, 9, 6, 3, tzinfo=timezone.utc),
            replacement,
            activation.contract_sha256,
            "user_reconfirmation",
        )
    )
    later_event = store.prepare_workflow_callback_event(
        blackboard,
        {
            "workflow_id": blackboard.workflow_id,
            "issue": "issue474",
            "event_type": "workflow_completed",
            "step": "review",
            "status_code": "ok",
        },
    )
    with pytest.raises(ValueError, match="stale Driver contract"):
        callback.run_callback(later_event, repository_root=tmp_path)


def test_unsafe_present_contract_cannot_fall_back_to_legacy_callback_policy(
    tmp_path: Path,
) -> None:
    """A damaged highest-authority contract stops instead of reviving a sidecar."""
    from cafe.core.blackboard import BlackboardStore

    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue474"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    proposal = _proposal()
    proposal["driver"] = {
        "mode": "event-driven",
        "clis": [{"cli": "claude", "model": "exact"}],
    }
    proposal["semantic_facts"] = _fresh_policy_facts(proposal)
    activate_confirmed_contract(
        _activation(issue_dir, proposal, workflow_id=blackboard.workflow_id)  # type: ignore[arg-type]
    )
    contract = issue_dir / "driver" / "contract.json"
    outside = tmp_path / "untrusted-contract.json"
    outside.write_text(contract.read_text(encoding="utf-8"), encoding="utf-8")
    contract.unlink()
    contract.symlink_to(outside)
    (issue_dir / "driver" / "config.yaml").write_text(
        "schema_version: 1\nmode: event-driven\ncli: codex\nmodel: legacy\n",
        encoding="utf-8",
    )
    event = BlackboardStore(issue_dir).prepare_workflow_callback_event(
        blackboard,
        {
            "workflow_id": blackboard.workflow_id,
            "issue": "issue474",
            "event_type": "phase_terminal",
        },
    )

    with pytest.raises(ValueError):
        callback.run_callback(event, repository_root=tmp_path)
    assert not (issue_dir / "driver" / "dispatch_state.json").exists()


def test_driver_entry_projections_are_deeply_immutable(tmp_path: Path) -> None:
    """Follow-up FUP-001: public results cannot be changed after validation."""
    issue_dir = tmp_path / "issue"
    proposal = _proposal()
    activate_confirmed_contract(_activation(issue_dir, proposal))
    result = evaluate_driver_entry(
        DriverEntryRequest(
            issue_dir,
            "issue474",
            "workflow-474",
            {
                "semantic_facts": proposal["semantic_facts"],
                "material_assumptions": proposal["material_assumptions"],
            },
        )
    )
    with pytest.raises(TypeError):
        result.runtime["driver"]["mode"] = "mutated"


def test_oversized_contract_and_legacy_evidence_fail_closed(tmp_path: Path) -> None:
    """Test List 3/4: untrusted reads enforce the byte budget before parsing."""
    from cafe.driver._store import MAX_CONTRACT_BYTES

    issue_dir = tmp_path / "issue"
    contract = issue_dir / "driver" / "contract.json"
    contract.parent.mkdir(parents=True)
    contract.write_bytes(b"x" * (MAX_CONTRACT_BYTES + 1))
    with pytest.raises(ValueError, match="maximum bounded size"):
        evaluate_driver_entry(
            DriverEntryRequest(
                issue_dir,
                "issue474",
                "workflow-474",
                {"semantic_facts": {}, "material_assumptions": {}},
            )
        )

    legacy = tmp_path / "legacy"
    evidence = legacy / "driver" / "legacy_confirmation.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"x" * (MAX_CONTRACT_BYTES + 1))
    adoption = adopt_legacy_contract(LegacyAdoptionRequest(legacy, "issue474", "workflow-474"))
    assert adoption.adopted is False
    assert adoption.disposition == "reconfirmation_required"


def test_replacement_is_compare_and_swap_and_delegation_cannot_change_policy(
    tmp_path: Path,
) -> None:
    """Test List 3: readers retain valid authority when a replacement is rejected."""
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir(parents=True)
    issue_config = issue_dir / "issue.yaml"
    issue_config.write_text(
        "confirmation_contract:\n  pr_auto_create: false\npr:\n  auto_create: false\n",
        encoding="utf-8",
    )
    original_issue_config = issue_config.read_bytes()
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
    reconfirmed["reactive_user_handoffs"]["alignment_checkpoint"] = "user_required"
    reconfirmed["semantic_facts"] = _fresh_policy_facts(reconfirmed)
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
    assert issue_config.read_bytes() == original_issue_config
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


def test_legacy_adoption_discards_co_located_generic_fields(tmp_path: Path) -> None:
    """Test List 4: legacy generic values never become Driver policy."""
    issue_dir = tmp_path / "legacy"
    proposal = _proposal()
    proposal["playbook"] = {"id": "standard"}
    proposal["pr"] = {"auto_create": False}
    proposal["confirmation_contract"]["pr_auto_create"] = False
    proposal["locales"]["repository_content"] = {"value": "en", "source": "legacy"}
    proposal["semantic_facts"] = _fresh_policy_facts(proposal)
    confirmation = issue_dir / "driver" / "legacy_confirmation.json"
    confirmation.parent.mkdir(parents=True)
    confirmation.write_text(
        json.dumps(
            {
                "identity": {"issue_name": "issue474", "workflow_id": "workflow-474"},
                "confirmed_by": "user",
                "confirmed_at": "2026-09-06T02:00:00+00:00",
                "proposal": proposal,
            }
        ),
        encoding="utf-8",
    )

    result = adopt_legacy_contract(LegacyAdoptionRequest(issue_dir, "issue474", "workflow-474"))

    assert result.adopted is True
    contract = json.loads((issue_dir / "driver" / "contract.json").read_text(encoding="utf-8"))
    assert "playbook" not in contract
    assert "pr" not in contract
    assert "pr_auto_create" not in contract["confirmation_contract"]


def test_mixed_experimental_contract_requires_reconfirmation(tmp_path: Path) -> None:
    """Test List 4: a persisted generic field is invalid, never rewritten in place."""
    issue_dir = tmp_path / "mixed"
    activated = activate_confirmed_contract(_activation(issue_dir))
    contract_path = issue_dir / "driver" / "contract.json"
    mixed = json.loads(contract_path.read_text(encoding="utf-8"))
    mixed["pr"] = {"auto_create": False}
    contract_path.write_text(json.dumps(mixed), encoding="utf-8")

    result = adopt_legacy_contract(LegacyAdoptionRequest(issue_dir, "issue474", "workflow-474"))

    assert activated.created is True
    assert result.adopted is False
    assert result.disposition == "reconfirmation_required"
    assert json.loads(contract_path.read_text(encoding="utf-8"))["pr"] == {"auto_create": False}


def test_legacy_adoption_ignores_generic_phase_projection(tmp_path: Path) -> None:
    """Test List 4: generic phase configuration is never migration evidence."""
    proposal = _proposal()

    def prepare_legacy_issue(name: str) -> Path:
        issue_dir = tmp_path / name / ".cafe" / "issues" / "issue474"
        confirmation = issue_dir / "driver" / "legacy_confirmation.json"
        confirmation.parent.mkdir(parents=True)
        confirmation.write_text(
            json.dumps(
                {
                    "identity": {"issue_name": "issue474", "workflow_id": "workflow-474"},
                    "confirmed_by": "user",
                    "confirmed_at": "2026-09-06T02:00:00+00:00",
                    "proposal": proposal,
                }
            ),
            encoding="utf-8",
        )
        phase_path = issue_dir.parent.parent / "phases.yaml"
        phase_path.parent.mkdir(parents=True, exist_ok=True)
        phase_path.write_text("develop:\n  clis: []\n", encoding="utf-8")
        return issue_dir

    matching = prepare_legacy_issue("matching")
    adopted = adopt_legacy_contract(LegacyAdoptionRequest(matching, "issue474", "workflow-474"))
    assert adopted.adopted is True


def test_delegated_model_adjustment_rejects_cli_and_chain_topology_changes(tmp_path: Path) -> None:
    """Test List 3: delegation changes exact model values, never transport authority."""
    issue_dir = tmp_path / "issue"
    current = _proposal()
    current["model_adjustment"]["authority"] = "driver_autonomous"
    current["semantic_facts"] = _fresh_policy_facts(current)
    activated = activate_confirmed_contract(_activation(issue_dir, current))

    changed_cli = deepcopy(current)
    changed_cli["phases"][0]["chain"][0]["cli"] = "unsupported-cli"
    changed_cli["semantic_facts"] = _fresh_policy_facts(changed_cli)
    with pytest.raises(ValueError):
        replace_confirmed_contract(
            ReplaceConfirmedContract(
                issue_dir,
                "issue474",
                "workflow-474",
                "driver",
                datetime(2026, 9, 6, 3, tzinfo=timezone.utc),
                changed_cli,
                activated.contract_sha256,
                "delegated_change",
                {"authority_field": "model_adjustment"},
            )
        )

    changed_topology = deepcopy(current)
    changed_topology["phases"][0]["chain"].append({"cli": "claude", "model": "fallback"})
    changed_topology["semantic_facts"] = _fresh_policy_facts(changed_topology)
    with pytest.raises(ValueError):
        replace_confirmed_contract(
            ReplaceConfirmedContract(
                issue_dir,
                "issue474",
                "workflow-474",
                "driver",
                datetime(2026, 9, 6, 3, tzinfo=timezone.utc),
                changed_topology,
                activated.contract_sha256,
                "delegated_change",
                {"authority_field": "model_adjustment"},
            )
        )

    changed_model = deepcopy(current)
    changed_model["phases"][0]["chain"][0]["model"] = "new-approved-model"
    changed_model["semantic_facts"] = _fresh_policy_facts(changed_model)
    replacement = replace_confirmed_contract(
        ReplaceConfirmedContract(
            issue_dir,
            "issue474",
            "workflow-474",
            "driver",
            datetime(2026, 9, 6, 3, tzinfo=timezone.utc),
            changed_model,
            activated.contract_sha256,
            "delegated_change",
            {"authority_field": "model_adjustment"},
        )
    )
    assert replacement.revision == 2


def test_legacy_sidecar_conflict_and_ancestor_symlink_fail_closed(tmp_path: Path) -> None:
    """Test List 3/4: ambiguous legacy evidence and aliased issue roots never mutate authority."""
    legacy = tmp_path / "legacy"
    confirmation = legacy / "driver" / "legacy_confirmation.json"
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
    (legacy / "driver" / "proactive_review.yaml").write_text(
        "phase_decisions:\n  - phase: develop\n    decision: required\n    rationale: conflict\n",
        encoding="utf-8",
    )
    assert not adopt_legacy_contract(
        LegacyAdoptionRequest(legacy, "issue474", "workflow-474")
    ).adopted

    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        activate_confirmed_contract(_activation(alias / "issue"))
    assert not (outside / "issue" / "driver" / "contract.json").exists()


def test_legacy_adoption_ignores_generic_issue_configuration(tmp_path: Path) -> None:
    """Generic issue configuration does not participate in Driver migration."""
    legacy = tmp_path / "legacy"
    confirmation = legacy / "driver" / "legacy_confirmation.json"
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
    (legacy / "issue.yaml").write_text(
        json.dumps(
            {
                "playbook_id": "standard",
                "confirmation_contract": {"pr_auto_create": False},
                "pr": {"auto_create": False},
            }
        ),
        encoding="utf-8",
    )

    result = adopt_legacy_contract(LegacyAdoptionRequest(legacy, "issue474", "workflow-474"))

    assert result.adopted is True
    assert (legacy / "driver" / "contract.json").is_file()


def test_legacy_adoption_rejects_conflicting_driver_sidecar(
    tmp_path: Path,
) -> None:
    """Legacy sidecars cannot silently override handoff or model authority."""
    legacy = tmp_path / "legacy"
    confirmation = legacy / "driver" / "legacy_confirmation.json"
    confirmation.parent.mkdir(parents=True)
    proposal = _proposal()
    confirmation.write_text(
        json.dumps(
            {
                "identity": {"issue_name": "issue474", "workflow_id": "workflow-474"},
                "confirmed_by": "user",
                "confirmed_at": "2026-09-06T02:00:00+00:00",
                "proposal": proposal,
            }
        ),
        encoding="utf-8",
    )
    (legacy / "driver" / "config.yaml").write_text("mode: attached\n", encoding="utf-8")

    result = adopt_legacy_contract(LegacyAdoptionRequest(legacy, "issue474", "workflow-474"))

    assert result.adopted is False
    assert result.disposition == "reconfirmation_required"
    assert not (legacy / "driver" / "contract.json").exists()


def test_legacy_adoption_ignores_playbook_overrides(tmp_path: Path) -> None:
    """A generic override does not become a Driver migration input."""
    legacy = tmp_path / "legacy"
    confirmation = legacy / "driver" / "legacy_confirmation.json"
    confirmation.parent.mkdir(parents=True)
    proposal = _proposal()
    confirmation.write_text(
        json.dumps(
            {
                "identity": {"issue_name": "issue474", "workflow_id": "workflow-474"},
                "confirmed_by": "user",
                "confirmed_at": "2026-09-06T02:00:00+00:00",
                "proposal": proposal,
            }
        ),
        encoding="utf-8",
    )
    (legacy / "issue.yaml").write_text(
        json.dumps(
            {
                "playbook_id": "standard",
                "playbook_overrides": {"steps": {"develop": {"max_attempts_per_cycle": 2}}},
            }
        ),
        encoding="utf-8",
    )

    result = adopt_legacy_contract(LegacyAdoptionRequest(legacy, "issue474", "workflow-474"))

    assert result.adopted is True
    assert (legacy / "driver" / "contract.json").is_file()
