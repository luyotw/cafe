"""Regression tests for bounded workflow discovery instructions."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _skill_text(root: Path, name: str) -> str:
    return (root / name / "SKILL.md").read_text(encoding="utf-8")


def test_packaged_workflow_common_uses_bounded_digest() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    text = _skill_text(builtin_root, "cafe-workflow-common")

    assert "version: 1.6.0" in text
    assert "Bounded blackboard digest" in text
    assert "Do **not** read or print the whole file" in text
    assert '"from_step": "<current step name>"' not in text
    assert '"created_at": "<ISO 8601 timestamp>"' not in text
    assert "The runtime derives and persists those fields" in text
    assert "Do not skip the blackboard read" not in text


def test_packaged_develop_skill_has_risk_driven_operation_guidance() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    text = _skill_text(builtin_root, "cafe-develop")

    assert "version: 1.6.0" in text
    assert "low 使用 `final-only`／`summary-only`" in text
    assert "max_read_only_commands" not in text
    assert "20 次" not in text
    assert "failing test" not in text
    assert "不得繼續探索" not in text
    assert "任兩次實質修改之間" not in text
    assert "3 次唯讀呼叫內" not in text


def test_behaviorally_changed_skills_have_minor_version_bumps() -> None:
    """Skill metadata tracks the delivered decomposition behavior."""
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"

    expected_versions = {
        "cafe-spec": "1.4.0",
        "cafe-plan": "1.6.0",
        "cafe-workflow-common": "1.6.0",
        "use-cafe-workflow": "1.18.0",
    }
    for name, version in expected_versions.items():
        assert f"version: {version}" in _skill_text(builtin_root, name)


def test_spec_and_plan_skills_pin_downstream_contract_schema() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    spec = _skill_text(builtin_root, "cafe-spec")
    plan = _skill_text(builtin_root, "cafe-plan")

    assert "`Contract-Version: 1`" in spec
    assert "`GOAL-*`, `NONGOAL-*`, `AC-*`, `INV-*`, and `TRUST-*`" in spec
    assert "`Contract-Version: 1`" in plan
    assert "Every Test List `Covers` value" in plan
    assert "must\nname an `INV-*`" in plan
    assert "do not copy source stable ID tokens" in plan
    assert "(`GOAL-*`, `NONGOAL-*`, `AC-*`, or `TRUST-*`)" in plan
    assert "Translate each requirement into the matching plan-owned" in plan


def test_packaged_develop_skill_checks_complete_production_wiring() -> None:
    reference = (
        PROJECT_ROOT
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "cafe-develop"
        / "references"
        / "basic_principles.md"
    ).read_text(encoding="utf-8")

    assert "schema/validation" in reference
    assert "effective resolver/defaults" in reference
    assert "primary、backup、retry 與 resume" in reference
    assert "public caller path" in reference
    assert "移除任一必要 forwarding 時該測試必須失敗" in reference
    assert "只直接測 helper 或手動傳值不足" in reference
