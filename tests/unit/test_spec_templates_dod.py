"""Tests for DoD format in spec templates."""

from pathlib import Path


class TestSpecTemplatesDoDFormat:
    """Tests to verify DoD format is present in spec templates."""

    def _get_template_path(self, template_name: str) -> Path:
        """Get the path to a spec template file."""
        return Path("src/cafe/data/templates/spec") / template_name

    def test_default_template_acceptance_criteria_contains_dod_format(self) -> None:
        """Test that default.md template's Acceptance Criteria section contains DoD format."""
        template_path = self._get_template_path("default.md")
        assert template_path.exists(), "default.md template should exist"
        content = template_path.read_text()
        assert "**DoD:**" in content, "default.md should contain DoD format in Acceptance Criteria"

    def test_default_template_dod_items_in_acceptance_criteria(self) -> None:
        """Test that DoD items appear within the Acceptance Criteria section in default.md."""
        template_path = self._get_template_path("default.md")
        content = template_path.read_text()
        # Verify DoD items are associated with Acceptance Criteria
        assert "Acceptance Criteria" in content
        ac_section_start = content.index("Acceptance Criteria")
        dod_index = content.index("**DoD:**")
        assert dod_index > ac_section_start, "DoD format should appear within Acceptance Criteria section"

    def test_simple_template_acceptance_criteria_contains_dod_format(self) -> None:
        """Test that simple.md template's Acceptance Criteria section contains DoD format."""
        template_path = self._get_template_path("simple.md")
        assert template_path.exists(), "simple.md template should exist"
        content = template_path.read_text()
        assert "**DoD:**" in content, "simple.md should contain DoD format in Acceptance Criteria"

    def test_simple_template_dod_items_in_acceptance_criteria(self) -> None:
        """Test that DoD items appear within the Acceptance Criteria section in simple.md."""
        template_path = self._get_template_path("simple.md")
        content = template_path.read_text()
        assert "Acceptance Criteria" in content
        ac_section_start = content.index("Acceptance Criteria")
        dod_index = content.index("**DoD:**")
        assert dod_index > ac_section_start, "DoD format should appear within Acceptance Criteria section"

    def test_detailed_template_acceptance_criteria_contains_dod_format(self) -> None:
        """Test that detailed.md template's Acceptance Criteria section contains DoD format."""
        template_path = self._get_template_path("detailed.md")
        assert template_path.exists(), "detailed.md template should exist"
        content = template_path.read_text()
        assert "**DoD:**" in content, "detailed.md should contain DoD format in Acceptance Criteria"

    def test_detailed_template_dod_items_in_acceptance_criteria(self) -> None:
        """Test that DoD items appear within the Acceptance Criteria section in detailed.md."""
        template_path = self._get_template_path("detailed.md")
        content = template_path.read_text()
        assert "Acceptance Criteria" in content
        ac_section_start = content.index("Acceptance Criteria")
        dod_index = content.index("**DoD:**")
        assert dod_index > ac_section_start, "DoD format should appear within Acceptance Criteria section"
