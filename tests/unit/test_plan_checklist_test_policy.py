"""Tests that plan execution steps reference test invariants policy."""

from cafe.skills.bridge import load_skill_reference

TEST_LIST_KEYWORDS = ("Test List", "test list", "test_invariants_policy")
INVARIANT_KEYWORDS = ("invariant", "journey", "UI copy", "CSS", "DOM")


def test_plan_detailed_stage_execution_steps_reference_test_policy() -> None:
    content = load_skill_reference("cafe-plan", "execution_steps_iteration_n.md")
    lower = content.lower()
    assert any(kw.lower() in lower for kw in TEST_LIST_KEYWORDS), (
        "plan/execution_steps_iteration_n.md must require Test List"
    )
    assert sum(1 for kw in INVARIANT_KEYWORDS if kw.lower() in lower) >= 3, (
        "plan/execution_steps_iteration_n.md must describe invariant-focused test planning"
    )
