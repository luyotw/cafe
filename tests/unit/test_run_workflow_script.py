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
FRESH_FACTS = {
    "semantic_facts": {"runtime": "current"},
    "material_assumptions": {"catalogs": "unchanged"},
}


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
        "reactive_user_handoffs": {"alignment_checkpoint": "driver_resolvable_when_clear"},
    }


class _Process:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


def _args(mode: str, *extra: str) -> list[str]:
    return [
        "--issue",
        "issue498",
        "--playbook",
        "direct",
        "--driver-mode",
        mode,
        "--fresh-facts",
        json.dumps(FRESH_FACTS),
        *extra,
    ]


def _install_contract_stubs(monkeypatch, module, contract):
    monkeypatch.setattr(
        module,
        "evaluate_driver_entry",
        lambda request: SimpleNamespace(
            freshness=module.Freshness.SAME_SEMANTICS,
            contract_sha256="digest",
        ),
    )
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
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"]["CAFE_SKIP_ENTRYPOINT_CHECK"] == "1"
        assert "PYTHONHOME" not in kwargs["env"]
        assert "PYTHONPATH" not in kwargs["env"]
        return _Process()

    result = module.run(
        _args(mode),
        cwd=tmp_path,
        process_factory=process_factory,
    )

    assert result == 0
    assert launched == [
        [
            str(Path(module.sys.executable).absolute()),
            "-I",
            "-m",
            "cafe.ui.cli",
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


def test_launch_uses_isolated_host_runtime_despite_python_path_overrides(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    _prepared(tmp_path)
    _install_contract_stubs(monkeypatch, module, _contract("unattended", tmp_path))
    monkeypatch.setenv("PYTHONPATH", "/old/worktree/src")
    monkeypatch.setenv("PYTHONHOME", "/old/python/home")
    launched: list[tuple[list[str], dict[str, object]]] = []

    def process_factory(argv, **kwargs):
        launched.append((argv, kwargs))
        return _Process()

    assert module.run(_args("unattended"), cwd=tmp_path, process_factory=process_factory) == 0

    command, kwargs = launched[0]
    assert command[:4] == [
        str(Path(module.sys.executable).absolute()),
        "-I",
        "-m",
        "cafe.ui.cli",
    ]
    assert kwargs["env"]["CAFE_SKIP_ENTRYPOINT_CHECK"] == "1"
    assert "PYTHONHOME" not in kwargs["env"]
    assert "PYTHONPATH" not in kwargs["env"]


def test_runtime_source_mismatch_fails_before_worker_launch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    _prepared(tmp_path)
    _install_contract_stubs(monkeypatch, module, _contract("unattended", tmp_path))
    monkeypatch.setattr(module, "_wrapper_source_root", lambda: tmp_path / "wrapper-src")
    monkeypatch.setattr(module, "_loaded_cafe_source_root", lambda: tmp_path / "other-src")

    assert (
        module.run(
            _args("unattended"),
            cwd=tmp_path,
            process_factory=lambda *args, **kwargs: pytest.fail("worker must not launch"),
        )
        == 2
    )
    assert "Driver runtime source differs" in capsys.readouterr().err


def test_global_wrapper_accepts_an_identical_runtime_copy(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    global_wrapper = tmp_path / "global/run_workflow.py"
    global_wrapper.parent.mkdir(parents=True)
    global_wrapper.write_bytes(SCRIPT.read_bytes())
    runtime_source_root = tmp_path / "runtime/src"
    runtime_wrapper = runtime_source_root / module._WRAPPER_RELATIVE_PATH
    runtime_wrapper.parent.mkdir(parents=True)
    runtime_wrapper.write_bytes(SCRIPT.read_bytes())
    monkeypatch.setattr(module, "__file__", str(global_wrapper))
    monkeypatch.setattr(module, "_loaded_cafe_source_root", lambda: runtime_source_root)

    assert module._validated_host_interpreter() == str(Path(module.sys.executable).absolute())


def test_global_wrapper_rejects_a_different_runtime_copy(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    global_wrapper = tmp_path / "global/run_workflow.py"
    global_wrapper.parent.mkdir(parents=True)
    global_wrapper.write_bytes(SCRIPT.read_bytes())
    runtime_source_root = tmp_path / "runtime/src"
    runtime_wrapper = runtime_source_root / module._WRAPPER_RELATIVE_PATH
    runtime_wrapper.parent.mkdir(parents=True)
    runtime_wrapper.write_text("different runtime wrapper", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(global_wrapper))
    monkeypatch.setattr(module, "_loaded_cafe_source_root", lambda: runtime_source_root)

    with pytest.raises(ValueError, match="global workflow wrapper"):
        module._validated_host_interpreter()


def test_isolated_bootstrap_removes_python_path_overrides(monkeypatch) -> None:
    module = _module()
    monkeypatch.delenv(module._ISOLATED_BOOTSTRAP_ENVIRONMENT_KEY, raising=False)
    monkeypatch.setenv("PYTHONPATH", "/old/worktree/src")
    monkeypatch.setenv("PYTHONHOME", "/old/python/home")
    captured: dict[str, object] = {}

    def fake_execvpe(executable, argv, environment):
        captured["executable"] = executable
        captured["argv"] = argv
        captured["environment"] = environment

    monkeypatch.setattr(module.os, "execvpe", fake_execvpe)
    module._bootstrap_isolated_runtime()

    assert captured["executable"] == str(Path(module.sys.executable).absolute())
    assert captured["argv"][:3] == [
        str(Path(module.sys.executable).absolute()),
        "-I",
        str(SCRIPT.resolve()),
    ]
    environment = captured["environment"]
    assert environment[module._ISOLATED_BOOTSTRAP_ENVIRONMENT_KEY] == "1"
    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment


def test_start_and_resume_use_identical_argv(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    issue_dir = _prepared(tmp_path, step="develop")
    _install_contract_stubs(monkeypatch, module, _contract("unattended", tmp_path))
    launched: list[list[str]] = []

    def process_factory(argv, **kwargs):
        launched.append(argv)
        return _Process()

    args = _args("unattended")
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
        _args(requested_mode),
        cwd=tmp_path,
        process_factory=process_factory,
    )

    assert result == 2
    assert launched is False
    assert capsys.readouterr().out.splitlines()[0] == (
        f'CAFE_DRIVER_DIRECTIVE {{"schema_version":1,"mode":"{requested_mode}",'
        '"action":"launch_failed","worker":"none","next_wake":["user_input"]}'
    )


def test_relative_worktree_identity_resolves_from_main_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    main_checkout = tmp_path / "project"
    worktree = main_checkout / ".cafe/worktrees/issue498"
    git_dir = main_checkout / ".git/worktrees/issue498"
    git_dir.mkdir(parents=True)
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    _prepared(worktree)
    contract = _contract("unattended", worktree)
    contract["checkout"] = {"kind": "worktree", "path": ".cafe/worktrees/issue498"}
    _install_contract_stubs(monkeypatch, module, contract)
    launched: list[list[str]] = []

    assert (
        module.run(
            _args("unattended"),
            cwd=worktree,
            process_factory=lambda argv, **kwargs: launched.append(argv) or _Process(),
        )
        == 0
    )
    assert len(launched) == 1


def test_stale_contract_identity_and_unreadable_state_fail_closed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    issue_dir = _prepared(tmp_path)
    contract = _contract("unattended", tmp_path)
    args = _args("unattended")

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
        _args("event-driven"),
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
    args = _args("unattended")

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


def test_attached_directive_is_flushed_before_wait(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    _prepared(tmp_path)
    _install_contract_stubs(monkeypatch, module, _contract("attached", tmp_path))
    printed: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_print(*values, **kwargs):
        printed.append((values, kwargs))

    class WaitingProcess(_Process):
        def wait(self) -> int:
            assert printed[0][0][0].startswith("CAFE_DRIVER_DIRECTIVE ")
            assert printed[0][1].get("flush") is True
            return 0

    monkeypatch.setattr("builtins.print", fake_print)
    assert (
        module.run(
            _args("attached"),
            cwd=tmp_path,
            process_factory=lambda *args, **kwargs: WaitingProcess(),
        )
        == 0
    )


def test_explicit_alignment_input_requires_durable_driver_authority(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    issue_dir = _prepared(tmp_path, step="user")
    state_path = issue_dir / "blackboard.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["handoff_contract"] = {
        "version": 1,
        "from_step": "develop",
        "to_owner": "user",
        "to_step": "user",
        "intent": "alignment_checkpoint",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    request_dir = issue_dir / "develop/iteration_001"
    request_dir.mkdir(parents=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps({"allowed_decisions": ["approve"]}), encoding="utf-8"
    )
    _install_contract_stubs(monkeypatch, module, _contract("unattended", tmp_path))
    launched: list[list[str]] = []
    payload = '{"decision":"approve","reason":"Within confirmed mandate."}'

    assert (
        module.run(
            _args("unattended", "--alignment-input", payload),
            cwd=tmp_path,
            process_factory=lambda argv, **kwargs: launched.append(argv) or _Process(),
        )
        == 0
    )
    assert launched == [
        [
            str(Path(module.sys.executable).absolute()),
            "-I",
            "-m",
            "cafe.ui.cli",
            "workflow",
            "--issue",
            "issue498",
            "--playbook",
            "direct",
            "--execute",
            "--mute-agent-output",
            "--background",
            "--user-input",
            payload,
        ]
    ]

    state["handoff_contract"]["intent"] = "need_clarification"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert (
        module.run(
            _args("unattended", "--alignment-input", payload),
            cwd=tmp_path,
            process_factory=lambda *args, **kwargs: pytest.fail("worker must not launch"),
        )
        == 2
    )


def test_rebuilt_fresh_facts_are_required_before_launch(tmp_path: Path) -> None:
    module = _module()

    with pytest.raises(SystemExit) as exc_info:
        module.run(
            ["--issue", "issue498", "--playbook", "direct", "--driver-mode", "unattended"],
            cwd=tmp_path,
            process_factory=lambda *args, **kwargs: pytest.fail("worker must not launch"),
        )

    assert exc_info.value.code == 2


@pytest.mark.parametrize("mode", ["attached", "unattended", "event-driven"])
@pytest.mark.parametrize("freshness_name", ["MATERIAL_CHANGE", "UNKNOWN"])
def test_freshness_mismatch_fails_before_worker_creation(
    tmp_path: Path,
    monkeypatch,
    capsys,
    mode: str,
    freshness_name: str,
) -> None:
    module = _module()
    _prepared(tmp_path)
    _install_contract_stubs(monkeypatch, module, _contract(mode, tmp_path))
    seen_facts: list[dict[str, object]] = []

    def evaluate(request):
        seen_facts.append(dict(request.fresh_facts))
        return SimpleNamespace(
            freshness=getattr(module.Freshness, freshness_name),
            contract_sha256="digest",
        )

    monkeypatch.setattr(module, "evaluate_driver_entry", evaluate)

    result = module.run(
        _args(mode),
        cwd=tmp_path,
        process_factory=lambda *args, **kwargs: pytest.fail("worker must not launch"),
    )

    assert result == 2
    assert seen_facts == [FRESH_FACTS]
    assert '"action":"launch_failed"' in capsys.readouterr().out
