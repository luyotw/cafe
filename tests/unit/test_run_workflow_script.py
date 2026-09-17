"""Driver launch invariants owned by the use-cafe-workflow skill."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = (
    Path(__file__).parents[2] / "src/cafe/data/skills/use-cafe-workflow/scripts/run_workflow.py"
)
CALLBACK_ID = "builtin:use-cafe-workflow:workflow_event_callback"


def _module():
    spec = importlib.util.spec_from_file_location("run_workflow_script_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(root: Path, *, step: str = "develop", playbook: str = "direct") -> Path:
    issue_dir = root / ".cafe/issues/issue498"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 4,
                "current_step": step,
                "playbook_id": playbook,
                "workflow_id": "workflow-498",
                "handoff_contract": (
                    {
                        "version": 1,
                        "to_owner": "user",
                        "to_step": "user",
                        "intent": "need_clarification",
                    }
                    if step == "user"
                    else None
                ),
            }
        ),
        encoding="utf-8",
    )
    return issue_dir


def _contract(mode: str, root: Path) -> dict[str, object]:
    driver: dict[str, object] = {"mode": mode}
    if mode == "attached":
        driver["poll_interval_seconds"] = 90
    elif mode == "event-driven":
        driver["clis"] = [{"cli": "codex"}, {"cli": "claude", "model": "sonnet"}]
    return {
        "identity": {"issue_name": "issue498", "workflow_id": "workflow-498"},
        "driver": driver,
        "checkout": {"kind": "worktree", "path": str(root)},
    }


class _Process:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


def _install_contract_stubs(monkeypatch, module, contract):
    monkeypatch.setattr(module, "load_contract", lambda *args, **kwargs: (contract, "digest"))
    monkeypatch.setattr(
        module,
        "resolve_builtin_workflow_event_callback",
        lambda callback_id, **kwargs: SimpleNamespace(callback_id=callback_id),
    )
    monkeypatch.setattr(
        module,
        "event_callback_projection",
        lambda request: SimpleNamespace(
            contract_sha256="digest",
            event={"clis": ({"cli": "codex"}, {"cli": "claude", "model": "sonnet"})},
        ),
    )


@pytest.mark.parametrize(
    ("mode", "tail", "directive"),
    [
        (
            "attached",
            [],
            'CAFE_DRIVER_DIRECTIVE {"schema_version":1,"mode":"attached","action":"wait",'
            '"worker":"foreground","next_wake":["process_exit","user_input"],'
            '"poll_interval_seconds":90}',
        ),
        (
            "unattended",
            ["--background"],
            'CAFE_DRIVER_DIRECTIVE {"schema_version":1,"mode":"unattended","action":"yield",'
            '"worker":"background","next_wake":["user_input"]}',
        ),
        (
            "event-driven",
            ["--background", "--on-workflow-event", CALLBACK_ID],
            'CAFE_DRIVER_DIRECTIVE {"schema_version":1,"mode":"event-driven","action":"yield",'
            '"worker":"background","next_wake":["workflow_event_callback","user_input"]}',
        ),
    ],
)
def test_modes_launch_exact_safe_argv_and_emit_exact_directive(
    tmp_path: Path, monkeypatch, capsys, mode: str, tail: list[str], directive: str
) -> None:
    module = _module()
    _prepared(tmp_path)
    _install_contract_stubs(monkeypatch, module, _contract(mode, tmp_path))
    launched: list[list[str]] = []

    def process_factory(argv, **kwargs):
        launched.append(argv)
        assert kwargs == {"cwd": str(tmp_path)}
        return _Process()

    result = module.run(
        ["--issue", "issue498", "--playbook", "direct", "--driver-mode", mode],
        cwd=tmp_path,
        process_factory=process_factory,
    )

    assert result == 0
    assert launched == [
        [
            "cafe",
            "workflow",
            "--issue",
            "issue498",
            "--playbook",
            "direct",
            "--execute",
            "--mute-agent-output",
            *tail,
        ]
    ]
    assert capsys.readouterr().out.splitlines()[0] == directive
    assert "--single-step" not in launched[0]
    assert "--start-step" not in launched[0]


def test_start_and_resume_use_identical_argv(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    issue_dir = _prepared(tmp_path, step="develop")
    _install_contract_stubs(monkeypatch, module, _contract("unattended", tmp_path))
    launched: list[list[str]] = []

    def process_factory(argv, **kwargs):
        launched.append(argv)
        return _Process()

    args = ["--issue", "issue498", "--playbook", "direct", "--driver-mode", "unattended"]
    assert module.run(args, cwd=tmp_path, process_factory=process_factory) == 0
    state = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    state["current_step"] = "review"
    state["handoff_contract"] = {
        "version": 1,
        "to_owner": "agent",
        "to_step": "review",
        "intent": "await_agent",
    }
    (issue_dir / "blackboard.json").write_text(json.dumps(state), encoding="utf-8")
    assert module.run(args, cwd=tmp_path, process_factory=process_factory) == 0

    assert launched[0] == launched[1]


@pytest.mark.parametrize(
    ("requested_mode", "prepared_playbook", "contract_mode", "checkout"),
    [
        ("attached", "direct", "unattended", None),
        ("unattended", "other", "unattended", None),
        ("unattended", "direct", "unattended", "/different/worktree"),
    ],
)
def test_contract_mode_prepared_identity_and_checkout_mismatches_fail_closed(
    tmp_path: Path,
    monkeypatch,
    capsys,
    requested_mode: str,
    prepared_playbook: str,
    contract_mode: str,
    checkout: str | None,
) -> None:
    module = _module()
    _prepared(tmp_path, playbook=prepared_playbook)
    contract = _contract(contract_mode, tmp_path)
    if checkout is not None:
        contract["checkout"] = {"kind": "worktree", "path": checkout}
    _install_contract_stubs(monkeypatch, module, contract)
    launched = False

    def process_factory(argv, **kwargs):
        nonlocal launched
        launched = True
        return _Process()

    result = module.run(
        ["--issue", "issue498", "--playbook", "direct", "--driver-mode", requested_mode],
        cwd=tmp_path,
        process_factory=process_factory,
    )

    assert result == 2
    assert launched is False
    assert capsys.readouterr().out.splitlines()[0] == (
        f'CAFE_DRIVER_DIRECTIVE {{"schema_version":1,"mode":"{requested_mode}",'
        '"action":"launch_failed","worker":"none","next_wake":["user_input"]}'
    )


def test_stale_contract_identity_and_unreadable_state_fail_closed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    issue_dir = _prepared(tmp_path)
    contract = _contract("unattended", tmp_path)
    args = ["--issue", "issue498", "--playbook", "direct", "--driver-mode", "unattended"]

    assert module.run(args, cwd=tmp_path, process_factory=lambda *a, **k: _Process()) == 2
    assert "launch_failed" in capsys.readouterr().out

    (issue_dir / "driver").mkdir()
    (issue_dir / "driver/contract.json").write_text("not json", encoding="utf-8")
    assert module.run(args, cwd=tmp_path, process_factory=lambda *a, **k: _Process()) == 2
    assert "launch_failed" in capsys.readouterr().out

    def stale_loader(*args, **kwargs):
        assert kwargs["workflow_id"] == "workflow-498"
        raise ValueError("contract belongs to a different workflow")

    monkeypatch.setattr(module, "load_contract", stale_loader)
    assert module.run(args, cwd=tmp_path, process_factory=lambda *a, **k: _Process()) == 2
    assert "launch_failed" in capsys.readouterr().out

    (issue_dir / "blackboard.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(module, "load_contract", lambda *a, **k: (contract, "digest"))
    assert module.run(args, cwd=tmp_path, process_factory=lambda *a, **k: _Process()) == 2
    assert "launch_failed" in capsys.readouterr().out


def test_event_binding_or_order_change_fails_before_launch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    _prepared(tmp_path)
    _install_contract_stubs(monkeypatch, module, _contract("event-driven", tmp_path))
    monkeypatch.setattr(
        module,
        "event_callback_projection",
        lambda request: SimpleNamespace(contract_sha256="different", event={"clis": ()}),
    )

    result = module.run(
        ["--issue", "issue498", "--playbook", "direct", "--driver-mode", "event-driven"],
        cwd=tmp_path,
        process_factory=lambda *args, **kwargs: pytest.fail("worker must not launch"),
    )

    assert result == 2
    assert "launch_failed" in capsys.readouterr().out


def test_launch_failure_and_durable_user_boundary_have_stable_directives(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    issue_dir = _prepared(tmp_path)
    _install_contract_stubs(monkeypatch, module, _contract("unattended", tmp_path))
    args = ["--issue", "issue498", "--playbook", "direct", "--driver-mode", "unattended"]

    assert module.run(args, cwd=tmp_path, process_factory=lambda *a, **k: _Process(7)) == 7
    assert capsys.readouterr().out.splitlines()[0] == (
        'CAFE_DRIVER_DIRECTIVE {"schema_version":1,"mode":"unattended",'
        '"action":"launch_failed","worker":"background","next_wake":["user_input"],'
        '"exit_code":7}'
    )

    state = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    state["current_step"] = "user"
    state["handoff_contract"] = {"to_owner": "user"}
    (issue_dir / "blackboard.json").write_text(json.dumps(state), encoding="utf-8")
    assert (
        module.run(
            args,
            cwd=tmp_path,
            process_factory=lambda *a, **k: pytest.fail("worker must not launch"),
        )
        == 0
    )
    assert capsys.readouterr().out.splitlines()[0] == (
        'CAFE_DRIVER_DIRECTIVE {"schema_version":1,"mode":"unattended",'
        '"action":"await_user","worker":"none","next_wake":["user_input"]}'
    )
