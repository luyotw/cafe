"""Immutable packet envelopes for validated source-owned contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cafe.core.downstream_contract import ContractValidationError, extract_downstream_contract
from cafe.core.packet_io import (
    canonical_json,
    file_metadata,
    load_or_persist_json,
    sha256_bytes,
)

CONTEXT_PACKET_SCHEMA_VERSION = 1


def build_context_packet(
    *,
    source_path: str | Path,
    contract_kind: str,
    target_step: str,
    iteration: int,
    placeholders: tuple[str, ...],
    source_artifact_name: str | None = None,
    source_artifact_version: int | None = None,
) -> dict[str, Any]:
    source = Path(source_path)
    contract = extract_downstream_contract(source, kind=contract_kind)
    source_metadata = file_metadata(source)
    source_metadata.update(
        {
            "artifact_name": source_artifact_name or source.stem,
            "artifact_version": source_artifact_version or 1,
        }
    )
    return {
        "schema_version": CONTEXT_PACKET_SCHEMA_VERSION,
        "packet_kind": "downstream_contract",
        "target": {"step": target_step, "iteration": iteration, "placeholders": list(placeholders)},
        "source": source_metadata,
        "contract": {
            "kind": contract.kind,
            "version": contract.version,
            "sha256": contract.sha256,
            "bytes": contract.bytes.decode("utf-8"),
        },
    }


def validate_context_packet(packet: Any) -> None:
    if not isinstance(packet, dict) or set(packet) != {
        "schema_version",
        "packet_kind",
        "target",
        "source",
        "contract",
    }:
        raise ValueError("Invalid context packet envelope")
    if (
        packet.get("schema_version") != CONTEXT_PACKET_SCHEMA_VERSION
        or packet.get("packet_kind") != "downstream_contract"
    ):
        raise ValueError("Invalid context packet schema")
    target = packet.get("target")
    source = packet.get("source")
    if (
        not isinstance(target, dict)
        or set(target) != {"step", "iteration", "placeholders"}
        or not isinstance(target["step"], str)
        or not isinstance(target["iteration"], int)
        or isinstance(target["iteration"], bool)
        or target["iteration"] < 1
        or not isinstance(target["placeholders"], list)
        or not target["placeholders"]
        or any(not isinstance(item, str) or not item for item in target["placeholders"])
        or len(set(target["placeholders"])) != len(target["placeholders"])
    ):
        raise ValueError("Invalid context packet target")
    if (
        not isinstance(source, dict)
        or set(source) != {"artifact_name", "artifact_version", "path", "state", "bytes", "sha256"}
        or source.get("state") != "file"
        or not isinstance(source.get("artifact_name"), str)
        or not source["artifact_name"]
        or not isinstance(source.get("artifact_version"), int)
        or isinstance(source["artifact_version"], bool)
        or source["artifact_version"] < 1
        or not isinstance(source.get("path"), str)
        or not source["path"]
        or not isinstance(source.get("bytes"), int)
        or isinstance(source["bytes"], bool)
        or source["bytes"] < 0
        or not _valid_sha256(source.get("sha256"))
    ):
        raise ValueError("Invalid context packet source")
    if not isinstance(packet.get("contract"), dict):
        raise ValueError("Invalid context packet envelope")
    contract = packet["contract"]
    if (
        set(contract) != {"kind", "version", "sha256", "bytes"}
        or contract.get("kind") not in {"spec", "plan"}
        or contract.get("version") != 1
        or not isinstance(contract.get("bytes"), str)
        or not _valid_sha256(contract.get("sha256"))
    ):
        raise ValueError("Invalid context packet contract")
    if sha256_bytes(contract["bytes"].encode("utf-8")) != contract.get("sha256"):
        raise ValueError("Invalid context packet contract hash")


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def persist_context_packet(
    path: Path, packet: Mapping[str, Any], *, expected_sha256: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    return load_or_persist_json(
        path,
        packet,
        validate=validate_context_packet,
        expected_sha256=expected_sha256,
        matches_identity=lambda old, new: old.get("target") == new.get("target")
        and old.get("source") == new.get("source")
        and old.get("contract") == new.get("contract"),
    )


def resolve_context_packet(
    *,
    source_path: str | Path,
    contract_kind: str,
    target_step: str,
    iteration: int,
    placeholders: tuple[str, ...],
    packet_path: Path,
    source_artifact_name: str | None = None,
    source_artifact_version: int | None = None,
) -> dict[str, Any]:
    """Return packet metadata or a deliberate full-source fallback result."""
    try:
        packet = build_context_packet(
            source_path=source_path,
            contract_kind=contract_kind,
            target_step=target_step,
            iteration=iteration,
            placeholders=placeholders,
            source_artifact_name=source_artifact_name,
            source_artifact_version=source_artifact_version,
        )
        # The deterministic packet derived from the current authority is the
        # trust anchor.  A mutable adjacent receipt cannot approve altered bytes.
        expected_sha256 = sha256_bytes(canonical_json(packet))
        persisted, metadata = persist_context_packet(
            packet_path, packet, expected_sha256=expected_sha256
        )
        return {
            "mode": "packet",
            "path": packet_path.as_posix(),
            "packet": persisted,
            "metadata": metadata,
            "source": dict(packet["source"]),
        }
    except (ContractValidationError, ValueError, OSError):
        reason = (
            "Unable to persist context packet"
            if not packet_path.exists()
            else "Invalid or altered context packet"
        )
        return {"mode": "full_fallback", "path": Path(source_path).as_posix(), "reason": reason}
