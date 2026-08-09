from pathlib import Path

from cafe.core.takeover import build_takeover_snapshot


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
