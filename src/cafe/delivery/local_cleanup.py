"""Git evidence and checkout validation for cleanup after local integration."""

from pathlib import Path

from cafe.core.git import GitError
from cafe.core.packet_io import read_regular_bytes
from cafe.delivery.contracts import ActionSnapshot

MAX_MANIFEST_BYTES = 1024 * 1024


def has_local_delivery_configuration(issue, config):
    """Detect a configured or recorded local route without granting cleanup authority."""
    if any(isinstance(values, dict) and values.get("mode") == "local" and "destination" in values
           for values in config.values()):
        return True
    # The retained approved route stays authoritative even if issue configuration
    # was removed or changed after delivery.
    manifests = list((issue / "delivery").glob("*/actions.json"))
    if len(manifests) > 100:
        raise ValueError("delivery history requires bounded reconciliation")
    return any(
        ActionSnapshot.model_validate_json(read_regular_bytes(path, MAX_MANIFEST_BYTES))
        .proposal.mode == "local"
        for path in manifests
    )


def _require_ancestry(git, source, target):
    try:
        git.require_integrated({"source_oid": source, "target_oid": target, "remote": None})
    except GitError as exc:
        raise ValueError("feature changes are not integrated into the retained checkout") from exc


def verify_local_receipt(git, proposal, receipt, *, exact_target=False):
    common = git.run_git("rev-parse", "--path-format=absolute", "--git-common-dir")
    if Path(common).resolve() != Path(proposal.repository).resolve():
        raise ValueError("local integration belongs to another repository")
    receipt_target = receipt.get("target_branch")
    if (receipt.get("state") != "succeeded" or not receipt.get("commit")
            or ((receipt_target is not None or exact_target) and receipt_target != proposal.target_branch)):
        raise ValueError("successful local integration receipt is missing")
    integrated = receipt["commit"]
    _require_ancestry(git, proposal.source_oid, integrated)
    target_ref = f"refs/heads/{proposal.target_branch}"
    if exact_target:
        if (git.get_current_branch() != proposal.target_branch
                or git.run_git("rev-parse", "HEAD") != integrated
                or git.run_git("rev-parse", "--verify", target_ref) != integrated):
            raise ValueError("local target differs from the accepted integration tip")
        _require_ancestry(git, proposal.target_oid, integrated)
        if integrated != proposal.target_oid:
            if proposal.strategy == "ff-only":
                if integrated != proposal.source_oid:
                    raise ValueError("fast-forward receipt differs from the reviewed source")
            else:
                parents = git.run_git("rev-list", "--parents", "-n", "1", integrated).split()[1:]
                if parents != [proposal.target_oid, proposal.source_oid]:
                    raise ValueError("merge receipt differs from the reviewed source and target")
        if receipt.get("local_before", {}).get("head", proposal.target_oid) != proposal.target_oid:
            raise ValueError("integration receipt differs from the approved pre-integration target")
    else:
        _require_ancestry(git, integrated, target_ref)
    return integrated


def observe_local_cleanup(source_git, target_git, *, checkout, destination, proposal, receipt):
    for ops, path in ((source_git, checkout), (target_git, destination)):
        if Path(ops.run_git("rev-parse", "--show-toplevel")).resolve() != path:
            raise ValueError("local cleanup checkout identity changed")
        if ops.has_in_progress_operation() or ops.run_git("status", "--porcelain", "--untracked-files=all"):
            raise ValueError("local cleanup requires clean, quiescent checkouts")
    if (source_git.get_current_branch() != proposal.source_branch
            or source_git.run_git("rev-parse", "HEAD") != proposal.source_oid
            or source_git.run_git("rev-parse", "--verify", f"refs/heads/{proposal.source_branch}")
            != proposal.source_oid):
        raise ValueError("local cleanup source differs from the accepted feature tip")
    trees = source_git.list_worktrees()
    if not all(any(Path(tree["path"]).resolve() == path and tree.get("branch") == branch
                   for tree in trees) for path, branch in (
                       (checkout, proposal.source_branch), (destination, proposal.target_branch))):
        raise ValueError("local cleanup checkouts are no longer registered")
    verify_local_receipt(source_git, proposal, receipt)
    return verify_local_receipt(target_git, proposal, receipt, exact_target=True)
