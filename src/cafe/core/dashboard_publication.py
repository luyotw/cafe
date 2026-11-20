"""Publish workflow summaries to the local, paired Dashboard connector."""

import hashlib
import json
import os
import stat
import subprocess
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
        "projectRoot": str(main.resolve()),
        "projectId": hashlib.sha256(str(common).encode()).hexdigest(),
        "projectName": main.name.encode()[:200].decode(errors="ignore"),
    }


def publish_workflow(issue_dir: Path, board: "BlackboardState", *, start: bool = False) -> None:
    """Relay a committed state. Only a running workflow opens a publication."""
    if not hasattr(os, "getuid"):
        return
    try:
        configured = os.environ.get("CAFE_DASHBOARD_STATE_DIR")
        default_parent = os.environ.get("XDG_STATE_HOME")
        state = Path(configured).absolute() if configured else (
            Path(default_parent) if default_parent else Path.home() / ".local/state"
        ).absolute() / "cafe-dashboard"
        inbox = state / "workflow-publications-v2"
        if not _private_directory(state) or not _private_directory(inbox):
            return
        validate_issue_directory(issue_dir)
        checkout = issue_project_root(issue_dir).resolve(strict=True)
        issue = issue_name_from_dir(issue_dir)
        if issue_dir.resolve(strict=True) != checkout / ".cafe" / "issues" / issue:
            return
        key = hashlib.sha256(f"{checkout}\0{issue}".encode()).hexdigest()
        path = inbox / f"{key}.json"
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8192:
            previous = json.loads(path.read_bytes())
        else:
            previous = None
        previous_workflow = previous["workflow"] if previous else {}
        if not start and previous_workflow.get("workflowId") != board.workflow_id:
            return

        records = HumanTaskRecordStore(issue_dir)
        if records.exists and not stat.S_ISREG(records.file_path.lstat().st_mode):
            return
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
        project = _project(checkout) if start else {
            field: previous[field] for field in ("projectRoot", "projectId", "projectName")
        }
        publication = {**project, "checkout": str(checkout), "workflow": workflow}
        if publication == previous:
            return
        workflow["updatedAt"] = board.updated_at
        atomic_write_bytes(path, canonical_json(publication))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError):
        # Progress reporting must never interrupt workflow execution.
        return
