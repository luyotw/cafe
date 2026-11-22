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

    with pytest.raises(ValueError, match="Persisted delta packet hash mismatch"):
        persist_delta_packet(
            packet_path,
            packet,
            expected_sha256=metadata["sha256"],
        )


def test_persisted_delta_packet_keeps_its_public_parse_diagnostic(tmp_path: Path) -> None:
    packet_path = tmp_path / "delta_packet.json"
    packet_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid persisted delta packet"):
        persist_delta_packet(packet_path, _minimal_valid_packet())


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


@pytest.mark.parametrize("change", ["none", "caller", "requirements", "evidence", "legacy"])
def test_review_correction_projection_reuses_only_current_boundary_evidence(tmp_path, change):
    from cafe.core.correction_context import review_projection
    from cafe.core.packet_io import sha256_bytes
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for path in ("caller.py", "implementation.py", "other.py"):
        (tmp_path / path).write_text("original")
    git("add", ".")
    git("commit", "-m", "Reviewed")
    previous = git("rev-parse", "HEAD")
    changed = "caller.py" if change == "caller" else "other.py"
    (tmp_path / changed).write_text("changed")
    git("commit", "-am", "Correction")
    head = git("rev-parse", "HEAD")
    issue = tmp_path / ".cafe/issues/correction"
    issue.mkdir(parents=True)
    proof = issue / "checks.txt"
    proof.write_text("caller and implementation check passed")
    requirements = {"spec": "a" * 64, "plan": "b" * 64}
    review = issue / "review.md"
    report = f"""## Review Baseline

- Reviewed HEAD: `{previous}`

## Finding Registry

| BLK-002 | BLK | Open caller issue | 004 | Important | 98 | open | — | {previous} |
| BLK-001 | BLK | Verified public boundary | 003 | Important | 98 | closed | — | {previous} |

## Todo List

- [ ] `BLK-002` — Source: `review` — Work: Fix one problem. — Closure: Regression passes. — Evidence: Original failure.
"""
    if change != "legacy":
        rows = [{
            "id": "BLK-001", "head": previous, "paths": ["caller.py", "implementation.py"],
            "evidence_path": "checks.txt", "sha256": sha256_bytes(proof.read_bytes()),
        }]
        report += "\n## Correction Evidence\n\n```json\n" + json.dumps(rows) + "\n```\n"
    if change == "evidence":
        proof.write_text("drift")
    current_requirements = {**requirements, "spec": "c" * 64} if change == "requirements" else requirements
    projection = review_projection(
        issue=issue, source=review, text=report, current_head=head, run_git=git,
        requirements=current_requirements, previous_requirements=requirements,
    )
    assert [item["id"] for item in projection["open_todos"]] == ["BLK-002"]
    assert projection["git"]["changed_paths"] == [changed]
    assert [item["id"] for item in projection["reusable"]] == (["BLK-001"] if change == "none" else [])
    assert projection["reopen"] == ([] if change == "none" else ["BLK-001"])
