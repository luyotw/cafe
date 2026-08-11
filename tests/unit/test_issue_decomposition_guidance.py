"""Contract coverage for staged issue-decomposition guidance."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"


def _read(relative_path: str) -> str:
    return (SKILLS_ROOT / relative_path).read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return " ".join(text.split())


def test_stable_assessment_contract_is_shared_by_roles_templates_and_driver() -> None:
    """UT-001 — each assessment surface exposes one keep-or-split contract."""
    contract = _read("cafe-workflow-common/references/issue_decomposition.md")
    driver = _read("use-cafe-workflow/references/issue_decomposition.md")
    surfaces = [
        contract,
        driver,
        _read("cafe-spec/SKILL.md"),
        _read("cafe-plan/SKILL.md"),
        *(
            _read(f"cafe-spec/assets/templates/{name}.md")
            for name in ("default", "detailed", "simple")
        ),
        *(
            _read(f"cafe-plan/assets/templates/{name}.md")
            for name in ("default", "bug", "simple")
        ),
    ]

    fields = (
        "Decision: `keep` or `split`",
        "Rationale",
        "Current issue scope",
        "Trigger",
        "Title",
        "Goal",
        "Depends on",
        "Definition of Done",
    )
    for surface in map(_normalized, surfaces):
        for field in fields:
            assert field in surface


def test_phase_roles_assess_distinct_scope_without_external_mutation() -> None:
    """UT-002 — phase roles recommend; the driver alone coordinates mutations."""
    contract = _read("cafe-workflow-common/references/issue_decomposition.md")
    spec = _read("cafe-spec/SKILL.md")
    plan = _read("cafe-plan/SKILL.md")

    assert "independently acceptable product capabilities" in spec
    assert "implementation-scope" in plan
    assert "repository evidence" in plan
    assert "must not silently change confirmed product scope" in plan
    assert "Phase agents recommend only" in contract
    assert "never create issues, update roadmaps, change priority" in _normalized(contract)


def test_driver_requires_authority_and_narrowed_scope_before_delivery() -> None:
    """UT-003 — unsupported or unauthorized splits cannot reach develop."""
    driver = _read("use-cafe-workflow/references/issue_decomposition.md")

    normalized = _normalized(driver)
    for rule in (
        "confirmed requirement",
        "strategic documents",
        "repository evidence",
        "existing open issues",
        "vague, overlapping, or unsupported",
        "required authority",
        "current issue is narrowed",
        "must not enter develop",
    ):
        assert rule in normalized


def test_project_position_is_reconstructed_from_durable_records() -> None:
    """UT-004 — position has required fields and no second state store."""
    driver = _read("use-cafe-workflow/references/issue_decomposition.md")

    for field in (
        "project",
        "milestone",
        "current issue",
        "current phase",
        "completed count",
        "blocked issues",
        "next action",
        "required user decision",
        "strategic context",
        "confirmed roadmap",
        "issue state",
        "active workflow records",
        "Do not create duplicate project state",
    ):
        assert field in driver
