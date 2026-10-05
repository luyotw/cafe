"""Manager-owned resolution of confirmed scope into generic execution inputs."""

from pathlib import Path
import sys

from cafe.core.file_scope import collect_changes, compare_scope, path_content, validate_scope_paths
from cafe.core.workspace_artifact import inspect_workspace
from ._store import load_contract


def prepare_file_scope(root: Path, paths):
    import subprocess

    paths = validate_scope_paths(paths)
    baseline = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    preexisting = []
    for change in inspect_workspace(root).changes:
        for key in ("path", "old_path"):
            if key in change and change[key] not in paths:
                preexisting.append(
                    {"path": change[key], "content": path_content(root, change[key])}
                )
    return {"paths": paths, "baseline_commit": baseline, "preexisting": preexisting}


def execution_scope_projection(issue_dir: Path, root: Path):
    contract, digest = load_contract(issue_dir)
    if contract.get("contract_mode") != "compact":
        return None
    scope = contract["file_scope"]
    return {
        "version": 1,
        "authority_digest": digest,
        "revision": contract["revision"]["generation"],
        "identity": dict(contract["identity"]),
        "root": str(root.resolve()),
        "paths": list(scope["paths"]),
        "baseline_commit": scope["baseline_commit"],
        "preexisting": scope["preexisting"],
        "review_configuration": contract["review_configuration"],
        "delivery_endpoint": contract["delivery_contract"],
        "native_reviewer_type": "cafe_reviewer",
        "checkpoint_command": [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "data/skills/use-cafe-workflow/scripts/check_execution_scope.py"
            ),
            "--issue-dir",
            str(issue_dir.resolve()),
            "--root",
            str(root.resolve()),
        ],
    }


def check_current_scope(issue_dir: Path, root: Path):
    projection = execution_scope_projection(issue_dir, root)
    if projection is None:
        return None
    changes = collect_changes(root, projection["baseline_commit"])
    return compare_scope(changes, projection["paths"], preexisting=projection["preexisting"])
