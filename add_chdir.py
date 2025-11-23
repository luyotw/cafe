#!/usr/bin/env python3
"""
Add monkeypatch.chdir(tmp_path) to test functions that need it.
Preserves exact indentation.
"""

import re
import sys

def add_chdir_to_test_file(filepath):
    """Add monkeypatch.chdir(tmp_path) to test functions with tmp_path parameter."""

    with open(filepath, 'r') as f:
        lines = f.readlines()

    modified = False
    new_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]
        new_lines.append(line)

        # Check if this is a test function definition with both tmp_path and monkeypatch
        if re.match(r'\s+def test_\w+\(.*tmp_path.*monkeypatch.*\).*:', line) or \
           re.match(r'\s+def test_\w+\(.*monkeypatch.*tmp_path.*\).*:', line):

            # Get the indentation level
            indent_match = re.match(r'(\s+)def ', line)
            if not indent_match:
                i += 1
                continue

            func_indent = indent_match.group(1)
            body_indent = func_indent + '    '  # Body is 4 spaces more than def

            # Look at next lines
            j = i + 1

            # Skip docstring if present
            if j < len(lines) and lines[j].strip().startswith('"""'):
                new_lines.append(lines[j])
                j += 1

                # Multi-line docstring - find closing """
                if '"""' not in lines[j-1][3:]:  # Not a one-liner
                    while j < len(lines) and '"""' not in lines[j]:
                        new_lines.append(lines[j])
                        j += 1
                    if j < len(lines):  # Found closing """
                        new_lines.append(lines[j])
                        j += 1

            # Now check if chdir already exists
            has_chdir = False
            check_lines = min(j + 10, len(lines))
            for k in range(j, check_lines):
                if 'monkeypatch.chdir' in lines[k]:
                    has_chdir = True
                    break

            if not has_chdir:
                # Insert chdir with correct indentation
                new_lines.append(f'{body_indent}monkeypatch.chdir(tmp_path)\n')
                modified = True
                print(f"Added chdir to: {line.strip()}")

            # Continue from j
            i = j
            continue

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
    if len(sys.argv) < 2:
        print("Usage: python add_chdir.py <test_file>")
        sys.exit(1)

    filepath = sys.argv[1]
    add_chdir_to_test_file(filepath)
