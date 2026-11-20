import hashlib
import json
from pathlib import Path
import stat

from cafe.core.dashboard_publication import announce_workflow
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime


def test_announces_only_with_private_connector_inbox(tmp_path, monkeypatch):
    issue_dir = tmp_path / "checkout" / ".cafe/issues/release/issue1"
    issue_dir.mkdir(parents=True)
    state = tmp_path / "dashboard"
    monkeypatch.setenv("CAFE_DASHBOARD_STATE_DIR", str(state))
    announce_workflow(issue_dir, "workflow")
    assert not state.exists()

    state.mkdir(mode=0o700)
    inbox = state / "workflow-publications-v1"
    inbox.mkdir(mode=0o755)
    announce_workflow(issue_dir, "workflow")
    assert list(inbox.iterdir()) == []
    inbox.chmod(0o700)

    announce_workflow(issue_dir, "workflow")
    checkout = Path(tmp_path).resolve() / "checkout"
    key = hashlib.sha256(f"{checkout}\0release/issue1".encode()).hexdigest()
    path = inbox / f"{key}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {
        "checkout": str(checkout), "issue": "release/issue1", "workflowId": "workflow",
    }
    mtime = path.stat().st_mtime_ns
    announce_workflow(issue_dir, "workflow")
    assert path.stat().st_mtime_ns == mtime
    announce_workflow(issue_dir, "replacement")
    assert json.loads(path.read_text())["workflowId"] == "replacement"


def test_runtime_announces_before_executing_first_phase(tmp_path, monkeypatch):
    issue_dir = tmp_path / "checkout" / ".cafe/issues/issue1"
    issue_dir.mkdir(parents=True)
    state = tmp_path / "dashboard"
    state.mkdir(mode=0o700)
    inbox = state / "workflow-publications-v1"
    inbox.mkdir(mode=0o700)
    monkeypatch.setenv("CAFE_DASHBOARD_STATE_DIR", str(state))
    playbook = {"playbook": {"id": "test"}, "steps": {"develop": {"on": {"completed": "done"}}}}

    observed = []

    def execute(_step, _declaration, board):
        path, = inbox.glob("*.json")
        value = json.loads(path.read_text())
        observed.append((value["issue"], value["workflowId"], board.workflow_id))
        raise RuntimeError("stop after observing first phase")

    runner = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=execute)
    runner.run()
    assert observed == [("issue1", runner.blackboard.workflow_id, runner.blackboard.workflow_id)]
