"""Cross-resource journeys for issue-decomposition guidance."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"


def _read(relative_path: str) -> str:
    return (SKILLS_ROOT / relative_path).read_text(encoding="utf-8")


def _journey_resources() -> str:
    return "\n".join(
        _read(path)
        for path in (
            "cafe-workflow-common/references/issue_decomposition.md",
            "cafe-spec/SKILL.md",
            "cafe-plan/SKILL.md",
            "use-cafe-workflow/references/issue_decomposition.md",
        )
    )


def _normalized(text: str) -> str:
    return " ".join(text.split())


def test_broad_product_request_split_preserves_non_overlapping_outcomes() -> None:
    """IT-001 — a split keeps a useful first outcome before coordination."""
    guidance = _normalized(_journey_resources())

    for outcome in (
        "useful, independently acceptable outcome",
        "non-overlapping follow-up outcomes",
        "scope boundary",
        "Definition of Done",
        "before external coordination",
    ):
        assert outcome in guidance


def test_ordinary_feature_keep_continues_existing_confirmation_flow() -> None:
    """IT-002 — a keep recommendation adds no new confirmation gate."""
    guidance = _normalized(_journey_resources())

    assert "Decision: `keep` or `split`" in guidance
    assert "For `keep`, continue the existing confirmation flow" in guidance
    assert "without an extra decomposition prompt" in guidance


def test_plan_led_split_preserves_scope_and_blocks_delivery() -> None:
    """IT-003 — implementation evidence can order work but cannot redefine it."""
    guidance = _normalized(_journey_resources())

    assert "repository evidence" in guidance
    assert "must not silently change confirmed product scope" in guidance
    assert "must not enter develop" in guidance


def test_fresh_driver_reconstructs_linked_project_position() -> None:
    """IT-004 — a resumed driver relies only on durable sources."""
    guidance = _normalized(_journey_resources())

    assert "reconstruct" in guidance
    assert "confirmed roadmap" in guidance
    assert "active workflow records" in guidance
    assert "required user decision" in guidance
