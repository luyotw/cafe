"""Utilities for checklist management."""

import os
import re
import stat
import tempfile
from collections import Counter
from pathlib import Path
from typing import Mapping, Union

_CHECKBOX_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-*][ \t]+)?\[(?P<state>[ xX])\](?P<body>.*)$"
)


def resolve_checklist_placeholders(checklist: str, placeholders: Mapping[str, object]) -> str:
    """Resolve placeholder variables in checklist content.

    Args:
        checklist: Checklist content with placeholders like {agent_file}
        placeholders: Dictionary mapping placeholder names to actual values

    Returns:
        Checklist content with placeholders resolved
    """
    result = checklist

    for key, value in placeholders.items():
        placeholder = f"{{{key}}}"
        result = result.replace(placeholder, str(value))

    return result


def _normalized_item_block(lines: list[str], start: int, end: int) -> str:
    """Return one checklist item's block with all completion markers cleared."""
    normalized: list[str] = []
    for line in lines[start:end]:
        match = _CHECKBOX_LINE.match(line.rstrip("\r\n"))
        if match:
            ending = line[len(line.rstrip("\r\n")) :]
            normalized.append(
                f"{match.group('indent')}{match.group('bullet') or ''}[ ]"
                f"{match.group('body')}{ending}"
            )
        else:
            normalized.append(line)
    return "".join(normalized)


def _checklist_item_blocks(content: str) -> list[tuple[int, str, bool]]:
    """Return checklist item starts, complete blocks, and their completion state.

    Continuation and nested lines are part of an item's identity.  A changed
    subordinate rule must therefore reopen its parent gate rather than retain
    a stale ``[x]`` merely because the leading checkbox text still matches.
    """
    lines = content.splitlines(keepends=True)
    items: list[tuple[int, str, bool]] = []
    for start, line in enumerate(lines):
        match = _CHECKBOX_LINE.match(line.rstrip("\r\n"))
        if match is None:
            continue
        indent = len(match.group("indent").expandtabs(4))
        end = start + 1
        while end < len(lines):
            continuation = lines[end]
            nested = _CHECKBOX_LINE.match(continuation.rstrip("\r\n"))
            if nested is not None and len(nested.group("indent").expandtabs(4)) <= indent:
                break
            if continuation.strip() and not continuation.startswith((" ", "\t")):
                break
            end += 1
        items.append(
            (
                start,
                _normalized_item_block(lines, start, end),
                match.group("state").lower() == "x",
            )
        )
    return items


def _restore_completed_items(content: str, previous: str) -> str:
    """Keep completion only for unchanged, skill-declared checklist items."""
    completed = Counter(
        block for _start, block, is_complete in _checklist_item_blocks(previous) if is_complete
    )
    if not completed:
        return content

    lines = content.splitlines(keepends=True)
    for start, block, _is_complete in _checklist_item_blocks(content):
        if completed[block] <= 0:
            continue
        line = lines[start]
        match = _CHECKBOX_LINE.match(line.rstrip("\r\n"))
        assert match is not None
        ending = line[len(line.rstrip("\r\n")) :]
        lines[start] = (
            f"{match.group('indent')}{match.group('bullet') or ''}[x]"
            f"{match.group('body')}{ending}"
        )
        completed[block] -= 1
    return "".join(lines)


def _read_existing_regular_file(path: Path) -> str | None:
    """Read one existing checklist only when it is a single-link regular file."""
    try:
        initial = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise ValueError(f"checklist path must be a single-link regular file: {path}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError(f"checklist path must be a single-link regular file: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _atomic_write_checklist(path: Path, content: str) -> None:
    """Replace a validated checklist atomically without following its target path."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Revalidate immediately before replacement.  ``replace`` itself
        # replaces a link rather than following it, so a racing link cannot
        # overwrite its target even after this check.
        _read_existing_regular_file(path)
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def generate_checklist_file(
    output_path: Union[str, Path],
    checklist_content: str,
    *,
    preserve_completed_items: bool = False,
) -> None:
    """Generate checklist file at specified path.

    Args:
        output_path: Path where checklist file should be created
        checklist_content: Content to write to the checklist file
        preserve_completed_items: Preserve ``[x]`` only for items whose text
            remains in the regenerated declared checklist.
    """
    output_path = Path(output_path)

    # Create parent directories if they don't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    previous = _read_existing_regular_file(output_path)
    if preserve_completed_items and previous is not None:
        checklist_content = _restore_completed_items(checklist_content, previous)

    _atomic_write_checklist(output_path, checklist_content)
