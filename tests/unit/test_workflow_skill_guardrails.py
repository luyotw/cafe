"""Regression tests for bounded workflow discovery instructions."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _skill_text(root: Path, name: str) -> str:
    return (root / name / "SKILL.md").read_text(encoding="utf-8")


def test_packaged_workflow_common_uses_bounded_digest() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    text = _skill_text(builtin_root, "cafe-workflow-common")

    assert "version: 1.4.0" in text
    assert "Bounded blackboard digest" in text
    assert "Do **not** read or print the whole file" in text
    assert "Do not skip the blackboard read" not in text


def test_packaged_develop_skill_has_risk_driven_operation_guidance() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    text = _skill_text(builtin_root, "cafe-develop")

    assert "version: 1.5.0" in text
    assert "low 使用 `final-only`／`summary-only`" in text
    assert "max_read_only_commands" not in text
    assert "20 次" not in text
    assert "failing test" not in text
    assert "不得繼續探索" not in text
    assert "任兩次實質修改之間" not in text
    assert "3 次唯讀呼叫內" not in text
