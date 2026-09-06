"""Ensure the event-driven contract stays owned by its workflow skill."""

from __future__ import annotations

from pathlib import Path
import re


def test_cafe_core_has_no_driver_mode_implementation() -> None:
    source_root = Path(__file__).parents[2] / "src" / "cafe"
    allowed_roots = (
        source_root / "data" / "skills" / "use-cafe-workflow",
        source_root / "driver",
    )
    forbidden = ("DriverPolicy", "driver_policy", "delegated", "driver_state")
    offenders: list[str] = []

    for path in source_root.rglob("*.py"):
        if any(path.is_relative_to(root) for root in allowed_roots):
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(source_root)))

    assert offenders == []


def test_event_driver_status_projection_stays_in_the_skill_boundary() -> None:
    source_root = Path(__file__).parents[2] / "src" / "cafe"
    callback = (
        source_root
        / "data"
        / "skills"
        / "use-cafe-workflow"
        / "scripts"
        / "workflow_event_callback.py"
    )

    assert "def read_status(" in callback.read_text(encoding="utf-8")
    for path in (source_root / "core").rglob("*.py"):
        assert "read_status" not in path.read_text(encoding="utf-8")


def test_driver_contract_application_has_one_production_skill_boundary() -> None:
    """Test List 6: generic runtime and phases stay independent of #474 authority."""
    source_root = Path(__file__).parents[2] / "src" / "cafe"
    driver_root = source_root / "driver"
    skill_root = source_root / "data" / "skills" / "use-cafe-workflow"
    importers: list[Path] = []

    for path in source_root.rglob("*.py"):
        if path.is_relative_to(driver_root):
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(?:from|import)\s+cafe\.driver\b", source, flags=re.MULTILINE):
            importers.append(path)
        if not path.is_relative_to(skill_root):
            assert "driver/contract.json" not in source

    assert importers
    assert all(path.is_relative_to(skill_root) for path in importers)
