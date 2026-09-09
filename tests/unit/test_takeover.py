from pathlib import Path

import pytest

from cafe.core import takeover as takeover_module
from cafe.core.packet_io import compact_json
from cafe.core.takeover import (
    TAKEOVER_FILE_INSPECTION_MAX_BYTES,
    build_takeover_snapshot,
    sanitize_failure_reason,
)


def test_takeover_is_bounded_and_does_not_preserve_session_secrets(tmp_path: Path) -> None:
    snapshot = build_takeover_snapshot(
        reason="api_key=private provider failed",
        step="custom_step",
        iteration=2,
        resolved_inputs={"source": {"mode": "packet", "path": "context.json"}},
        output_file=tmp_path / "output.md",
        checklist_file=tmp_path / "checklist.md",
        workspace={"head": "abc", "changed": ["src/file.py"]},
    )

    assert snapshot["reason"] == "api_key=[redacted] provider failed"
    assert "session" not in str(snapshot)


def test_takeover_reuses_the_standard_diagnostic_redaction_and_reports_progress(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output.md"
    checklist = tmp_path / "checklist.md"
    output.write_text("partial", encoding="utf-8")
    checklist.write_text("[x] done\n[ ] pending\n", encoding="utf-8")

    snapshot = build_takeover_snapshot(
        reason="Authorization: Bearer abc123 credential=private",
        step="custom_step",
        iteration=2,
        resolved_inputs={},
        output_file=output,
        checklist_file=checklist,
    )

    assert snapshot["reason"] == sanitize_failure_reason(
        "Authorization: Bearer abc123 credential=private"
    )
    assert snapshot["partial"]["checklist"]["completed"] == 1
    assert snapshot["partial"]["checklist"]["pending"] == 1


def test_takeover_snapshot_accepts_limit_and_rejects_limit_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = {
        "reason": "provider unavailable",
        "step": "develop",
        "iteration": 2,
        "resolved_inputs": {"source": {"mode": "full", "path": "source.md"}},
        "output_file": tmp_path / "output.md",
        "checklist_file": tmp_path / "checklist.md",
    }
    snapshot = build_takeover_snapshot(**arguments)
    exact_size = len(compact_json(snapshot).encode("utf-8"))

    monkeypatch.setattr(takeover_module, "TAKEOVER_SNAPSHOT_MAX_BYTES", exact_size)
    assert build_takeover_snapshot(**arguments) == snapshot

    monkeypatch.setattr(takeover_module, "TAKEOVER_SNAPSHOT_MAX_BYTES", exact_size - 1)
    with pytest.raises(ValueError, match="Takeover snapshot exceeds .*byte limit"):
        build_takeover_snapshot(**arguments)


def test_takeover_omits_content_inspection_for_large_partial_files(tmp_path: Path) -> None:
    output = tmp_path / "output.md"
    checklist = tmp_path / "checklist.md"
    oversized = TAKEOVER_FILE_INSPECTION_MAX_BYTES + 1
    output.write_bytes(b"x" * oversized)
    checklist.write_bytes(b"[x] done\n" + b"x" * oversized)

    snapshot = build_takeover_snapshot(
        reason="provider unavailable",
        step="develop",
        iteration=2,
        resolved_inputs={},
        output_file=output,
        checklist_file=checklist,
    )

    assert snapshot["partial"]["output"] == {
        "path": output.as_posix(),
        "state": "file",
        "bytes": oversized,
        "content_inspection": "omitted_size_limit",
    }
    checklist_metadata = snapshot["partial"]["checklist"]
    assert checklist_metadata["content_inspection"] == "omitted_size_limit"
    assert "completed" not in checklist_metadata
