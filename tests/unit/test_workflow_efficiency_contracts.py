"""Contract tests for token- and wall-time-efficiency workflow guidance."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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


def test_develop_and_review_share_machine_checked_full_test_receipt() -> None:
    common = (SKILLS / "cafe-workflow-common/SKILL.md").read_text(encoding="utf-8")
    develop = (SKILLS / "cafe-develop/SKILL.md").read_text(encoding="utf-8")
    develop_steps = (
        SKILLS / "cafe-develop/references/execution_steps_normal.md"
    ).read_text(encoding="utf-8")
    review = (SKILLS / "cafe-review/SKILL.md").read_text(encoding="utf-8")
    review_steps = (SKILLS / "cafe-review/references/execution_steps.md").read_text(
        encoding="utf-8"
    )
    receipt_instruction = (
        SKILLS / "cafe-review/references/verification_receipt_instruction.md"
    ).read_text(encoding="utf-8")

    assert "## Develop-to-review verification receipts" in common
    assert "Develop-to-review verification receipts" in develop
    assert "cafe verification run --output-file {output_file}" in develop_steps
    assert "Develop-to-review verification receipts" in review
    assert "{verification_receipt_instruction}" in review_steps
    assert "cafe verification check --output-file {develop_file}" in receipt_instruction
    assert "recorded command is the repository-defined full suite" in receipt_instruction
    assert "do not rerun the same full suite or coverage command" in receipt_instruction

    review_contract = yaml.safe_load(review.split("---", 2)[1])["workflow"]
    assert review_contract["required_tools"] == [
        "Bash(cafe verification check:*)"
    ]

    for playbook_name in ("default", "hotfix", "tdd"):
        playbook = yaml.safe_load((PLAYBOOKS / f"{playbook_name}.yaml").read_text())
        assert "Bash(cafe verification check:*)" in playbook["steps"]["review"][
            "allowed_tools"
        ]
        assert "Bash(cafe verification focus:*)" in playbook["steps"]["review"][
            "allowed_tools"
        ]


def test_review_corrections_close_root_causes_without_restarting_full_audit() -> None:
    review = (SKILLS / "cafe-review/SKILL.md").read_text(encoding="utf-8")
    review_steps = (SKILLS / "cafe-review/references/execution_steps.md").read_text(
        encoding="utf-8"
    )
    correction = (
        SKILLS / "cafe-review/references/correction_review_strategy.md"
    ).read_text(encoding="utf-8")

    contract = yaml.safe_load(review.split("---", 2)[1])["workflow"]["checklist"]
    assert contract["variants"][0]["when"] == {"iteration": 1}
    assert contract["variants"][1]["when"] == {"min_iteration": 2}
    assert contract["variants"][1]["sections"][0] == {
        "reference": "correction_review_strategy.md"
    }
    assert "Trace each candidate defect to its root cause" in review_steps
    assert "re-verify every prior finding item by item" in correction
    assert "directly related equivalence classes in one pass" in correction
    assert "do not drip-feed sibling cases" in correction
    assert "do not restart an unrelated repository-wide audit" in correction
    assert "one bounded closure sweep" in correction


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


def _run_ab_script(tmp_path: Path, manifest: dict[str, object]) -> subprocess.CompletedProcess[str]:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    script = SKILLS / "use-cafe-workflow/scripts/analyze_correction_ab.py"
    return subprocess.run(
        [sys.executable, str(script), str(manifest_path), "--json"],
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
    assert report["aggregate"]["credit_reduction"]["median"] == 0.4
    assert report["arm_order_balanced"] is True
    assert report["protocol_ready"] is True
    assert report["quality_regressions"] == []
    assert report["claim_ready"] is True


def test_correction_ab_does_not_claim_from_too_few_pairs(tmp_path: Path) -> None:
    result = _run_ab_script(tmp_path, _manifest(9))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["claim_ready"] is False


def test_correction_ab_does_not_claim_unbalanced_arm_order(tmp_path: Path) -> None:
    manifest = _manifest(10)
    for pair in manifest["pairs"]:  # type: ignore[union-attr]
        pair["arm_order"] = "fresh_first"

    result = _run_ab_script(tmp_path, manifest)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["arm_order_balanced"] is False
    assert report["protocol_ready"] is False
    assert report["claim_ready"] is False


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
    reference = (
        SKILLS / "use-cafe-workflow/references/correction_ab_experiment.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(reference.split())

    assert "references/correction_ab_experiment.md" in skill
    assert "scripts/analyze_correction_ab.py" in reference
    assert "Do not claim the 30% target until `claim_ready: yes`" in normalized
    assert "actual billed Codex credits" in reference
    assert "Do not substitute a rate-card estimate" in reference
    assert "Do not run the second arm on files mutated by the first" in reference
    assert "environment_sha256" in reference
    assert "Balance the two arm orders" in reference
