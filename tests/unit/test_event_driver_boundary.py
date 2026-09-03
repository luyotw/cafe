"""Ensure the event-driven contract stays owned by its workflow skill."""

from __future__ import annotations

from pathlib import Path


def test_cafe_core_has_no_driver_mode_implementation() -> None:
    source_root = Path(__file__).parents[2] / "src" / "cafe"
    allowed_root = source_root / "data" / "skills" / "use-cafe-workflow"
    forbidden = ("DriverPolicy", "driver_policy", "delegated", "driver_state")
    offenders: list[str] = []

    for path in source_root.rglob("*.py"):
        if path.is_relative_to(allowed_root):
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(source_root)))

    assert offenders == []
