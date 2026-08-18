"""Deterministic correction-context manifests for fresh workflow sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from cafe.core.blackboard import ArtifactEntry, BlackboardState
from cafe.core.packet_io import (
    atomic_write_bytes,
    canonical_json,
    compact_json,
    file_metadata,
    load_or_persist_json,
    sha256_bytes,
)
from cafe.utils.git_utils import to_cwd_relative_path

DELTA_PACKET_SCHEMA_VERSION = 1
DELTA_PACKET_INLINE_LIMIT_BYTES = 16 * 1024


def _display_path(path: Path) -> str:
    try:
        return str(to_cwd_relative_path(path))
    except (OSError, ValueError):
        return path.as_posix()


def _file_record(path: str | Path) -> dict[str, Any]:
    return file_metadata(path, display_path=_display_path)


def _artifact_record(name: str, artifact: ArtifactEntry) -> dict[str, Any]:
    return {
        "name": name,
        "kind": artifact.kind.value,
        "version": artifact.version,
        "updated_by": artifact.updated_by,
        **_file_record(artifact.path),
    }


def _baton_snapshot(state: BlackboardState) -> Optional[dict[str, Any]]:
    if state.handoff_contract is None:
        return None
    return dict(state.handoff_contract.to_dict())


def build_delta_packet(
    *,
    issue_name: str,
    step_name: str,
    iteration: int,
    blackboard_state: BlackboardState,
    declared_artifacts: Mapping[str, ArtifactEntry],
    previous_output: Path,
    user_input_path: Path,
    user_input: str,
    git_snapshot: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Build a derived manifest from authoritative workflow inputs."""
    user_input_bytes = user_input.encode("utf-8")
    packet: dict[str, Any] = {
        "schema_version": DELTA_PACKET_SCHEMA_VERSION,
        "run_kind": "correction",
        "issue": issue_name,
        "step": step_name,
        "iteration": iteration,
        "predecessor": {
            "step": step_name,
            "iteration": iteration - 1,
        },
        "baton": _baton_snapshot(blackboard_state),
        "declared_artifacts": [
            _artifact_record(name, artifact)
            for name, artifact in sorted(declared_artifacts.items())
        ],
        "previous_output": _file_record(previous_output),
        "user_input": {
            "path": _display_path(user_input_path),
            "bytes": len(user_input_bytes),
            "sha256": sha256_bytes(user_input_bytes),
        },
    }
    if git_snapshot:
        packet["git"] = dict(sorted(git_snapshot.items()))
    return packet


def serialize_delta_packet(packet: Mapping[str, Any]) -> bytes:
    """Return canonical UTF-8 JSON bytes."""
    return canonical_json(packet)


def persist_delta_input_snapshot(path: Path, user_input: str) -> str:
    """Persist the exact correction input once and return its immutable text."""
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"Invalid persisted delta input snapshot: {path}") from exc
    atomic_write_bytes(path, user_input.encode("utf-8"))
    return user_input


def _validate_file_record(record: Any, *, field: str) -> None:
    if not isinstance(record, dict):
        raise ValueError(f"Invalid delta packet field: {field}")
    if not isinstance(record.get("path"), str) or not record["path"]:
        raise ValueError(f"Invalid delta packet path: {field}")
    if record.get("state") not in {"file", "missing", "not_file", "unreadable"}:
        raise ValueError(f"Invalid delta packet state: {field}")
    if record["state"] == "file":
        if not isinstance(record.get("bytes"), int) or record["bytes"] < 0:
            raise ValueError(f"Invalid delta packet byte count: {field}")
        if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
            raise ValueError(f"Invalid delta packet digest: {field}")


def validate_delta_packet(packet: Any) -> None:
    """Validate the complete persisted schema before it can enter a prompt."""
    if not isinstance(packet, dict):
        raise ValueError("Invalid delta packet object")
    if packet.get("schema_version") != DELTA_PACKET_SCHEMA_VERSION:
        raise ValueError("Invalid delta packet schema_version")
    if packet.get("run_kind") != "correction":
        raise ValueError("Invalid delta packet run_kind")
    if not isinstance(packet.get("issue"), str) or not packet["issue"]:
        raise ValueError("Invalid delta packet issue")
    if not isinstance(packet.get("step"), str) or not packet["step"]:
        raise ValueError("Invalid delta packet step")
    iteration = packet.get("iteration")
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 2:
        raise ValueError("Invalid delta packet iteration")

    predecessor = packet.get("predecessor")
    if not isinstance(predecessor, dict):
        raise ValueError("Invalid delta packet predecessor")
    if predecessor.get("step") != packet["step"] or predecessor.get("iteration") != iteration - 1:
        raise ValueError("Invalid delta packet predecessor identity")
    if packet.get("baton") is not None and not isinstance(packet["baton"], dict):
        raise ValueError("Invalid delta packet baton")

    artifacts = packet.get("declared_artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Invalid delta packet declared_artifacts")
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise ValueError(f"Invalid delta packet artifact: {index}")
        if not isinstance(artifact.get("name"), str) or not artifact["name"]:
            raise ValueError(f"Invalid delta packet artifact name: {index}")
        if not isinstance(artifact.get("kind"), str):
            raise ValueError(f"Invalid delta packet artifact kind: {index}")
        _validate_file_record(artifact, field=f"declared_artifacts[{index}]")

    _validate_file_record(packet.get("previous_output"), field="previous_output")
    user_input = packet.get("user_input")
    if not isinstance(user_input, dict):
        raise ValueError("Invalid delta packet user_input")
    if not isinstance(user_input.get("path"), str) or not user_input["path"]:
        raise ValueError("Invalid delta packet user_input path")
    if not isinstance(user_input.get("bytes"), int) or user_input["bytes"] < 0:
        raise ValueError("Invalid delta packet user_input bytes")
    if not isinstance(user_input.get("sha256"), str) or len(user_input["sha256"]) != 64:
        raise ValueError("Invalid delta packet user_input digest")
    if "git" in packet and not isinstance(packet["git"], dict):
        raise ValueError("Invalid delta packet git")


def persist_delta_packet(
    path: Path,
    packet: Mapping[str, Any],
    *,
    expected_sha256: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist once, or reuse and validate the immutable packet from an interrupted run."""

    def matches_identity(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
        return all(
            old.get(key) == new.get(key) for key in ("schema_version", "issue", "step", "iteration")
        ) and old.get("user_input") == new.get("user_input")

    try:
        return load_or_persist_json(
            path,
            packet,
            validate=validate_delta_packet,
            matches_identity=matches_identity,
            expected_sha256=expected_sha256,
            display_path=_display_path,
        )
    except ValueError as exc:
        message = str(exc)
        if "Invalid persisted packet" in message:
            raise ValueError(f"Invalid persisted delta packet: {path}") from exc
        if "Persisted packet hash mismatch" in message:
            raise ValueError(f"Persisted delta packet hash mismatch: {path}") from exc
        if "identity mismatch" in message:
            raise ValueError(f"Persisted delta packet identity mismatch: {path}") from exc
        raise


def inline_delta_packet(
    packet: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> str:
    """Inline the packet when bounded, otherwise inline a deterministic pointer."""
    compact = compact_json(packet)
    if len(compact.encode("utf-8")) <= DELTA_PACKET_INLINE_LIMIT_BYTES:
        return compact

    pointer = {
        "schema_version": packet.get("schema_version"),
        "run_kind": packet.get("run_kind"),
        "issue": packet.get("issue"),
        "step": packet.get("step"),
        "iteration": packet.get("iteration"),
        "previous_output": packet.get("previous_output"),
        "packet": dict(metadata),
    }
    return compact_json(pointer)
