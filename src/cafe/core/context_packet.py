"""Immutable packet envelopes for validated source-owned contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cafe.core.downstream_contract import ContractValidationError, extract_downstream_contract
from cafe.core.packet_io import file_metadata, load_or_persist_json, sha256_bytes

CONTEXT_PACKET_SCHEMA_VERSION = 1


def build_context_packet(*, source_path: str | Path, contract_kind: str, target_step: str, iteration: int, placeholders: tuple[str, ...]) -> dict[str, Any]:
    source = Path(source_path)
    contract = extract_downstream_contract(source, kind=contract_kind)
    return {
        "schema_version": CONTEXT_PACKET_SCHEMA_VERSION,
        "packet_kind": "downstream_contract",
        "target": {"step": target_step, "iteration": iteration, "placeholders": list(placeholders)},
        "source": file_metadata(source),
        "contract": {"kind": contract.kind, "version": contract.version, "sha256": contract.sha256, "bytes": contract.bytes.decode("utf-8")},
    }


def validate_context_packet(packet: Any) -> None:
    if not isinstance(packet, dict) or packet.get("schema_version") != CONTEXT_PACKET_SCHEMA_VERSION or packet.get("packet_kind") != "downstream_contract":
        raise ValueError("Invalid context packet schema")
    if not isinstance(packet.get("target"), dict) or not isinstance(packet.get("source"), dict) or not isinstance(packet.get("contract"), dict):
        raise ValueError("Invalid context packet envelope")
    contract = packet["contract"]
    if contract.get("kind") not in {"spec", "plan"} or contract.get("version") != 1 or not isinstance(contract.get("bytes"), str):
        raise ValueError("Invalid context packet contract")
    if sha256_bytes(contract["bytes"].encode("utf-8")) != contract.get("sha256"):
        raise ValueError("Invalid context packet contract hash")


def persist_context_packet(path: Path, packet: Mapping[str, Any], *, expected_sha256: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    return load_or_persist_json(path, packet, validate=validate_context_packet, expected_sha256=expected_sha256, matches_identity=lambda old, new: old.get("target") == new.get("target") and old.get("source") == new.get("source") and old.get("contract") == new.get("contract"))


def resolve_context_packet(*, source_path: str | Path, contract_kind: str, target_step: str, iteration: int, placeholders: tuple[str, ...], packet_path: Path) -> dict[str, Any]:
    """Return packet metadata or a deliberate full-source fallback result."""
    try:
        packet = build_context_packet(source_path=source_path, contract_kind=contract_kind, target_step=target_step, iteration=iteration, placeholders=placeholders)
        persisted, metadata = persist_context_packet(packet_path, packet)
        return {"mode": "packet", "path": packet_path.as_posix(), "packet": persisted, "metadata": metadata}
    except (ContractValidationError, ValueError) as exc:
        return {"mode": "full_fallback", "path": Path(source_path).as_posix(), "reason": str(exc)}
