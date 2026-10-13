"""U9/U10/U13: only the exact committed companion receipt closes an iteration."""
import json

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.packet_io import canonical_json, sha256_bytes
from cafe.core.phase_state_mixin import next_runnable_iteration_number, validated_completion_receipt


@pytest.mark.parametrize('case', ['valid', 'reserved_only', 'wrong_workflow', 'wrong_slot',
                                'wrong_step', 'wrong_path', 'changed_budget', 'changed_session'])
def test_issue646_blk005_iteration_receipt_is_bound_to_durable_original_control(tmp_path, case):
    issue = tmp_path / '.cafe/issues/receipt-case'
    directory = issue / 'bespoke/iteration_001'; directory.mkdir(parents=True)
    store = BlackboardStore(issue); state = store.load_or_create('bespoke')
    control = {'end_time': 'saved', 'session_id': 'original', 'workspace_completion': {'consumed': 3}}
    path = directory / 'iteration.json'
    payload = {'step': 'another' if case == 'wrong_step' else 'bespoke', 'completion_metadata': {
        'path': 'another/iteration_001/iteration.json' if case == 'wrong_path' else 'bespoke/iteration_001/iteration.json',
        'sha256': sha256_bytes(canonical_json(control))}}
    if case == 'reserved_only': seq = store.audit.reserve(state.workflow_id)
    else:
        store.record_event(state, 'step_completed', payload)
        seq = store.audit.high_water(state.workflow_id)
    if case == 'changed_budget': control['workspace_completion']['consumed'] = 0
    if case == 'changed_session': control['session_id'] = 'other'
    control['workflow_completion_trusted'] = {'workflow_id': 'other' if case == 'wrong_workflow' else state.workflow_id,
        'sequence': seq + 1 if case == 'wrong_slot' else seq}
    path.write_text(json.dumps(control))
    assert (validated_completion_receipt(directory, control) is not None) is (case == 'valid')
    assert next_runnable_iteration_number(issue / 'bespoke') == (2 if case == 'valid' else 1)
