"""U3: durable workspace repair authority uses the existing iteration store."""

import json

import pytest

from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor


def executor():
    return object.__new__(GenericWorkflowStepExecutor)


@pytest.mark.parametrize("history", [None, {}, [None], [{}], [{"version": 1,
    "state": "completed", "result_id": "unbound"}]])
def test_malformed_workspace_recovery_history_cannot_authorize_model_rotation(tmp_path, history):
    runner = executor()
    runner.issue_dir = tmp_path
    runner.iteration = 1
    with pytest.raises(ValueError):
        runner._workspace_recovery_request(
            metadata={"model": "original", "workspace_recovery_history": history},
            workflow_id="workflow", step_name="custom_step", configured_model="replacement",
        )


@pytest.mark.parametrize("defect", [
    None, "empty", "unknown", "override", "subclass", "publish", "started",
    "result", "extra", "index", "bool_index", "name", "malformed",
])
def test_control_checkpoint_recovery_requires_inert_builtin_identity(defect):
    from dataclasses import asdict
    from types import SimpleNamespace

    from cafe.core.hooks import HookResult, NoChangesNeededHandler, PermissionRetryHandler

    runner = executor()
    runner.generic_phase = SimpleNamespace(hook_registry={
        "NoChangesNeededHandler": NoChangesNeededHandler,
        "PermissionRetryHandler": PermissionRetryHandler,
    })
    declaration = {"hooks": {"after_execute": ["NoChangesNeededHandler", "PermissionRetryHandler"]}}
    identity = ["after_execute", 0, "NoChangesNeededHandler"]
    item = {"state": "completed", "result": asdict(HookResult())}
    if defect == "unknown":
        runner.generic_phase.hook_registry[identity[2]] = object
    elif defect == "override":
        runner.generic_phase.hook_registry[identity[2]] = lambda: NoChangesNeededHandler()
    elif defect == "subclass":
        runner.generic_phase.hook_registry[identity[2]] = type("Custom", (NoChangesNeededHandler,), {})
    elif defect == "publish":
        identity[0] = "publish_output"
    elif defect == "started":
        item = {"state": "started"}
    elif defect == "result":
        item["result"]["events"] = [{"type": "external_effect"}]
    elif defect == "extra":
        item["unknown"] = True
    elif defect == "index":
        identity[1] = 2
    elif defect == "bool_index":
        identity[1] = False
    elif defect == "name":
        identity[2] = "PermissionRetryHandler"
    hooks = {json.dumps(identity): item}
    if defect == "empty":
        hooks = {}
    elif defect == "malformed":
        hooks = {"bad": item}
    assert runner._is_control_only_publication({"hooks": hooks}, declaration) is (defect is None)


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        [],
        {"consumed": -1},
        {"consumed": True},
        {"consumed": 4},
        {"consumed": 1, "rejections": "bad"},
        {"consumed": 0, "rejections": []},
        {"consumed": 1, "rejections": ["dirty"], "context": None},
    ],
)
def test_u3_malformed_diagnostics_never_replenish_budget(tmp_path, malformed):
    (tmp_path / "iteration.json").write_text(json.dumps({"workspace_completion": malformed}))
    with pytest.raises((ValueError, RuntimeError)):
        executor()._load_workspace_completion(tmp_path)


def test_u3_reservation_is_durable_bounded_and_preserves_metadata(tmp_path):
    path = tmp_path / "iteration.json"
    path.write_text(json.dumps({"unrelated": "preserved"}))
    budget = executor()._load_workspace_completion(tmp_path)
    budget["context"] = {"metadata": {}, "directories": [], "agent": "test"}
    for count in range(1, 4):
        executor()._reserve_workspace_correction(tmp_path, budget, "dirty owned.txt")
        budget = executor()._load_workspace_completion(tmp_path)
        assert budget["consumed"] == count
        assert json.loads(path.read_text())["unrelated"] == "preserved"
    with pytest.raises(RuntimeError) as rejected:
        executor()._reserve_workspace_correction(tmp_path, budget, "dirty owned.txt")
    assert "3" in str(rejected.value) and "owned.txt" in str(rejected.value)
    assert executor()._load_workspace_completion(tmp_path)["consumed"] == 3


def test_u3_unreadable_metadata_is_not_zero_budget(tmp_path):
    (tmp_path / "iteration.json").write_text("{bad")
    with pytest.raises((ValueError, RuntimeError)):
        executor()._load_workspace_completion(tmp_path)


@pytest.mark.parametrize("extra", [0, 1])
def test_u3_workspace_writer_reserves_runtime_recovery_capacity(tmp_path, extra):
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    path = tmp_path / "iteration.json"
    budget = {"consumed": 0, "rejections": [], "context": None}
    candidate = {"padding": "", "workspace_completion": budget}
    limit = 1_048_576 - 65_536
    overhead = len((json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode())
    candidate["padding"] = "x" * (limit - overhead + extra)
    path.write_text(json.dumps({"padding": candidate["padding"]}))
    before = path.read_bytes()
    if extra:
        with pytest.raises(RuntimeError):
            executor()._save_workspace_completion(tmp_path, budget)
        assert path.read_bytes() == before
    else:
        executor()._save_workspace_completion(tmp_path, budget)
        assert executor()._load_workspace_completion(tmp_path)["consumed"] == 0
    runtime = object.__new__(BlackboardWorkflowRuntime)
    runtime._latest_iteration_dir = lambda step: tmp_path
    runtime._mark_latest_iteration_completion_untrusted("custom")
    assert path.stat().st_size <= 1_048_576
    assert json.loads(path.read_text())["workflow_completion_trusted"] is False


@pytest.mark.parametrize("extra", [0, 1])
def test_u3_reader_limit_sized_reason_is_bounded_before_persistence(tmp_path, extra):
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    path = tmp_path / "iteration.json"
    path.write_text("{}")
    budget = {
        "consumed": 1,
        "rejections": ["dirty owned.txt; " + "x" * (1_048_576 + extra)],
        "context": {"metadata": {}, "directories": [], "agent": "test"},
    }
    executor()._save_workspace_completion(tmp_path, budget)
    restored = executor()._load_workspace_completion(tmp_path)
    assert restored["consumed"] == 1
    assert "owned.txt" in restored["rejections"][0]
    assert path.stat().st_size < 16384
    runtime = object.__new__(BlackboardWorkflowRuntime)
    runtime._latest_iteration_dir = lambda step: tmp_path
    runtime._mark_latest_iteration_completion_untrusted("custom")
    assert executor()._load_workspace_completion(tmp_path)["consumed"] == 1


@pytest.mark.parametrize("extra", [0, 1])
def test_i8_hook_result_byte_boundary_retains_replay_safety(tmp_path, extra):
    from dataclasses import asdict
    from cafe.phases.generic_phase import HookResult

    (tmp_path / "iteration.json").write_text("{}")
    result = HookResult(context_updates={"payload": ""})
    overhead = len(json.dumps(asdict(result), ensure_ascii=False).encode())
    result.context_updates["payload"] = "x" * (32768 - overhead + extra)
    calls = []

    def effect():
        calls.append(1)
        return result

    kwargs = dict(
        iteration_dir=tmp_path,
        delivery="a" * 64,
        response="await_agent",
        stage="after_execute",
        index=0,
        identity="Effect",
        invoke=effect,
    )
    if extra:
        with pytest.raises(RuntimeError):
            executor()._run_workspace_publication_hook(**kwargs)
        with pytest.raises(RuntimeError):
            executor()._run_workspace_publication_hook(**kwargs)
    else:
        assert executor()._run_workspace_publication_hook(**kwargs) == result
        assert executor()._run_workspace_publication_hook(**kwargs) == result
    assert len(calls) == 1
