"""Selected and empty proactive-review contract journeys."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow"


def _module():
    spec = importlib.util.spec_from_file_location(
        "proactive_review_integration", SKILL_ROOT / "scripts" / "proactive_review.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy(*, selected: str | None) -> dict[str, object]:
    phases = []
    for phase in ("spec", "plan", "develop", "review", "pr"):
        row: dict[str, object] = {
            "phase": phase,
            "selected": phase == selected,
            "rationale": "The driver assessed independent coverage, risk, and cost.",
            "factors": {
                name: "assessed"
                for name in (
                    "ambiguity",
                    "novelty",
                    "blast_radius",
                    "protected_risk",
                    "durable_contract",
                    "downstream_review",
                    "late_correction",
                    "cost",
                )
            },
        }
        if phase == selected:
            row.update(
                {
                    "reviewer": {"cli": "codex", "model": "gpt-5.6-sol"},
                    "ordering": "non_gating",
                    "initial_review_cost": {
                        "tokens": {"estimate": "2k"},
                        "latency": {"estimate": "1 minute"},
                        "assumptions": "one complete output",
                        "delay_impact": "driver acceptance only",
                    },
                    "rereview_cost": {"foreseeable": False, "reason": "no grounded estimate"},
                }
            )
        phases.append(row)
    return {"playbook_id": "standard", "phases": phases}


def _confirmation(issue_name: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "issue_name": issue_name,
        "playbook_id": "standard",
        "confirmed_by": "user",
        "confirmed_at": "2026-09-04T12:00:00+00:00",
    }


@pytest.mark.parametrize("selected", ["develop", None])
def test_confirmed_initial_policy_activates_only_after_issue_preparation(
    tmp_path: Path, selected: str | None
) -> None:
    """I1–I2 — both selected and empty plans persist only the active envelope."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "journey"
    with pytest.raises(ValueError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=PROJECT_ROOT,
            policy=_policy(selected=selected),
            confirmation=_confirmation("journey"),
        )
    assert not issue_dir.exists()

    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    contract_path = module.activate_contract(
        issue_dir=issue_dir,
        project_root=PROJECT_ROOT,
        policy=_policy(selected=selected),
        confirmation=_confirmation("journey"),
    )

    assert contract_path.is_file()
    assert not (issue_dir / "driver" / "proactive_review" / "candidate.yaml").exists()
    assert not (issue_dir / "driver" / "proactive_review" / "history").exists()


def test_reconfirmed_replacement_leaves_prior_contract_untouched_until_atomic_success(
    tmp_path: Path,
) -> None:
    """I3 — a complete digest-bound reconfirmation is the only replacement route."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "replacement"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    first = module.activate_contract(
        issue_dir=issue_dir,
        project_root=PROJECT_ROOT,
        policy=_policy(selected="develop"),
        confirmation=_confirmation("replacement"),
    )
    original = first.read_bytes()
    with pytest.raises(ValueError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=PROJECT_ROOT,
            policy=_policy(selected="review"),
            confirmation=_confirmation("replacement"),
            expected_active_digest="wrong",
        )
    assert first.read_bytes() == original
