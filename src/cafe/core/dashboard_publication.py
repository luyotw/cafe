"""Publish workflow summaries to the local, paired Dashboard connector."""

import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.utils.issue_paths import issue_name_from_dir, issue_project_root, validate_issue_directory

if TYPE_CHECKING:
    from cafe.core.blackboard import BlackboardState


def _private_directory(path: Path) -> bool:
    info = path.lstat()
    return (
        stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )


def _project(checkout: Path) -> dict:
    def git(*args: str) -> str:
        return subprocess.run(
            ("git", *args), cwd=checkout, check=True, capture_output=True, text=True, timeout=2,
        ).stdout

    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()
    entry = git("worktree", "list", "--porcelain", "-z").split("\0", 1)[0]
    main = Path(entry.removeprefix("worktree "))
    if not main.is_absolute():
        raise ValueError("missing main checkout")
    return {
        "projectId": hashlib.sha256(str(common).encode()).hexdigest(),
        "projectName": main.name.encode()[:200].decode(errors="ignore"),
    }


def _inbox() -> Path | None:
    if not hasattr(os, "getuid"):
        return None
    configured = os.environ.get("CAFE_DASHBOARD_STATE_DIR")
    default_parent = os.environ.get("XDG_STATE_HOME")
    state = Path(configured).absolute() if configured else (
        Path(default_parent) if default_parent else Path.home() / ".local/state"
    ).absolute() / "cafe-dashboard"
    if not _private_directory(state):
        return None
    marker = state / "publication-pairing.json"
    info = marker.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024):
        return None
    connection_id = json.loads(marker.read_bytes())["connectionId"]
    if not re.fullmatch(r"[a-f0-9-]{36}", connection_id):
        return None
    inbox = state / f"workflow-publications-v3-{connection_id}"
    inbox.mkdir(mode=0o700, exist_ok=True)
    return inbox if _private_directory(inbox) else None


def _snapshot(
    inbox: Path, issue_dir: Path, board: "BlackboardState", *, start: bool, archived: bool,
) -> tuple[Path, bytes] | None:
    validate_issue_directory(issue_dir)
    checkout = issue_project_root(issue_dir).resolve(strict=True)
    issue = issue_name_from_dir(issue_dir)
    if issue_dir.resolve(strict=True) != checkout / ".cafe" / "issues" / issue:
        return None
    key = hashlib.sha256(f"{checkout}\0{issue}\0{board.workflow_id}".encode()).hexdigest()
    path = inbox / f"{key}.json"
    previous = None
    try:
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_size <= 16_384:
            previous = json.loads(path.read_bytes())
    except (FileNotFoundError, ValueError):
        pass
    if not isinstance(previous, dict) or not isinstance(previous.get("workflow"), dict):
        previous = None
    previous_workflow = previous["workflow"] if previous else {}
    if not start and not archived:
        if previous and previous.get("archived"):
            return None
        # Starting before the connector was available must not suppress later committed events.
        if not previous and not (board.applied_event_sequence or board.step_attempt_counts
                                 or board.ownership_cursor):
            return None

    tasks = []
    if not archived:
        records = HumanTaskRecordStore(issue_dir)
        if records.exists and not stat.S_ISREG(records.file_path.lstat().st_mode):
            return None
        tasks = [task for task in records.tasks() if task.workflow_id == board.workflow_id]
    pending = sum(task.status is HumanTaskStatus.PENDING for task in tasks)
    handoff = board.handoff_contract
    step = board.current_step
    status = "active"
    if step in ("done", "_done") or (handoff and handoff.to_owner == "done"):
        status = "completed"
    elif handoff and (handoff.status_code.startswith("INTERRUPTED:")
                      or handoff.intent == "agent_execution_interrupted"):
        status = "interrupted"
    elif pending:
        status = "waiting"
    elif handoff and handoff.to_owner == "user":
        status = "resuming" if tasks else "unknown"
    workflow = {
        "issue": issue, "workflowId": board.workflow_id, "playbookId": board.playbook_id,
        "step": (handoff.from_step or None) if step == "user" and handoff else step,
        "status": status, "pendingTasks": pending,
        "updatedAt": previous_workflow.get("updatedAt"),
    }
    project = _project(checkout) if start or not previous else {
        field: previous[field] for field in ("projectId", "projectName")
    }
    publication = {**project, "checkout": str(checkout), "workflow": workflow, "archived": archived}
    if publication == previous:
        return None
    workflow["updatedAt"] = (
        datetime.now().astimezone().isoformat() if archived else board.updated_at
    )
    return path, canonical_json(publication)


_PUBLICATION_ERRORS = (OSError, ValueError, RuntimeError, KeyError, TypeError,
                       subprocess.SubprocessError)


def _write_snapshot(snapshot: tuple[Path, bytes] | None) -> None:
    if snapshot is None:
        return
    try:
        atomic_write_bytes(*snapshot)
    except OSError:
        # Progress reporting must never interrupt workflow execution or cleanup.
        return


def publish_workflow(issue_dir: Path, board: "BlackboardState", *, start: bool = False) -> None:
    """Relay committed state only when a local Dashboard state directory exists."""
    try:
        inbox = _inbox()
        if inbox is not None:
            _write_snapshot(_snapshot(inbox, issue_dir, board, start=start, archived=False))
    except _PUBLICATION_ERRORS:
        return


def prepare_workflow_archive(issue_dir: Path) -> Callable[[], None]:
    """Capture the status before removal. The caller publishes it after removal succeeds."""
    from cafe.core.blackboard import BLACKBOARD_FILENAME, BlackboardState

    snapshot = None
    try:
        inbox = _inbox()
        if inbox is not None:
            validate_issue_directory(issue_dir)
            raw = json.loads((issue_dir / BLACKBOARD_FILENAME).read_bytes())
            board = BlackboardState.from_dict(raw, initial_step=raw["current_step"])
            snapshot = _snapshot(inbox, issue_dir, board, start=False, archived=True)
    except _PUBLICATION_ERRORS:
        pass
    return lambda: _write_snapshot(snapshot)
