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
