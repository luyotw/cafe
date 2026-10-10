"""Manager-owned resolution of confirmed scope into generic execution inputs."""

from pathlib import Path
import sys

from cafe.core.file_scope import collect_changes, compare_scope, workspace_content, git_content_entries, validate_scope_paths
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
    entries = git_content_entries(root)
    for change in inspect_workspace(root).changes:
        for key in ("path", "old_path"):
            if key in change and change[key] not in paths:
                preexisting.append(
                    {"path": change[key], "content": workspace_content(root, change[key], index_entries=entries)}
                )
    return {"paths": paths, "baseline_commit": baseline, "preexisting": preexisting}


def execution_scope_projection(issue_dir: Path, root: Path):
    contract, digest = load_contract(issue_dir)
    if contract.get("contract_mode") != "compact":
        return None
    from cafe.catalogs.resolver import CatalogResolver, CatalogKind
    from cafe.core.playbook import load_playbook_file, execution_graph_digest
    from cafe.skills.loader import SkillLoader

    resolver = CatalogResolver(project_root=root, read_only=True)
    entry = resolver.resolve(CatalogKind.PLAYBOOK, contract["execution"]["playbook_id"])
    if entry is None:
        raise ValueError("confirmed execution graph is unavailable")
    graph = load_playbook_file(entry.path, source=entry.source,
        skill_loader=SkillLoader(project_root=root, read_only=True), strict=True).model.model_dump(mode="json")
    if execution_graph_digest(graph) != contract["execution"]["graph_digest"]:
        raise ValueError("execution graph differs from confirmed authority")
    review_policy = next((step["execution"]["review_policy"] for step in graph["steps"].values()
                          if step["execution"]["review_policy"]), None)
    declared = contract["execution"]
    if "review_policy" in declared and declared["review_policy"] != review_policy:
        raise ValueError("confirmed review policy differs from the declared graph")
    if review_policy == "single_native":
        import subprocess
        from cafe.agents.executor import validate_native_review_projection
        review = contract["review_configuration"]
        observed = subprocess.run([review["cli"], "--version"], capture_output=True,
            text=True, timeout=10, check=True).stdout.strip()
        if observed != review["provider_version"]:
            raise ValueError("confirmed native provider version changed")
        validate_native_review_projection(
            {phase["name"]: phase["chain"] for phase in contract["phases"]},
            [name for name, step in graph["steps"].items() if step["execution"]["review_policy"]],
            review, working_directory=root,
        )
    from ._execution_projection import execution_inputs
    from cafe.core.execution_artifacts import bounded_execution_json
    context = execution_inputs(contract, identity=contract["identity"],
        revision=contract["revision"]["generation"], digest=digest,
        root=str(root.resolve()), review_policy=review_policy,
        checkpoint_command=[
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "data/skills/use-cafe-workflow/scripts/check_execution_scope.py"
            ),
            "--issue-dir",
            str(issue_dir.resolve()),
            "--root",
            str(root.resolve()),
        ])
    bounded_execution_json(context)
    return context


def check_current_scope(issue_dir: Path, root: Path):
    projection = execution_scope_projection(issue_dir, root)
    if projection is None:
        return None
    changes = collect_changes(root, projection["baseline_commit"])
    return compare_scope(changes, projection["paths"], preexisting=projection["preexisting"])
