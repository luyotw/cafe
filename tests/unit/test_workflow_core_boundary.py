"""Architecture guardrails for the mode-neutral workflow core."""

from pathlib import Path


def test_workflow_core_never_imports_outer_orchestration() -> None:
    core_dir = Path(__file__).resolve().parents[2] / "src" / "cafe" / "core"
    offenders = [
        path.relative_to(core_dir)
        for path in core_dir.rglob("*.py")
        if "cafe.orchestration" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
