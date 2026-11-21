"""A permission response follows its staged Git state into the producing step."""

import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cafe.core.blackboard import ArtifactEntry, ArtifactKind
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.workspace_artifact import build_workspace_artifact
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration.test_workflow_artifact_correction import CORRECTED, journey
from tests.integration.test_workspace_completion_workflow import git


@pytest.mark.parametrize("drift", [None, "index", "mode", "unstaged", "untracked", "HEAD", "denied", "operation"])
def test_only_approved_staged_work_reaches_the_custom_consumer(journey, drift):
    calls = []

    def act(repo, iteration, call):
        if call == 1:
            (repo / "owned.txt").write_text("reviewed repair\n")
            git(repo, "add", "owned.txt")
            (iteration / "staged_commit_request.json").write_text(json.dumps({
                "message": "repair the fixture", "skip_hooks": False,
            }))
        else:
            assert "commit-staged" in j.manager.calls[-1][1]
            def invoke(_):
                run = subprocess.run(
                    [sys.executable, "-m", "cafe.ui.cli", "task", "commit-staged", task.id, "--issue-dir", str(j.issue)],
                    cwd=repo, capture_output=True, text=True,
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
                )
                assert run.returncode == 0, run.stderr
                return run.stdout.strip()
            with ThreadPoolExecutor(max_workers=2) as pool:
                calls.extend(pool.map(invoke, range(2)))
            # Simulate a stop after the atomic ref update and before its final receipt.
            receipt = next((j.issue / task.step / "iteration_001").glob("commit-*.json"))
            record = json.loads(receipt.read_bytes())
            record["state"] = "prepared"
            receipt.write_text(json.dumps(record))
            assert invoke(None) == calls[0]

    j = journey([(CORRECTED, "need_permission"), CORRECTED], workspace=True,
                workspace_action=act, human="need_permission")
    step = j.playbook["steps"]["inspect_custom"]
    step.update(workspace_input_artifact="parent_tree", input_artifacts=["parent_tree"])
    step["human_tasks"] = [{"trigger": "need_permission", "task_id": "decision",
                            "outcomes": {"submit": "inspect_custom"}}]
    step["allowed_tools"].append("Bash")
    j.runtime = j.reconstruct()
    parent = git(j.repo, "rev-parse", "HEAD").decode().strip()
    artifact = build_workspace_artifact(repo=j.repo, name="parent_tree", base_sha=parent,
                                        head_sha=parent, version=1, producer_step="diagnose_custom")
    path = j.issue / "parent_tree.json"
    path.write_text(json.dumps(artifact.to_dict()))
    j.runtime.blackboard.artifacts["parent_tree"] = ArtifactEntry(
        name="parent_tree", kind=ArtifactKind.WORKSPACE, version=1, updated_by="diagnose_custom",
        path=str(path), base_sha=parent, head_sha=parent, updated_at=artifact.updated_at,
    )
    j.runtime.blackboard_store.save(j.runtime.blackboard)
    j.runtime.run(start_step="inspect_custom")
    records = HumanTaskRecordStore(j.issue)
    task, = records.tasks()
    tree = git(j.repo, "write-tree").decode().strip()
    if drift == "HEAD":
        git(j.repo, "commit", "--allow-empty", "--only", "-m", "intervening")
    if drift in ("index", "unstaged"):
        (j.repo / "owned.txt").write_text("other bytes\n")
        if drift == "index":
            git(j.repo, "add", "owned.txt")
    if drift == "mode":
        git(j.repo, "update-index", "--chmod=+x", "owned.txt")
    if drift == "untracked":
        (j.repo / "unrelated").write_text("retain")
    if drift == "operation":
        request = j.iteration / "staged_commit_request.json"
        request.write_text(json.dumps({"message": "repair the fixture", "skip_hooks": True}))
    before = git(j.repo, "status", "--porcelain=v1"), git(j.repo, "rev-parse", "HEAD")
    answer = "deny" if drift == "denied" else "approve-staged-commit " + tree
    result = apply_human_task_payload(issue_dir=j.issue, playbook_data=j.playbook,
        blackboard=j.runtime.blackboard, from_step=task.step, trigger=task.trigger,
        source="interactive", raw_payload={"human_task_id": task.id, "feedback": answer})
    assert result.target == task.step
    if drift:
        try:
            j.runtime.run()
        except ValueError as exc:
            assert any(word in str(exc) for word in ("owned.txt", "unrelated", "HEAD", "dirty", "approval"))
        assert not calls and not j.manager.deliveries
        assert before == (git(j.repo, "status", "--porcelain=v1"), git(j.repo, "rev-parse", "HEAD"))
    else:
        j.runtime.run()
        assert len(calls) == 2 and calls[0] == calls[1]
        assert git(j.repo, "rev-parse", "HEAD^").decode().strip() == parent
        assert git(j.repo, "rev-parse", "HEAD^{tree}").decode().strip() == tree
        assert not git(j.repo, "status", "--porcelain=v1")
        assert j.manager.deliveries == 1
        assert records.get_result(task.id).task_id == task.id
        assert j.runtime.blackboard.artifacts["custom_snapshot"].head_sha == calls[0]
        assert j.runtime.blackboard.artifacts["parent_tree"].head_sha == parent
