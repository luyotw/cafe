"""A single staged commit, performed inside the continuing agent's tool environment."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from cafe.core.blackboard import BlackboardStore
from cafe.core.git import GitOperations
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.core.workspace_artifact import _git, inspect_workspace
from cafe.utils.issue_paths import issue_project_root


def snapshot(root):
    changes = {}
    for change in inspect_workspace(root).changes:
        path = root / change["path"]
        if not path.parent.resolve().is_relative_to(root):
            raise ValueError("changed path is outside the worktree")
        entry = dict(change)
        if change["state"][1] not in (" ", "D"):
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                value = os.fsencode(os.readlink(path))
                digest = hashlib.sha256(value)
            elif stat.S_ISREG(mode):
                with path.open("rb") as stream:
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(128 * 1024), b""):
                        digest.update(chunk)
            else:
                raise ValueError(f"unsupported worktree change at {change['path']}")
            entry.update(content=digest.hexdigest(), mode=mode)
        changes[change["path"]] = entry
    staged = _git(root, "diff", "--cached", "--raw", "--no-abbrev", "--no-renames", "-z").split("\0")
    for header, path in zip(staged[::2], staged[1::2]):
        if path in changes:
            changes[path]["index"] = header
    return {"worktree": str(root), "parent": _git(root, "rev-parse", "HEAD"),
            "ref": _git(root, "rev-parse", "--symbolic-full-name", "HEAD"),
            "tree": _git(root, "write-tree"), "changes": changes}


def _operation(issue, step, iteration):
    request = issue / step / f"iteration_{iteration:03}" / "staged_commit_request.json"
    if not request.exists():
        return None
    if request.is_symlink() or request.stat().st_size > 4096:
        raise ValueError("invalid staged commit request")
    operation = json.loads(request.read_bytes())
    if (not isinstance(operation, dict) or set(operation) != {"message", "skip_hooks"}
            or not isinstance(operation["message"], str) or not operation["message"].strip()
            or any(ord(c) < 32 for c in operation["message"]) or len(operation["message"]) > 240
            or type(operation["skip_hooks"]) is not bool):
        raise ValueError("a staged commit request needs a single-line message and explicit hook choice")
    return operation


def capture(issue, step, iteration):
    operation = _operation(issue, step, iteration)
    if operation is None:
        return None
    root = issue_project_root(issue.resolve())
    state = snapshot(root)
    if state["tree"] == _git(root, "rev-parse", "HEAD^{tree}"):
        raise ValueError("the requested commit has no staged change")
    if len(canonical_json(state)) > 128 * 1024:
        raise ValueError("staged change exceeds the permission task evidence limit")
    return {"operation": operation, **state}


def active_task(issue, state, step):
    handoff = state.handoff_contract
    if (not handoff or handoff.from_step != step or handoff.to_step != step
            or not handoff.source.startswith("human_task.") or state.current_step != step):
        return None
    event = BlackboardStore(issue).audit.latest_record(
        state.workflow_id, {"human_task_completed"}, step=step, bounded=True)
    if not event:
        return None
    data = event["data"]
    if (data.get("trigger") != "need_permission" or data.get("to_step") != step
            or data.get("continuation_handoff_created_at") != handoff.created_at
            or not data.get("human_task_id")):
        return None
    records = HumanTaskRecordStore(issue)
    task = records.get_task(data["human_task_id"])
    if not task.staged_commit:
        return None
    result = records.get_result(task.id)
    if (task.workflow_id != state.workflow_id or task.step != step or task.trigger != "need_permission"
            or task.status != HumanTaskStatus.COMPLETED or not result or result.id != data.get("result_id")
            or result.payload.get("continuation") != step
            or result.payload.get("feedback") != "approve-staged-commit " + task.staged_commit["tree"]):
        raise ValueError("the scoped staged commit approval is absent")
    return task


def _receipt_path(issue, task):
    return issue / task.step / f"iteration_{task.iteration:03}" / f"commit-{task.id}.json"


def verify(issue, task, *, completed=False):
    """Return the committed HEAD or verify the untouched input to a first attempt."""
    root = issue_project_root(issue.resolve())
    expected = task.staged_commit
    if _operation(issue, task.step, task.iteration) != expected["operation"]:
        raise ValueError("requested operation differs from the scoped staged commit approval")
    if expected["worktree"] != str(root):
        raise ValueError("approved worktree changed")
    receipt = _receipt_path(issue, task)
    head = _git(root, "rev-parse", "HEAD")
    if receipt.exists():
        record = json.loads(receipt.read_bytes())
        if (record.get("commit") == head and record.get("task") == task.id
                and record.get("state") in {"prepared", "committed"}):
            if (_git(root, "show", "-s", "--format=%P %T", head)
                    == expected["parent"] + " " + expected["tree"]
                    and _git(root, "rev-parse", "--symbolic-full-name", "HEAD") == expected["ref"]):
                inspect_workspace(root).require_clean()
                return head
        raise ValueError("scoped commit attempt needs reconciliation at its approved HEAD")
    if completed:
        raise ValueError("approved commit is still pending")
    current = snapshot(root)
    changes = [name for name in sorted(current["changes"].keys() | expected["changes"].keys())
               if current["changes"].get(name) != expected["changes"].get(name)]
    fields = [label for key, label in (("parent", "HEAD"), ("ref", "ref"), ("tree", "staged tree"))
              if current[key] != expected[key]]
    if changes or fields:
        raise ValueError("staged commit approval differs: " + ", ".join((fields + changes)[:16])[:1600])
    return None


@contextmanager
def provider_scope(issue, task):
    """Expose this exact task only while the continuing provider holds the workspace lease."""
    if task is None:
        yield
        return
    path = _receipt_path(issue, task).with_suffix(".lease")
    with path.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def commit(issue, task_id):
    """Called by the provider inside its existing sandbox, never by the workflow host."""
    issue = Path(issue).resolve()
    root = issue_project_root(issue)
    state = BlackboardStore(issue).load_read_only()
    task = active_task(issue, state, state.current_step)
    if task is None or task.id != task_id or root != Path.cwd().resolve():
        raise ValueError("approval does not own this workflow, step and worktree")
    receipt = _receipt_path(issue, task)
    with receipt.with_suffix(".lease").open("rb") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise ValueError("the continuing provider is not active")
        with receipt.with_suffix(".lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = verify(issue, task)
            if existing:
                return existing
            return _commit_tree(root, task, receipt)


def _commit_tree(root, task, receipt):
    # Build the candidate with normal Git commit semantics. The private HEAD and
    # index allow hooks to reject or modify the candidate before the actual ref moves.
    expected = task.staged_commit
    git = GitOperations(str(root))
    if git.has_in_progress_operation():
        raise ValueError("another Git operation is in progress")
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    with tempfile.TemporaryDirectory(dir=receipt.parent, prefix=".commit-") as work:
        private = Path(work).resolve()
        (private / "HEAD").write_text(expected["parent"] + "\n")
        (private / "commondir").write_text(common + "\n")
        candidate = (f"--git-dir={private}", f"--work-tree={root}")
        git.run_git(*candidate, "read-tree", expected["tree"])
        atomic_write_bytes(receipt, canonical_json({"task": task.id, "state": "started"}))
        operation = expected["operation"]
        options = ["--no-verify"] if operation["skip_hooks"] else []
        git.run_git(*candidate, "commit", *options, "-m", operation["message"])
        oid = git.run_git(*candidate, "rev-parse", "HEAD")
        if git.run_git("show", "-s", "--format=%P %T", oid) != expected["parent"] + " " + expected["tree"]:
            raise ValueError("Git hooks changed the approved candidate")
        current = snapshot(root)
        if any(current[key] != expected[key] for key in current):
            raise ValueError("worktree or staged content changed during the commit")
        atomic_write_bytes(receipt, canonical_json({"task": task.id, "commit": oid, "state": "prepared"}))
        git.run_git("update-ref", "-m", "commit: " + operation["message"], expected["ref"], oid, expected["parent"])
        atomic_write_bytes(receipt, canonical_json({"task": task.id, "commit": oid, "state": "committed"}))
    return verify(receipt.parents[2], task, completed=True)

