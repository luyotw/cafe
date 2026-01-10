"""Unit tests for checklist_validator module."""

import pytest
from pathlib import Path
from cafe.utils.checklist_validator import ChecklistValidationResult, validate_checklist


def test_validate_checklist_all_complete(tmp_path):
    """Test validation with all items checked."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""## Checklist
[x] Task 1
[x] Task 2
[x] Task 3
""")

    result = validate_checklist(checklist_file)

    assert result.is_complete is True
    assert result.unchecked_count == 0
    assert result.checklist_path == checklist_file


def test_validate_checklist_partial_complete(tmp_path):
    """Test validation with some unchecked items."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""## Checklist
[x] Task 1
[ ] Task 2
[x] Task 3
[ ] Task 4
[ ] Task 5
""")

    result = validate_checklist(checklist_file)

    assert result.is_complete is False
    assert result.unchecked_count == 3
    assert result.checklist_path == checklist_file


def test_validate_checklist_empty_file(tmp_path):
    """Test validation with empty file."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("")

    result = validate_checklist(checklist_file)

    assert result.is_complete is True
    assert result.unchecked_count == 0
    assert result.checklist_path == checklist_file


def test_validate_checklist_special_formats(tmp_path):
    """Test that special formats are not counted as unchecked.

    Only "[ ]" (bracket space bracket) should be counted as unchecked.
    Other variations like "[]", "[ x ]", "[x]", etc. should not be counted.
    """
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""## Checklist
[] Not a checkbox
[ x ] Task with spaces
[x] Completed task
[X] Also completed
[-] Partial task
[~] In progress task
[ ] Unchecked task 1
Some text with [] brackets
[ ] Unchecked task 2
""")

    result = validate_checklist(checklist_file)

    # Only the two "[ ]" patterns should be counted
    assert result.is_complete is False
    assert result.unchecked_count == 2
    assert result.checklist_path == checklist_file


def test_validate_checklist_file_not_found(tmp_path):
    """Test validation with non-existent file."""
    checklist_file = tmp_path / "nonexistent.md"

    with pytest.raises(FileNotFoundError) as exc_info:
        validate_checklist(checklist_file)

    assert "Checklist file not found" in str(exc_info.value)
    assert str(checklist_file) in str(exc_info.value)


def test_validate_checklist_with_nested_checkboxes(tmp_path):
    """Test validation with nested/indented checkboxes."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""## Checklist
- [ ] Top level unchecked
  - [x] Nested checked
  - [ ] Nested unchecked
- [x] Top level checked
    - [ ] Deep nested unchecked
""")

    result = validate_checklist(checklist_file)

    assert result.is_complete is False
    assert result.unchecked_count == 3
    assert result.checklist_path == checklist_file


def test_validate_checklist_mixed_content(tmp_path):
    """Test validation with mixed markdown content."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""# Title

Some description text

## Section 1
- [x] Done
- [ ] Not done

Some more text with [ ] in it (should count!)

## Section 2
- [x] All done here

Code example:
```python
# This [ ] should count as it's the exact string
result = some_function()
```
""")

    result = validate_checklist(checklist_file)

    # Should find 2 unchecked: one in Section 1, one in the text
    # (and potentially one in code block)
    assert result.is_complete is False
    assert result.unchecked_count == 3  # 1 in checklist + 1 in text + 1 in code
    assert result.checklist_path == checklist_file
