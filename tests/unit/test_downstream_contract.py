from pathlib import Path

import pytest

from cafe.core.downstream_contract import ContractValidationError, extract_downstream_contract


def test_extracts_only_the_exact_versioned_spec_contract(tmp_path: Path) -> None:
    source = tmp_path / "spec.md"
    source.write_text(
        "# Spec\n\nBODY-ONLY-SENTINEL\n\n## Downstream Contract\n\n"
        "- Contract-Version: `1`\n- Artifact-Kind: `spec`\n\n"
        "### Goals\n\n| ID | Statement |\n| --- | --- |\n| GOAL-001 | Goal |\n\n"
        "### Non-Goals\n\n| ID | Statement |\n| --- | --- |\n| NONGOAL-001 | Not a goal |\n\n"
        "### Acceptance Criteria\n\n| ID | Priority | Statement |\n| --- | --- | --- |\n| AC-001 | must | Accepted |\n\n"
        "### Invariants\n\n| ID | Statement |\n| --- | --- |\n| INV-001 | Always true |\n\n"
        "### Trust Boundaries\n\n| ID | Statement |\n| --- | --- |\n| TRUST-001 | Boundary |\n",
        encoding="utf-8",
    )

    contract = extract_downstream_contract(source, kind="spec")

    assert contract.bytes.startswith(b"## Downstream Contract\n")
    assert b"BODY-ONLY-SENTINEL" not in contract.bytes
    assert contract.kind == "spec"


def test_rejects_missing_contract_to_preserve_full_source_fallback(tmp_path: Path) -> None:
    source = tmp_path / "legacy.md"
    source.write_text("# Legacy artifact\n", encoding="utf-8")

    with pytest.raises(ContractValidationError):
        extract_downstream_contract(source, kind="spec")


def test_rejects_plan_task_state_that_disagrees_with_complete_plan(tmp_path: Path) -> None:
    source = tmp_path / "plan.md"
    source.write_text(
        "# Plan\n\n## Development Task Breakdown\n\n"
        "- [x] **TASK-001** — Completed implementation\n\n"
        "## Downstream Contract\n\n"
        "- Contract-Version: `1`\n- Artifact-Kind: `plan`\n\n"
        "### Architecture Boundaries\n| ID | Location | Responsibility |\n| --- | --- | --- |\n| ARCH-001 | src | Boundary |\n\n"
        "### Invariants\n| ID | Statement |\n| --- | --- |\n| INV-001 | Safe |\n\n"
        "### Test List\n| ID | Type | Covers |\n| --- | --- | --- |\n| UT-001 | unit | INV-001 |\n\n"
        "### Dependency ADR References\n| ID | Decision | Requirement / invariant |\n| --- | --- | --- |\n| ADR-001 | Keep safe | INV-001 |\n\n"
        "### Task Status\n| ID | Status | Summary | Depends On |\n| --- | --- | --- | --- |\n| TASK-001 | pending | Implementation | — |\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="task state"):
        extract_downstream_contract(source, kind="plan")


@pytest.mark.parametrize(
    ("relative_path", "kind"),
    [
        ("src/cafe/data/skills/cafe-spec/assets/templates/default.md", "spec"),
        ("src/cafe/data/skills/cafe-spec/assets/templates/detailed.md", "spec"),
        ("src/cafe/data/skills/cafe-spec/assets/templates/simple.md", "spec"),
        ("src/cafe/data/skills/cafe-plan/assets/templates/default.md", "plan"),
        ("src/cafe/data/skills/cafe-plan/assets/templates/bug.md", "plan"),
        ("src/cafe/data/skills/cafe-plan/assets/templates/simple.md", "plan"),
    ],
)
def test_builtin_source_templates_have_valid_contracts(relative_path: str, kind: str) -> None:
    root = Path(__file__).resolve().parents[2]

    contract = extract_downstream_contract(root / relative_path, kind=kind)

    assert contract.kind == kind
    assert contract.bytes.startswith(b"## Downstream Contract\n")
