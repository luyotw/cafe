"""Tests for deterministic alignment policy decisions."""

from pathlib import Path

from cafe.core.alignment import (
    AgentAlignmentEvidence,
    AlignmentDecisionLevel,
    AlignmentPolicyConfig,
    AlignmentPolicyInput,
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


def _context_with_roadmap_mapped_strategy(tmp_path: Path):
    (tmp_path / ".cafe").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "roadmap.md").write_text(
        "# Roadmap\n\nNorth star and product direction.", encoding="utf-8"
    )
    (tmp_path / ".cafe" / "strategic_context.yaml").write_text(
        """
version: 1
documents:
  roadmap:
    path: docs/roadmap.md
    status: exists
  product_direction:
    path: docs/roadmap.md
    status: exists
  principles:
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
""",
        encoding="utf-8",
    )
    return load_strategic_context(tmp_path)


def _context_with_confirmed_strategy(tmp_path: Path):
    (tmp_path / ".cafe").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "roadmap.md").write_text(
        "# Roadmap\n\nCapability contract consolidation is in scope.",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "positioning.md").write_text(
        "# Positioning\n\nCapability contracts protect trusted host boundaries.",
        encoding="utf-8",
    )
    (tmp_path / ".cafe" / "strategic_context.yaml").write_text(
        """
version: 1
documents:
  roadmap:
    path: docs/roadmap.md
    status: exists
  product_direction:
    path: docs/roadmap.md
    status: exists
  principles:
    path: docs/roadmap.md
    status: exists
  positioning:
    path: docs/positioning.md
    status: exists
mandate:
  axes:
    product_scope:
      level: escalate
      grounds: [roadmap, principles]
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
    assert any(
        rule.rule_id == "mandate_escalation:product_scope" for rule in result.triggered_rules
    )


def test_out_of_mandate_overlap_forces_checkpoint(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="develop", user_input="Add pricing approval automation."),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert any(rule.rule_id == "out_of_mandate" for rule in result.triggered_rules)


def test_out_of_mandate_terms_in_non_scope_section_do_not_pause(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="spec",
            user_input=(
                "All four functional DoD items are confirmed. Keep the custom "
                "artifact, checklist, template catalog, and default workflow parity."
            ),
            artifacts={
                "current_output": """
## Implementation Scope

- **Out of scope:**
  - Do not turn this into a broader workflow-product redesign.
  - Any new external-system access, deployment approval, pricing change,
    or release tagging.

- **Future considerations:**
  - Authoring aids for declaration validation.
"""
            },
        ),
        strategic_context=context,
    )

    assert result.level == AlignmentDecisionLevel.NO_ALIGNMENT
    assert not any(rule.rule_id == "out_of_mandate" for rule in result.triggered_rules)


def test_inline_negated_plan_confirmation_does_not_pause(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="plan",
            user_input=(
                "Plan approved. Proceed exactly within the confirmed #344 scope and "
                "roadmap constraints; do not broaden into dashboard, HumanTask, "
                "subflow, external permissions, pricing, deployment approval, or "
                "release tagging."
            ),
            artifacts={
                "current_output": (
                    "| Declined | Reason |\n"
                    "| New database | The roadmap keeps execution state file-based; "
                    "this issue changes definitions, not workflow-instance ownership. |"
                )
            },
        ),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.NO_ALIGNMENT
    assert result.payload is None


def test_affirmative_work_after_negated_clause_still_forces_checkpoint(
    tmp_path: Path,
) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="plan",
            user_input=(
                "Do not broaden roadmap scope; add pricing approval automation."
            ),
        ),
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
        AlignmentPolicyInput(
            step_name="draft", user_input="Draft positioning guidance for launch."
        ),
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


def test_routine_scope_wording_does_not_trigger_product_alignment(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="spec",
            user_input=(
                "Implement the remaining operational scope, preserve existing fallback "
                "behavior, and cover it with regression tests."
            ),
        ),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.NO_ALIGNMENT
    assert result.payload is None


def test_spec_boilerplate_and_negated_positioning_change_do_not_pause(
    tmp_path: Path,
) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="spec",
            user_input=("四項全選：保存安全摘要、同一 CLI 重試一次、保持相容，並加入回歸測試。"),
            artifacts={
                "current_output": """
## 範圍與紅線

- 本次不做產品方向擴張。
- 不改變產品定位、治理原則或受信任權限邊界。

## Principles 對應

這是既有 workflow reliability 行為的修正。
"""
            },
        ),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.NO_ALIGNMENT
    assert result.payload is None


def test_plan_confirmation_and_preserved_trust_boundary_do_not_pause(
    tmp_path: Path,
) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="plan",
            user_input=(
                "計畫已確認。此計畫符合既有 roadmap 與產品定位；"
                "依已授權的 driver confirmation 繼續進入 develop。"
            ),
            artifacts={
                "current_output": """
這符合 roadmap 的 repo-first 與可復原 execution state，
並且不擴張 trusted host capability boundary。
""",
                "spec": """
- 不新增外部系統操作、權限或可信任能力。
- 不改變人員判斷、外部操作或受信任權限邊界。
""",
            },
        ),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.NO_ALIGNMENT
    assert result.payload is None


def test_chinese_product_direction_change_still_forces_checkpoint(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="spec", user_input="這次需要調整產品方向與路線圖。"),
        strategic_context=_context(tmp_path),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert any(
        rule.rule_id == "mandate_escalation:product_scope" for rule in result.triggered_rules
    )


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


def test_trusted_host_capability_contract_forces_checkpoint(tmp_path: Path) -> None:
    issue_text = """
    Replace PR-specific publish hooks with generic capability contracts.
    CAFE should keep the long-term direction for host-side execution: agents
    produce declarative requests and trusted host capabilities perform external
    mutations. Preserve a strict trust boundary while broadening capability
    request and receipt handling.
    """

    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="spec", artifacts={"issue": issue_text}),
        strategic_context=_context(tmp_path),
        config=AlignmentPolicyConfig(
            affected_document_categories=(
                "roadmap",
                "product_direction",
                "principles",
                "positioning",
                "strategic_context",
            ),
            pause_threshold=5,
            note_threshold=2,
        ),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    assert result.payload is not None
    rule_ids = {rule.rule_id for rule in result.triggered_rules}
    assert "trusted_capability_boundary" in rule_ids
    assert "external_mutation_risk" in rule_ids
    affected_categories = {doc.category for doc in result.payload.affected_documents}
    assert {
        "roadmap",
        "product_direction",
        "principles",
        "positioning",
        "strategic_context",
    } <= affected_categories


def test_trusted_capability_uses_mapped_roadmap_docs_without_missing_false_positive(
    tmp_path: Path,
) -> None:
    issue_text = """
    Replace PR-specific publish hooks with generic capability contracts.
    Keep trusted host-side execution and a strict trust boundary.
    """

    result = evaluate_alignment_policy(
        AlignmentPolicyInput(step_name="spec", artifacts={"issue": issue_text}),
        strategic_context=_context_with_roadmap_mapped_strategy(tmp_path),
        config=AlignmentPolicyConfig(
            affected_document_categories=(
                "roadmap",
                "product_direction",
                "principles",
                "positioning",
                "strategic_context",
            ),
            pause_threshold=5,
            note_threshold=2,
        ),
    )

    assert result.level == AlignmentDecisionLevel.MUST_ALIGN
    rule_ids = {rule.rule_id for rule in result.triggered_rules}
    assert "strategic_document_missing:product_direction" not in rule_ids
    assert "strategic_document_missing:principles" not in rule_ids
    assert "strategic_document_missing:positioning" in rule_ids
    assert result.payload is not None
    docs = {doc.category: doc for doc in result.payload.affected_documents}
    assert docs["product_direction"].path == "docs/roadmap.md"
    assert docs["principles"].path == "docs/roadmap.md"
    assert docs["positioning"].status == "missing"


def test_confirmed_capability_boundary_scope_does_not_pause(
    tmp_path: Path,
) -> None:
    spec_text = """
    Issue #347 is limited to generic capability request validation, execution
    receipt handling, and preserving existing PR publish behavior. Out of scope:
    expanding the work into policy, strategy, or execution-surface changes
    outside the confirmed capability-contract boundary.
    """

    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="spec",
            user_input="Q1: select 1. Q2: select all. Q3: select 1.",
            artifacts={"spec": spec_text},
        ),
        strategic_context=_context_with_confirmed_strategy(tmp_path),
        config=AlignmentPolicyConfig(
            affected_document_categories=(
                "roadmap",
                "product_direction",
                "principles",
                "positioning",
                "strategic_context",
            ),
            pause_threshold=5,
            note_threshold=2,
        ),
    )

    assert result.level == AlignmentDecisionLevel.NO_ALIGNMENT
    assert not any(rule.rule_id == "trusted_capability_boundary" for rule in result.triggered_rules)


def test_medium_risk_records_note_only(tmp_path: Path) -> None:
    result = evaluate_alignment_policy(
        AlignmentPolicyInput(
            step_name="develop", user_input="Touch an external API wrapper boundary."
        ),
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
    second = evaluate_alignment_policy(
        input_data, strategic_context=load_strategic_context(tmp_path)
    )

    assert first.payload is not None
    assert second.payload is not None
    assert first.payload.fingerprint != second.payload.fingerprint
