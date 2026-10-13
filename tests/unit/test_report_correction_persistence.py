"""U1/I1: correction bookkeeping keeps the original recovery admission bound."""
import json

import pytest

from cafe.core.artifact_validation import ArtifactCorrectionBudget
from cafe.core.restart_policy import load_restart_context
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor


@pytest.mark.parametrize('operation', ['budget', 'invocation', 'restart', 'packets', 'todos'])
@pytest.mark.parametrize('target_size', [983_041, 1_048_576, 1_048_577])
def test_issue646_blk001_bookkeeping_admits_exact_bound_or_preserves_original(tmp_path, operation, target_size):
    executor = object.__new__(GenericWorkflowStepExecutor)
    directory = tmp_path / 'iteration_001'
    directory.mkdir()
    path = directory / 'iteration.json'
    control = {'session_id': 'original', 'model': 'original-model', 'cli': 'codex',
               'workspace_completion': {'consumed': 3}, 'publication': {'status': 'unknown'}, 'padding': ''}
    updates = {
        'budget': {'artifact_correction': ArtifactCorrectionBudget(consumed=1).to_dict()},
        'invocation': {'agent_invoked': True},
        'restart': {'restart_recovery_consumption': {'human_task_id': 'task', 'result_id': 'result',
                    'interruption_id': 'interruption', 'invocation_id': 'x' * 36}, 'agent_invoked': True},
        'packets': {'effective_inputs': {}}, 'todos': {'todo_projections': []},
    }[operation]
    control['padding'] = 'x' * (target_size - len(json.dumps(dict(control, **updates), ensure_ascii=False, indent=2).encode()))
    path.write_text(json.dumps(control, ensure_ascii=False, indent=2))
    before = path.read_bytes()
    executor._restart_recovery = {'human_task_id': 'task', 'result_id': 'result', 'interruption': {'id': 'interruption'}}
    actions = {
        'budget': lambda: executor._save_artifact_correction(directory, ArtifactCorrectionBudget(consumed=1)),
        'invocation': lambda: executor._persist_agent_invocation_marker(iteration_dir=directory, agent_invoked=True),
        'restart': lambda: executor._consume_restart_recovery(path),
        'packets': lambda: executor._persist_context_packet_diagnostics(directory, {}),
        'todos': lambda: executor._persist_todo_projection_snapshot(directory, []),
    }
    if target_size > 1_048_576:
        with pytest.raises(RuntimeError): actions[operation]()
        assert path.read_bytes() == before
    else:
        actions[operation]()
        assert path.stat().st_size == target_size
    saved = load_restart_context(path)
    assert all(saved[key] == value for key, value in control.items())


def test_issue646_blk001_budget_atomic_failure_keeps_original(tmp_path, monkeypatch):
    from cafe.core import packet_io
    executor = object.__new__(GenericWorkflowStepExecutor)
    path = tmp_path / 'iteration.json'
    path.write_text(json.dumps({'session_id': 'original', 'artifact_correction': {'consumed': 0, 'rejections': []}}))
    original = path.read_bytes()
    def refuse(path, payload):
        raise OSError('atomic publication failed before replacement')
    monkeypatch.setattr(packet_io, 'atomic_write_bytes', refuse)
    with pytest.raises(OSError): executor._save_artifact_correction(tmp_path, ArtifactCorrectionBudget(consumed=1))
    assert path.read_bytes() == original
