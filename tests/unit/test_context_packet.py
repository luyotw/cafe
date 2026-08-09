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


def test_paired_placeholders_share_one_effective_packet_binding(tmp_path: Path) -> None:
    """One source relationship must not produce divergent paired packet inputs."""
    source = tmp_path / "spec.md"
    source.write_text(_spec(), encoding="utf-8")
    contract = SkillWorkflowContract.model_validate(
        {"prompt_inputs": [
            {"artifacts": ["spec"], "placeholder": "spec_file", "load_policy": [{"mode": "packet", "contract_kind": "spec"}]},
            {"artifacts": ["spec"], "placeholder": "spec_file_path", "load_policy": [{"mode": "packet", "contract_kind": "spec"}]},
        ]}
    )

    resolved = resolve_effective_prompt_inputs(
        contract,
        {"spec": source},
        step="custom",
        iteration=1,
        feedback=False,
        packet_dir=tmp_path / "packets",
    )

    assert resolved["spec_file"]["mode"] == "packet"
    assert resolved["spec_file"] == resolved["spec_file_path"]
    packet = Path(resolved["spec_file"]["path"])
    assert packet.name == "context_spec_file.json"
    assert __import__("json").loads(packet.read_text(encoding="utf-8"))["target"]["placeholders"] == [
        "spec_file",
        "spec_file_path",
    ]
