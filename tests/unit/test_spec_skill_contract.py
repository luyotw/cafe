from pathlib import Path


def test_spec_skills_require_user_handoff_before_advance() -> None:
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

    assert "next-step baton 寫入 `user`" in spec_first
    assert "不要直接交給 `plan`" in spec_first
    assert "next-step baton 寫入 `user`" in spec_revise
    assert "不要直接交給 `plan`" in spec_revise
    assert "next-step baton 寫入 `user`" in plan
    assert "不要直接交給 `develop`" in plan


def test_spec_and_plan_skills_require_script_sync_before_confirm() -> None:
    project_root = Path(__file__).resolve().parents[2]

    spec_first = project_root / "src" / "cafe" / "data" / "skills" / "spec_first"
    spec_revise = project_root / "src" / "cafe" / "data" / "skills" / "spec_revise"
    plan = project_root / "src" / "cafe" / "data" / "skills" / "plan"

    spec_first_skill = (spec_first / "SKILL.md").read_text(encoding="utf-8")
    spec_revise_skill = (spec_revise / "SKILL.md").read_text(encoding="utf-8")
    plan_skill = (plan / "SKILL.md").read_text(encoding="utf-8")

    assert "scripts/sync_github.sh" in spec_first_skill
    assert "scripts/sync_github.sh" in spec_revise_skill
    assert "scripts/sync_github.sh" in plan_skill

    assert (spec_first / "scripts" / "sync_github.sh").exists()
    assert (spec_revise / "scripts" / "sync_github.sh").exists()
    assert (plan / "scripts" / "sync_github.sh").exists()
