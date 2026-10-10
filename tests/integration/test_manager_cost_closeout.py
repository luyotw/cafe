"""I2/I3/U5/U6: verified closeout preference never approves/replays cleanup."""

import json
import shutil
import sys

import pytest

from cafe.manager.costs import preserve_worker_cost
from tests.fixtures.manager_chat import adapter
from tests.unit._kickoff_test_support import SCRIPT_ROOT
from tests.unit.test_closeout_execution import _journey, _run
from tests.unit.test_manager_costs import cost_journey


def removed_journey(tmp_path):
    worktree = tmp_path / "issue-worktree"
    root, issue, evidence = _journey(
        tmp_path,
        [["git", "-C", str(tmp_path / "repo"), "worktree", "remove", "--force", str(worktree)]],
        stage="cleanup",
    )
    assert _run(root, issue, "--initialize").returncode == 0
    assert _run(root, issue, "--execute", "--stage", "cleanup", "--index", "0").returncode == 0
    return root, issue, evidence


def test_verified_cleanup_offers_human_choice_and_no_never_reads_inclusive_costs(
    tmp_path, monkeypatch
):
    root, issue, evidence = removed_journey(tmp_path)
    module = adapter("report_closeout_cost")
    offered = module.closeout_cost(root, "issue474", "workflow-474", issue, locale="zh-TW")
    assert offered["prompt"] == "是否需要計算包含 Manager 的總成本？"
    monkeypatch.setattr(
        module, "inclusive_report", lambda *a, **k: pytest.fail("decline aggregated costs")
    )
    before = evidence.read_bytes()
    assert (
        module.closeout_cost(root, "issue474", "workflow-474", issue, choice="no")["status"]
        == "declined"
    )
    assert evidence.read_bytes() == before


@pytest.mark.parametrize("status", ["not_started", "failed", "unknown"])
def test_incomplete_cleanup_never_offers_cost_choice(tmp_path, status):
    root, issue, evidence = removed_journey(tmp_path)
    data = json.loads(evidence.read_text())
    data["commands"]["cleanup"][0]["status"] = status
    evidence.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        adapter("report_closeout_cost").closeout_cost(root, "issue474", "workflow-474", issue)


def test_yes_is_as_of_unknown_manager_breakdown_and_resume_is_read_only(tmp_path):
    root, issue, evidence = removed_journey(tmp_path)
    module = adapter("report_closeout_cost")
    first = module.closeout_cost(
        root, "issue474", "workflow-474", issue, choice="yes", locale="zh-TW"
    )
    assert first["report"]["combined"]["incomplete"]
    assert first["report"]["captured_at"].endswith("+00:00")
    assert "USD" in first["text"]
    before = evidence.read_bytes()
    assert (
        module.closeout_cost(root, "issue474", "workflow-474", issue, choice="yes")["status"]
        == "reported"
    )
    assert evidence.read_bytes() == before


def test_archive_identity_and_leave_gate(tmp_path, monkeypatch):
    root, issue = cost_journey(tmp_path)
    board = json.loads((issue / "blackboard.json").read_text())
    board["current_step"] = "done"
    (issue / "blackboard.json").write_text(json.dumps(board))
    preserve_worker_cost(root, issue, "topic", "wf")
    from pathlib import Path

    from cafe.manager.costs import _archive

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    archive = _archive(root, "topic")
    archive.parent.mkdir(parents=True)
    shutil.move(issue, archive)
    module = adapter("report_closeout_cost")
    assert (
        module.closeout_cost(root, "topic", "wf", issue, operation="archive", archive_dir=archive)[
            "status"
        ]
        == "offered"
    )
    with pytest.raises(ValueError):
        module.closeout_cost(root, "topic", "wf", issue, operation="leave", archive_dir=archive)
    with pytest.raises(ValueError):
        module.closeout_cost(
            root, "topic", "other", issue, operation="archive", archive_dir=archive
        )


def test_public_report_cli_is_read_only_after_completed_cleanup(tmp_path):
    import subprocess

    root, issue, evidence = removed_journey(tmp_path)
    args = [
        sys.executable,
        str(SCRIPT_ROOT / "report_closeout_cost.py"),
        "--project-root",
        str(root),
        "--issue-dir",
        str(issue),
        "--issue-name",
        "issue474",
        "--workflow-id",
        "workflow-474",
        "--locale",
        "zh-TW",
    ]
    before = {p: p.read_bytes() for p in (root / ".git/cafe").rglob("*") if p.is_file()}
    for choice in ("no", "yes"):
        result = subprocess.run([*args, "--choice", choice], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        if choice == "yes":
            assert output["report"]["combined"]["incomplete"]
            assert "已知小計" in output["text"]
        else:
            assert output["status"] == "declined" and "report" not in output
    assert before == {p: p.read_bytes() for p in (root / ".git/cafe").rglob("*") if p.is_file()}


def test_remote_only_cleanup_checks_closed_issue_without_requiring_worktree_removal(
    tmp_path, monkeypatch
):
    import subprocess

    root, issue, evidence = _journey(
        tmp_path, [["gh", "issue", "close", "42", "--repo", "owner/repo"]], stage="cleanup"
    )
    assert _run(root, issue, "--initialize").returncode == 0
    data = json.loads(evidence.read_text())
    data["commands"]["cleanup"][0].update(status="succeeded", returncode=0)
    evidence.write_text(json.dumps(data))
    module = adapter("report_closeout_cost")
    original = subprocess.run
    state = {"state": "CLOSED"}

    def read_only(command, **kwargs):
        if command[0] == "gh":
            assert command == [
                "gh",
                "issue",
                "view",
                "42",
                "--json",
                "state",
                "--repo",
                "owner/repo",
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps(state), "")
        return original(command, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", read_only)
    assert module.closeout_cost(root, "issue474", "workflow-474", issue)["status"] == "offered"
    state["state"] = "OPEN"
    with pytest.raises(ValueError):
        module.closeout_cost(root, "issue474", "workflow-474", issue)


def test_cleanup_cannot_substitute_an_absent_issue_path(tmp_path):
    root, issue, evidence = _journey(tmp_path, [[sys.executable, "-c", "pass"]], stage="cleanup")
    assert _run(root, issue, "--initialize").returncode == 0
    assert _run(root, issue, "--execute", "--stage", "cleanup", "--index", "0").returncode == 0
    module = adapter("report_closeout_cost")
    with pytest.raises(ValueError, match="target"):
        module.closeout_cost(root, "issue474", "workflow-474", tmp_path / "unrelated-missing")
    assert issue.is_dir()


def test_unknown_custom_effect_requires_existing_recovery_even_after_removal(tmp_path):
    worktree = tmp_path / "issue-worktree"
    root, issue, evidence = _journey(
        tmp_path,
        [[sys.executable, "-c", f"import shutil; shutil.rmtree({str(worktree)!r})"]],
        stage="cleanup",
    )
    assert _run(root, issue, "--initialize").returncode == 0
    assert _run(root, issue, "--execute", "--stage", "cleanup", "--index", "0").returncode == 0
    assert not issue.exists()
    with pytest.raises(ValueError, match="external state"):
        adapter("report_closeout_cost").closeout_cost(root, "issue474", "workflow-474", issue)
