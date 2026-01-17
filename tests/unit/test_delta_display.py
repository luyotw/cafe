"""Unit tests for delta display formatter."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

from cafe.services.delta_display import DeltaDisplay


class TestGenerateInlineDiff:
    """Test cases for generate_inline_diff() method."""

    def test_generate_diff_with_changes(self):
        """Test generating diff with actual changes."""
        display = DeltaDisplay()
        old_content = "Line 1\nLine 2\nLine 3\n"
        new_content = "Line 1\nLine 2 modified\nLine 3\n"

        diff_data = display.generate_inline_diff(old_content, new_content)

        assert len(diff_data) > 0
        # Should have equal, delete, and insert operations
        operations = [op for op, _, _ in diff_data]
        assert "equal" in operations
        assert "delete" in operations
        assert "insert" in operations

    def test_generate_diff_no_changes(self):
        """Test generating diff with identical content."""
        display = DeltaDisplay()
        old_content = "Line 1\nLine 2\nLine 3\n"
        new_content = "Line 1\nLine 2\nLine 3\n"

        diff_data = display.generate_inline_diff(old_content, new_content)

        # All operations should be 'equal'
        operations = [op for op, _, _ in diff_data]
        assert all(op == "equal" for op in operations)

    def test_generate_diff_empty_files(self):
        """Test generating diff with empty files."""
        display = DeltaDisplay()
        old_content = ""
        new_content = ""

        diff_data = display.generate_inline_diff(old_content, new_content)

        assert len(diff_data) == 0

    def test_generate_diff_additions_only(self):
        """Test generating diff with only additions."""
        display = DeltaDisplay()
        old_content = "Line 1\n"
        new_content = "Line 1\nLine 2\nLine 3\n"

        diff_data = display.generate_inline_diff(old_content, new_content)

        operations = [op for op, _, _ in diff_data]
        assert "equal" in operations
        assert "insert" in operations
        assert "delete" not in operations

    def test_generate_diff_deletions_only(self):
        """Test generating diff with only deletions."""
        display = DeltaDisplay()
        old_content = "Line 1\nLine 2\nLine 3\n"
        new_content = "Line 1\n"

        diff_data = display.generate_inline_diff(old_content, new_content)

        operations = [op for op, _, _ in diff_data]
        assert "equal" in operations
        assert "delete" in operations
        assert "insert" not in operations

    def test_generate_diff_replacements(self):
        """Test generating diff with line replacements."""
        display = DeltaDisplay()
        old_content = "Line 1\nOld line\nLine 3\n"
        new_content = "Line 1\nNew line\nLine 3\n"

        diff_data = display.generate_inline_diff(old_content, new_content)

        operations = [op for op, _, _ in diff_data]
        # Replace operations are shown as delete + insert
        assert "delete" in operations
        assert "insert" in operations


class TestRenderInlineDiff:
    """Test cases for render_inline_diff() method."""

    @patch("cafe.services.delta_display.RICH_AVAILABLE", True)
    def test_render_diff_with_changes(self):
        """Test rendering diff with changes."""
        display = DeltaDisplay()
        diff_data = [
            ("equal", "Line 1\n", "Line 1\n"),
            ("delete", "Old line\n", None),
            ("insert", None, "New line\n"),
        ]

        mock_console = Mock()
        display.render_inline_diff(diff_data, mock_console)

        # Should print header, content, and footer
        assert mock_console.print.call_count >= 3

    @patch("cafe.services.delta_display.RICH_AVAILABLE", True)
    def test_render_diff_no_changes(self):
        """Test rendering diff with no changes."""
        display = DeltaDisplay()
        diff_data = [
            ("equal", "Line 1\n", "Line 1\n"),
            ("equal", "Line 2\n", "Line 2\n"),
        ]

        mock_console = Mock()
        display.render_inline_diff(diff_data, mock_console)

        # Should print "No changes" message
        calls = [str(call) for call in mock_console.print.call_args_list]
        assert any("No changes" in str(call) for call in calls)

    @patch("cafe.services.delta_display.RICH_AVAILABLE", False)
    def test_render_diff_rich_not_available(self):
        """Test rendering when Rich is not available."""
        display = DeltaDisplay()
        diff_data = [("equal", "Line 1\n", "Line 1\n")]

        mock_console = Mock()
        display.render_inline_diff(diff_data, mock_console)

        # Should not print anything when Rich is unavailable
        assert mock_console.print.call_count == 0

    @patch("cafe.services.delta_display.RICH_AVAILABLE", True)
    def test_render_diff_truncation(self):
        """Test rendering diff with truncation for large diffs."""
        display = DeltaDisplay()
        # Create a very large diff (more than 1000 lines)
        diff_data = [("insert", None, f"Line {i}\n") for i in range(1500)]

        mock_console = Mock()
        display.render_inline_diff(diff_data, mock_console)

        # Should print truncation warning
        calls = [str(call) for call in mock_console.print.call_args_list]
        assert any("truncated" in str(call).lower() for call in calls)


class TestDisplayDelta:
    """Test cases for display_delta() method."""

    @patch("cafe.services.delta_display.RICH_AVAILABLE", True)
    def test_display_delta_normal_case(self, tmp_path):
        """Test displaying delta with normal files."""
        display = DeltaDisplay()

        # Create test files
        prev_file = tmp_path / "previous.md"
        curr_file = tmp_path / "current.md"
        prev_file.write_text("Line 1\nLine 2\n", encoding="utf-8")
        curr_file.write_text("Line 1\nLine 2 modified\n", encoding="utf-8")

        mock_console = Mock()
        display.display_delta(curr_file, prev_file, mock_console)

        # Should call print to display the diff
        assert mock_console.print.call_count > 0

    @patch("cafe.services.delta_display.RICH_AVAILABLE", True)
    def test_display_delta_missing_previous_file(self, tmp_path):
        """Test displaying delta when previous file doesn't exist."""
        display = DeltaDisplay()

        curr_file = tmp_path / "current.md"
        prev_file = tmp_path / "previous.md"  # Not created
        curr_file.write_text("Line 1\n", encoding="utf-8")

        mock_console = Mock()
        display.display_delta(curr_file, prev_file, mock_console)

        # Should print "First iteration" message
        calls = [str(call) for call in mock_console.print.call_args_list]
        assert any("first iteration" in str(call).lower() for call in calls)

    @patch("cafe.services.delta_display.RICH_AVAILABLE", True)
    def test_display_delta_missing_current_file(self, tmp_path):
        """Test displaying delta when current file doesn't exist."""
        display = DeltaDisplay()

        prev_file = tmp_path / "previous.md"
        curr_file = tmp_path / "current.md"  # Not created
        prev_file.write_text("Line 1\n", encoding="utf-8")

        mock_console = Mock()
        display.display_delta(curr_file, prev_file, mock_console)

        # Should print warning message
        calls = [str(call) for call in mock_console.print.call_args_list]
        assert any("warning" in str(call).lower() for call in calls)

    @patch("cafe.services.delta_display.RICH_AVAILABLE", True)
    def test_display_delta_file_read_error(self, tmp_path):
        """Test displaying delta when file read fails."""
        display = DeltaDisplay()

        prev_file = tmp_path / "previous.md"
        curr_file = tmp_path / "current.md"
        prev_file.write_text("Line 1\n", encoding="utf-8")
        curr_file.write_text("Line 1\n", encoding="utf-8")

        mock_console = Mock()

        # Mock read_text to raise an exception
        with patch.object(Path, "read_text", side_effect=IOError("Read error")):
            display.display_delta(curr_file, prev_file, mock_console)

            # Should print warning message
            calls = [str(call) for call in mock_console.print.call_args_list]
            assert any("warning" in str(call).lower() or "could not read" in str(call).lower() for call in calls)

    @patch("cafe.services.delta_display.RICH_AVAILABLE", False)
    def test_display_delta_rich_not_available(self, tmp_path):
        """Test displaying delta when Rich is not available."""
        display = DeltaDisplay()

        prev_file = tmp_path / "previous.md"
        curr_file = tmp_path / "current.md"
        prev_file.write_text("Line 1\n", encoding="utf-8")
        curr_file.write_text("Line 2\n", encoding="utf-8")

        mock_console = Mock()
        display.display_delta(curr_file, prev_file, mock_console)

        # Should not print anything when Rich is unavailable
        assert mock_console.print.call_count == 0

    @patch("cafe.services.delta_display.RICH_AVAILABLE", True)
    def test_display_delta_empty_files(self, tmp_path):
        """Test displaying delta with empty files."""
        display = DeltaDisplay()

        prev_file = tmp_path / "previous.md"
        curr_file = tmp_path / "current.md"
        prev_file.write_text("", encoding="utf-8")
        curr_file.write_text("", encoding="utf-8")

        mock_console = Mock()
        display.display_delta(curr_file, prev_file, mock_console)

        # Should display "No changes" message
        calls = [str(call) for call in mock_console.print.call_args_list]
        assert any("no changes" in str(call).lower() for call in calls)
