"""Regression tests for bounded workflow discovery instructions."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _skill_text(root: Path, name: str) -> str:
    return (root / name / "SKILL.md").read_text(encoding="utf-8")


def test_packaged_workflow_common_uses_bounded_digest() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    text = _skill_text(builtin_root, "cafe-workflow-common")

    assert "version: 1.8.2" in text
    assert "Bounded blackboard digest" in text
    assert "Do **not** read or print the whole file" in text
    assert '"from_step": "<current step name>"' not in text
    assert '"created_at": "<ISO 8601 timestamp>"' not in text
    assert "The runtime derives and persists those fields" in text
    assert "Do not skip the blackboard read" not in text
    assert "Release-check authority decision matrix" in text


def test_packaged_develop_skill_uses_repository_quality_gate_guidance() -> None:
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    text = _skill_text(builtin_root, "cafe-develop")

    assert "version: 1.10.0" in text
    assert "與變更直接相關的 targeted checks" in text
    assert "Repository-owned quality gates" in text
    assert "shared skill `cafe-workflow-common`" in text
    assert "allow、deny 與 reauthorize 分支" in text
    assert "release_authority_contract" not in text
    assert "max_read_only_commands" not in text
    assert "20 次" not in text
    assert "failing test" not in text
    assert "不得繼續探索" not in text
    assert "任兩次實質修改之間" not in text
    assert "3 次唯讀呼叫內" not in text
    assert "`Task Status` 僅使用 schema 允許的 `completed`" in text
    assert "不得寫 `done`" in text
    assert "在 handoff 前寫入非空的 development summary" in text


def test_release_authority_contract_enforces_assignment_and_stale_rerun_outcomes() -> None:
    contract_text = (
        _skill_text(
            PROJECT_ROOT / "src" / "cafe" / "data" / "skills",
            "cafe-workflow-common",
        )
        .split("### Release-check authority decision matrix", maxsplit=1)[1]
        .split("## What Not To Do", maxsplit=1)[0]
    )
    rows = [
        tuple(cell.strip() for cell in line.strip("|").split("|"))
        for line in contract_text.splitlines()
        if line.startswith("|") and not line.startswith("| ---")
    ][1:]

    decisions = {scenario: decision for scenario, *_, decision in rows}

    assert decisions == {
        "current-develop-step-assignment": "allow",
        "explicit-user-request": "allow",
        "separate-verify-step-assignment": "deny",
        "separate-verification-step-assignment": "deny",
        "risk": "deny",
        "scale": "deny",
        "precaution": "deny",
        "insurance": "deny",
        "pr-preparation": "deny",
        "review": "deny",
        "proactive-review": "deny",
        "agent-judgment": "deny",
        "same-authorized-work-scope-stale-rerun": "allow",
        "authority-withdrawn": "reauthorize",
        "material-work-scope-change": "reauthorize",
    }


def test_behaviorally_changed_skills_have_minor_version_bumps() -> None:
    """Skill metadata tracks the delivered decomposition behavior."""
    builtin_root = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"

    expected_versions = {
        "cafe-spec": "version: 1.4.0",
        "cafe-plan": "version: 1.8.1",
        "cafe-develop": "version: 1.10.0",
        "cafe-review": "version: 1.13.0",
        "cafe-pr": "version: 1.4.1",
        "cafe-workflow-common": "version: 1.8.2",
        "use-cafe-workflow": "metadata: {version: 1.42.0}",
    }
    for name, version in expected_versions.items():
        assert version in _skill_text(builtin_root, name)


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
