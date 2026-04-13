from pathlib import Path


def test_spec_skills_require_ready_for_review_before_confirm() -> None:
    project_root = Path(__file__).resolve().parents[2]

    spec_first = (project_root / "src" / "cafe" / "data" / "skills" / "spec_first" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    spec_revise = (project_root / "src" / "cafe" / "data" / "skills" / "spec_revise" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    plan = (project_root / "src" / "cafe" / "data" / "skills" / "plan" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "CAFE_READY_FOR_REVIEW" in spec_first
    assert "不要直接回 `CAFE_CONFIRMED`" in spec_first
    assert "CAFE_READY_FOR_REVIEW" in spec_revise
    assert "不要直接回 `CAFE_CONFIRMED`" in spec_revise
    assert "CAFE_READY_FOR_REVIEW" in plan
    assert "不要直接回 `CAFE_CONFIRMED`" in plan
