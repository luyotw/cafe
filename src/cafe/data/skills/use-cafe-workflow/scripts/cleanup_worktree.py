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


def _delivery_branches(issue, selection, workflow_id, issue_name, branch, source):
    """Select only the accepted branch and a receipt-proven original issue branch."""
    from cafe.delivery.contracts import ActionSnapshot
    from cafe.delivery.records import ActionStore

    snapshot_id = (selection or {}).get("snapshot")
    if not snapshot_id:
        if branch != issue_name:
            raise ValueError("cleanup worktree is on another branch")
        return [(branch, source, None, None)]
    directory = issue / "delivery" / snapshot_id
    snapshot = ActionSnapshot.model_validate_json((directory / "actions.json").read_bytes())
    proposal = snapshot.proposal
    if (snapshot.digest != snapshot_id or proposal.workflow_id != workflow_id
            or proposal.source_branch != branch or proposal.source_oid != source):
        raise ValueError("feature tip differs from the accepted delivery")
    report = json.loads((directory / "result.json").read_text())
    receipt = ActionStore(issue, snapshot).read("integration")
    if not receipt or receipt.get("state") != "succeeded" or report["actions"]["integration"] != receipt:
        raise ValueError("accepted integration receipt changed")
    branches = [(branch, source, proposal, receipt)]
    if branch == issue_name:
        return branches
    # inspect_closeout already validates these retained effects against the accepted
    # report. Do not select arbitrary similarly named or historical branches.
    originals = {}
    for manifest in (issue / "delivery").glob("*/actions.json"):
        previous = ActionSnapshot.model_validate_json(manifest.read_bytes())
        p = previous.proposal
        if (p.workflow_id != workflow_id or p.source_branch != issue_name
                or p.mode != proposal.mode or p.repository != proposal.repository
                or p.target_branch != proposal.target_branch or p.destination != proposal.destination):
            continue
        old = ActionStore(issue, previous).read("integration")
        retained = report.get("previous_results", {}).get(f"{previous.digest}:integration")
        if old and old.get("state") == "succeeded" and old == retained:
            originals[p.source_oid] = (issue_name, p.source_oid, p, old)
    # A changed original branch cannot be silently treated as unrelated and deleted.
    # Choose its exact current tip later, before any archive or deletion.
    return branches + list(originals.values())


def _verify_integration(root, source, proposal, receipt):
    if proposal is None:
        if _git(root, "merge-base", "--is-ancestor", source, "HEAD", check=False).returncode:
            raise ValueError("feature changes are not integrated into the retained checkout")
    elif proposal.mode == "github":
        observed = subprocess.run(
            ["gh", "pr", "view", str(proposal.pr_number), "--repo", proposal.repository,
             "--json", "state,headRefOid,headRefName,baseRefName,mergeCommit"],
            check=True, text=True, capture_output=True, timeout=120,
        )
        pr = json.loads(observed.stdout)
        if (pr["state"] != "MERGED" or pr["headRefOid"] != source
                or pr["headRefName"] != proposal.source_branch
                or pr["baseRefName"] != proposal.target_branch
                or pr["mergeCommit"]["oid"] != receipt["commit"]):
            raise ValueError("GitHub delivery identity changed")
    else:
        common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        if Path(proposal.repository).resolve() != Path(common).resolve():
            raise ValueError("local integration belongs to another repository")
        if (_git(root, "merge-base", "--is-ancestor", source, receipt["commit"], check=False).returncode
                or _git(root, "merge-base", "--is-ancestor", receipt["commit"],
                        f"refs/heads/{proposal.target_branch}", check=False).returncode):
            raise ValueError("local integration does not contain the feature tip in its target branch")


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
    branch = _git(target, "branch", "--show-current").stdout.strip()
    if not branch:
        raise ValueError("cleanup requires an attached feature branch")
    if _git(target, "status", "--porcelain", "--untracked-files=all").stdout.strip():
        raise ValueError("cleanup worktree has uncommitted or untracked changes")
    source = _git(target, "rev-parse", "HEAD").stdout.strip()
    candidates = _delivery_branches(issue, selected.get("selection"), workflow_id, issue_name, branch, source)
    repository = candidates[0][2].repository if candidates[0][2] and candidates[0][2].mode == "github" else None
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
    owned = {}
    names = {branch, issue_name}
    for name in sorted(names):
        ref = f"refs/heads/{name}"
        local = _git(root, "rev-parse", "--verify", ref, check=False)
        local_tip = local.stdout.strip() if local.returncode == 0 else None
        refs = _git(root, "ls-remote", "--heads", push_url, ref).stdout.splitlines() if remote else []
        remote_tip = refs[0].split()[0] if len(refs) == 1 else None
        if len(refs) > 1:
            raise ValueError("ambiguous remote feature branch")
        if local_tip is None and remote_tip is None:
            continue
        matching = [c for c in candidates if c[0] == name and c[1] == (local_tip or remote_tip)]
        if len(matching) != 1:
            raise ValueError("feature branch lacks an exact retained integration receipt")
        _, tip, proposal, receipt = matching[0]
        if proposal and name == proposal.target_branch:
            raise ValueError("cannot clean up the integration target branch")
        if remote_tip is not None and remote_tip != tip:
            raise ValueError("remote feature branch differs from the accepted tip")
        _verify_integration(root, tip, proposal, receipt)
        if local_tip:
            # Ensure ordinary deletion can succeed; never fall back to -D.
            upstream = _git(root, "rev-parse", "--verify", f"{name}@{{upstream}}", check=False)
            merged_into = upstream.stdout.strip() if upstream.returncode == 0 else "HEAD"
            if _git(root, "merge-base", "--is-ancestor", tip, merged_into, check=False).returncode:
                raise ValueError("ordinary branch deletion would refuse this feature tip")
        owned[name] = (tip, bool(local_tip), bool(remote_tip))
    for record in _git(root, "worktree", "list", "--porcelain", "-z").stdout.split("\0\0"):
        fields = record.split("\0")
        paths = [f.removeprefix("worktree ") for f in fields if f.startswith("worktree ")]
        branches = [f.removeprefix("branch refs/heads/") for f in fields if f.startswith("branch refs/heads/")]
        if branches and branches[0] in owned and (not paths or Path(paths[0]).resolve() != target):
            raise ValueError("cleanup branch is checked out in another worktree")
    if active.exists():
        if archive.exists():
            raise ValueError("an existing archive would be overwritten")
        subprocess.run(["cafe", "close", "--archive-only", "--issue", issue_name],
                       cwd=target, check=True, timeout=120)
    validate_issue_identity(archive, issue_name, workflow_id)
    # The archive and cost evidence now survive removal of the source checkout.
    if (_git(target, "branch", "--show-current").stdout.strip() != branch
            or _git(target, "rev-parse", "HEAD").stdout.strip() != source
            or _git(target, "status", "--porcelain", "--untracked-files=all").stdout.strip()):
        raise ValueError("cleanup worktree changed during archival")
    for name, (tip, local_exists, _) in owned.items():
        if local_exists and _git(root, "rev-parse", "--verify", f"refs/heads/{name}").stdout.strip() != tip:
            raise ValueError("local feature branch changed during cleanup")
    _git(root, "worktree", "remove", str(target))
    for name, (tip, local_exists, remote_exists) in owned.items():
        if local_exists:
            if _git(root, "rev-parse", "--verify", f"refs/heads/{name}").stdout.strip() != tip:
                raise ValueError("local feature branch changed during cleanup")
            _git(root, "branch", "-d", name)
        if remote_exists:
            from cafe.core.git_delivery import pinned_remote_command

            # A lease makes deletion fail if someone advanced the remote after preflight.
            command = pinned_remote_command("push", push_url,
                f"--force-with-lease=refs/heads/{name}:{tip}", f":refs/heads/{name}")
            _git(root, *command[1:])
    return {"status": "completed", "archive_dir": str(archive),
            "worktree": str(target), "branch": branch, "branches": sorted(owned), "remote": remote}


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
