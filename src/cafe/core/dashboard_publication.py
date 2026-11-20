"""Announce a running workflow to the local, paired Dashboard connector."""

import hashlib
import os
from pathlib import Path
import stat

from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.utils.issue_paths import issue_name_from_dir, issue_project_root, validate_issue_directory


def _private_directory(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700


def announce_workflow(issue_dir: Path, workflow_id: str) -> None:
    """Write only a local pointer. A missing connector never blocks CAFE."""
    if not hasattr(os, "getuid"):
        return
    try:
        configured = os.environ.get("CAFE_DASHBOARD_STATE_DIR")
        default_parent = os.environ.get("XDG_STATE_HOME")
        state = Path(configured).absolute() if configured else (
            Path(default_parent) if default_parent else Path.home() / ".local/state"
        ).absolute() / "cafe-dashboard"
        inbox = state / "workflow-publications-v1"
        if not _private_directory(state) or not _private_directory(inbox):
            return
        validate_issue_directory(issue_dir)
        checkout = issue_project_root(issue_dir).resolve(strict=True)
        issue = issue_name_from_dir(issue_dir)
        if issue_dir.resolve(strict=True) != checkout / ".cafe" / "issues" / issue or not workflow_id:
            return
        key = hashlib.sha256(f"{checkout}\0{issue}".encode()).hexdigest()
        announcement = inbox / f"{key}.json"
        content = canonical_json({"checkout": str(checkout), "issue": issue, "workflowId": workflow_id})
        if announcement.is_file() and not announcement.is_symlink() and announcement.stat().st_size == len(content):
            if announcement.read_bytes() == content:
                return
        atomic_write_bytes(announcement, content)
    except (OSError, ValueError, RuntimeError):
        # Display telemetry cannot interrupt execution or change a handoff.
        return
