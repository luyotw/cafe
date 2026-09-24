"""Utilities for checklist management."""

import os
import re
import stat
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Mapping, Union

from cafe.core.checklist import _CHECKBOX_LINE, _checklist_item_blocks
from cafe.utils.checklist_validator import (
    EXPECTED_LEDGER_FIELDS,
    GIT_EVIDENCE_TIMEOUT_SECONDS,
    validate_todo_evidence_set,
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


def _restore_completed_items(
    content: str, previous: str, *, todo_ledger_path: Path | None = None
) -> str:
    """Keep completion only for unchanged, skill-declared checklist items."""
    completed = Counter(
        block for _start, block, is_complete in _checklist_item_blocks(previous) if is_complete
    )
    if not completed:
        return content

    valid_projected: set[tuple[str, str]] = set()
    if todo_ledger_path is not None and todo_ledger_path.is_file():
        evidence_by_id: dict[str, dict[str, str]] = {}
        projected = re.compile(
            r"^\[[ xX]\] `(?P<id>[^`]+)` — (?P<work>.+) "
            r"\(source fingerprint: (?P<fp>[0-9a-f]{64})\)$"
        )
        for line in content.splitlines():
            match = projected.fullmatch(line.strip())
            if match:
                # Only identity/fingerprint are needed here. The synthetic
                # fields deliberately reproduce the rendered fingerprint via
                # the ledger's exact fingerprint comparison below.
                valid_projected.add((match.group("id"), match.group("fp")))
        ledger = todo_ledger_path.read_text(encoding="utf-8")
        # Parse exact sections locally because the source Todo fields are not
        # available at this generic file-publication layer.
        for item_id, fingerprint in tuple(valid_projected):
            marker = re.compile(rf"^### {re.escape(item_id)}\s*$", re.MULTILINE)
            matches = list(marker.finditer(ledger))
            if len(matches) != 1:
                valid_projected.discard((item_id, fingerprint))
                continue
            start = matches[0].end()
            following = re.search(r"^#{2,3} ", ledger[start:], re.MULTILINE)
            block = ledger[start : start + following.start() if following else None]
            required = {
                "Status": "completed",
                "Source fingerprint": f"`{fingerprint}`",
            }
            fields = {}
            # `Targeted evidence` is optional informational text: every occurrence,
            # and any genuinely indented continuation under it, is skipped before
            # accounting. Only leading whitespace marks continuation, so a line
            # without it is a top-level field whatever trailing whitespace it has.
            in_evidence = False
            for raw in block.splitlines():
                stripped = raw.strip()
                if stripped.startswith("- Targeted evidence:"):
                    in_evidence = True
                    continue
                if in_evidence and raw != raw.lstrip():
                    continue
                in_evidence = False
                match = re.fullmatch(r"- ([A-Za-z ]+):\s*(.*)", stripped)
                if not match:
                    continue
                fields.setdefault(match.group(1), []).append(match.group(2).strip())
            if (
                set(fields) != EXPECTED_LEDGER_FIELDS
                or any(fields.get(name) != [value] for name, value in required.items())
                or any(len(values) != 1 for values in fields.values())
            ):
                valid_projected.discard((item_id, fingerprint))
                continue
            for name in ("Files", "Commit"):
                values = fields.get(name, [])
                if len(values) != 1 or values[0].lower() in {
                    "",
                    "n/a",
                    "none",
                    "unavailable",
                    "unknown",
                }:
                    valid_projected.discard((item_id, fingerprint))
            if (item_id, fingerprint) in valid_projected:
                evidence_by_id[item_id] = {name: values[0] for name, values in fields.items()}
        if evidence_by_id:
            try:
                repository = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(todo_ledger_path.parent),
                        "rev-parse",
                        "--show-toplevel",
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=GIT_EVIDENCE_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired):
                repository = None
            if repository is None or repository.returncode != 0 or not repository.stdout.strip():
                valid_projected.clear()
            else:
                evidence_errors = validate_todo_evidence_set(
                    evidence_by_id,
                    Path(repository.stdout.strip()),
                )
                # Validation is one atomic evidence-set decision. This also
                # protects against older validators returning sparse errors.
                if any(evidence_errors.values()):
                    valid_projected.clear()

    projected_row = re.compile(
        r"^\[[ xX]\] `(?P<id>[^`]+)` — .+ " r"\(source fingerprint: (?P<fp>[0-9a-f]{64})\)$"
    )
    lines = content.splitlines(keepends=True)
    for start, block, _is_complete in _checklist_item_blocks(content):
        if completed[block] <= 0:
            continue
        line = lines[start]
        match = _CHECKBOX_LINE.match(line.rstrip("\r\n"))
        assert match is not None
        projected_match = projected_row.fullmatch(line.strip())
        if (
            projected_match
            and (projected_match.group("id"), projected_match.group("fp")) not in valid_projected
        ):
            continue
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
    todo_ledger_path: Path | None = None,
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
        checklist_content = _restore_completed_items(
            checklist_content, previous, todo_ledger_path=todo_ledger_path
        )

    _atomic_write_checklist(output_path, checklist_content)


def publish_materialized_checklist(path, materialized, *, preserve=False, todo_ledger_path=None):
    """Restore proven source identities and publish through the existing atomic writer."""
    from cafe.core.checklist import load_materialization, normalized_checklist

    previous = _read_existing_regular_file(path)
    content = materialized.content
    if preserve and previous is not None:
        try:
            pinned = load_materialization(path.parent / "iteration.json")
        except ValueError:
            pinned = None
            legacy_allowed = False
        else:
            legacy_allowed = pinned is None and not materialized.overlays
        if pinned is not None and normalized_checklist(previous) == normalized_checklist(
            pinned.content
        ):
            prior_blocks = _checklist_item_blocks(previous)
            complete = {
                gate.identity
                for gate, (_, _, checked) in zip(pinned.gates, prior_blocks)
                if checked
            }
            # Reuse live ledger and Git evidence checks, then intersect with
            # source identities instead of transferring completion by text.
            eligible = _restore_completed_items(
                content, content.replace("[ ]", "[x]"), todo_ledger_path=todo_ledger_path
            )
            lines = content.splitlines(keepends=True)
            for gate, (line, _, valid) in zip(materialized.gates, _checklist_item_blocks(eligible)):
                if gate.identity in complete and valid:
                    lines[line] = lines[line].replace("[ ]", "[x]", 1)
            content = "".join(lines)
        elif legacy_allowed:
            blocks = [block for _, block, _ in _checklist_item_blocks(content)]
            prior = [block for _, block, _ in _checklist_item_blocks(previous)]
            if len(set(blocks)) == len(blocks) and len(set(prior)) == len(prior):
                content = _restore_completed_items(
                    content, previous, todo_ledger_path=todo_ledger_path
                )
    generate_checklist_file(path, content)
