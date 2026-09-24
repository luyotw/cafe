"""User journeys for durable Driver closeout command evidence."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cafe.driver import activate_confirmed_contract
from cafe.driver.delivery import MAX_CLOSEOUT_EVIDENCE_BYTES, maximum_closeout_evidence_size
from tests.unit.test_driver_contract_application import _activation, _proposal

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "src/cafe/data/skills/use-cafe-workflow/scripts/execute_closeout.py"
)


def _journey(
    tmp_path: Path, commands: list[list[str]], *, stage: str = "deliver"
) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "README").write_text("fixture\n")
    subprocess.run(["git", "-C", str(root), "add", "README"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    worktree = tmp_path / "issue-worktree"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-qb", "issue-worktree", str(worktree)],
        check=True,
    )
    issue = worktree / ".cafe/issues/issue474"
    proposal = _proposal()
    proposal["delivery_contract"]["closeout_plan"] = {
        name: [{"argv": argv} for argv in commands] if name == stage else []
        for name in ("deliver", "cleanup")
    }
    activate_confirmed_contract(_activation(issue, proposal))
    return root, issue, root / ".git/cafe/closeout/issue474/workflow-474.json"


def _run(root: Path, issue: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(root),
            "--issue-name",
            "issue474",
            "--workflow-id",
            "workflow-474",
            "--issue-dir",
            str(issue),
            *args,
        ],
        text=True,
        capture_output=True,
    )


def test_closeout_success_and_resume_never_replays(tmp_path: Path) -> None:
    marker = tmp_path / "executions"
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; p=Path('"
        + str(marker)
        + "'); p.write_text(p.read_text()+'x' if p.exists() else 'x')",
    ]
    root, issue, evidence = _journey(tmp_path, [command])
    assert _run(root, issue, "--initialize").returncode == 0
    first = _run(root, issue, "--execute", "--stage", "deliver", "--index", "0")
    assert first.returncode == 0, first.stderr
    assert evidence.is_file() and not evidence.is_relative_to(issue)
    assert json.loads(evidence.read_text())["commands"]["deliver"][0]["status"] == "succeeded"
    second = _run(root, issue, "--execute", "--stage", "deliver", "--index", "0")
    assert second.returncode != 0
    assert marker.read_text() == "x"


def test_closeout_failure_stops_order_and_resume(tmp_path: Path) -> None:
    marker = tmp_path / "later"
    root, issue, evidence = _journey(
        tmp_path,
        [
            [sys.executable, "-c", "raise SystemExit(7)"],
            [sys.executable, "-c", "from pathlib import Path; Path('" + str(marker) + "').touch()"],
        ],
    )
    assert _run(root, issue, "--initialize").returncode == 0
    assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "0").returncode != 0
    assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "1").returncode != 0
    assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "0").returncode != 0
    assert not marker.exists()
    statuses = [row["status"] for row in json.loads(evidence.read_text())["commands"]["deliver"]]
    assert statuses == ["failed", "not_started"]


def test_closeout_unknown_after_interruption_requires_user_direction(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    root, issue, evidence = _journey(
        tmp_path,
        [
            [sys.executable, "-c", "from pathlib import Path; Path('" + str(marker) + "').touch()"],
        ],
    )
    assert _run(root, issue, "--initialize").returncode == 0
    document = json.loads(evidence.read_text())
    document["commands"]["deliver"][0]["status"] = "unknown"
    evidence.write_text(json.dumps(document))
    inspected = _run(root, issue, "--inspect")
    assert inspected.returncode == 0
    assert json.loads(inspected.stdout)["commands"]["deliver"][0]["status"] == "unknown"
    assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "0").returncode != 0
    assert not marker.exists()


def test_closeout_evidence_survives_final_worktree_removal(tmp_path: Path) -> None:
    worktree = tmp_path / "issue-worktree"
    root, issue, evidence = _journey(
        tmp_path,
        [[sys.executable, "-c", f"import shutil; shutil.rmtree({str(worktree)!r})"]],
        stage="cleanup",
    )
    assert _run(root, issue, "--initialize").returncode == 0
    assert _run(root, issue, "--execute", "--stage", "cleanup", "--index", "0").returncode == 0
    assert not worktree.exists()
    result = _run(root, issue, "--inspect")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["commands"]["cleanup"][0]["status"] == "succeeded"
    assert evidence.exists()


def test_closeout_resume_continues_only_not_started_command(tmp_path: Path) -> None:
    marker = tmp_path / "order"
    commands = [
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('first')",
        ],
        [
            sys.executable,
            "-c",
            (
                f"from pathlib import Path; p=Path({str(marker)!r}); "
                "p.write_text(p.read_text()+' second')"
            ),
        ],
    ]
    root, issue, _ = _journey(tmp_path, commands)
    assert _run(root, issue, "--initialize").returncode == 0
    assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "1").returncode != 0
    assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "0").returncode == 0
    assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "1").returncode == 0
    assert marker.read_text() == "first second"


def test_closeout_interrupted_process_leaves_unknown_without_replay(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    root, issue, evidence = _journey(
        tmp_path,
        [
            [
                sys.executable,
                "-c",
                (
                    f"import time; from pathlib import Path; Path({str(marker)!r}).touch(); "
                    "time.sleep(30)"
                ),
            ]
        ],
    )
    assert _run(root, issue, "--initialize").returncode == 0
    process = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(root),
            "--issue-name",
            "issue474",
            "--workflow-id",
            "workflow-474",
            "--issue-dir",
            str(issue),
            "--execute",
            "--stage",
            "deliver",
            "--index",
            "0",
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        assert json.loads(evidence.read_text())["commands"]["deliver"][0]["status"] == "unknown"
        assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "0").returncode != 0
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_closeout_inspection_of_missing_evidence_is_read_only(tmp_path: Path) -> None:
    root, issue, evidence = _journey(tmp_path, [["git", "--version"]])
    assert _run(root, issue, "--inspect").returncode != 0
    assert _run(root, issue, "--execute", "--stage", "deliver", "--index", "0").returncode != 0
    assert not evidence.parent.exists()


def test_confirmed_plan_fits_durable_evidence_at_exact_boundary(tmp_path: Path) -> None:
    commands = [["true", str(index)] for index in range(1500)]
    plan = {
        "deliver": [{"argv": argv} for argv in commands],
        "cleanup": [],
    }
    padding = MAX_CLOSEOUT_EVIDENCE_BYTES - maximum_closeout_evidence_size(plan)
    assert padding > 0
    commands[-1][-1] += "x" * padding
    plan["deliver"][-1]["argv"] = commands[-1]
    assert maximum_closeout_evidence_size(plan) == MAX_CLOSEOUT_EVIDENCE_BYTES

    accepted = tmp_path / "accepted"
    accepted.mkdir()
    root, issue, evidence = _journey(accepted, commands)
    assert _run(root, issue, "--initialize").returncode == 0
    assert evidence.is_file()

    oversized = _proposal()
    oversized["delivery_contract"]["closeout_plan"] = plan
    oversized["delivery_contract"]["closeout_plan"]["deliver"][-1]["argv"][-1] += "x"
    assert maximum_closeout_evidence_size(plan) == MAX_CLOSEOUT_EVIDENCE_BYTES + 1
    rejected = tmp_path / "rejected"
    with pytest.raises(ValueError):
        activate_confirmed_contract(_activation(rejected, oversized))
    assert not (rejected / "driver" / "contract.json").exists()
