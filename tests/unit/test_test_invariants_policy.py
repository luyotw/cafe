"""Tests for test invariants policy reference document."""

from pathlib import Path

POLICY_PATH = Path(
    "src/cafe/data/skills/plan/references/test_invariants_policy.md"
)


def test_test_invariants_policy_file_exists() -> None:
    assert POLICY_PATH.is_file(), f"{POLICY_PATH} must exist"


def test_test_invariants_policy_contains_good_bad_examples_and_invariant_language() -> None:
    content = POLICY_PATH.read_text(encoding="utf-8")
    assert "Good" in content
    assert "Bad" in content
    lower = content.lower()
    assert "invariant" in lower
    assert "journey" in lower or "user journey" in lower
