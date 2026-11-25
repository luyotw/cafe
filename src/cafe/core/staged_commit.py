"""A single staged commit, performed inside the continuing agent's tool environment."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import subprocess
import tempfile

from cafe.core.blackboard import BlackboardStore
from cafe.core.git import GitOperations
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.packet_io import atomic_write_bytes, canonical_json, read_regular_bytes
from cafe.core.workspace_artifact import _git, inspect_workspace
from cafe.utils.issue_paths import issue_project_root


SCOPE_ENV = "CAFE_STAGED_SCOPE"
_provider_scope = ContextVar("staged_commit_scope", default=None)


def snapshot(root, *, compare_tree=None):
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
    if compare_tree is None:
        tree = _git(root, "write-tree")
    else:
        # write-tree needs the real index lock even for a read. Compare to the
        # approved tree without asking Git to rewrite the already locked index.
        diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--ignore-submodules=none",
                               compare_tree, "--"], cwd=root, capture_output=True, text=True)
        if diff.returncode not in (0, 1):
            raise ValueError("could not verify the reserved Git index: " + diff.stderr.strip())
        tree = compare_tree if diff.returncode == 0 else None
    return {"worktree": str(root), "parent": _git(root, "rev-parse", "HEAD"),
            "ref": _git(root, "rev-parse", "--symbolic-full-name", "HEAD"),
            "tree": tree, "changes": changes}


def _operation(issue, step, iteration):
    request = issue / step / f"iteration_{iteration:03}" / "staged_commit_request.json"
    if not request.exists():
        return None
    operation = json.loads(read_regular_bytes(request, 4096))
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
    if (data.get("to_step") != step
            or data.get("continuation_handoff_created_at") != handoff.created_at
            or not data.get("human_task_id")):
        return None
    records = HumanTaskRecordStore(issue)
    task = records.get_task(data["human_task_id"])
    result_id = data.get("result_id")
    if data.get("trigger") == "agent_execution_interrupted":
        retry = records.get_result(task.id)
        prepared = BlackboardStore(issue).audit.latest_record(
            state.workflow_id, {"agent_interruption_prepared"}, step=step, bounded=True)
        if not prepared:
            return None
        interruption = prepared["data"]
        grant = interruption.get("staged_commit_permission")
        contract = interruption["contract"]
        key = f"user-handoff:{state.workflow_id}:{step}:{contract['intent']}:{contract['created_at']}"
        if (not grant or task.trigger != "agent_execution_interrupted"
                or task.workflow_id != state.workflow_id or task.step != step
                or task.iteration != interruption["iteration"] or task.handoff_key != key
                or task.status != HumanTaskStatus.COMPLETED or not retry or retry.id != result_id
                or retry.payload.get("continuation") != step
                or retry.payload.get("decision") not in {"retry", "retry_fresh_session", "retry_configured_order"}):
            return None
        task = records.get_task(grant["task_id"])
        result_id = grant["result_id"]
    elif data.get("trigger") != "need_permission":
        return None
    if not task.staged_commit:
        return None
    result = records.get_result(task.id)
    if (task.workflow_id != state.workflow_id or task.step != step or task.trigger != "need_permission"
            or task.status != HumanTaskStatus.COMPLETED or not result or result.id != result_id
            or result.payload.get("continuation") != step
            or result.payload.get("feedback") != "approve-staged-commit " + task.staged_commit["tree"]):
        raise ValueError("the scoped staged commit approval is absent")
    return task


def _receipt_path(issue, task):
    return issue / task.step / f"iteration_{task.iteration:03}" / f"commit-{task.id}.json"


def _matches_approved_commit(root, oid, expected):
    # Read the actual object, including the entire message and every parent.
    obj = subprocess.run(["git", "--no-replace-objects", "cat-file", "-p", oid], cwd=root, capture_output=True)
    if obj.returncode:
        return False
    header, separator, message = obj.stdout.partition(b"\n\n")
    parents = [line[7:] for line in header.splitlines() if line.startswith(b"parent ")]
    return (bool(separator) and header.split(b"\n", 1)[0] == b"tree " + expected["tree"].encode()
            and parents == [expected["parent"].encode()]
            and message == (expected["operation"]["message"] + "\n").encode())


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
    compare_tree = None
    if receipt.exists():
        record = json.loads(read_regular_bytes(receipt, 4096))
        candidate = record.get("commit")
        if (record.get("task") != task.id or record.get("state") not in {"prepared", "committed"}
                or not isinstance(candidate, str) or len(candidate) not in {40, 64}
                or any(char not in "0123456789abcdef" for char in candidate)
                or not _matches_approved_commit(root, candidate, expected)):
            raise ValueError("scoped commit attempt needs reconciliation at its approved HEAD")
        if head == candidate and _git(root, "rev-parse", "--symbolic-full-name", "HEAD") == expected["ref"]:
            inspect_workspace(root).require_clean()
            return head
        compare_tree = expected["tree"]
        if head != expected["parent"] or record["state"] != "prepared":
            raise ValueError("scoped commit HEAD differs from the approved revision")
    if completed:
        raise ValueError("approved commit is still pending")
    current = snapshot(root, compare_tree=compare_tree)
    changes = [name for name in sorted(current["changes"].keys() | expected["changes"].keys())
               if current["changes"].get(name) != expected["changes"].get(name)]
    fields = [label for key, label in (("parent", "HEAD"), ("ref", "ref"), ("tree", "staged tree"))
              if current[key] != expected[key]]
    if changes or fields:
        raise ValueError("staged commit approval differs: " + ", ".join((fields + changes)[:16])[:1600])
    return None


def provider_environment():
    token = _provider_scope.get()
    return {SCOPE_ENV: token} if token else {}


@contextmanager
def provider_scope(issue, task):
    """Bind the permission to one continuing provider command environment."""
    if task is None:
        yield
        return
    import fcntl
    receipt = _receipt_path(issue, task)
    with receipt.with_suffix(".lease").open("a+b") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        secret = secrets.token_hex(32)
        lease.seek(0)
        lease.truncate()
        lease.write(hashlib.sha256(secret.encode()).digest())
        lease.flush()
        context = _provider_scope.set(secret)
        try:
            yield
        finally:
            _provider_scope.reset(context)
            # Serialize revocation with the final ref transaction. A provider
            # that has exited must not lend its lifetime to queued calls or Git hooks.
            with BlackboardStore(issue).state_transaction():
                lease.truncate(0)
                lease.flush()
                fcntl.flock(lease, fcntl.LOCK_UN)


def _require_owner(issue, task, lease):
    import fcntl
    state = BlackboardStore(issue).load_read_only()
    if active_task(issue, state, task.step) != task:
        raise ValueError("the scoped staged commit approval is no longer active")
    try:
        fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        raise ValueError("the continuing provider is not active")
    lease.seek(0)
    secret = os.environ.get(SCOPE_ENV)
    if not secret or not secrets.compare_digest(hashlib.sha256(secret.encode()).digest(), lease.read(33)):
        raise ValueError("the staged commit requires the continuing provider's execution scope")


def commit(issue, task_id):
    """Called by the provider inside its existing sandbox, never by the workflow host."""
    import fcntl
    issue = Path(issue).resolve()
    root = issue_project_root(issue)
    state = BlackboardStore(issue).load_read_only()
    task = active_task(issue, state, state.current_step)
    if task is None or task.id != task_id or root != Path.cwd().resolve():
        raise ValueError("approval does not own this workflow, step and worktree")
    receipt = _receipt_path(issue, task)
    with receipt.with_suffix(".lock").open("a+b") as lock, receipt.with_suffix(".lease").open("rb") as lease:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _require_owner(issue, task, lease)
        existing = verify(issue, task)
        if existing:
            # An interruption after Git committed may precede index-lock cleanup.
            _release_index(*_index_paths(root, task))
            return existing
        if receipt.exists():
            oid = json.loads(read_regular_bytes(receipt, 4096))["commit"]
        else:
            oid = _commit_tree(root, task, receipt)
        return _promote_candidate(root, task, receipt, oid, lease)


def _known_hook_rejection(trace):
    # Only Git's own pre-publication hook exit can make a failed attempt retryable.
    # Missing or excessive trace evidence leaves the started receipt for reconciliation.
    try:
        records = [json.loads(line) for line in read_regular_bytes(trace, 1024 * 1024).splitlines()]
        session = records[0]["sid"]
        hooks = {row["child_id"] for row in records if row.get("sid") == session
                 and row.get("event") == "child_start" and row.get("child_class") == "hook"
                 and row.get("hook_name") in {"pre-commit", "prepare-commit-msg", "commit-msg"}}
        return any(row.get("sid") == session and row.get("event") == "child_exit"
                   and row.get("child_id") in hooks and 0 < row.get("code", 0) <= 255
                   and row["code"] - 128 not in signal.valid_signals() for row in records)
    except (ValueError, OSError, KeyError, IndexError, TypeError):
        return False


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
        trace = private / "commit.trace"
        proc = subprocess.run(["git", *candidate, "commit", *options, "--cleanup=verbatim", "-m", operation["message"]],
                              cwd=root, capture_output=True, text=True,
                              env={**os.environ, "GIT_TRACE2_EVENT": str(trace)})
        if proc.returncode:
            if (proc.returncode == 1 and _known_hook_rejection(trace)
                    and git.run_git(*candidate, "rev-parse", "HEAD") == expected["parent"]
                    and git.run_git(*candidate, "write-tree") == expected["tree"]
                    and not git.has_in_progress_operation()
                    and _operation(receipt.parents[2], task.step, task.iteration) == operation
                    and all(expected[k] == v for k, v in snapshot(root).items())):
                receipt.unlink()
            raise ValueError("Git commit failed: " + proc.stderr.strip())
        oid = git.run_git(*candidate, "rev-parse", "HEAD")
        if not _matches_approved_commit(root, oid, expected):
            raise ValueError("Git hooks changed the approved candidate")
        current = snapshot(root)
        if any(current[key] != expected[key] for key in current):
            raise ValueError("worktree or staged content changed during the commit")
        atomic_write_bytes(receipt, canonical_json({"task": task.id, "commit": oid, "state": "prepared"}))
        return oid


def _index_paths(root, task):
    index = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    return index.with_name(index.name + ".lock"), index.with_name(f"{index.name}.cafe-{task.id}")


def _release_index(lock, marker):
    if marker.exists():
        if lock.exists() and lock.samefile(marker):
            lock.unlink()
        marker.unlink()


@contextmanager
def _reserve_index(root, task):
    # The operation lock serializes this task. A hard link identifies its own
    # leftover reservation after process loss without removing another Git lock.
    lock, marker = _index_paths(root, task)
    marker.touch(exist_ok=True)
    try:
        try:
            os.link(marker, lock)
        except FileExistsError:
            if not lock.samefile(marker):
                raise ValueError("the Git index is locked by another operation")
        yield
    finally:
        _release_index(lock, marker)


def _promote_candidate(root, task, receipt, oid, lease):
    expected = task.staged_commit
    issue = receipt.parents[2]
    # Updating HEAD by dereference locks both the checkout and its branch while
    # the recorded grant and the exact inputs are checked for the final time.
    with HumanTaskRecordStore(issue).transaction(), BlackboardStore(issue).state_transaction(), _reserve_index(root, task):
        with subprocess.Popen(
            ["git", "update-ref", "-m", "commit: " + expected["operation"]["message"], "--stdin"],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        ) as transaction:
            try:
                for command, response in [
                    ("start", "start: ok"),
                    (f"update HEAD {oid} {expected['parent']}\nprepare", "prepare: ok"),
                ]:
                    transaction.stdin.write(command + "\n")
                    transaction.stdin.flush()
                    if transaction.stdout.readline().strip() != response:
                        raise ValueError("Git could not reserve the approved checkout")
                _require_owner(issue, task, lease)
                if GitOperations(str(root)).has_in_progress_operation():
                    raise ValueError("another Git operation is in progress")
                verify(issue, task)
                if _operation(issue, task.step, task.iteration) != expected["operation"]:
                    raise ValueError("requested operation changed before ref publication")
                transaction.stdin.write("commit\n")
                transaction.stdin.flush()
                if transaction.stdout.readline().strip() != "commit: ok":
                    raise ValueError("Git did not acknowledge the approved ref update")
            finally:
                transaction.stdin.close()
            if transaction.wait():
                raise ValueError("Git failed to publish the approved ref")
    atomic_write_bytes(receipt, canonical_json({"task": task.id, "commit": oid, "state": "committed"}))
    return verify(issue, task, completed=True)
