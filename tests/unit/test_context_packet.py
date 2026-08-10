from pathlib import Path

import pytest

import json

from cafe.core.context_packet import (
    build_context_packet_diagnostics,
    resolve_context_packet,
    validate_context_packet,
)
from cafe.core.downstream_contract import ContractValidationError
from cafe.skills.contracts import SkillWorkflowContract, resolve_effective_prompt_inputs


def _spec() -> str:
    return """# Source

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
        {
            "prompt_inputs": [
                {
                    "artifacts": ["spec"],
                    "placeholder": "packet_spec",
                    "load_policy": [{"mode": "packet", "contract_kind": "spec"}],
                },
                {"artifacts": ["notes"], "placeholder": "full_notes"},
            ]
        }
    )

    resolved = resolve_effective_prompt_inputs(
        contract,
        {"spec": source, "notes": tmp_path / "notes.md"},
        step="custom",
        iteration=1,
        feedback=False,
        packet_dir=tmp_path / "packets",
    )

    assert resolved["packet_spec"]["mode"] == "packet"
    assert resolved["packet_spec"]["source"]["artifact_name"] == "spec"
    assert resolved["packet_spec"]["source"]["artifact_version"] == 1
    assert resolved["full_notes"] == {"mode": "full", "path": str(tmp_path / "notes.md")}
    source.write_text("# legacy", encoding="utf-8")
    with pytest.raises(ContractValidationError):
        resolve_context_packet(
            source_path=source,
            contract_kind="spec",
            target_step="custom",
            iteration=2,
            placeholders=("packet_spec",),
            packet_path=tmp_path / "new.json",
        )


def test_paired_placeholders_share_one_effective_packet_binding(tmp_path: Path) -> None:
    """One source relationship must not produce divergent paired packet inputs."""
    source = tmp_path / "spec.md"
    source.write_text(_spec(), encoding="utf-8")
    contract = SkillWorkflowContract.model_validate(
        {
            "prompt_inputs": [
                {
                    "artifacts": ["spec"],
                    "placeholder": "spec_file",
                    "load_policy": [{"mode": "packet", "contract_kind": "spec"}],
                },
                {
                    "artifacts": ["spec"],
                    "placeholder": "spec_file_path",
                    "load_policy": [{"mode": "packet", "contract_kind": "spec"}],
                },
            ]
        }
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
    assert __import__("json").loads(packet.read_text(encoding="utf-8"))["target"][
        "placeholders"
    ] == [
        "spec_file",
        "spec_file_path",
    ]


def test_packet_diagnostics_are_strict_and_deduplicate_paired_bindings() -> None:
    """One requested relation produces one validated, durable diagnostic."""
    bindings = {
        "spec_file": {
            "requested_mode": "packet",
            "mode": "full_fallback",
            "path": "spec.md",
            "source": {"artifact_name": "spec", "artifact_version": 2},
            "reason": "packet_invalid",
            "fallback_reason": "packet_invalid",
            "detail": "context packet validation failed",
        },
        "spec_file_path": {
            "requested_mode": "packet",
            "mode": "full_fallback",
            "path": "spec.md",
            "source": {"artifact_name": "spec", "artifact_version": 2},
            "reason": "packet_invalid",
            "fallback_reason": "packet_invalid",
            "detail": "context packet validation failed",
        },
    }

    diagnostics = build_context_packet_diagnostics(bindings)

    assert diagnostics == [
        {
            "placeholders": ["spec_file", "spec_file_path"],
            "source": {"artifact_name": "spec", "artifact_version": 2},
            "requested_mode": "packet",
            "effective_mode": "full_fallback",
            "fallback_reason": "packet_invalid",
            "detail": "context packet validation failed",
            "path": "spec.md",
        }
    ]
    bindings["spec_file"]["fallback_reason"] = "untrusted-agent-text"
    with pytest.raises(ValueError, match="persisted context packet decision"):
        build_context_packet_diagnostics(bindings)


@pytest.mark.parametrize(
    "mutation",
    [
        {"fallback_reason": "contract_invalid"},
        {"reason": "packet_persist_failed"},
        {"detail": "agent supplied secret: abc"},
        {"detail": "x" * 161},
        {"source": {"artifact_name": "spec", "artifact_version": "two"}},
    ],
)
def test_persisted_packet_bindings_reject_agent_tampering(mutation: dict[str, object]) -> None:
    """UT-004: only runtime-owned fallback values can survive persistence."""
    binding = {
        "requested_mode": "packet",
        "mode": "full_fallback",
        "path": "spec.md",
        "source": {"artifact_name": "spec", "artifact_version": 2},
        "reason": "packet_invalid",
        "fallback_reason": "packet_invalid",
        "detail": "context packet validation failed",
    }
    binding.update(mutation)

    with pytest.raises(ValueError, match="persisted context packet decision"):
        build_context_packet_diagnostics({"spec_file": binding})



def test_packet_rejects_extra_envelope_fields_and_persisted_format_tampering(
    tmp_path: Path,
) -> None:
    source = tmp_path / "spec.md"
    source.write_text(_spec(), encoding="utf-8")
    packet_path = tmp_path / "packet.json"

    first = resolve_context_packet(
        source_path=source,
        contract_kind="spec",
        target_step="custom",
        iteration=2,
        placeholders=("packet_spec",),
        packet_path=packet_path,
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["unexpected"] = True
    with pytest.raises(ValueError, match="envelope"):
        validate_context_packet(packet)

    packet_path.write_text(json.dumps(first["packet"], indent=4), encoding="utf-8")
    fallback = resolve_context_packet(
        source_path=source,
        contract_kind="spec",
        target_step="custom",
        iteration=2,
        placeholders=("packet_spec",),
        packet_path=packet_path,
    )
    assert fallback["mode"] == "full_fallback"


def test_packet_tampering_cannot_be_approved_by_rewriting_a_sidecar_receipt(tmp_path: Path) -> None:
    source = tmp_path / "spec.md"
    source.write_text(_spec(), encoding="utf-8")
    packet_path = tmp_path / "packet.json"
    first = resolve_context_packet(
        source_path=source,
        contract_kind="spec",
        target_step="custom",
        iteration=2,
        placeholders=("packet_spec",),
        packet_path=packet_path,
    )

    packet_path.write_text(json.dumps(first["packet"], indent=4), encoding="utf-8")
    packet_path.with_suffix(".json.sha256").write_text(
        __import__("hashlib").sha256(packet_path.read_bytes()).hexdigest() + "\n",
        encoding="ascii",
    )

    fallback = resolve_context_packet(
        source_path=source,
        contract_kind="spec",
        target_step="custom",
        iteration=2,
        placeholders=("packet_spec",),
        packet_path=packet_path,
    )
    assert fallback["mode"] == "full_fallback"


def test_packet_persistence_errors_fall_back_to_authoritative_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "spec.md"
    source.write_text(_spec(), encoding="utf-8")
    monkeypatch.setattr(
        "cafe.core.context_packet.persist_context_packet",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read-only")),
    )

    resolved = resolve_context_packet(
        source_path=source,
        contract_kind="spec",
        target_step="custom",
        iteration=2,
        placeholders=("packet_spec",),
        packet_path=tmp_path / "packet.json",
    )

    assert resolved["mode"] == "full_fallback"
    assert resolved["path"] == str(source)
    assert resolved["fallback_reason"] == "packet_persist_failed"
    assert resolved["detail"] == "context packet could not be persisted"


def test_packet_build_failures_use_the_build_boundary_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "spec.md"
    source.write_text(_spec(), encoding="utf-8")
    monkeypatch.setattr(
        "cafe.core.context_packet.build_context_packet",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("source unavailable")),
    )

    resolved = resolve_context_packet(
        source_path=source,
        contract_kind="spec",
        target_step="custom",
        iteration=2,
        placeholders=("packet_spec",),
        packet_path=tmp_path / "packet.json",
    )

    assert resolved["fallback_reason"] == "packet_build_failed"
    assert resolved["detail"] == "context packet could not be built"
