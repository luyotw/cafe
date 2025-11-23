#!/usr/bin/env python3
"""
Fix test files to add monkeypatch parameter and chdir call.
Preserves exact indentation.
"""

import re

def fix_test_file(filepath):
    """Add monkeypatch parameter and chdir to test functions with tmp_path."""

    with open(filepath, 'r') as f:
        lines = f.readlines()

    modified = False
    new_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check if this is a test function with tmp_path but not monkeypatch
        if re.match(r'(\s+)def (test_\w+)\(self, tmp_path: Path, (mock_\w+)\) -> None:', line):
            match = re.match(r'(\s+)def (test_\w+)\(self, tmp_path: Path, (mock_\w+)\) -> None:', line)
            indent = match.group(1)
            func_name = match.group(2)
            mock_param = match.group(3)

            # Replace with version that includes monkeypatch
            new_line = f'{indent}def {func_name}(self, tmp_path: Path, {mock_param}, monkeypatch) -> None:\n'
            new_lines.append(new_line)
            print(f"Added monkeypatch param to: {func_name}")

            # Now look for docstring and add chdir after it
            j = i + 1
            body_indent = indent + '    '

            # Next line should be docstring
            if j < len(lines) and lines[j].strip().startswith('"""'):
                new_lines.append(lines[j])
                j += 1

            # Add chdir
            new_lines.append(f'{body_indent}monkeypatch.chdir(tmp_path)\n')
            print(f"Added chdir to: {func_name}")
            modified = True

            # Continue from j
            i = j
            continue

        else:
            new_lines.append(line)

        i += 1

    if modified:
        with open(filepath, 'w') as f:
            f.writelines(new_lines)
        print(f"\n✓ Modified {filepath}")
        return True
    else:
        print(f"No changes needed for {filepath}")
        return False

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python fix_test_chdir.py <test_file>")
        sys.exit(1)

    filepath = sys.argv[1]
    fix_test_file(filepath)
