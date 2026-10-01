"""U3: durable workspace repair authority uses the existing iteration store."""
import json

import pytest

from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor


def executor():
    return object.__new__(GenericWorkflowStepExecutor)


@pytest.mark.parametrize("malformed", [None, [], {"consumed": -1}, {"consumed": True}, {"consumed": 4}, {"consumed": 1, "rejections": "bad"}])
def test_u3_malformed_diagnostics_never_replenish_budget(tmp_path, malformed):
    (tmp_path / "iteration.json").write_text(json.dumps({"workspace_completion": malformed}))
    with pytest.raises((ValueError, RuntimeError)):
        executor()._load_workspace_completion(tmp_path)


def test_u3_reservation_is_durable_bounded_and_preserves_metadata(tmp_path):
    path = tmp_path / "iteration.json"
    path.write_text(json.dumps({"unrelated": "preserved"}))
    budget = executor()._load_workspace_completion(tmp_path)
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
