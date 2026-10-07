"""Fixed Git/GitHub operations with positive observation and bounded child cleanup."""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from cafe.delivery.contracts import ActionSnapshot, digest
from cafe.delivery.records import ActionStore


class OperationError(ValueError):
    def __init__(self, code, *, state="blocked", returncode=None, timed_out=False):
        super().__init__(code)
        self.state = state
        self.returncode = returncode
        self.timed_out = timed_out


class Commands:
    """One aggregate deadline, including observation, child exit and cleanup."""

    def __init__(self, timeout=120):
        self.deadline = time.monotonic() + min(timeout, 180)

    def run(self, argv, *, cwd=None, input=None, allow_failure=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise OperationError("batch_deadline", state="unknown")
        try:
            child = subprocess.Popen(
                argv,
                cwd=cwd,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise OperationError("tool_unavailable", state="not_dispatched") from exc
        try:
            stdout, _ = child.communicate(input, timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=5)
            raise OperationError(
                "child_timeout", state="unknown", returncode=child.returncode, timed_out=True
            ) from exc
        except BaseException:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=5)
            raise
        if child.returncode and not allow_failure:
            raise OperationError("command_failed", state="unknown", returncode=child.returncode)
        if len(stdout.encode()) > 1024 * 1024:
            raise OperationError(
                "observation_oversized", state="unknown", returncode=child.returncode
            )
        return stdout.strip(), child.returncode

    def git(self, root, *args):
        return self.run(["git", "-C", str(root), *args])[0]

    def api(self, endpoint, *, payload=None):
        argv = ["gh", "api", endpoint]
        raw = None
        if payload is not None:
            argv += ["--method", "POST", "--input", "-"]
            raw = json.dumps(payload)
        try:
            return json.loads(self.run(argv, input=raw)[0])
        except (TypeError, json.JSONDecodeError) as exc:
            raise OperationError("malformed_observation", state="unknown") from exc


@contextmanager
def _destination_lock(destination):
    """Use the existing workspace lease file without unbounded lock waiting."""
    directory = Path(destination) / ".cafe"
    directory.mkdir(exist_ok=True)
    with (directory / "workspace-use.lock").open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OperationError("destination_in_use") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _local_identity(commands, root, proposal, *, reconcile_only=False):
    dest = Path(proposal.destination).resolve(strict=True)
    common = commands.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if (
        str(Path(common).resolve()) != proposal.repository
        or commands.git(dest, "rev-parse", "--path-format=absolute", "--git-common-dir") != common
        or (
            not reconcile_only
            and (
                commands.git(root, "symbolic-ref", "--short", "HEAD") != proposal.source_branch
                or commands.git(root, "rev-parse", "HEAD") != proposal.source_oid
            )
        )
        or commands.git(dest, "symbolic-ref", "--short", "HEAD") != proposal.target_branch
    ):
        raise OperationError("changed_repository_or_source")
    if not reconcile_only and commands.git(root, "status", "--porcelain", "--untracked-files=all"):
        raise OperationError("dirty_source")
    if not reconcile_only and commands.git(dest, "status", "--porcelain", "--untracked-files=all"):
        raise OperationError("dirty_destination")
    return dest


def _local_result(commands, dest, proposal):
    head = commands.git(dest, "rev-parse", "HEAD")
    _, code = commands.run(
        ["git", "-C", str(dest), "merge-base", "--is-ancestor", proposal.source_oid, head],
        allow_failure=True,
    )
    if code == 0:
        return {"state": "succeeded", "commit": head, "target_branch": proposal.target_branch}
    if code != 1:
        raise OperationError("ancestry_unavailable", state="unknown", returncode=code)
    return None


def _local_evidence(commands, dest):
    pending = []
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        path = Path(commands.git(dest, "rev-parse", "--git-path", name))
        if (path if path.is_absolute() else dest / path).exists():
            pending.append(name)
    return {
        "head": commands.git(dest, "rev-parse", "HEAD"),
        "tree": commands.git(dest, "rev-parse", "HEAD^{tree}"),
        "clean": not commands.git(dest, "status", "--porcelain", "--untracked-files=all"),
        "pending": pending,
    }


def _settled_local_attempt(commands, dest, prior, proposal):
    """A failed child alone is insufficient: require the exact pristine pre-effect state."""
    before = prior.get("local_before")
    process = prior.get("process", {})
    code = process.get("returncode")
    if (
        process.get("status") != "exited"
        or process.get("timed_out") is not False
        or not isinstance(code, int)
        or isinstance(code, bool)
        or code <= 0
    ):
        return None
    if before is None:
        # Previous adapter versions checked this exact pinned HEAD and clean destination
        # before dispatch, but did not persist the tree. Retain their recorded authority.
        before = {
            "head": proposal.target_oid,
            "tree": commands.git(dest, "rev-parse", proposal.target_oid + "^{tree}"),
            "clean": True,
            "pending": [],
        }
    if not before.get("clean") or before.get("pending"):
        return None
    after = _local_evidence(commands, dest)
    if after != before:
        return None
    return {
        "state": "settled",
        "local_before": before,
        "settlement": {"reason": "verified_failed_no_effect", "before": before, "after": after},
        "process": process,
    }


def _github_pr(commands, root, proposal, *, reconcile_only=False):
    remote = commands.git(root, "remote", "get-url", "origin").removesuffix(".git")
    if remote not in {
        f"https://github.com/{proposal.repository}",
        f"git@github.com:{proposal.repository}",
    }:
        raise OperationError("changed_repository")
    if not reconcile_only and (
        commands.git(root, "rev-parse", "HEAD") != proposal.source_oid
        or commands.git(root, "symbolic-ref", "--short", "HEAD") != proposal.source_branch
    ):
        raise OperationError("changed_source")
    if not reconcile_only and commands.git(root, "status", "--porcelain", "--untracked-files=all"):
        raise OperationError("dirty_source")
    data = commands.api(f"repos/{proposal.repository}/pulls/{proposal.pr_number}")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("head"), dict)
        or not isinstance(data.get("base"), dict)
        or not isinstance(data.get("base", {}).get("repo"), dict)
        or data.get("number") != proposal.pr_number
        or data.get("head", {}).get("sha") != proposal.source_oid
        or data.get("head", {}).get("ref") != proposal.source_branch
        or data.get("base", {}).get("ref") != proposal.target_branch
        or data.get("base", {}).get("repo", {}).get("full_name") != proposal.repository
    ):
        raise OperationError("changed_pr_identity")
    return data


def _merged(data):
    commit = data.get("merge_commit_sha")
    if (
        data.get("merged") is True
        and isinstance(commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", commit)
    ):
        return {"state": "succeeded", "commit": commit}
    return None


def _issue_identity(row, repo):
    if not isinstance(row, dict) or "pull_request" in row:
        raise OperationError("issue_marker_identity_changed", state="unknown")
    url = str(row.get("html_url", ""))
    match = re.fullmatch(f"https://github.com/{re.escape(repo)}/issues/([1-9][0-9]*)", url)
    if not match or row.get("number", int(match[1])) != int(match[1]):
        raise OperationError("issue_marker_identity_changed", state="unknown")
    if "number" in row and (not isinstance(row["number"], int) or isinstance(row["number"], bool)):
        raise OperationError("issue_marker_identity_changed", state="unknown")
    return {"number": int(match[1]), "url": url}


def _verify_issue(row, snapshot, item):
    identity = _issue_identity(row, snapshot.proposal.issue_repository)
    if row.get("title") != item.title or row.get("body") != item.body + "\n\n" + snapshot.marker(
        item.id
    ):
        raise OperationError("issue_marker_identity_changed", state="unknown")
    return identity


def _known_issue(commands, snapshot, item, identity):
    repo = snapshot.proposal.issue_repository
    number = identity.get("number") if isinstance(identity, dict) else None
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or number < 1
        or identity.get("url") != f"https://github.com/{repo}/issues/{number}"
    ):
        raise OperationError("issue_marker_identity_changed", state="unknown")
    row = commands.api(f"repos/{repo}/issues/{identity['number']}")
    if _verify_issue(row, snapshot, item) != identity:
        raise OperationError("issue_marker_identity_changed", state="unknown")
    return row


def _issue_matches(commands, snapshot, item, store, prior):
    """Persist at most ten pages per invocation; an incomplete scan never permits replay."""
    repo = snapshot.proposal.issue_repository
    marker = snapshot.marker(item.id)
    scan = (prior or {}).get("issue_observation") or {
        "next_page": 1,
        "matches": [],
        "marker": marker,
    }
    if (
        scan.get("marker") != marker
        or not isinstance(scan.get("next_page"), int)
        or isinstance(scan.get("next_page"), bool)
        or scan["next_page"] < 1
        or not isinstance(scan.get("matches"), list)
        or len(scan["matches"]) > 2
    ):
        raise OperationError("invalid_issue_observation", state="unknown")
    matches = list(scan["matches"])
    observed = {}
    if len(matches) > 1:
        raise OperationError("ambiguous_issue_marker", state="unknown")
    budget = 10

    def endpoint(page):
        return f"repos/{repo}/issues?state=all&per_page=100&page={page}&sort=created&direction=asc"

    if scan["next_page"] > 1:
        anchor = scan.get("anchor", {})
        if anchor.get("page") != scan["next_page"] - 1:
            raise OperationError("invalid_issue_observation", state="unknown")
        rows = commands.api(endpoint(anchor["page"]))
        budget -= 1
        if digest(rows) != anchor.get("sha256"):
            # Deletions or edits must not silently shift pagination past a possible duplicate.
            scan = {"next_page": 1, "matches": [], "marker": marker}
            matches = []
    for page in range(scan["next_page"], scan["next_page"] + budget):
        # Creation order is stable as new issues/PRs append, unlike updated-time ordering.
        rows = commands.api(endpoint(page))
        if not isinstance(rows, list) or len(rows) > 100:
            raise OperationError("issue_observation_unavailable", state="unknown")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("body") or "", str):
                raise OperationError("issue_observation_unavailable", state="unknown")
            if marker in (row.get("body") or "") and "pull_request" not in row:
                identity = _verify_issue(row, snapshot, item)
                matches.append(identity)
                observed[identity["number"]] = row
                if len(matches) > 1:
                    store.finish(
                        item.id,
                        {
                            "state": (prior or {}).get("state", "not_dispatched"),
                            "issue_observation": {
                                "next_page": page,
                                "matches": matches,
                                "marker": marker,
                            },
                        },
                    )
                    raise OperationError("ambiguous_issue_marker", state="unknown")
        scan = {
            "next_page": page + 1,
            "matches": matches,
            "marker": marker,
            "anchor": {"page": page, "sha256": digest(rows)},
        }
        store.finish(
            item.id,
            {
                "state": (prior or {}).get("state", "not_dispatched"),
                "issue_observation": scan,
                **({"process": prior["process"]} if prior and "process" in prior else {}),
            },
        )
        if len(rows) < 100:
            # Clear the cursor only after complete observation. Exact identity is verified directly.
            store.finish(
                item.id,
                {"state": (prior or {}).get("state", "not_dispatched"), "issue_observation": None},
            )
            if not matches:
                return None
            return observed.get(matches[0]["number"]) or _known_issue(
                commands, snapshot, item, matches[0]
            )
    raise OperationError("issue_observation_incomplete", state="unknown")


def _dispatch(commands, store, action, argv, **kwargs):
    """Persist mutation exit evidence before any separately observed outcome."""
    try:
        output, code = commands.run(argv, **kwargs)
    except OperationError as exc:
        store.finish(
            action,
            {
                "state": "unknown",
                "process": {
                    "status": "exited" if exc.returncode is not None else "not_started",
                    "returncode": exc.returncode,
                    "timed_out": exc.timed_out,
                    "error": str(exc),
                },
            },
        )
        raise
    store.finish(
        action,
        {
            "state": "unknown",
            "process": {"status": "exited", "returncode": code, "timed_out": False},
        },
    )
    return output, code


def _process_evidence(store, action, prior):
    receipt = store.read(action) or prior
    if receipt and "process" in receipt:
        return receipt["process"]
    # A lost receipt after dispatch is different from an observed preexisting effect.
    status = "unavailable" if receipt else "not_dispatched"
    return {"status": status, "returncode": None, "timed_out": None}


def execute_action(
    root: Path,
    issue_dir: Path,
    snapshot: ActionSnapshot,
    action: str,
    *,
    timeout=120,
    observe_only=False,
):
    """Mutate only through approved adapters; recovery/observe_only paths are read-only."""
    commands = Commands(timeout)
    store = ActionStore(issue_dir, snapshot)
    p = snapshot.proposal
    with store.locked():
        prior = store.read(action) or store.correlated_attempt(action)
        if prior and prior["state"] == "settled":
            return prior
        reconcile_only = bool(prior and prior["state"] in {"unknown", "succeeded", "settled"})
        try:
            if action == "integration" and p.mode == "local":
                with _destination_lock(p.destination):
                    dest = _local_identity(commands, root, p, reconcile_only=reconcile_only)
                    result = _local_result(commands, dest, p)
                    if result is None:
                        if prior and prior["state"] not in {"not_dispatched", "blocked"}:
                            settled = _settled_local_attempt(commands, dest, prior, p)
                            if settled:
                                store.finish(action, settled)
                                return store.read(action)
                            raise OperationError("unreconciled_local_attempt", state="unknown")
                        if commands.git(dest, "rev-parse", "HEAD") != p.target_oid:
                            raise OperationError("changed_destination")
                        before = _local_evidence(commands, dest)
                        if before["pending"]:
                            raise OperationError("unfinished_destination_operation")
                        store.start(action, local_before=before)
                        flag = "--ff-only" if p.strategy == "ff-only" else "--no-ff"
                        _, code = _dispatch(
                            commands,
                            store,
                            action,
                            ["git", "-C", str(dest), "merge", flag, "--no-edit", p.source_oid],
                            allow_failure=True,
                        )
                        result = _local_result(commands, dest, p)
                        if result is None:
                            if code:
                                raise OperationError("integration_conflict", returncode=code)
                            raise OperationError("integration_unobserved", state="unknown")
            elif action == "integration":
                data = _github_pr(commands, root, p, reconcile_only=reconcile_only)
                result = _merged(data)
                if result is None:
                    if prior and prior["state"] not in {"not_dispatched", "blocked"}:
                        raise OperationError("unreconciled_merge_attempt", state="unknown")
                    if (
                        data.get("base", {}).get("sha") != p.target_oid
                        or data.get("state") != "open"
                    ):
                        raise OperationError("changed_pr_base")
                    store.start(action)
                    _dispatch(
                        commands,
                        store,
                        action,
                        [
                            "gh",
                            "pr",
                            "merge",
                            str(p.pr_number),
                            "--repo",
                            p.repository,
                            "--" + p.strategy,
                            "--match-head-commit",
                            p.source_oid,
                        ],
                    )
                    result = _merged(_github_pr(commands, root, p))
                    if result is None:
                        raise OperationError("merge_pending", state="unknown")
            else:
                item = next((item for item in snapshot.selected if item.id == action), None)
                if item is None:
                    raise OperationError("unselected_issue")
                identity = (prior or {}).get("issue_identity")
                row = (
                    _known_issue(commands, snapshot, item, identity)
                    if identity
                    else (
                        None
                        if prior
                        and prior.get("issue_observation_ready")
                        and prior["state"] in {"not_dispatched", "blocked"}
                        and not observe_only
                        else _issue_matches(commands, snapshot, item, store, prior)
                    )
                )
                if row is None:
                    if prior and prior["state"] not in {"not_dispatched", "blocked"}:
                        raise OperationError("unreconciled_issue_attempt", state="unknown")
                    if observe_only:
                        result = {"state": "not_dispatched", "issue_observation_ready": True}
                        store.finish(action, result)
                        return store.read(action)
                    store.start(action)
                    response, _ = _dispatch(
                        commands,
                        store,
                        action,
                        [
                            "gh",
                            "api",
                            f"repos/{p.issue_repository}/issues",
                            "--method",
                            "POST",
                            "--input",
                            "-",
                        ],
                        input=json.dumps(
                            {
                                "title": item.title,
                                "body": item.body + "\n\n" + snapshot.marker(item.id),
                            }
                        ),
                    )
                    try:
                        identity = _verify_issue(json.loads(response), snapshot, item)
                    except json.JSONDecodeError as exc:
                        raise OperationError("malformed_observation", state="unknown") from exc
                    # Retain the positive creation response before another I/O boundary.
                    store.finish(
                        action,
                        {
                            "state": "unknown",
                            "issue_identity": identity,
                            "process": _process_evidence(store, action, prior),
                        },
                    )
                    row = _known_issue(commands, snapshot, item, identity)
                identity = _verify_issue(row, snapshot, item)
                result = {
                    "state": "succeeded",
                    "url": identity["url"],
                    "number": identity["number"],
                    "proposal_id": item.id,
                    "issue_identity": identity,
                }
            result["process"] = _process_evidence(store, action, prior)
            store.finish(action, result)
            return store.read(action)
        except (OperationError, OSError, ValueError) as exc:
            # Once dispatched, uncertainty must never be converted into retry permission.
            attempted = store.read(action)
            state = getattr(exc, "state", "blocked")
            if (attempted and attempted["state"] in {"unknown", "succeeded"}) or (
                prior and prior["state"] in {"unknown", "succeeded"}
            ):
                state = "unknown"
            elif state == "unknown":
                state = "not_dispatched"
            result = {
                "state": state,
                "error": str(exc)[:1024],
                "returncode": getattr(exc, "returncode", None),
                "process": _process_evidence(store, action, prior),
            }
            if not (attempted and attempted["state"] == "succeeded"):
                store.finish(action, result)
            return result
