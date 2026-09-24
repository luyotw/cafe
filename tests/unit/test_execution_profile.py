"""Tests for skill-selected provider-neutral execution requirements."""

from pathlib import Path

from cafe.skills.execution_profile import (
    resolve_execution_profile,
)
from cafe.skills.loader import SkillLoader
from cafe.skills.selectors import resolve_skill_selector, skill_selector_names


def _write_skill(root: Path, name: str, profile: str = "") -> None:
    skill_dir = root / ".cafe" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    workflow = f"workflow:\n{profile}" if profile else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n{workflow}\n---\n\n# Test\n",
        encoding="utf-8",
    )


def test_iteration_selector_resolution_matches_runtime_order() -> None:
    selector = {"3": "third", "1": "first", "default": "later"}

    assert skill_selector_names(selector) == ("first", "third", "later")
    assert resolve_skill_selector(selector, 1) == "first"
    assert resolve_skill_selector(selector, 2) == "later"
    assert resolve_skill_selector(selector, 3) == "third"
    assert resolve_skill_selector({"2": "second", "10": "tenth"}, 1) == "tenth"


def test_kickoff_aggregates_all_variants_and_iteration_resolves_actual_skill(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path,
        "first",
        """  execution_profile:
    workload: content
    reasoning: standard
    risk_domains: [audience]
    fallback_strength: equivalent
""",
    )
    _write_skill(
        tmp_path,
        "later",
        """  execution_profile:
    workload: review
    reasoning: high
    risk_domains: [correctness]
    fallback_strength: equivalent_or_stronger
""",
    )
    loader = SkillLoader(project_root=tmp_path)
    selector = {"1": "first", "default": "later"}

    kickoff = resolve_execution_profile(loader, selector)
    actual = resolve_execution_profile(loader, selector, iteration=1)

    assert kickoff.skill_names == ("first", "later")
    assert kickoff.workloads == ("content", "review")
    assert kickoff.reasoning == "high"
    assert kickoff.risk_domains == ("audience", "correctness")
    assert kickoff.fallback_strength == "equivalent_or_stronger"
    assert not kickoff.uses_default
    assert actual.skill_names == ("first",)
    assert actual.reasoning == "standard"


def test_legacy_custom_skill_is_explicitly_defaulted(tmp_path: Path) -> None:
    _write_skill(tmp_path, "legacy")

    profile = resolve_execution_profile(SkillLoader(project_root=tmp_path), "legacy")

    assert profile.uses_default
    assert profile.workloads == ("general",)
    assert profile.reasoning == "standard"
    assert profile.fallback_strength == "equivalent"


def test_workflow_contributor_profile_is_aggregated_without_empty_overlay_default(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path,
        "primary",
        """  execution_profile:
    workload: implementation
    reasoning: routine
    risk_domains: [state-change]
    fallback_strength: equivalent
""",
    )
    _write_skill(tmp_path, "empty")
    _write_skill(
        tmp_path,
        "support",
        """  execution_profile:
    workload: review
    reasoning: high
    risk_domains: [integration]
    fallback_strength: equivalent_or_stronger
""",
    )

    profile = resolve_execution_profile(
        SkillLoader(project_root=tmp_path),
        "primary",
        workflow_skills=["empty", "support"],
        step_name="develop",
    )

    assert profile.skill_names == ("primary", "empty", "support")
    assert profile.workloads == ("implementation", "review")
    assert profile.reasoning == "high"
    assert profile.risk_domains == ("state-change", "integration")
    assert profile.fallback_strength == "equivalent_or_stronger"
    assert not profile.uses_default
