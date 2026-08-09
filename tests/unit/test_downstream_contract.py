from pathlib import Path

import pytest

from cafe.core.downstream_contract import ContractValidationError, extract_downstream_contract


def test_extracts_only_the_exact_versioned_spec_contract(tmp_path: Path) -> None:
    source = tmp_path / "spec.md"
    source.write_text(
        "# Spec\n\nBODY-ONLY-SENTINEL\nGOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001\n\n## Downstream Contract\n\n"
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


def test_rejects_required_table_without_data_rows(tmp_path: Path) -> None:
    """An empty declared section is invalid and must allow the full-source fallback."""
    source = tmp_path / "empty-goals.md"
    source.write_text(
        _valid_spec_contract().replace("| GOAL-001 | Goal |\n", "", 1),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="non-empty table"):
        extract_downstream_contract(source, kind="spec")


def test_rejects_table_with_an_invalid_separator_row(tmp_path: Path) -> None:
    """Only a Markdown separator may appear between a header and its rows."""
    source = tmp_path / "invalid-separator.md"
    source.write_text(
        _valid_spec_contract().replace(
            "| --- | --- |", "| untrusted | ignored |", 1
        ),
        encoding="utf-8",
    )

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


def test_rejects_contract_that_omits_stable_ids_after_the_contract(tmp_path: Path) -> None:
    source = tmp_path / "spec.md"
    source.write_text(
        _valid_spec_contract()
        + "\n## Acceptance criteria\n\n| ID | Statement |\n| --- | --- |\n| AC-002 | Later requirement |\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="cover"):
        extract_downstream_contract(source, kind="spec")


def test_rejects_contract_without_authoritative_body_ids(tmp_path: Path) -> None:
    source = tmp_path / "spec.md"
    source.write_text(
        _valid_spec_contract().replace("GOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001\n\n", "", 1),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="authoritative body"):
        extract_downstream_contract(source, kind="spec")


def test_rejects_kind_incompatible_contract_references(tmp_path: Path) -> None:
    source = tmp_path / "plan.md"
    source.write_text(
        _valid_plan_contract().replace(
            "| UT-001 | unit | INV-001 |", "| UT-001 | unit | ADR-001 |"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="Test List"):
        extract_downstream_contract(source, kind="plan")


def test_ignores_contract_headings_inside_fenced_code_blocks(tmp_path: Path) -> None:
    """Examples must not be mistaken for a second authoritative contract."""
    source = tmp_path / "spec.md"
    source.write_text(
        "```markdown\n## Downstream Contract\n```\n\n" + _valid_spec_contract(),
        encoding="utf-8",
    )

    assert extract_downstream_contract(source, kind="spec").kind == "spec"


def test_rejects_contract_ids_that_are_not_declared_by_the_body(tmp_path: Path) -> None:
    source = tmp_path / "spec.md"
    source.write_text(
        _valid_spec_contract().replace("| GOAL-001 | Goal |", "| GOAL-001 | Goal |\n| GOAL-002 | Stale |"),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="cover"):
        extract_downstream_contract(source, kind="spec")


@pytest.mark.parametrize(
    "replacement",
    [
        ("| UT-001 | unit | INV-001 |", "| UT-001 | unit | none |"),
        ("| ADR-001 | Keep safe | INV-001 |", "| ADR-001 | Keep safe | none |"),
        ("| TASK-001 | completed | Implementation | — |", "| TASK-001 | completed | Implementation | none |"),
    ],
)
def test_rejects_plan_reference_rows_without_a_required_id(
    tmp_path: Path, replacement: tuple[str, str]
) -> None:
    source = tmp_path / "plan.md"
    source.write_text(_valid_plan_contract().replace(*replacement), encoding="utf-8")

    with pytest.raises(ContractValidationError):
        extract_downstream_contract(source, kind="plan")


def _valid_spec_contract() -> str:
    return """# Spec

GOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001

## Downstream Contract

- Contract-Version: `1`
- Artifact-Kind: `spec`

### Goals
| ID | Statement |
| --- | --- |
| GOAL-001 | Goal |

### Non-Goals
| ID | Statement |
| --- | --- |
| NONGOAL-001 | Not a goal |

### Acceptance Criteria
| ID | Priority | Statement |
| --- | --- | --- |
| AC-001 | must | Accepted |

### Invariants
| ID | Statement |
| --- | --- |
| INV-001 | Always true |

### Trust Boundaries
| ID | Statement |
| --- | --- |
| TRUST-001 | Boundary |
"""


def _valid_plan_contract() -> str:
    return """# Plan

ARCH-001 INV-001 UT-001 ADR-001
- [ ] **TASK-001** — Task

## Downstream Contract

- Contract-Version: `1`
- Artifact-Kind: `plan`

### Architecture Boundaries
| ID | Location | Responsibility |
| --- | --- | --- |
| ARCH-001 | src | Boundary |

### Invariants
| ID | Statement |
| --- | --- |
| INV-001 | Safe |

### Test List
| ID | Type | Covers |
| --- | --- | --- |
| UT-001 | unit | INV-001 |

### Dependency ADR References
| ID | Decision | Requirement / invariant |
| --- | --- | --- |
| ADR-001 | Keep safe | INV-001 |

### Task Status
| ID | Status | Summary | Depends On |
| --- | --- | --- | --- |
| TASK-001 | completed | Implementation | — |
"""


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
