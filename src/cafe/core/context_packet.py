"""Immutable, runtime-derived views of authoritative Markdown artifacts."""

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

CONTEXT_PACKET_SCHEMA_VERSION = 2
_LEGACY_CONTEXT_PACKET_SCHEMA_VERSION = 1
_STRUCTURAL_MANIFEST_VERSION = 1
_FALLBACK_DETAILS = {
    "packet_build_failed": "context packet could not be built",
    "packet_invalid": "context packet validation failed",
    "packet_persist_failed": "context packet could not be persisted",
}
_FALLBACK_REASONS = frozenset(_FALLBACK_DETAILS)
_MAX_DIAGNOSTIC_DETAIL = 160


def _visible_heading_fragments(text: str) -> list[dict[str, Any]]:
    """Extract deterministic, source-owned Markdown fragments.

    A duplicate heading path is deliberately not a packet candidate: selecting
    one occurrence would make a compact view ambiguous, so the caller falls
    back to the complete authority instead.
    """
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, int, str, tuple[str, ...]]] = []
    stack: list[tuple[int, str]] = []
    fence: str | None = None
    offset = 0
    for line_number, line in enumerate(lines):
        visible = fence is None
        fence = _update_fence(fence, line)
        if visible and fence is None:
            match = __import__("re").match(r"^(#{1,6})[ \t]+(.+?)[ \t]*#?[ \t]*$", line.rstrip("\r\n"))
            if match:
                level, title = len(match.group(1)), match.group(2)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                headings.append((line_number, level, title, tuple(item[1] for item in stack)))
        offset += len(line)
    paths = [heading[3] for heading in headings]
    if len(paths) != len(set(paths)):
        raise ValueError("Ambiguous Markdown heading path")
    fragments: list[dict[str, Any]] = []
    starts = [item[0] for item in headings]
    if starts and starts[0] > 0:
        content = "".join(lines[: starts[0]])
        fragments.append(_fragment(("document",), 1, content))
    elif not starts:
        fragments.append(_fragment(("document",), 1, text))
        return fragments
    for index, (start, _level, _title, path) in enumerate(headings):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        fragments.append(_fragment(path, 1, "".join(lines[start:end])))
    return fragments


def _fragment(path: tuple[str, ...], ordinal: int, content: str) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    return {
        "heading_path": list(path),
        "ordinal": ordinal,
        "bytes": content,
        "sha256": sha256_bytes(encoded),
    }


def _update_fence(fence: str | None, line: str) -> str | None:
    import re
    marker = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
    if marker is None:
        return fence
    token, trailing = marker.groups()
    if fence is None:
        return token
    return None if token[0] == fence[0] and len(token) >= len(fence) and not trailing.strip() else fence


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
    packet_requested_placeholders: frozenset[str] | None = None,
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
    if packet_requested_placeholders is not None and (
        not isinstance(packet_requested_placeholders, frozenset)
        or any(
            not isinstance(placeholder, str) or not placeholder
            for placeholder in packet_requested_placeholders
        )
    ):
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
        _validate_declared_packet_bindings(
            validated,
            authoritative_inputs,
            packet_requested_placeholders,
        )
        _validate_declared_paired_bindings(validated, authoritative_inputs)
        _validate_binding_authority(
            validated,
            authoritative_inputs=authoritative_inputs,
            packet_dir=expected_packet_dir,
            target_step=target_step,
            iteration=iteration,
        )
    return validated


def canonical_context_packet_path(
    packet_dir: str | Path, placeholders: tuple[str, ...]
) -> Path:
    """Return the sole runtime-owned path for one declared packet relation."""
    if not placeholders:
        raise ValueError("Invalid persisted context packet decision")
    relation = _packet_relation(placeholders[0])
    if any(_packet_relation(placeholder) != relation for placeholder in placeholders):
        raise ValueError("Invalid persisted context packet decision")
    return Path(packet_dir) / f"context_{relation}.json"


def _packet_relation(placeholder: str) -> str:
    return placeholder.removesuffix("_path")


def _declared_relation_placeholders(
    placeholder: str, authoritative_inputs: Mapping[str, str | Path]
) -> tuple[str, ...]:
    relation = _packet_relation(placeholder)
    return tuple(
        declared
        for declared in authoritative_inputs
        if _packet_relation(declared) == relation
    )


def _validate_declared_paired_bindings(
    bindings: Mapping[str, Mapping[str, Any]],
    authoritative_inputs: Mapping[str, str | Path],
) -> None:
    """Require every declared conventional alias pair to share one decision."""
    checked: set[str] = set()
    for placeholder in authoritative_inputs:
        relation = _packet_relation(placeholder)
        if relation in checked:
            continue
        checked.add(relation)
        declared = _declared_relation_placeholders(placeholder, authoritative_inputs)
        if len(declared) < 2:
            continue
        if any(name not in bindings for name in declared):
            raise ValueError("Invalid persisted context packet decision")
        first = bindings[declared[0]]
        if any(bindings[name] != first for name in declared[1:]):
            raise ValueError("Invalid persisted context packet decision")


def _validate_declared_packet_bindings(
    bindings: Mapping[str, Mapping[str, Any]],
    authoritative_inputs: Mapping[str, str | Path],
    packet_requested_placeholders: frozenset[str] | None,
) -> None:
    """Require every active packet policy relation, including singletons."""
    if packet_requested_placeholders is None:
        return
    if not packet_requested_placeholders.issubset(authoritative_inputs):
        raise ValueError("Invalid persisted context packet decision")
    if any(placeholder not in bindings for placeholder in packet_requested_placeholders):
        raise ValueError("Invalid persisted context packet decision")
    if any(
        bindings[placeholder].get("requested_mode") != "packet"
        or bindings[placeholder].get("mode") not in {"packet", "full_fallback"}
        for placeholder in packet_requested_placeholders
    ):
        raise ValueError("Invalid persisted context packet decision")


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
        declared_placeholders = _declared_relation_placeholders(placeholder, authoritative_inputs)
        if packet_dir is not None and packet_path != canonical_context_packet_path(
            packet_dir, declared_placeholders
        ).resolve():
            raise ValueError("Invalid persisted context packet decision")
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            validate_context_packet(packet)
            expected_packet = build_context_packet(
                source_path=authority,
                contract_kind=_packet_contract_kind(packet),
                target_step=target_step if target_step is not None else packet["target"]["step"],
                iteration=iteration if iteration is not None else packet["target"]["iteration"],
                placeholders=declared_placeholders,
                source_artifact_name=str(binding["source"]["artifact_name"]),
                source_artifact_version=int(binding["source"]["artifact_version"]),
            )
        except (OSError, json.JSONDecodeError, ValueError, ContractValidationError) as exc:
            raise ValueError("Invalid persisted context packet decision") from exc
        source = packet["source"]
        if (
            Path(str(source["path"])).resolve() != authority_path
            or source != binding["source"]
            or packet != expected_packet
        ):
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
    source_path = source.get("path")
    if (
        not isinstance(source_path, str)
        or not source_path
        or Path(source_path).resolve() != authority_path
        or any(source.get(key) != current[key] for key in ("state", "bytes", "sha256"))
    ):
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
    source_metadata = file_metadata(source)
    if source_metadata.get("state") != "file":
        raise OSError("Unreadable context source")
    source_metadata.update(
        {
            "artifact_name": source_artifact_name or source.stem,
            "artifact_version": source_artifact_version or 1,
        }
    )
    # A valid v1 contract remains readable during migration.  New and malformed
    # documents never need contract metadata: they use the structural manifest.
    try:
        contract = extract_downstream_contract(source, kind=contract_kind)
    except ContractValidationError:
        contract = None
    if contract is not None:
        return {
            "schema_version": _LEGACY_CONTEXT_PACKET_SCHEMA_VERSION,
            "packet_kind": "downstream_contract",
            "target": {"step": target_step, "iteration": iteration, "placeholders": list(placeholders)},
            "source": source_metadata,
            "contract": {"kind": contract.kind, "version": contract.version,
                         "sha256": contract.sha256, "bytes": contract.bytes.decode("utf-8")},
        }
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OSError("Unreadable context source") from exc
    return {
        "schema_version": CONTEXT_PACKET_SCHEMA_VERSION,
        "packet_kind": "structural_manifest",
        "target": {"step": target_step, "iteration": iteration, "placeholders": list(placeholders)},
        "source": source_metadata,
        "manifest": {
            "kind": contract_kind,
            "version": _STRUCTURAL_MANIFEST_VERSION,
            "extraction_profile": "markdown-headings-v1",
            "source_sha256": source_metadata["sha256"],
            "fragments": _visible_heading_fragments(text),
            "checkboxes": _checkbox_records(text) if contract_kind == "plan" else [],
        },
    }


def validate_context_packet(packet: Any) -> None:
    if not isinstance(packet, dict) or not {"schema_version", "packet_kind", "target", "source"}.issubset(packet):
        raise ValueError("Invalid context packet envelope")
    legacy = packet.get("schema_version") == _LEGACY_CONTEXT_PACKET_SCHEMA_VERSION and packet.get("packet_kind") == "downstream_contract"
    structural = packet.get("schema_version") == CONTEXT_PACKET_SCHEMA_VERSION and packet.get("packet_kind") == "structural_manifest"
    if not (legacy or structural):
        raise ValueError("Invalid context packet schema")
    expected_keys = {"schema_version", "packet_kind", "target", "source", "contract"} if legacy else {"schema_version", "packet_kind", "target", "source", "manifest"}
    if set(packet) != expected_keys:
        raise ValueError("Invalid context packet envelope")
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
    if legacy:
        contract = packet.get("contract")
        if set(packet) != {"schema_version", "packet_kind", "target", "source", "contract"} or not isinstance(contract, dict) or set(contract) != {"kind", "version", "sha256", "bytes"} or contract.get("kind") not in {"spec", "plan"} or contract.get("version") != 1 or not isinstance(contract.get("bytes"), str) or not _valid_sha256(contract.get("sha256")) or sha256_bytes(contract["bytes"].encode("utf-8")) != contract.get("sha256"):
            raise ValueError("Invalid context packet contract")
        return
    manifest = packet.get("manifest")
    if set(packet) != {"schema_version", "packet_kind", "target", "source", "manifest"} or not isinstance(manifest, dict) or set(manifest) != {"kind", "version", "extraction_profile", "source_sha256", "fragments", "checkboxes"} or manifest.get("kind") not in {"spec", "plan"} or manifest.get("version") != _STRUCTURAL_MANIFEST_VERSION or manifest.get("extraction_profile") != "markdown-headings-v1" or manifest.get("source_sha256") != source["sha256"] or not isinstance(manifest["fragments"], list) or not manifest["fragments"]:
        raise ValueError("Invalid structural context packet")
    for fragment in manifest["fragments"]:
        if not isinstance(fragment, dict) or set(fragment) != {"heading_path", "ordinal", "bytes", "sha256"} or not isinstance(fragment["heading_path"], list) or not fragment["heading_path"] or any(not isinstance(item, str) or not item for item in fragment["heading_path"]) or not isinstance(fragment["ordinal"], int) or fragment["ordinal"] < 1 or not isinstance(fragment["bytes"], str) or not _valid_sha256(fragment["sha256"]) or sha256_bytes(fragment["bytes"].encode("utf-8")) != fragment["sha256"]:
            raise ValueError("Invalid structural context packet")
    if not isinstance(manifest["checkboxes"], list):
        raise ValueError("Invalid structural context packet")


def _checkbox_records(text: str) -> list[dict[str, Any]]:
    import re
    records: list[dict[str, Any]] = []
    fence: str | None = None
    for line in text.splitlines():
        visible = fence is None
        fence = _update_fence(fence, line)
        match = re.match(r"^\s*-\s+\[([ xX])\]\s+(.+)$", line)
        if visible and fence is None and match:
            marker, label = match.groups()
            records.append({"ordinal": len(records) + 1, "checked": marker.lower() == "x", "text": label.strip()})
    return records


def _packet_contract_kind(packet: Mapping[str, Any]) -> str:
    if packet.get("packet_kind") == "downstream_contract":
        return str(packet["contract"]["kind"])
    return str(packet["manifest"]["kind"])


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
        and old.get("packet_kind") == new.get("packet_kind")
        and old.get("contract") == new.get("contract")
        and old.get("manifest") == new.get("manifest"),
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
    except (ContractValidationError, OSError, ValueError):
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
