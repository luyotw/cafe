"""A permission response follows its staged Git state into the producing step."""

import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from cafe.core import staged_commit
from cafe.core.blackboard import ArtifactEntry, ArtifactKind
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.workspace_artifact import build_workspace_artifact
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration.test_workflow_artifact_correction import CORRECTED, journey
from tests.integration.test_workspace_completion_workflow import git


@pytest.mark.parametrize("drift", [None, "index", "mode", "unstaged", "untracked", "HEAD", "denied", "operation",
                                  "restored", "hook_retry", "hook_git_error", "hook_message", "operation_at_publish", "missing_scope", "hook_drift"])
def test_only_approved_staged_work_reaches_the_custom_consumer(journey, drift, monkeypatch):
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
            scope = staged_commit.provider_environment()
            cli = [sys.executable, "-m", "cafe.ui.cli", "task", "commit-staged", task.id, "--issue-dir", str(j.issue)]
            package_path = str(Path(__file__).resolve().parents[2] / "src")

            def command(env):
                return subprocess.run(
                    cli, cwd=repo, capture_output=True, text=True, timeout=30,
                    env={**os.environ, **env, "PYTHONPATH": package_path},
                )
            def invoke(_):
                run = command(scope)
                assert run.returncode == 0, run.stderr
                return run.stdout.strip()
            if drift == "missing_scope":
                rejected = command({})
                assert rejected.returncode and git(repo, "rev-parse", "HEAD").decode().strip() == parent
                # Substitute only the external provider binary. The actual executor
                # must forward the scope into its child before that child can commit.
                from cafe.agents.executor import AgentExecutor
                from cafe.core.types import AgentCLI, AgentConfig
                executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.CODEX))
                script = (
                    "import subprocess,sys,json\n"
                    "subprocess.run(sys.argv[1:], check=True, stdout=subprocess.PIPE)\n"
                    "print(json.dumps({'type':'turn.completed', 'usage':{}}))"
                )
                monkeypatch.setattr(executor, "_build_controlled_command", lambda *args, **kwargs:
                                    ([sys.executable, "-c", script, *cli], str(repo)))
                executor.execute("Complete the approved operation", environment_overrides={"PYTHONPATH": package_path})
                assert git(repo, "rev-parse", "HEAD").decode().strip() != parent
            if call == 2 and drift in {"hook_retry", "hook_git_error", "hook_message", "operation_at_publish", "hook_drift"}:
                hooks = j.issue / "hooks"
                hooks.mkdir()
                git(repo, "config", "core.hooksPath", str(hooks))
                hook = hooks / "pre-commit"
                request_path = j.issue / task.step / "iteration_001" / "staged_commit_request.json"
                hook_body = "exit 1" if drift == "hook_retry" else (
                    "echo " + shlex.quote(json.dumps({"message": "changed", "skip_hooks": False}))
                    + " > " + shlex.quote(str(request_path)))
                if drift == "hook_drift":
                    hook_body = "touch unrelated\nexit 1"
                if drift == "hook_git_error":
                    hook_body = "exec git rev-parse --verify missing-fixture-revision"
                if drift == "hook_message":
                    hook = hooks / "commit-msg"
                    hook_body = 'echo "Normalized subject" > "$1"'
                hook.write_text("#!/bin/sh\n" + hook_body + "\n")
                hook.chmod(0o755)
                assert command(scope).returncode
                assert git(repo, "rev-parse", "HEAD").decode().strip() == parent
                assert git(repo, "write-tree").decode().strip() == tree
                hook.unlink()
                if drift == "hook_message":
                    calls.append("changed message refused")
                    return
                if drift in {"hook_retry", "hook_git_error"}:
                    raise RuntimeError("Git hook rejected the staged operation")
                if drift == "hook_drift":
                    unrelated = repo / "unrelated"
                    assert unrelated.exists()
                    unrelated.unlink()
                    assert command(scope).returncode
                    calls.append("reconciliation required")
                    return
                if drift == "operation_at_publish":
                    request_path.write_text(json.dumps(task.staged_commit["operation"]))
            with ThreadPoolExecutor(max_workers=2) as pool:
                calls.extend(pool.map(invoke, range(2)))
            # Simulate a stop after the atomic ref update and before its final receipt.
            receipt = next((j.issue / task.step / "iteration_001").glob("commit-*.json"))
            record = json.loads(receipt.read_bytes())
            record["state"] = "prepared"
            receipt.write_text(json.dumps(record))
            assert invoke(None) == calls[0]
            # A stop after saving the candidate but before moving the approved ref.
            git(repo, "update-ref", "HEAD", parent, calls[0])
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
    if drift in ("index", "unstaged", "restored"):
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
    if drift in {"restored", "hook_retry", "hook_git_error"}:
        j.runtime.run()
        retry, = [t for t in records.tasks() if t.status.value == "pending"]
        assert retry.trigger == "agent_execution_interrupted"
        if drift == "restored":
            (j.repo / "owned.txt").write_text("reviewed repair\n")
        apply_human_task_payload(issue_dir=j.issue, playbook_data=j.playbook,
            blackboard=j.runtime.blackboard, from_step=retry.step, trigger=retry.trigger,
            source="interactive", raw_payload={"human_task_id": retry.id, "task": retry.policy_id, "decision": "retry"})
    if drift == "hook_message":
        j.runtime.run()
        assert calls == ["changed message refused"] and not j.manager.deliveries
        assert before == (git(j.repo, "status", "--porcelain=v1"), git(j.repo, "rev-parse", "HEAD"))
    elif drift == "hook_drift":
        j.runtime.run()
        assert calls == ["reconciliation required"]
        assert not j.manager.deliveries
        assert before == (git(j.repo, "status", "--porcelain=v1"), git(j.repo, "rev-parse", "HEAD"))
    elif drift in {"index", "mode", "unstaged", "untracked", "HEAD", "denied", "operation"}:
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


def test_provider_exit_does_not_wait_for_private_candidate_preparation(tmp_path):
    """Revocation only needs to serialize with publication, even if Git is still busy."""
    import fcntl
    from threading import Event
    from types import SimpleNamespace

    task = SimpleNamespace(id="fixture-grant", step="repair", iteration=1)
    iteration = tmp_path / task.step / "iteration_001"
    iteration.mkdir(parents=True)
    preparing = Event()
    finish = Event()

    def prepare_candidate():
        # Model a commit already holding the command lock during a Git hook.
        with (iteration / "commit-fixture-grant.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            preparing.set()
            finish.wait(3)

    def provider():
        with staged_commit.provider_scope(tmp_path, task):
            assert preparing.wait(3)

    with ThreadPoolExecutor(max_workers=2) as workers:
        candidate = workers.submit(prepare_candidate)
        try:
            workers.submit(provider).result(timeout=2)
        finally:
            finish.set()
        candidate.result(timeout=3)
