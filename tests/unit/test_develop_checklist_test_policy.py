"""Tests that develop execution steps reference test invariants policy."""

from cafe.skills.bridge import load_skill_reference

TEST_LIST_KEYWORDS = ("Test List", "test list", "test_invariants_policy")
BRITTLE_KEYWORDS = (
    "UI copy",
    "CSS",
    "DOM",
    "internal state",
    "brittle",
    "invariant",
)


def _assert_develop_steps_include_test_policy(reference_name: str) -> None:
    content = load_skill_reference("cafe-develop", reference_name)
    lower = content.lower()
    assert any(kw.lower() in lower for kw in TEST_LIST_KEYWORDS), (
        f"develop/{reference_name} must mention plan Test List"
    )
    assert sum(1 for kw in BRITTLE_KEYWORDS if kw.lower() in lower) >= 2, (
        f"develop/{reference_name} must warn about brittle test bindings"
    )


def test_develop_normal_execution_steps_reference_test_policy() -> None:
    _assert_develop_steps_include_test_policy("execution_steps_normal.md")


def test_develop_correction_execution_steps_reference_test_policy() -> None:
    _assert_develop_steps_include_test_policy("execution_steps_correction.md")
