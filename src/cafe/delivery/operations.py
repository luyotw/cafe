"""Fixed Git/GitHub operations with positive observation and bounded child cleanup."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from cafe.core.workspace_lock import workspace_execution_lock
from cafe.delivery.contracts import ActionSnapshot
from cafe.delivery.records import ActionStore


class OperationError(ValueError):
    def __init__(self, code, *, state="blocked", returncode=None):
        super().__init__(code)
        self.state = state
        self.returncode = returncode


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
                "child_timeout", state="unknown", returncode=child.returncode
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


def _local_identity(commands, root, proposal):
    dest = Path(proposal.destination).resolve(strict=True)
    common = commands.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if (
        str(Path(common).resolve()) != proposal.repository
        or commands.git(dest, "rev-parse", "--path-format=absolute", "--git-common-dir") != common
        or commands.git(root, "symbolic-ref", "--short", "HEAD") != proposal.source_branch
        or commands.git(root, "rev-parse", "HEAD") != proposal.source_oid
        or commands.git(dest, "symbolic-ref", "--short", "HEAD") != proposal.target_branch
    ):
        raise OperationError("changed_repository_or_source")
    if commands.git(dest, "status", "--porcelain", "--untracked-files=all"):
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


def _github_pr(commands, root, proposal):
    remote = commands.git(root, "remote", "get-url", "origin").removesuffix(".git")
    if remote not in {
        f"https://github.com/{proposal.repository}",
        f"git@github.com:{proposal.repository}",
    }:
        raise OperationError("changed_repository")
    if (
        commands.git(root, "rev-parse", "HEAD") != proposal.source_oid
        or commands.git(root, "symbolic-ref", "--short", "HEAD") != proposal.source_branch
    ):
        raise OperationError("changed_source")
    data = commands.api(f"repos/{proposal.repository}/pulls/{proposal.pr_number}")
    if (
        not isinstance(data, dict)
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
    if data.get("merged") is True and isinstance(commit, str) and len(commit) == 40:
        return {"state": "succeeded", "commit": commit}
    return None


def _issue_matches(commands, snapshot, item):
    repo = snapshot.proposal.issue_repository
    marker = snapshot.marker(item.id)
    # Paginate the repository listing; search indexing is not proof of absence.
    matches = []
    for page in range(1, 11):
        rows = commands.api(f"repos/{repo}/issues?state=all&per_page=100&page={page}")
        if not isinstance(rows, list):
            raise OperationError("issue_observation_unavailable", state="unknown")
        for row in rows:
            if marker in (row.get("body") or "") and "pull_request" not in row:
                if (
                    row.get("title") != item.title
                    or row.get("body") != item.body + "\n\n" + marker
                    or not str(row.get("html_url", "")).startswith(
                        f"https://github.com/{repo}/issues/"
                    )
                ):
                    raise OperationError("issue_marker_identity_changed", state="unknown")
                matches.append(row)
        if len(rows) < 100:
            if len(matches) > 1:
                raise OperationError("ambiguous_issue_marker", state="unknown")
            return matches[0] if matches else None
    raise OperationError("issue_observation_incomplete", state="unknown")


def execute_action(
    root: Path, issue_dir: Path, snapshot: ActionSnapshot, action: str, *, timeout=120
):
    """Called only by registered host adapters after exact capability approval."""
    commands = Commands(timeout)
    store = ActionStore(issue_dir, snapshot)
    p = snapshot.proposal
    with store.locked():
        prior = store.read(action)
        try:
            if action == "integration" and p.mode == "local":
                with workspace_execution_lock(Path(p.destination)):
                    dest = _local_identity(commands, root, p)
                    result = _local_result(commands, dest, p)
                    if result is None:
                        if prior and prior["state"] != "not_dispatched":
                            raise OperationError("unreconciled_local_attempt", state="unknown")
                        if commands.git(dest, "rev-parse", "HEAD") != p.target_oid:
                            raise OperationError("changed_destination")
                        store.start(action)
                        flag = "--ff-only" if p.strategy == "ff-only" else "--no-ff"
                        _, code = commands.run(
                            ["git", "-C", str(dest), "merge", flag, "--no-edit", p.source_oid],
                            allow_failure=True,
                        )
                        if code:
                            raise OperationError("integration_conflict", returncode=code)
                        result = _local_result(commands, dest, p)
                        if result is None:
                            raise OperationError("integration_unobserved", state="unknown")
            elif action == "integration":
                data = _github_pr(commands, root, p)
                result = _merged(data)
                if result is None:
                    if prior and prior["state"] != "not_dispatched":
                        raise OperationError("unreconciled_merge_attempt", state="unknown")
                    if (
                        data.get("base", {}).get("sha") != p.target_oid
                        or data.get("state") != "open"
                    ):
                        raise OperationError("changed_pr_base")
                    store.start(action)
                    commands.run(
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
                        ]
                    )
                    result = _merged(_github_pr(commands, root, p))
                    if result is None:
                        raise OperationError("merge_pending", state="unknown")
            else:
                item = next((item for item in snapshot.selected if item.id == action), None)
                if item is None:
                    raise OperationError("unselected_issue")
                row = _issue_matches(commands, snapshot, item)
                if row is None:
                    if prior and prior["state"] != "not_dispatched":
                        raise OperationError("unreconciled_issue_attempt", state="unknown")
                    store.start(action)
                    commands.api(
                        f"repos/{p.issue_repository}/issues",
                        payload={
                            "title": item.title,
                            "body": item.body + "\n\n" + snapshot.marker(item.id),
                        },
                    )
                    row = _issue_matches(commands, snapshot, item)
                    if row is None:
                        raise OperationError("issue_unobserved", state="unknown")
                result = {"state": "succeeded", "url": row["html_url"], "proposal_id": item.id}
            store.finish(action, result)
            return result
        except (OperationError, OSError, ValueError) as exc:
            # Once dispatched, uncertainty must never be converted into retry permission.
            attempted = store.read(action)
            state = getattr(exc, "state", "blocked")
            if attempted and attempted["state"] in {"unknown", "succeeded"}:
                state = "unknown"
            result = {
                "state": state,
                "error": str(exc)[:1024],
                "returncode": getattr(exc, "returncode", None),
            }
            if not (attempted and attempted["state"] == "succeeded"):
                store.finish(action, result)
            return result
