"""Bounded coverage completion notification tests."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/notify-test-result.py"
MODULE_SPEC = importlib.util.spec_from_file_location("notify_test_result", SCRIPT_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
notify_test_result = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = notify_test_result
MODULE_SPEC.loader.exec_module(notify_test_result)


class _BoundedLog:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.requested_reads: list[int] = []

    def open(self, mode: str):  # type: ignore[no-untyped-def]
        assert mode == "rb"
        owner = self

        class _TrackedBytesIO(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                owner.requested_reads.append(size)
                return super().read(size)

        return _TrackedBytesIO(self.content)


def test_read_test_summary_bounds_input_and_selected_output() -> None:
    """Unit 10: log scanning and the selected Slack summary stay bounded."""
    log = _BoundedLog(b"x" * 1_000_000 + b" 1 passed in 0.01s\n" + b"TOTAL 100 0 100%\n")

    summary, coverage = notify_test_result.read_test_summary(log)

    assert log.requested_reads
    assert all(0 <= size <= 128 * 1024 for size in log.requested_reads)
    assert len(summary) <= 1024
    assert summary.endswith("1 passed in 0.01s")
    assert coverage == "100%"


def test_main_posts_a_bounded_coverage_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration 6: the production caller cannot emit an oversized payload."""
    log_path = tmp_path / "coverage.log"
    log_path.write_text(
        f"{'x' * 1_000_000} 1 passed in 0.01s\nTOTAL 100 0 100%\n",
        encoding="utf-8",
    )
    delivered: list[dict[str, str]] = []
    monkeypatch.setattr(notify_test_result, "load_test_webhook_url", lambda: "trusted")
    monkeypatch.setattr(
        notify_test_result,
        "post_message",
        lambda _url, message: delivered.append(message),
    )

    result = notify_test_result.main(
        ["notify-test-result.py", "0", "1", "coverage.xml", str(log_path)]
    )

    assert result == 0
    assert len(delivered) == 1
    assert len(delivered[0]["text"]) <= 4096
    assert "1 passed in 0.01s" in delivered[0]["text"]
    assert "Coverage: 100%" in delivered[0]["text"]
