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


def _assert_develop_steps_include_test_policy(*reference_names: str) -> None:
    content = "\n".join(
        load_skill_reference("cafe-develop", reference_name)
        for reference_name in reference_names
    )
    lower = content.lower()
    assert any(kw.lower() in lower for kw in TEST_LIST_KEYWORDS), (
        f"develop/{', '.join(reference_names)} must mention plan Test List"
    )
    assert sum(1 for kw in BRITTLE_KEYWORDS if kw.lower() in lower) >= 2, (
        f"develop/{', '.join(reference_names)} must warn about brittle test bindings"
    )


def test_develop_normal_execution_steps_reference_test_policy() -> None:
    _assert_develop_steps_include_test_policy(
        "execution_steps_normal.md", "normal_plan_verification.md"
    )


def test_develop_correction_execution_steps_reference_test_policy() -> None:
    _assert_develop_steps_include_test_policy(
        "execution_steps_correction.md", "correction_plan_test_list.md"
    )


def test_develop_keeps_plan_task_contract_status_in_sync() -> None:
    context = load_skill_reference("cafe-develop", "normal_plan_context.md")
    verification = load_skill_reference(
        "cafe-develop", "normal_plan_verification.md"
    )

    assert "Downstream Contract" in context
    assert "Task Status" in context
    assert "pending" in context
    assert "completed" in context
    assert "Downstream Contract" in verification
    assert "Task Status" in verification
    assert "agree" in verification
