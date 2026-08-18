"""Tests that builtin plan templates include a required Test List section."""

from pathlib import Path

import pytest

TEMPLATES_DIR = Path("src/cafe/data/skills/cafe-plan/assets/templates")
BUILTIN_TEMPLATES = ("default.md", "simple.md", "bug.md")


@pytest.mark.parametrize("template_name", BUILTIN_TEMPLATES)
def test_builtin_plan_template_includes_test_list_section(template_name: str) -> None:
    path = TEMPLATES_DIR / template_name
    assert path.is_file(), f"{path} must exist"
    content = path.read_text(encoding="utf-8")
    assert "## Test List" in content
    assert "Unit tests" in content
    assert "Integration tests" in content
