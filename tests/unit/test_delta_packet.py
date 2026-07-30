"""Tests for deterministic correction delta packets."""

import hashlib
import json
from pathlib import Path

import pytest

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardState,
    HandoffContract,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.delta_packet import (
    build_delta_packet,
    inline_delta_packet,
    persist_delta_input_snapshot,
    persist_delta_packet,
    serialize_delta_packet,
)


def _state(artifact_path: Path) -> BlackboardState:
    return BlackboardState(
        current_step="review",
        artifacts={
            "code": ArtifactEntry(
                name="code",
                kind=ArtifactKind.WORKSPACE,
                version=2,
                updated_by="develop",
                path=str(artifact_path),
            )
        },
        handoff_contract=HandoffContract(
            version=1,
            to_owner=HandoffOwner.AGENT,
            to_step="review",
            intent=HandoffIntent.AWAIT_AGENT,
            from_step="develop",
            created_at="2026-07-30T00:00:00+08:00",
        ),
    )


def _minimal_valid_packet(*, issue: str = "issue381") -> dict:
    return {
        "schema_version": 1,
        "run_kind": "correction",
        "issue": issue,
        "step": "review",
        "iteration": 2,
        "predecessor": {"step": "review", "iteration": 1},
        "baton": None,
        "declared_artifacts": [],
        "previous_output": {"path": "output.md", "state": "missing"},
        "user_input": {
            "path": "iteration_002/delta_input.md",
            "bytes": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
    }


def test_delta_packet_is_deterministic_and_hashes_authoritative_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    artifact = tmp_path / "code.diff"
    artifact.write_text("changed code\n", encoding="utf-8")
    previous = tmp_path / "review" / "iteration_001" / "output.md"
    previous.parent.mkdir(parents=True)
    previous.write_text("finding F1\n", encoding="utf-8")
    state = _state(artifact)

    kwargs = {
        "issue_name": "issue381",
        "step_name": "review",
        "iteration": 2,
        "blackboard_state": state,
        "declared_artifacts": {"code": state.artifacts["code"]},
        "previous_output": previous,
        "user_input_path": tmp_path / "review" / "iteration_002" / "user_input.md",
        "user_input": "verify the fix",
        "git_snapshot": {"head_sha": "b", "base_sha": "a", "base_ref": "develop"},
    }
    first = build_delta_packet(**kwargs)
    second = build_delta_packet(**kwargs)

    assert serialize_delta_packet(first) == serialize_delta_packet(second)
    assert first["previous_output"]["sha256"] == hashlib.sha256(b"finding F1\n").hexdigest()
    assert first["declared_artifacts"][0]["name"] == "code"
    assert first["declared_artifacts"][0]["sha256"] == hashlib.sha256(b"changed code\n").hexdigest()
    assert "events" not in first
    assert "streaming_log" not in first
    assert "findings" not in first


def test_persisted_packet_is_immutable_across_interrupted_resume(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    artifact = tmp_path / "code.diff"
    artifact.write_text("before\n", encoding="utf-8")
    previous = tmp_path / "output.md"
    previous.write_text("review\n", encoding="utf-8")
    state = _state(artifact)
    packet_path = tmp_path / "iteration_002" / "delta_packet.json"
    snapshot_path = tmp_path / "iteration_002" / "delta_input.md"
    original_input = persist_delta_input_snapshot(snapshot_path, "first")
    original = build_delta_packet(
        issue_name="issue381",
        step_name="review",
        iteration=2,
        blackboard_state=state,
        declared_artifacts=state.artifacts,
        previous_output=previous,
        user_input_path=snapshot_path,
        user_input=original_input,
    )
    persisted, metadata = persist_delta_packet(packet_path, original)
    original_bytes = packet_path.read_bytes()
    assert list(packet_path.parent.glob(".delta_packet.json.*.tmp")) == []

    artifact.write_text("after\n", encoding="utf-8")
    retry_input = persist_delta_input_snapshot(snapshot_path, "different")
    changed = build_delta_packet(
        issue_name="issue381",
        step_name="review",
        iteration=2,
        blackboard_state=state,
        declared_artifacts=state.artifacts,
        previous_output=previous,
        user_input_path=snapshot_path,
        user_input=retry_input,
    )
    reused, reused_metadata = persist_delta_packet(packet_path, changed)

    assert persisted == reused
    assert packet_path.read_bytes() == original_bytes
    assert metadata == reused_metadata
    assert metadata["sha256"] == hashlib.sha256(original_bytes).hexdigest()


def test_persisted_packet_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    packet_path = tmp_path / "delta_packet.json"
    packet = _minimal_valid_packet()
    _, metadata = persist_delta_packet(packet_path, packet)
    packet_path.write_text(
        json.dumps({**packet, "tampered": True}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        persist_delta_packet(
            packet_path,
            packet,
            expected_sha256=metadata["sha256"],
        )


def test_persisted_packet_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    packet_path = tmp_path / "delta_packet.json"
    user_input = {
        "path": "iteration_002/delta_input.md",
        "bytes": 0,
        "sha256": hashlib.sha256(b"").hexdigest(),
    }
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_kind": "correction",
                "issue": "other",
                "step": "review",
                "iteration": 2,
                "predecessor": {"step": "review", "iteration": 1},
                "baton": None,
                "declared_artifacts": [],
                "previous_output": {"path": "output.md", "state": "missing"},
                "user_input": user_input,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity mismatch"):
        persist_delta_packet(
            packet_path,
            {
                "schema_version": 1,
                "run_kind": "correction",
                "issue": "issue381",
                "step": "review",
                "iteration": 2,
                "predecessor": {"step": "review", "iteration": 1},
                "baton": None,
                "declared_artifacts": [],
                "previous_output": {"path": "output.md", "state": "missing"},
                "user_input": user_input,
            },
        )


def test_persisted_packet_rejects_incomplete_schema(tmp_path: Path) -> None:
    packet_path = tmp_path / "delta_packet.json"
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "issue": "issue381",
                "step": "review",
                "iteration": 2,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="run_kind"):
        persist_delta_packet(
            packet_path,
            {
                "schema_version": 1,
                "issue": "issue381",
                "step": "review",
                "iteration": 2,
            },
        )


def test_delta_input_snapshot_is_immutable(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "iteration_002" / "delta_input.md"

    first = persist_delta_input_snapshot(snapshot_path, "original correction")
    second = persist_delta_input_snapshot(snapshot_path, "changed retry marker")

    assert first == "original correction"
    assert second == "original correction"
    assert snapshot_path.read_text(encoding="utf-8") == "original correction"


def test_large_packet_inlines_bounded_pointer() -> None:
    packet = {
        "schema_version": 1,
        "run_kind": "correction",
        "issue": "issue381",
        "step": "review",
        "iteration": 2,
        "previous_output": {"path": "review.md", "sha256": "a"},
        "payload": "x" * 20_000,
    }
    inline = inline_delta_packet(
        packet,
        {"path": "delta_packet.json", "bytes": 20_000, "sha256": "b"},
    )

    assert len(inline.encode("utf-8")) < 16 * 1024
    assert "delta_packet.json" in inline
    assert '"payload"' not in inline
