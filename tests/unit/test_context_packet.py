from pathlib import Path

from cafe.core.context_packet import resolve_context_packet
from cafe.skills.contracts import SkillWorkflowContract, resolve_effective_prompt_inputs


def _spec() -> str:
    return """# Source

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
| NONGOAL-001 | No |
### Acceptance Criteria
| ID | Priority | Statement |
| --- | --- | --- |
| AC-001 | must | Yes |
### Invariants
| ID | Statement |
| --- | --- |
| INV-001 | Safe |
### Trust Boundaries
| ID | Statement |
| --- | --- |
| TRUST-001 | Local |
"""


def test_packet_relationship_falls_back_without_affecting_other_inputs(tmp_path: Path) -> None:
    source = tmp_path / "spec.md"
    source.write_text(_spec(), encoding="utf-8")
    contract = SkillWorkflowContract.model_validate(
        {"prompt_inputs": [
            {"artifacts": ["spec"], "placeholder": "packet_spec", "load_policy": [{"mode": "packet", "contract_kind": "spec"}]},
            {"artifacts": ["notes"], "placeholder": "full_notes"},
        ]}
    )

    resolved = resolve_effective_prompt_inputs(contract, {"spec": source, "notes": tmp_path / "notes.md"}, step="custom", iteration=1, feedback=False, packet_dir=tmp_path / "packets")

    assert resolved["packet_spec"]["mode"] == "packet"
    assert resolved["full_notes"] == {"mode": "full", "path": str(tmp_path / "notes.md")}
    source.write_text("# legacy", encoding="utf-8")
    fallback = resolve_context_packet(source_path=source, contract_kind="spec", target_step="custom", iteration=2, placeholders=("packet_spec",), packet_path=tmp_path / "new.json")
    assert fallback["mode"] == "full_fallback"
