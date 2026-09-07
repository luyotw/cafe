"""Architecture guardrails for the mode-neutral workflow core."""

from pathlib import Path


def test_workflow_core_never_imports_workflow_execution() -> None:
    core_dir = Path(__file__).resolve().parents[2] / "src" / "cafe" / "core"
    offenders = [
        path.relative_to(core_dir)
        for path in core_dir.rglob("*.py")
        if "cafe.workflow_execution" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_only_driver_skill_resources_describe_proactive_review_consensus() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "cafe"
    protected = [root / "core", root / "phases", root / "data" / "playbooks"]
    protected.extend(
        path
        for path in (root / "data" / "skills").iterdir()
        if path.name != "use-cafe-workflow"
    )
    forbidden = ("proactive driver review", "proactive-review consensus", "decision-packet")
    offenders = []
    for base in protected:
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".md", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8").lower()
            if any(term in text for term in forbidden):
                offenders.append(path.relative_to(root))

    assert offenders == []
