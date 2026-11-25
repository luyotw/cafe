"""Regression tests for bounded workflow discovery instructions."""

from pathlib import Path

from packaging.version import Version

from cafe.skills.loader import read_skill_frontmatter

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _skill_text(root: Path, name: str) -> str:
    return (root / name / "SKILL.md").read_text(encoding="utf-8")


def test_packaged_workflow_common_uses_bounded_digest() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    text = _skill_text(builtin_root, "cafe-workflow-common")

    assert "Bounded blackboard digest" in text
    assert "Do **not** read or print the whole file" in text
    assert '"from_step": "<current step name>"' not in text
    assert '"created_at": "<ISO 8601 timestamp>"' not in text
    assert "The runtime derives and persists those fields" in text
    assert "Do not skip the blackboard read" not in text
    assert "Workflow phase agents must never execute it" in text
    assert "in-workflow request" in text
    assert "outside the active workflow" in text
    assert "and Driver" not in text
    assert "A final response ends the current CLI invocation" in text
    assert "not as a neutral progress checkpoint" in text


def test_packaged_develop_skill_uses_repository_quality_gate_guidance() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    text = _skill_text(builtin_root, "cafe-develop")

    assert "Run bounded targeted checks for the changed behavior" in text
    assert "Repository-owned quality gates" in text
    assert "max_read_only_commands" not in text
    assert "20 次" not in text
    assert "failing test" not in text
    assert "不得繼續探索" not in text
    assert "任兩次實質修改之間" not in text
    assert "3 次唯讀呼叫內" not in text
    assert "Accepted plan and feedback are immutable" in text
    assert "never edit their checkboxes or Task Status" in text
    assert "For other handoffs, write a non-empty development summary" in text
    assert "A bounded unit, commit or targeted check is progress" in text
    assert "A progress-only reply or request for another `continue`/`resume` is not a valid stopping point" in text
    assert "A checked box or existing commit alone is not completion" in text


def test_behaviorally_changed_skills_have_minor_version_bumps() -> None:
    """Skill metadata tracks the delivered decomposition behavior."""
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"

    minimum_versions = {
        "cafe-spec": "1.7.0",
        "cafe-plan": "1.11.0",
        "cafe-develop": "1.12.0",
        "cafe-review": "1.15.0",
        "cafe-pr": "1.9.0",
        "cafe-workflow-common": "1.9.0",
        "use-cafe-workflow": "1.74.0",
    }
    for name, version in minimum_versions.items():
        frontmatter = read_skill_frontmatter(builtin_root / name / "SKILL.md")
        actual = frontmatter.get("version") or frontmatter.get("metadata", {}).get("version")
        assert Version(str(actual)) >= Version(version), name


def test_spec_and_plan_skills_describe_runtime_owned_context_packets() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    spec = _skill_text(builtin_root, "cafe-spec")
    plan = _skill_text(builtin_root, "cafe-plan")

    for text in (spec, plan):
        assert "runtime" in text
        assert "Downstream" in text and "Contract" in text
        assert "packet-specific IDs" in text
        assert "Contract-Version: 1" not in text


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


def test_develop_and_review_check_long_running_resource_amplification() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    expected = "確認 long-running script 不會造成不可接受的系統負荷或資源放大"

    for skill_name in ("cafe-develop", "cafe-review"):
        reference = (builtin_root / skill_name / "references" / "basic_principles.md").read_text(
            encoding="utf-8"
        )
        assert expected in reference
