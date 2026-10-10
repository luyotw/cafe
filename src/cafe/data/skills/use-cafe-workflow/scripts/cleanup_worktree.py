#!/usr/bin/env python3
"""Archive an accepted delivery, then remove its exact feature resources.

Run as the final confirmed cleanup command through execute_closeout.py.
No merge, rebase, checkout, pull, or deployment is performed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from cafe.manager._store import load_contract
from cafe.manager.costs import _archive, validate_issue_identity


def _git(root, *arguments, check=True):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=check, text=True, capture_output=True, timeout=120,
    )


def cleanup(project_root, worktree, issue_name, remote=None):
    """Fail before archiving if source identity, integration, or cleanliness changed."""
    from inspect_delivery_closeout import inspect

    root, target = Path(project_root).resolve(), Path(worktree).resolve()
    if root == target or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", issue_name):
        raise ValueError("full cleanup requires a separate named feature worktree")
    archive = _archive(root, issue_name)
    active = target / ".cafe/issues" / issue_name
    issue = active if active.is_dir() else archive
    contract, _ = load_contract(issue, issue_name=issue_name)
    workflow_id = contract["identity"]["workflow_id"]
    validate_issue_identity(issue, issue_name, workflow_id)
    if Path(contract["checkout"]["path"]).resolve() != target:
        raise ValueError("cleanup target differs from the confirmed checkout")
    selected = inspect(issue, workflow_id, project_root=root)
    if selected["status"] == "accepted":
        if selected["selection"]["choice"] != "cleanup":
            raise ValueError("full cleanup requires the accepted cleanup selection")
    elif selected["status"] != "legacy":
        raise ValueError("delivery acceptance is still pending")
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip() != common:
        raise ValueError("cleanup worktree belongs to another repository")
    if _git(target, "branch", "--show-current").stdout.strip() != issue_name:
        raise ValueError("cleanup worktree is on another branch")
    if _git(target, "status", "--porcelain", "--untracked-files=all").stdout.strip():
        raise ValueError("cleanup worktree has uncommitted or untracked changes")
    source = _git(target, "rev-parse", "HEAD").stdout.strip()
    repository = None
    tasks = json.loads((issue / "human_tasks.json").read_text()).get("tasks", [])
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    outcome = next((t for t in reversed(tasks) if t.get("id") == selected.get("selection", {}).get("task_id")), None)
    if outcome:
        match = re.search(r"Action snapshot SHA256: ([0-9a-f]{64})", outcome["prompt"])
        proposal = json.loads((issue / "delivery" / match[1] / "actions.json").read_text())["proposal"]
        if proposal["source_branch"] != issue_name or proposal["source_oid"] != source:
            raise ValueError("feature tip differs from the accepted delivery")
        report = json.loads((issue / "delivery" / match[1] / "result.json").read_text())
        integration = report["actions"]["integration"]
        if proposal["mode"] == "github":
            repository = proposal["repository"]
            observed = subprocess.run(
                ["gh", "pr", "view", str(proposal["pr_number"]), "--repo", proposal["repository"],
                 "--json", "state,headRefOid,baseRefName,mergeCommit"],
                check=True, text=True, capture_output=True, timeout=120,
            )
            pr = json.loads(observed.stdout)
            if (pr["state"] != "MERGED" or pr["headRefOid"] != source
                    or pr["baseRefName"] != proposal["target_branch"]
                    or pr["mergeCommit"]["oid"] != integration["commit"]):
                raise ValueError("GitHub delivery identity changed")
        elif _git(root, "merge-base", "--is-ancestor", source, integration["commit"], check=False).returncode:
            raise ValueError("local integration does not contain the feature tip")
    elif _git(root, "merge-base", "--is-ancestor", source, "HEAD", check=False).returncode:
        raise ValueError("feature changes are not integrated into the retained checkout")
    remote_tip = None
    push_url = None
    if remote:
        if not remote.strip() or remote.startswith("-"):
            raise ValueError("invalid cleanup remote")
        from cafe.core.git_delivery import remote_configuration

        endpoint = remote_configuration(root, remote, "pr")
        if repository and any(url.removesuffix(".git") not in {
                f"git@github.com:{repository}", f"https://github.com/{repository}",
                f"ssh://git@github.com/{repository}"} for url in endpoint.values()):
            raise ValueError("cleanup remote differs from the accepted GitHub repository")
        push_url = endpoint["push"]
        refs = _git(root, "ls-remote", "--heads", remote, f"refs/heads/{issue_name}").stdout.splitlines()
        if refs:
            if len(refs) != 1 or refs[0].split()[0] != source:
                raise ValueError("remote feature branch differs from the accepted tip")
            remote_tip = source
    # Ensure ordinary branch deletion can succeed; never fall back to -D.
    upstream = _git(root, "rev-parse", "--verify", f"{issue_name}@{{upstream}}", check=False)
    merged_into = upstream.stdout.strip() if upstream.returncode == 0 else "HEAD"
    if _git(root, "merge-base", "--is-ancestor", source, merged_into, check=False).returncode:
        raise ValueError("ordinary branch deletion would refuse this feature tip")
    if active.exists():
        if archive.exists():
            raise ValueError("an existing archive would be overwritten")
        subprocess.run(["cafe", "close", "--archive-only"], cwd=target, check=True, timeout=120)
    validate_issue_identity(archive, issue_name, workflow_id)
    # The archive and cost evidence now survive removal of the source checkout.
    _git(root, "worktree", "remove", str(target))
    _git(root, "branch", "-d", issue_name)
    if remote_tip:
        from cafe.core.git_delivery import pinned_remote_command

        # A lease makes deletion fail if someone advanced the remote after preflight.
        command = pinned_remote_command("push", push_url,
            f"--force-with-lease=refs/heads/{issue_name}:{remote_tip}", f":refs/heads/{issue_name}")
        _git(root, *command[1:])
    return {"status": "completed", "archive_dir": str(archive),
            "worktree": str(target), "branch": issue_name, "remote": remote}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--remote")
    args = parser.parse_args()
    try:
        print(json.dumps(cleanup(args.project_root, args.worktree, args.issue_name, args.remote)))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
