"""Contract tests for token- and wall-time-efficiency workflow guidance."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS = PROJECT_ROOT / "src/cafe/data/skills"
PLAYBOOKS = PROJECT_ROOT / "src/cafe/data/playbooks"


def test_workflow_common_bounds_search_output_and_generated_logs() -> None:
    skill = (SKILLS / "cafe-workflow-common/SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "## Bounded repository inspection" in skill
    assert "`rg -l`" in skill
    assert "roughly 200 lines and 32 KiB" in normalized
    assert "Never include `streaming.jsonl`" in normalized
    assert "Do not search `.cafe/` together with source and test trees" in normalized


def test_develop_and_review_defer_repository_wide_gates_to_hooks_and_ci() -> None:
    common = (SKILLS / "cafe-workflow-common/SKILL.md").read_text(encoding="utf-8")
    develop = (SKILLS / "cafe-develop/SKILL.md").read_text(encoding="utf-8")
    develop_steps = (SKILLS / "cafe-develop/references/execution_steps_normal.md").read_text(
        encoding="utf-8"
    )
    review = (SKILLS / "cafe-review/SKILL.md").read_text(encoding="utf-8")
    review_steps = (SKILLS / "cafe-review/references/execution_steps.md").read_text(
        encoding="utf-8"
    )
    plan = (SKILLS / "cafe-plan/SKILL.md").read_text(encoding="utf-8")
    bug_plan = (SKILLS / "cafe-plan/assets/templates/bug.md").read_text(encoding="utf-8")
    correction_steps = (SKILLS / "cafe-develop/references/execution_steps_correction.md").read_text(
        encoding="utf-8"
    )

    assert "## Repository-owned quality gates" in common
    assert "versioned Git hooks and CI configuration" in common
    assert "use `--no-verify` only with explicit user authorization" in common
    assert "Repository-owned quality gates" in develop
    assert "when a plan is supplied, map them to its Test List" in develop_steps
    assert "when a plan is supplied, map them to its Test List" in correction_steps
    assert "pre-commit hooks ran for normal commits when configured" in develop_steps
    assert "pre-commit hooks ran for normal commits when configured" in correction_steps
    assert "cafe verification run" not in develop_steps
    assert "cafe verification run" not in correction_steps
    assert "Repository-owned quality gates" in review
    assert "do not require a CAFE verification receipt" in review_steps
    assert "cafe verification check" not in review_steps
    assert "targeted checks" in plan
    assert "pre-commit、pre-push、CI、coverage 與 release gate" in plan
    assert "Run all existing tests" not in bug_plan
    assert "configured Git hooks or CI" in bug_plan

    review_contract = yaml.safe_load(review.split("---", 2)[1])["workflow"]
    assert "required_tools" not in review_contract

    for playbook_name in (
        "direct",
        "hotfix",
        "standard",
        "standard-qa",
        "tdd",
        "tdd-qa",
    ):
        playbook = yaml.safe_load((PLAYBOOKS / f"{playbook_name}.yaml").read_text())
        allowed_tools = playbook["steps"]["review"]["allowed_tools"]
        assert "Bash(git:*)" in allowed_tools
        assert not any("cafe verification" in tool for tool in allowed_tools)


def test_planless_development_uses_change_scoped_targeted_checks() -> None:
    develop_steps = (SKILLS / "cafe-develop/references/execution_steps_normal.md").read_text(
        encoding="utf-8"
    )

    assert "targeted tests for new or changed behavior" in develop_steps
    assert "when a plan is supplied, map them to its Test List" in develop_steps

    for playbook_name in ("direct", "hotfix"):
        playbook = yaml.safe_load((PLAYBOOKS / f"{playbook_name}.yaml").read_text())
        inputs = playbook["steps"]["develop"]["input_artifacts"]
        assert "plan" not in inputs


def test_review_corrections_close_root_causes_without_restarting_full_audit() -> None:
    review = (SKILLS / "cafe-review/SKILL.md").read_text(encoding="utf-8")
    review_steps = (SKILLS / "cafe-review/references/execution_steps.md").read_text(
        encoding="utf-8"
    )
    review_root = SKILLS / "cafe-review/references"
    correction = (review_root / "execution_correction.md").read_text(encoding="utf-8")

    contract = yaml.safe_load(review.split("---", 2)[1])["workflow"]["checklist"]
    assert contract["variants"][0]["when"] == {"iteration": 1}
    assert contract["variants"][1]["when"] == {"min_iteration": 2}
    assert [section.get("reference") for section in contract["variants"][0]["sections"]] == [
        "execution_preflight.md",
        "execution_risk_assessment.md",
        "execution_first_pass.md",
        "execution_acceptance_closure.md",
        "execution_exit_audit.md",
        "execution_finalize.md",
        None,
    ]
    assert [section.get("reference") for section in contract["variants"][1]["sections"]] == [
        "execution_preflight.md",
        "execution_correction.md",
        "execution_risk_assessment.md",
        "execution_acceptance_closure.md",
        "execution_exit_audit.md",
        "execution_finalize.md",
        None,
    ]
    assert "Trace each candidate defect to its root cause" in review_steps
    assert "re-verify each finding and corrected root cause" in correction
    assert "map its complete boundary" in correction
    assert "direct file byte equality alone is insufficient" in correction
    assert "without restarting an unrelated repository-wide audit" in correction
    assert "closed_reused" in correction
    assert "closed_fresh" in correction
    assert "Triggered Risk Coverage" in review_steps
    assert "At most the twelve fixed obligations" in review_steps
    assert "minimal production-path probe" in review_steps
    assert "synthetic fixtures or mocks" in review_steps
    assert "Acceptance Closure Evidence" in review_steps
    assert "derive a bounded planless baseline" in review_steps
    assert (
        "latest authoritative user feedback from PR comments or workflow inputs override"
        in review_steps
    )
    assert "request clarification instead of guessing" in review_steps
    assert "recorded planless baseline" in review_steps

    checkbox = re.compile(r"\[ \]")
    context_references = (
        "spec_read_instruction.md",
        "plan_read_instruction.md",
        "feedback_instruction.md",
        "spec_comparison_instruction.md",
    )
    context_count = sum(
        len(checkbox.findall((review_root / name).read_text(encoding="utf-8")))
        for name in context_references
    )
    expected_modes = {
        "first": (
            "execution_preflight.md",
            "execution_risk_assessment.md",
            "execution_first_pass.md",
            "execution_acceptance_closure.md",
            "execution_exit_audit.md",
            "execution_finalize.md",
        ),
        "correction": (
            "execution_preflight.md",
            "execution_correction.md",
            "execution_risk_assessment.md",
            "execution_acceptance_closure.md",
            "execution_exit_audit.md",
            "execution_finalize.md",
        ),
    }
    for names in expected_modes.values():
        phase_owned_count = sum(
            len(checkbox.findall((review_root / name).read_text(encoding="utf-8")))
            for name in names
        )
        assert phase_owned_count == 16
        assert phase_owned_count + context_count <= 20


def _arm(*, policy: str, credits: float, quality: bool = True) -> dict[str, object]:
    return {
        "policy": policy,
        "credits": credits,
        "wall_seconds": 100 if policy == "resume" else 70,
        "input_tokens": 1000 if policy == "resume" else 700,
        "cached_input_tokens": 700 if policy == "resume" else 550,
        "cache_write_input_tokens": 0,
        "output_tokens": 100 if policy == "resume" else 70,
        "reasoning_output_tokens": 30 if policy == "resume" else 20,
        "success": quality,
        "artifact_correct": quality,
        "checklist_correct": quality,
        "baton_correct": quality,
        "high_severity_findings": 0 if quality else 1,
    }


def _manifest(pair_count: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": {
            "randomized_order": True,
            "isolated_worktrees": True,
            "actual_billed_credits": True,
        },
        "pairs": [
            {
                "id": f"pair-{index}",
                "model": "gpt-5.6-terra",
                "effort": "high",
                "cli_version": "codex-cli 1.2.3",
                "repo_sha": "a" * 40,
                "correction_sha256": f"{index:064x}",
                "playbook_sha256": "b" * 64,
                "environment_sha256": "c" * 64,
                "arm_order": "fresh_first" if index % 2 == 0 else "resume_first",
                "resume": _arm(policy="resume", credits=100),
                "fresh": _arm(policy="fresh", credits=60),
            }
            for index in range(pair_count)
        ],
    }


def _run_ab_script(
    tmp_path: Path, manifest: dict[str, object], *, as_json: bool = True
) -> subprocess.CompletedProcess[str]:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    script = SKILLS / "use-cafe-workflow/scripts/analyze_correction_ab.py"
    command = [sys.executable, str(script), str(manifest_path)]
    if as_json:
        command.append("--json")
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_correction_ab_requires_ten_quality_preserving_pairs_for_claim(
    tmp_path: Path,
) -> None:
    result = _run_ab_script(tmp_path, _manifest(10))

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["pair_count"] == 10
    assert report["aggregate_available"] is True
    assert report["aggregate"]["credit_reduction"]["median"] == 0.4
    assert report["arm_order_balanced"] is True
    assert report["protocol_ready"] is True
    assert report["quality_regressions"] == []
    assert report["claim_ready"] is True


def test_correction_ab_does_not_claim_from_too_few_pairs(tmp_path: Path) -> None:
    result = _run_ab_script(tmp_path, _manifest(9))

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["aggregate_available"] is False
    assert "aggregate" not in report
    assert report["claim_ready"] is False


def test_correction_ab_does_not_claim_unbalanced_arm_order(tmp_path: Path) -> None:
    manifest = _manifest(10)
    for pair in manifest["pairs"]:  # type: ignore[union-attr]
        pair["arm_order"] = "fresh_first"

    result = _run_ab_script(tmp_path, manifest)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["aggregate_available"] is False
    assert "aggregate" not in report
    assert report["arm_order_balanced"] is False
    assert report["protocol_ready"] is False
    assert report["claim_ready"] is False


def test_correction_ab_hides_aggregate_for_quality_regression(tmp_path: Path) -> None:
    manifest = _manifest(10)
    manifest["pairs"][0]["fresh"] = _arm(policy="fresh", credits=60, quality=False)  # type: ignore[index]

    result = _run_ab_script(tmp_path, manifest)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["aggregate_available"] is False
    assert "aggregate" not in report
    assert report["claim_ready"] is False


@pytest.mark.parametrize(
    "failure",
    ("insufficient_pairs", "unbalanced_arm_order", "quality_regression"),
)
def test_correction_ab_default_report_hides_aggregate_until_evidence_is_valid(
    tmp_path: Path, failure: str
) -> None:
    manifest = _manifest(9 if failure == "insufficient_pairs" else 10)
    if failure == "unbalanced_arm_order":
        for pair in manifest["pairs"]:  # type: ignore[union-attr]
            pair["arm_order"] = "fresh_first"
    elif failure == "quality_regression":
        manifest["pairs"][0]["fresh"] = _arm(  # type: ignore[index]
            policy="fresh", credits=60, quality=False
        )

    result = _run_ab_script(tmp_path, manifest, as_json=False)

    assert result.returncode == 0, result.stderr
    assert "Aggregate statistics are unavailable" in result.stdout
    assert "| Metric | Median |" not in result.stdout


def test_correction_ab_rejects_missing_billed_credits(tmp_path: Path) -> None:
    manifest = _manifest(1)
    manifest["pairs"][0]["fresh"].pop("credits")  # type: ignore[index, union-attr]

    result = _run_ab_script(tmp_path, manifest)

    assert result.returncode == 2
    assert "fresh.credits must be a number" in result.stderr


def test_correction_ab_rejects_non_finite_telemetry(tmp_path: Path) -> None:
    manifest = _manifest(10)
    manifest["pairs"][0]["fresh"]["credits"] = float("nan")  # type: ignore[index]

    result = _run_ab_script(tmp_path, manifest)

    assert result.returncode == 2
    assert "fresh.credits must be finite" in result.stderr


def test_correction_ab_rejects_non_git_repo_sha(tmp_path: Path) -> None:
    manifest = _manifest(10)
    manifest["pairs"][0]["repo_sha"] = "not-a-git-object-id"  # type: ignore[index]

    result = _run_ab_script(tmp_path, manifest)

    assert result.returncode == 2
    assert "repo_sha must be a 40- or 64-character lowercase Git OID" in result.stderr


def test_driver_skill_requires_controlled_correction_ab_before_claim() -> None:
    skill = (SKILLS / "use-cafe-workflow/SKILL.md").read_text(encoding="utf-8")
    reference = (SKILLS / "use-cafe-workflow/references/correction_ab_experiment.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(reference.split())

    assert "references/correction_ab_experiment.md" in skill
    assert "scripts/analyze_correction_ab.py" in reference
    assert "Do not claim the 30% target until `claim_ready: yes`" in normalized
    assert "actual billed Codex credits" in reference
    assert "Do not substitute a rate-card estimate" in reference
    assert "Do not run the second arm on files mutated by the first" in reference
    assert "environment_sha256" in reference
    assert "Balance the two arm orders" in reference
