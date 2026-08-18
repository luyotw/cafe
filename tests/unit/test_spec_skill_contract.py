from pathlib import Path


def test_spec_skill_requires_user_handoff_before_advance() -> None:
    project_root = Path(__file__).resolve().parents[2]

    spec = (project_root / "src" / "cafe" / "data" / "skills" / "cafe-spec" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    plan = (project_root / "src" / "cafe" / "data" / "skills" / "cafe-plan" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "next-step baton 寫入 `user`" in spec
    assert "不要直接交給 `plan`" in spec
    assert "next-step baton 寫入 `user`" in plan
    assert "不要直接交給 `develop`" in plan


def test_spec_and_plan_skills_require_script_sync_before_confirm() -> None:
    project_root = Path(__file__).resolve().parents[2]

    spec = project_root / "src" / "cafe" / "data" / "skills" / "cafe-spec"
    plan = project_root / "src" / "cafe" / "data" / "skills" / "cafe-plan"

    spec_skill = (spec / "SKILL.md").read_text(encoding="utf-8")
    plan_skill = (plan / "SKILL.md").read_text(encoding="utf-8")

    assert "scripts/sync_github.sh" in spec_skill
    assert "scripts/sync_github.sh" in plan_skill

    assert (spec / "scripts" / "sync_github.sh").exists()
    assert (plan / "scripts" / "sync_github.sh").exists()
