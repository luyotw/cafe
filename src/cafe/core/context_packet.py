"""Immutable packet envelopes for validated source-owned contracts."""

from __future__ import annotations

import json
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
_FALLBACK_DETAILS = {
    "packet_build_failed": "context packet could not be built",
    "packet_invalid": "context packet validation failed",
    "packet_persist_failed": "context packet could not be persisted",
}
_FALLBACK_REASONS = frozenset(_FALLBACK_DETAILS)
_MAX_DIAGNOSTIC_DETAIL = 160


def format_context_packet_diagnostic(binding: Mapping[str, Any]) -> str:
    """Return the shared, sanitized diagnostic used by prompt and status views."""
    effective_mode = str(binding.get("effective_mode", binding.get("mode", "")))
    if effective_mode == "packet":
        return "verified"
    reason = str(binding.get("fallback_reason") or "")
    detail = str(binding.get("detail") or "")
    diagnostic = f"{effective_mode}:{reason}" if reason else effective_mode
    return f"{diagnostic} ({detail})" if detail else diagnostic


def build_context_packet_diagnostics(
    bindings: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build strict, one-per-relation diagnostics from effective input bindings."""
    bindings = validate_effective_input_bindings(bindings)
    grouped: dict[tuple[Any, ...], list[str]] = {}
    for placeholder, binding in bindings.items():
        if binding.get("requested_mode") != "packet":
            continue
        effective_mode = binding.get("mode")
        fallback_reason = binding.get("fallback_reason", "")
        detail = binding.get("detail", "")
        source = binding.get("source")
        key = (
            tuple(sorted(source.items())),
            str(binding.get("path", "")),
            effective_mode,
            fallback_reason,
            detail,
        )
        grouped.setdefault(key, []).append(placeholder)

    diagnostics: list[dict[str, Any]] = []
    for (source_items, path, effective_mode, fallback_reason, detail), placeholders in grouped.items():
        diagnostics.append(
            {
                "placeholders": sorted(placeholders),
                "source": dict(source_items),
                "requested_mode": "packet",
                "effective_mode": effective_mode,
                "fallback_reason": fallback_reason or None,
                "detail": detail or None,
                "path": path,
            }
        )
    return diagnostics


def validate_context_packet_diagnostic(diagnostic: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a projected record before any status formatter renders it."""
    required = {
        "placeholders", "source", "requested_mode", "effective_mode", "fallback_reason", "detail", "path"
    }
    if (
        not isinstance(diagnostic, Mapping)
        or not required.issubset(diagnostic)
        or set(diagnostic) - (required | {"consumer", "iteration"})
    ):
        raise ValueError("Invalid context packet diagnostic")
    consumer = diagnostic.get("consumer")
    if consumer is not None and (
        not isinstance(consumer, str) or not consumer or len(consumer) > 64
    ):
        raise ValueError("Invalid context packet diagnostic")
    projected_iteration = diagnostic.get("iteration")
    if projected_iteration is not None and (
        not isinstance(projected_iteration, int)
        or isinstance(projected_iteration, bool)
        or not 1 <= projected_iteration <= 999_999
    ):
        raise ValueError("Invalid context packet diagnostic")
    placeholders = diagnostic.get("placeholders")
    if (
        not isinstance(placeholders, list)
        or not placeholders
        or any(not isinstance(item, str) or not item for item in placeholders)
        or len(set(placeholders)) != len(placeholders)
    ):
        raise ValueError("Invalid context packet diagnostic")
    fallback_reason = diagnostic.get("fallback_reason") or ""
    detail = diagnostic.get("detail") or ""
    binding = {
        "requested_mode": diagnostic.get("requested_mode"),
        "mode": diagnostic.get("effective_mode"),
        "path": diagnostic.get("path"),
        "reason": fallback_reason,
        "fallback_reason": fallback_reason,
        "detail": detail,
        "source": diagnostic.get("source"),
    }
    validate_effective_input_bindings({placeholders[0]: binding})
    return dict(diagnostic)


def validate_effective_input_bindings(
    bindings: Mapping[str, Mapping[str, Any]],
    *,
    authoritative_inputs: Mapping[str, str | Path] | None = None,
    packet_dir: str | Path | None = None,
    target_step: str | None = None,
    iteration: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Validate the sole persisted effective-input decision without normalizing it.

    Iteration metadata is agent-writable.  A consumer and status view must therefore
    accept only the exact runtime-owned packet decision shapes and messages.
    """
    if not isinstance(bindings, Mapping):
        raise ValueError("Invalid persisted context packet decision")
    if authoritative_inputs is not None and not isinstance(authoritative_inputs, Mapping):
        raise ValueError("Invalid persisted context packet decision")
    expected_packet_dir = Path(packet_dir).resolve() if packet_dir is not None else None
    validated: dict[str, dict[str, Any]] = {}
    for placeholder, binding in bindings.items():
        if not isinstance(placeholder, str) or not placeholder or not isinstance(binding, Mapping):
            raise ValueError("Invalid persisted context packet decision")
        mode = binding.get("mode")
        path = binding.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("Invalid persisted context packet decision")
        if mode == "full":
            if set(binding) != {"mode", "path"}:
                raise ValueError("Invalid persisted context packet decision")
        elif mode in {"packet", "full_fallback"}:
            if set(binding) != {
                "requested_mode", "mode", "path", "reason", "fallback_reason", "detail", "source"
            } or binding.get("requested_mode") != "packet":
                raise ValueError("Invalid persisted context packet decision")
            _validate_packet_source(binding.get("source"))
            reason = binding.get("fallback_reason")
            detail = binding.get("detail")
            if not isinstance(reason, str) or not isinstance(detail, str) or len(detail) > _MAX_DIAGNOSTIC_DETAIL:
                raise ValueError("Invalid persisted context packet decision")
            if mode == "packet":
                if binding.get("reason") != "" or reason or detail:
                    raise ValueError("Invalid persisted context packet decision")
            elif (
                reason not in _FALLBACK_REASONS
                or binding.get("reason") != reason
                or detail != _FALLBACK_DETAILS[reason]
            ):
                raise ValueError("Invalid persisted context packet decision")
        else:
            raise ValueError("Invalid persisted context packet decision")
        validated[placeholder] = dict(binding)
    _validate_paired_packet_bindings(validated)
    if authoritative_inputs is not None:
        _validate_binding_authority(
            validated,
            authoritative_inputs=authoritative_inputs,
            packet_dir=expected_packet_dir,
            target_step=target_step,
            iteration=iteration,
        )
    return validated


def _validate_paired_packet_bindings(bindings: Mapping[str, Mapping[str, Any]]) -> None:
    """Reject distinct decisions for placeholders declaring the same packet source."""
    relations: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for binding in bindings.values():
        if binding.get("requested_mode") != "packet":
            continue
        source = binding.get("source")
        assert isinstance(source, Mapping)  # already shape-validated above
        relation = tuple(sorted(source.items()))
        prior = relations.setdefault(relation, binding)
        if any(
            binding.get(field) != prior.get(field)
            for field in ("mode", "path", "reason", "fallback_reason", "detail")
        ):
            raise ValueError("Invalid persisted context packet decision")


def _validate_binding_authority(
    bindings: Mapping[str, Mapping[str, Any]],
    *,
    authoritative_inputs: Mapping[str, str | Path],
    packet_dir: Path | None,
    target_step: str | None,
    iteration: int | None,
) -> None:
    """Bind persisted decisions to their declared source and envelope on disk."""
    paired: dict[str, Mapping[str, Any]] = {}
    for placeholder, binding in bindings.items():
        authority = authoritative_inputs.get(placeholder)
        if authority is None:
            raise ValueError("Invalid persisted context packet decision")
        authority_path = Path(authority).resolve()
        mode = binding["mode"]
        if mode == "full":
            if Path(str(binding["path"])).resolve() != authority_path:
                raise ValueError("Invalid persisted context packet decision")
            continue
        if mode == "full_fallback":
            if Path(str(binding["path"])).resolve() != authority_path:
                raise ValueError("Invalid persisted context packet decision")
            _validate_full_source_metadata(binding["source"], authority_path)
            _validate_declared_pair(placeholder, binding, paired)
            continue
        packet_path = Path(str(binding["path"])).resolve()
        if packet_dir is not None and packet_path.parent != packet_dir:
            raise ValueError("Invalid persisted context packet decision")
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            validate_context_packet(packet)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("Invalid persisted context packet decision") from exc
        source = packet["source"]
        if Path(str(source["path"])).resolve() != authority_path or source != binding["source"]:
            raise ValueError("Invalid persisted context packet decision")
        _validate_full_source_metadata(source, authority_path)
        target = packet["target"]
        if target_step is not None and target["step"] != target_step:
            raise ValueError("Invalid persisted context packet decision")
        if iteration is not None and target["iteration"] != iteration:
            raise ValueError("Invalid persisted context packet decision")
        if placeholder not in target["placeholders"]:
            raise ValueError("Invalid persisted context packet decision")
        _validate_declared_pair(placeholder, binding, paired)


def _validate_full_source_metadata(source: Any, authority_path: Path) -> None:
    """When available, make fallback provenance agree with the full authority."""
    if not isinstance(source, Mapping) or "path" not in source:
        return
    try:
        current = file_metadata(authority_path)
    except OSError as exc:
        raise ValueError("Invalid persisted context packet decision") from exc
    if any(source.get(key) != value for key, value in current.items()):
        raise ValueError("Invalid persisted context packet decision")


def _validate_declared_pair(
    placeholder: str,
    binding: Mapping[str, Any],
    paired: dict[str, Mapping[str, Any]],
) -> None:
    """Keep conventional ``*_file`` / ``*_file_path`` aliases one decision."""
    relation = placeholder.removesuffix("_path")
    prior = paired.setdefault(relation, binding)
    if any(binding.get(field) != prior.get(field) for field in (
        "mode", "path", "reason", "fallback_reason", "detail", "source"
    )):
        raise ValueError("Invalid persisted context packet decision")


def _validate_packet_source(source: Any) -> None:
    if not isinstance(source, Mapping):
        raise ValueError("Invalid persisted context packet decision")
    keys = set(source)
    if keys not in ({"artifact_name", "artifact_version"}, {"artifact_name", "artifact_version", "path", "state", "bytes", "sha256"}):
        raise ValueError("Invalid persisted context packet decision")
    if not isinstance(source.get("artifact_name"), str) or not source["artifact_name"]:
        raise ValueError("Invalid persisted context packet decision")
    version = source.get("artifact_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("Invalid persisted context packet decision")
    if len(keys) == 6 and (
        source.get("state") != "file"
        or not isinstance(source.get("path"), str)
        or not source["path"]
        or not isinstance(source.get("bytes"), int)
        or isinstance(source["bytes"], bool)
        or source["bytes"] < 0
        or not _valid_sha256(source.get("sha256"))
    ):
        raise ValueError("Invalid persisted context packet decision")


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
    except ContractValidationError:
        # Invalid source contracts are confirmation failures, not safe packet
        # fallbacks.  The producer must receive the contract validator's
        # relation-specific feedback before a consumer can start.
        raise
    except OSError:
        return _context_packet_fallback(
            source_path, source_artifact_name, source_artifact_version, "packet_build_failed"
        )

    try:
        validate_context_packet(packet)
    except ValueError:
        return _context_packet_fallback(
            source_path, source_artifact_name, source_artifact_version, "packet_invalid"
        )

    try:
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
    except OSError:
        return _context_packet_fallback(
            source_path, source_artifact_name, source_artifact_version, "packet_persist_failed"
        )
    except ValueError:
        return _context_packet_fallback(
            source_path, source_artifact_name, source_artifact_version, "packet_invalid"
        )


def _context_packet_fallback(
    source_path: str | Path,
    source_artifact_name: str | None,
    source_artifact_version: int | None,
    fallback_reason: str,
) -> dict[str, Any]:
    source = {
        "artifact_name": source_artifact_name or Path(source_path).stem,
        "artifact_version": source_artifact_version or 1,
    }
    try:
        source.update(file_metadata(Path(source_path)))
    except OSError:
        pass
    return {
        "mode": "full_fallback",
        "path": Path(source_path).as_posix(),
        "reason": fallback_reason,
        "fallback_reason": fallback_reason,
        "detail": _FALLBACK_DETAILS[fallback_reason],
        "source": source,
    }
