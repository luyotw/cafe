from pathlib import Path

from cafe.core.takeover import build_takeover_snapshot, sanitize_failure_reason


def test_takeover_is_bounded_and_does_not_preserve_session_secrets(tmp_path: Path) -> None:
    snapshot = build_takeover_snapshot(
        reason="api_key=private provider failed",
        step="custom_step",
        iteration=2,
        resolved_inputs={"source": {"mode": "packet", "path": "context.json"}},
        output_file=tmp_path / "output.md",
        checklist_file=tmp_path / "checklist.md",
        operation={"state": "running", "id": "operation-1", "session_id": "must-not-leak"},
        workspace={"head": "abc", "changed": ["src/file.py"]},
    )

    assert snapshot["reason"] == "api_key=[redacted] provider failed"
    assert snapshot["operation"] == {"state": "running", "id": "operation-1"}
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
        operation={"state": "unknown"},
    )

    assert snapshot["reason"] == sanitize_failure_reason(
        "Authorization: Bearer abc123 credential=private"
    )
    assert snapshot["partial"]["checklist"]["completed"] == 1
    assert snapshot["partial"]["checklist"]["pending"] == 1
    assert snapshot["operation"] == {"state": "unknown"}
