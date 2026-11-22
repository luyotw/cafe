import hashlib
import json
import shutil
import stat
import subprocess

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.dashboard_publication import prepare_workflow_archive, publish_workflow
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime


def _mark_pairing(state):
    connection_id = "e9302025-7b5b-4cab-af95-4091c945e362"
    marker = state / "publication-pairing.json"
    marker.write_text(json.dumps({"connectionId": connection_id}))
    marker.chmod(0o600)
    return state / f"workflow-publications-v3-{connection_id}"


def test_publishes_committed_summaries_only_after_workflow_start(tmp_path, monkeypatch):
    issue_dir = tmp_path / "checkout" / ".cafe/issues/release/issue1"
    issue_dir.mkdir(parents=True)
    checkout = issue_dir.parents[3].resolve()
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    store = BlackboardStore(issue_dir)
    board = store.load_or_create("develop", playbook_id="direct")
    state = tmp_path / "dashboard"
    monkeypatch.setenv("CAFE_DASHBOARD_STATE_DIR", str(state))
    publish_workflow(issue_dir, board, start=True)
    assert not state.exists()

    state.mkdir(mode=0o700)
    publish_workflow(issue_dir, board, start=True)
    assert list(state.iterdir()) == []
    inbox = _mark_pairing(state)
    inbox.mkdir(mode=0o755)
    publish_workflow(issue_dir, board, start=True)
    assert list(inbox.iterdir()) == []
    inbox.chmod(0o700)

    store.save(board)
    assert list(inbox.iterdir()) == []
    publish_workflow(issue_dir, board, start=True)
    key = hashlib.sha256(f"{checkout}\0release/issue1\0{board.workflow_id}".encode()).hexdigest()
    path = inbox / f"{key}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {
        "checkout": str(checkout), "projectName": "checkout", "archived": False,
        "projectId": hashlib.sha256(str(checkout / ".git").encode()).hexdigest(),
        "workflow": {
            "issue": "release/issue1", "workflowId": board.workflow_id, "playbookId": "direct",
            "step": "develop", "status": "active", "updatedAt": board.updated_at, "pendingTasks": 0,
        },
    }
    mtime = path.stat().st_mtime_ns
    store.save(board)
    assert path.stat().st_mtime_ns == mtime

    develop = issue_dir / "develop"
    (develop / "iteration_002").mkdir(parents=True)
    (develop / "iteration_002" / "iteration.json").write_text("{}")
    (develop / "iteration_003").mkdir()
    (develop / "iteration_003" / "user_input.md").write_text("prepared only")
    board.step_attempt_counts["develop"] = 5
    store.save(board)
    assert json.loads(path.read_text())["workflow"]["iteration"] == 2

    records = HumanTaskRecordStore(issue_dir)
    task = records.materialize(workflow_id=board.workflow_id, step="review", iteration=4,
        trigger="confirm_output", policy_id="review", prompt="private prompt",
        expected_result={}, continuations={"confirm": "done"}, assignee_type="user")
    store.update_handoff_contract(board, from_step="review", to_owner=HandoffOwner.USER,
        to_step="user", intent=HandoffIntent.CONFIRM_OUTPUT)
    store.set_current_step(board, "user")
    waiting = json.loads(path.read_text())["workflow"]
    assert (waiting["step"], waiting["status"], waiting["pendingTasks"]) == ("review", "waiting", 1)
    assert waiting["iteration"] == 4
    assert "private prompt" not in path.read_text()

    records.complete(workflow_id=board.workflow_id, task_id=task.id,
        payload={"decision": "confirm"}, source="test")
    store.set_current_step(board, "done")
    completed = json.loads(path.read_text())["workflow"]
    assert completed["step"] == "done"
    assert (completed["status"], completed["pendingTasks"]) == ("completed", 0)
    assert "iteration" not in completed

    # A different run at the same issue path must not overwrite a pending completion.
    shutil.rmtree(issue_dir)
    replacement = store.load_or_create("develop")
    publish_workflow(issue_dir, replacement, start=True)
    assert len(list(inbox.glob("*.json"))) == 2
    assert json.loads(path.read_text())["workflow"] == completed

    # Archive is emitted by the lifecycle operation, even when the workflow was not finished.
    publish_archive = prepare_workflow_archive(issue_dir)
    shutil.rmtree(issue_dir)
    publish_archive()
    archived, = [json.loads(item.read_text()) for item in inbox.glob("*.json") if item != path]
    assert archived["archived"] is True
    assert archived["workflow"]["workflowId"] == replacement.workflow_id
    assert archived["workflow"]["status"] == "active"


def test_connector_can_appear_after_workflow_start(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    issue_dir = checkout / ".cafe/issues/issue1"
    issue_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    state = tmp_path / "dashboard"
    monkeypatch.setenv("CAFE_DASHBOARD_STATE_DIR", str(state))
    store = BlackboardStore(issue_dir)
    board = store.load_or_create("develop")
    publish_workflow(issue_dir, board, start=True)
    assert not state.exists()
    state.mkdir(mode=0o700)
    inbox = _mark_pairing(state)
    store.record_event(board, "step_started", {"step": "develop"})
    path, = inbox.glob("*.json")
    assert json.loads(path.read_text())["workflow"]["step"] == "develop"


def test_runtime_publishes_before_executing_first_phase(tmp_path, monkeypatch):
    issue_dir = tmp_path / "checkout" / ".cafe/issues/issue1"
    issue_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(tmp_path / "checkout")], check=True)
    state = tmp_path / "dashboard"
    state.mkdir(mode=0o700)
    inbox = _mark_pairing(state)
    monkeypatch.setenv("CAFE_DASHBOARD_STATE_DIR", str(state))
    playbook = {"playbook": {"id": "test"}, "steps": {"develop": {"on": {"completed": "done"}}}}

    observed = []

    def execute(_step, _declaration, board):
        path, = inbox.glob("*.json")
        value = json.loads(path.read_text())["workflow"]
        observed.append((value["issue"], value["workflowId"], value["step"], value["status"]))
        raise RuntimeError("stop after observing first phase")

    runner = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=execute)
    runner.run()
    assert observed == [("issue1", runner.blackboard.workflow_id, "develop", "active")]
