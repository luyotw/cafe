"""Tests for Driver-side execution of exact confirmed closeout argv arrays."""

from __future__ import annotations

import importlib.util
import json
import multiprocessing
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.driver import activate_confirmed_contract
from tests.unit.test_driver_contract_application import _activation, _proposal

SCRIPT = (
    Path(__file__).parents[2]
    / "src/cafe/data/skills/use-cafe-workflow/scripts/execute_closeout_plan.py"
)
spec = importlib.util.spec_from_file_location("execute_closeout_plan", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _plan() -> dict[str, object]:
    return {
        "deliver": [
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('deliver-receipt.txt').write_text('done')",
                ]
            }
        ],
        "cleanup": [
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('cleanup-receipt.txt').write_text('done')",
                ]
            }
        ],
    }


def _execute_stage_in_process(
    closeout_plan: dict[str, object],
    issue_worktree: str,
    receipt_file: str,
    results: object,
) -> None:
    try:
        module.execute_stage(
            closeout_plan=closeout_plan,
            contract_sha256="a" * 64,
            stage="deliver",
            issue_worktree=Path(issue_worktree),
            receipt_file=Path(receipt_file),
        )
    except Exception as exc:  # pragma: no cover - asserted by the parent process
        results.put(f"{type(exc).__name__}: {exc}")
    else:
        results.put("ok")


def test_execute_closeout_stages_in_order_and_persists_receipts(tmp_path: Path) -> None:
    issue_worktree = tmp_path / "issue-worktree"
    issue_worktree.mkdir()
    receipt = tmp_path / "driver-receipts" / "closeout.json"
    plan = _plan()

    module.execute_stage(
        closeout_plan=plan,
        contract_sha256="a" * 64,
        stage="deliver",
        issue_worktree=issue_worktree,
        receipt_file=receipt,
    )
    module.execute_stage(
        closeout_plan=plan,
        contract_sha256="a" * 64,
        stage="cleanup",
        issue_worktree=issue_worktree,
        receipt_file=receipt,
    )

    assert (issue_worktree / "deliver-receipt.txt").read_text(encoding="utf-8") == "done"
    assert (issue_worktree / "cleanup-receipt.txt").read_text(encoding="utf-8") == "done"
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert [record["status"] for record in saved["stages"]["deliver"]] == ["succeeded"]
    assert [record["status"] for record in saved["stages"]["cleanup"]] == ["succeeded"]


def test_cleanup_requires_recorded_delivery(tmp_path: Path) -> None:
    issue_worktree = tmp_path / "issue-worktree"
    issue_worktree.mkdir()
    receipt = tmp_path / "closeout.json"

    with pytest.raises(ValueError, match="requires every deliver command"):
        module.execute_stage(
            closeout_plan=_plan(),
            contract_sha256="a" * 64,
            stage="cleanup",
            issue_worktree=issue_worktree,
            receipt_file=receipt,
        )


def test_empty_closeout_stages_are_explicit_noops(tmp_path: Path) -> None:
    issue_worktree = tmp_path / "issue-worktree"
    issue_worktree.mkdir()
    receipt = tmp_path / "closeout.json"
    plan: dict[str, object] = {"deliver": [], "cleanup": []}

    module.execute_stage(
        closeout_plan=plan,
        contract_sha256="a" * 64,
        stage="deliver",
        issue_worktree=issue_worktree,
        receipt_file=receipt,
    )
    saved = module.execute_stage(
        closeout_plan=plan,
        contract_sha256="a" * 64,
        stage="cleanup",
        issue_worktree=issue_worktree,
        receipt_file=receipt,
    )

    assert saved["stages"] == {"deliver": [], "cleanup": []}


def test_closeout_runner_derives_the_issue_worktree_from_the_confirmed_contract(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / ".cafe" / "worktrees" / "issue539"
    worktree.mkdir(parents=True)

    assert (
        module._confirmed_issue_worktree(
            {"checkout": {"kind": "worktree", "path": ".cafe/worktrees/issue539"}},
            project_root=tmp_path,
        )
        == worktree.resolve()
    )
    assert (
        module._confirmed_issue_worktree(
            {"checkout": {"kind": "current_checkout"}}, project_root=tmp_path
        )
        == tmp_path.resolve()
    )


def test_closeout_runner_loads_the_compact_confirmed_plan(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue474"
    proposal = _proposal()
    proposal["delivery_contract"]["closeout_plan"] = _plan()
    activated = activate_confirmed_contract(_activation(issue_dir, proposal))
    args = SimpleNamespace(
        issue_dir=issue_dir,
        issue_name="issue474",
        workflow_id="workflow-474",
        project_root=tmp_path,
        issue_worktree=tmp_path,
    )

    plan, digest = module._confirmed_plan(args)

    assert plan == _plan()
    assert digest == activated.contract_sha256


def test_closeout_runner_never_replays_an_unresolved_command(tmp_path: Path) -> None:
    issue_worktree = tmp_path / "issue-worktree"
    issue_worktree.mkdir()
    receipt = tmp_path / "closeout.json"
    plan = _plan()
    module._write_receipt(
        receipt,
        {
            "schema_version": 1,
            "contract_sha256": "a" * 64,
            "stages": {
                "deliver": [{"argv": plan["deliver"][0]["argv"], "status": "started"}],
                "cleanup": [],
            },
        },
    )

    with pytest.raises(ValueError, match="not safe to replay"):
        module.execute_stage(
            closeout_plan=plan,
            contract_sha256="a" * 64,
            stage="deliver",
            issue_worktree=issue_worktree,
            receipt_file=receipt,
        )


def test_closeout_runner_rejects_shell_code_and_lifecycle_recursion(tmp_path: Path) -> None:
    issue_worktree = tmp_path / "issue-worktree"
    issue_worktree.mkdir()
    receipt = tmp_path / "closeout.json"

    for argv, message in (
        (["sh", "-c", "do-a-thing"], "shell code strings"),
        (["cafe", "close", "issue539"], "recursively invoke"),
    ):
        with pytest.raises(ValueError, match=message):
            module.execute_stage(
                closeout_plan={"deliver": [{"argv": argv}], "cleanup": [{"argv": ["true"]}]},
                contract_sha256="a" * 64,
                stage="deliver",
                issue_worktree=issue_worktree,
                receipt_file=receipt,
            )


def test_closeout_runner_serializes_concurrent_receipt_execution(tmp_path: Path) -> None:
    issue_worktree = tmp_path / "issue-worktree"
    issue_worktree.mkdir()
    receipt = tmp_path / "closeout.json"
    counter = tmp_path / "counter.txt"
    command = (
        "from pathlib import Path; import time; time.sleep(0.2); "
        f"Path({str(counter)!r}).open('a', encoding='utf-8').write('x\\n')"
    )
    plan: dict[str, object] = {
        "deliver": [{"argv": [sys.executable, "-c", command]}],
        "cleanup": [],
    }
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    processes = [
        context.Process(
            target=_execute_stage_in_process,
            args=(plan, str(issue_worktree), str(receipt), results),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(results.get(timeout=1) for _ in processes) == ["ok", "ok"]
    assert counter.read_text(encoding="utf-8") == "x\n"
