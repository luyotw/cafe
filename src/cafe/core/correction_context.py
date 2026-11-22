"""Bounded projection of the independent Review evidence for both correction roles."""

from __future__ import annotations

import json
from pathlib import Path
import re
from subprocess import CalledProcessError

from cafe.core.context_packet import _visible_heading_fragments
from cafe.core.git import GitError
from cafe.core.packet_io import read_regular_bytes, sha256_bytes
from cafe.core.todo import MAX_TODO_ITEMS, parse_todo_list

MAX_REVIEW_BYTES = 256 * 1024
MAX_REVIEW_FRAGMENT_BYTES = 12 * 1024
MAX_CORRECTION_PROJECTION_BYTES = 16 * 1024
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_CLOSED_STATES = {"closed", "fixed", "withdrawn", "closed_fresh", "closed_reused"}
_PROSE_LINE = re.compile(r"(?:#{1,6} )?[A-Za-z][A-Za-z0-9 ,.?!'’–—-]*")


def _previous_head(sections):
    baseline = sections.get("Review Baseline", "")
    candidates = re.findall(r"(?im)^\s*-\s*Reviewed HEAD:\s*`?([0-9a-f]{40})\b", baseline)
    return candidates[0] if len(candidates) == 1 else None


def _changed_paths(run_git, previous, current):
    if not previous or not current or not _HEAD.fullmatch(current):
        return None
    try:
        run_git("merge-base", "--is-ancestor", previous, current)
        changed = run_git("diff", "--name-only", "-z", "--no-renames", previous, current, "--")
    except (OSError, ValueError, RuntimeError, GitError, CalledProcessError):
        return None
    paths = sorted(set(filter(None, changed.split("\0"))))
    return paths if len(paths) <= 256 else None


def _evidence_rows(sections):
    section = sections.get("Correction Evidence", "")
    match = re.search(r"(?ms)^```json\s*\n(.*?)^```\s*$", section)
    if not match:
        return {}
    try:
        rows = json.loads(match[1])
        if not isinstance(rows, list) or len(rows) > MAX_TODO_ITEMS:
            return {}
        indexed = {row["id"]: row for row in rows if isinstance(row, dict)}
        return indexed if len(indexed) == len(rows) else {}
    except (ValueError, KeyError, TypeError):
        return {}


def _plain_document(run_git, head, path):
    """Recognize only small, existing Markdown files made of headings and prose."""
    blob = f"{head}:{path}"
    size = int(run_git("cat-file", "-s", blob))
    if not 0 < size <= 4096:
        return False
    lines = run_git("cat-file", "blob", blob).splitlines()
    return bool(lines and lines[0].startswith("# ") and all(
        not line or _PROSE_LINE.fullmatch(line) for line in lines
    ))


def _reusable(issue, row, *, safe_changes_since):
    if not isinstance(row, dict):
        return False
    head = row.get("head")
    paths = row.get("paths")
    if (not isinstance(head, str) or not _HEAD.fullmatch(head)
            or not isinstance(paths, list) or not 1 <= len(paths) <= 64
            or any(not isinstance(path, str) or not path or Path(path).is_absolute()
                   or ".." in Path(path).parts for path in paths)):
        return False
    try:
        changes = safe_changes_since(head)
        if changes is None or set(paths).intersection(changes):
            return False
        reference = Path(row["evidence_path"])
        if reference.is_absolute() or ".." in reference.parts:
            return False
        path = (issue / reference).resolve(strict=True)
        return (path.is_relative_to(issue.resolve())
                and sha256_bytes(read_regular_bytes(path, MAX_REVIEW_BYTES)) == row["sha256"])
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, GitError, CalledProcessError):
        return False


def review_projection(*, issue, source, text, current_head, run_git, requirements, previous_requirements):
    fragments = _visible_heading_fragments(text)
    sections = {
        f["heading_path"][-1]: f["bytes"] for f in fragments
        if len(f["heading_path"]) == 1
        or f["heading_path"] == ["Review Baseline", "Correction Evidence"]
    }
    previous = _previous_head(sections)
    paths = _changed_paths(run_git, previous, current_head)
    rows = _evidence_rows(sections)
    todos = [
        {"id": item.item_id, "source": item.source, "fingerprint": item.fingerprint,
         "work": item.work, "closure": item.closure, "evidence": item.evidence}
        for item in parse_todo_list(text, expected_source="review") if not item.checked
    ]
    registry = []
    closed = []
    for line in sections.get("Finding Registry", "").splitlines():
        if not line.startswith("|"):
            continue
        fields = [cell.strip().strip("`") for cell in re.split(r"(?<!\\)\|", line.strip("|"))]
        if len(fields) == 9 and re.fullmatch(r"(BLK|FUP)-[0-9]+", fields[0]):
            registry.append(line)
            if fields[6] in _CLOSED_STATES:
                closed.append(fields[0])
    closed.extend(identifier for identifier in rows if identifier not in closed
                  and identifier not in {todo["id"] for todo in todos})
    since = {previous: paths}

    def changes_since(head):
        if head not in since:
            if len(since) >= 16:
                return None
            try:
                run_git("merge-base", "--is-ancestor", head, previous)
                since[head] = _changed_paths(run_git, head, current_head)
            except (OSError, ValueError, GitError, CalledProcessError):
                since[head] = None
        return since[head]

    safe_changes = {}

    def safe_changes_since(head):
        if head not in safe_changes:
            changes = changes_since(head)
            safe_changes[head] = None
            # A known path boundary can miss new callers or gates. The only
            # exception is a small edit to existing, positively recognized prose.
            if changes is not None and len(changes) <= 8 and all(
                name.startswith("docs/") and name.endswith(".md")
                and _plain_document(run_git, head, name)
                and _plain_document(run_git, current_head, name)
                for name in changes
            ):
                safe_changes[head] = changes
        return safe_changes[head]

    reusable = [
        {"id": identifier, **rows[identifier]}
        for identifier in closed if identifier in rows and paths is not None and requirements
        and requirements == previous_requirements
        and _reusable(issue, rows[identifier], safe_changes_since=safe_changes_since)
    ]
    retained_ids = {row["id"] for row in reusable}
    recovery = []
    for fragment in fragments:
        if fragment["heading_path"][-1] in {
            "Review Baseline", "Carried Evidence Summary", "Cumulative Seam Coverage Summary",
            "Finding Registry", "Risk Ledger", "Triggered Risk Coverage", "Acceptance Closure Evidence",
            "Follow-up Proposals", "Outcome",
        }:
            body = fragment["bytes"]
            recovery.append({"heading": fragment["heading_path"], "sha256": fragment["sha256"],
                             "text": body if len(body.encode()) <= MAX_REVIEW_FRAGMENT_BYTES else None})
    return {
        "source": {"path": str(source), "sha256": sha256_bytes(text.encode())},
        "git": {"previous_reviewed_head": previous, "head": current_head, "changed_paths": paths},
        "requirements": requirements,
        "requirements_match": bool(requirements and requirements == previous_requirements),
        "open_todos": todos,
        "finding_registry": registry,
        "reusable": reusable,
        "reopen": [identifier for identifier in closed if identifier not in retained_ids],
        "recovery": recovery,
    }


def project_published_review(*, issue, root, state, entry, artifacts, run_git):
    """Read only the pinned Review and the requirement digests it consumed."""
    from cafe.core.blackboard import BlackboardStore

    path = (root / entry.path).resolve(strict=True)
    path.relative_to(issue.resolve())
    report = read_regular_bytes(path, MAX_REVIEW_BYTES)
    if entry.content_sha256 != sha256_bytes(report):
        raise ValueError("the published Review digest has changed")
    audit = BlackboardStore(issue).audit
    completed = audit.latest_record(state.workflow_id, {"step_completed", "single_step_completed"},
                                    step=entry.updated_by, bounded=True)
    consumed = {}
    if completed:
        started = audit.latest_record(state.workflow_id, {"step_started"}, step=entry.updated_by,
                                      before_sequence=completed["sequence"], bounded=True)
        if (started and completed["data"].get("agent_invoked")
                and started["data"].get("attempt") == completed["data"].get("attempt")):
            consumed = started["data"].get("consumed_inputs", {})
    requirements = {}
    previous_requirements = {}
    for name in ("spec", "plan"):
        if (requirement := artifacts.get(name)) is not None:
            source = (root / requirement.path).resolve(strict=True)
            source.relative_to(issue.resolve())
            requirements[name] = sha256_bytes(read_regular_bytes(source, MAX_REVIEW_BYTES))
            previous_requirements[name] = consumed.get(name, {}).get("digest")
    if set(requirements) != {"spec", "plan"}:
        previous_requirements = {}
    return review_projection(
        issue=issue, source=path.relative_to(root), text=report.decode("utf-8"),
        current_head=run_git("rev-parse", "HEAD"), run_git=run_git,
        requirements=requirements, previous_requirements=previous_requirements,
    )


def render_review_projection(projection, *, mode):
    from cafe.core.packet_io import compact_json

    if projection is None:
        return ""
    base = {key: value for key, value in projection.items() if key != "recovery"}
    if mode != "exact":
        base["recovery"] = [
            {**fragment, "text": None} for fragment in projection["recovery"]
        ]
        # Add complete fragments in source order while keeping the prompt bounded.
        for fallback, source in zip(base["recovery"], projection["recovery"]):
            fallback["text"] = source["text"]
            if len(compact_json(base).encode()) > MAX_CORRECTION_PROJECTION_BYTES:
                fallback["text"] = None
    encoded = compact_json(base)
    if len(encoded.encode()) <= MAX_CORRECTION_PROJECTION_BYTES:
        return encoded
    # Preserve the resolvable source and exact range. The worktree can produce
    # the path set from the range, even when listing it inline exceeds budget.
    fallback = {
        "source": base["source"],
        "git": {key: base["git"][key] for key in ("previous_reviewed_head", "head")},
        "read_source_sections": ["Todo List", "Finding Registry", "Review Baseline"],
        "reuse_unproven": True,
        "requirements": base["requirements"],
    }
    with_todos = compact_json({**fallback, "open_todos": base["open_todos"]})
    if len(with_todos.encode()) <= MAX_CORRECTION_PROJECTION_BYTES:
        return with_todos
    return compact_json(fallback)
