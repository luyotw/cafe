"""Tests for deterministic alignment policy decisions."""

from pathlib import Path

from cafe.core.alignment import (
    AlignmentDecisionLevel,
    AlignmentPolicyConfig,
    AlignmentPolicyInput,
    AgentAlignmentEvidence,
    evaluate_alignment_policy,
    merge_agent_alignment_evidence,
)
from cafe.core.strategic_context import load_strategic_context


def _context(tmp_path: Path):
    (tmp_path / ".cafe").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "roadmap.md").write_text("roadmap v1", encoding="utf-8")
    (tmp_path / ".cafe" / "strategic_context.yaml").write_text(
        """
version: 1
documents:
  roadmap:
    path: docs/roadmap.md
    status: exists
  positioning:
    path: docs/positioning.md
    status: missing
mandate:
  axes:
    product_scope:
      level: escalate
      grounds: [roadmap]
    technical:
      level: agent
  out_of_mandate:
    - pricing
""",
        encoding="utf-8",
    )
    return load_strategic_context(tmp_path)


def test_explicit_alignment_request_forces_checkpoint(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="plan", user_input="Please align first before planning."),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert any(rule.rule_id == "explicit_alignment_request" for rule in result.triggered_rules)


def test_chinese_explicit_alignment_request_forces_checkpoint(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="plan", user_input="這個 issue 請先對標再開始做。"),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert any(rule.rule_id == "explicit_alignment_request" for rule in result.triggered_rules)


def test_mandate_escalation_trigger_forces_checkpoint(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="spec", user_input="This changes roadmap scope."),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert any(rule.rule_id == "mandate_escalation:product_scope" for rule in result.triggered_rules)


def test_out_of_mandate_overlap_forces_checkpoint(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="develop", user_input="Add pricing approval automation."),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert any(rule.rule_id == "out_of_mandate" for rule in result.triggered_rules)


def test_required_strategic_document_update_is_in_payload(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="plan",
            user_input="Update strategic_context before implementation.",
            required_document_categories=("strategic_context",),
        ),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert result.payload is not None
    assert result.payload.strategic_document_update_requirements[0].category == "strategic_context"
    assert "update_strategic_documents_first" in result.payload.allowed_decisions


def test_chinese_roadmap_update_is_in_payload(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="plan", user_input="這個結論需要先更新路線圖。"),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert result.payload is not None
    requirements = {
        requirement.category
        for requirement in result.payload.strategic_document_update_requirements
    }
    assert "roadmap" in requirements


def test_missing_positioning_document_is_surfaced_when_relevant(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="draft", user_input="Draft positioning guidance for launch."),
        strategic_context=_context(tmp_path),
        config=AlignmentPolicyConfig(pause_threshold=5, note_threshold=2),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert result.payload is not None
    assert result.payload.affected_documents[0].category == "positioning"
    assert result.payload.affected_documents[0].status == "missing"


def test_routine_low_risk_work_does_not_pause(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="develop", user_input="Fix a typo in a unit test helper."),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.NO_ALIGNMENT
    assert result.payload is None


def test_configured_document_categories_do_not_trigger_by_themselves(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="develop", user_input="Fix a typo in a unit test helper."),
        strategic_context=_context(tmp_path),
        config=AlignmentPolicyConfig(
            affected_document_categories=("roadmap", "positioning"),
            pause_threshold=5,
            note_threshold=2,
        ),
    )

    assert result.level == AlignmentDecisionLevel.NO_ALIGNMENT


def test_configured_document_categories_apply_to_strategic_signals(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="spec", user_input="Clarify product onboarding direction."),
        strategic_context=_context(tmp_path),
        config=AlignmentPolicyConfig(
            affected_document_categories=("product_direction", "positioning"),
            pause_threshold=5,
            note_threshold=2,
        ),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert result.payload is not None
    categories = {doc.category for doc in result.payload.affected_documents}
    assert {"product_direction", "positioning"} <= categories
    assert any(
        rule.rule_id == "strategic_document_missing:product_direction"
        for rule in result.triggered_rules
    )


def test_medium_risk_records_note_only(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="develop", user_input="Touch an external API wrapper boundary."),
        strategic_context=_context(tmp_path),
        config=AlignmentPolicyConfig(pause_threshold=5, note_threshold=2),
    )

    assert result.level == AlignmentDecisionLevel.ALIGNMENT_NOTE
    assert result.payload is not None


def test_agent_evidence_cannot_downgrade_policy_required_checkpoint(tmp_path: Path) -> None:
    policy_result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="spec", user_input="Change product direction."),
        strategic_context=_context(tmp_path),
    )

    merged = merge_agent_alignment_evidence(
        policy_result,
        AgentAlignmentEvidence(
            suggested_level=AlignmentDecisionLevel.NO_ALIGNMENT,
            risks=("Agent thinks this is routine.",),
        ),
    )

    assert merged.level == AlignmentDecisionLevel.MUST_ALIGN
    assert merged.payload is not None
    assert "Agent thinks this is routine." in merged.payload.risks


def test_fingerprint_changes_when_affected_document_changes(tmp_path: Path) -> None:
    context = _context(tmp_path)
    input_data = AlignmentPolicyInput(step_name="spec", user_input="This changes roadmap scope.")
    first = evaluate_alignment_policy(input_data, strategic_context=context)

    (tmp_path / "docs" / "roadmap.md").write_text("roadmap v2", encoding="utf-8")
    second = evaluate_alignment_policy(input_data, strategic_context=load_strategic_context(tmp_path))

    assert first.payload is not None
    assert second.payload is not None
    assert first.payload.fingerprint != second.payload.fingerprint
