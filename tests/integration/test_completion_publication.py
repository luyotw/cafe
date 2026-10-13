"""U9–U13/I5–I7: completion gates, marker and terminal publication agree."""
import json
from pathlib import Path
import pytest
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.phase_state_mixin import next_runnable_iteration_number
from tests.integration.test_workflow_artifact_correction import journey, CORRECTED
from tests.unit.test_execution_checkpoints import execution_context
from tests.unit.test_file_scope import repository


@pytest.mark.parametrize('terminal_bytes', [262_143, 262_144, 262_145])
def test_issue646_blk005_terminal_envelope_capacity_precedes_marker_and_handoff(
        journey, monkeypatch, terminal_bytes):
    """U9–U13/I5–I7: the real producer and bounded receipt agree at the ceiling."""
    from cafe.core.audit_events import AuditEventStore, MAX_EVENT_BYTES
    from cafe.core.phase_state_mixin import validated_completion_receipt
    from tests.unit.test_workflow_progress_renderer import _module
    import cafe.core.blackboard as blackboard_module
    import cafe.core.workflow_runtime as runtime_module

    # A valid clock precision selects the adjacent odd/even serialized envelopes.
    fraction = '00000' if terminal_bytes == 262_144 else '000000'
    monkeypatch.setattr(blackboard_module, '_now_iso',
                        lambda: f'2026-10-13T00:00:00.{fraction}+00:00')
    j = journey([CORRECTED], workspace=False)
    callbacks, original = [], []
    j.runtime._workflow_event_callback = lambda event: callbacks.append(event)
    execute = j.manager.execute
    def provider(*args, **kwargs):
        result = execute(*args, **kwargs)
        path = j.iteration / 'iteration.json'
        data = json.loads(path.read_text())
        data.update(workflow_completion_trusted=False, workspace_completion={'consumed': 3},
                    workspace_publication={'original': {'status': 'unknown'}})
        path.write_text(json.dumps(data))
        status_bytes = 130_635 if terminal_bytes == 262_143 else 130_636
        (j.issue / 'next_step.txt').write_text(json.dumps({
            'version': 1, 'to_owner': 'agent', 'to_step': 'deliver_custom',
            'intent': 'await_agent', 'status_code': 'S' * status_bytes}))
        return result
    j.manager.execute = provider
    commit = AuditEventStore.commit
    def capture_prepared_control(self, workflow_id, record):
        if record['event_type'] == 'step_completion_prepared':
            original.append((j.iteration / 'iteration.json').read_bytes())
        return commit(self, workflow_id, record)
    monkeypatch.setattr(AuditEventStore, 'commit', capture_prepared_control)
    write = runtime_module.atomic_write_bytes
    def defer_compaction(path, payload):
        if path == j.iteration / 'iteration.json' and 'workflow_completion_trusted' not in json.loads(payload):
            raise OSError('optional receipt compaction unavailable')
        return write(path, payload)
    monkeypatch.setattr(runtime_module, 'atomic_write_bytes', defer_compaction)

    if terminal_bytes > MAX_EVENT_BYTES:
        with pytest.raises((RuntimeError, ValueError)):
            j.runtime.run(start_step='inspect_custom', single_step=True)
    else:
        result = j.runtime.run(start_step='inspect_custom', single_step=True)
        assert not result.completed and j.runtime.blackboard.current_step == 'deliver_custom'
    events = list(j.runtime.blackboard_store.audit.iter_records(j.runtime.blackboard.workflow_id))
    terminal = [e for e in events if e['step'] == 'inspect_custom'
                and e['event_type'] in {'step_completed', 'single_step_completed'}]
    saved = json.loads((j.iteration / 'iteration.json').read_text())
    rendered = _module()._runtime_progress(j.issue, j.playbook)[0]['inspect_custom']
    if terminal_bytes > MAX_EVENT_BYTES:
        assert not terminal and rendered != 'completed'
        assert j.runtime.blackboard.current_step == 'inspect_custom'
        assert (j.iteration / 'iteration.json').read_bytes() == original[0]
        assert next_runnable_iteration_number(j.issue / 'inspect_custom') == 1
        assert not any(e['event_type'] == 'phase_terminal' for e in callbacks)
    else:
        assert len(terminal) == 1
        assert j.runtime.blackboard_store.audit._path(terminal[0]['sequence']).stat().st_size == terminal_bytes
        assert validated_completion_receipt(j.iteration, saved)['event_id'] == terminal[0]['event_id']
        assert rendered == 'completed'
        assert next_runnable_iteration_number(j.issue / 'inspect_custom') == 2
    assert saved['workspace_completion']['consumed'] == 3
    assert saved['workspace_publication'] == {'original': {'status': 'unknown'}}
    assert saved['session_id'] == 'exact-report-session'
    assert len(j.manager.calls) == 1 and j.manager.deliveries == 0
    assert j.effects == ['prepare', 'after']


def test_issue646_blk005_required_receipt_read_failure_is_not_optional_compaction(
        journey, monkeypatch):
    """U9/U13/I7: required receipt failure blocks, then saved reentry does no work."""
    from cafe.core.audit_events import AuditEventStore
    from tests.unit.test_workflow_progress_renderer import _module
    j = journey([CORRECTED], workspace=False)
    execute = j.manager.execute
    def provider(*args, **kwargs):
        result = execute(*args, **kwargs)
        path = j.iteration / 'iteration.json'
        data = json.loads(path.read_text())
        data.update(workflow_completion_trusted=False, workspace_completion={'consumed': 3})
        path.write_text(json.dumps(data))
        return result
    j.manager.execute = provider
    read = AuditEventStore.read
    def unavailable(self, workflow_id, sequence, *, bounded=False):
        record = read(self, workflow_id, sequence, bounded=bounded)
        if bounded and record and record['event_type'] in {'step_completed', 'single_step_completed'}:
            raise OSError('required receipt temporarily unavailable')
        return record
    monkeypatch.setattr(AuditEventStore, 'read', unavailable)
    with pytest.raises(RuntimeError):
        j.runtime.run(start_step='inspect_custom', single_step=True)
    assert j.runtime.blackboard.current_step == 'inspect_custom'
    assert next_runnable_iteration_number(j.issue / 'inspect_custom') == 1
    assert _module()._runtime_progress(j.issue, j.playbook)[0]['inspect_custom'] != 'completed'
    monkeypatch.setattr(AuditEventStore, 'read', read)
    recovered = j.reconstruct()
    result = recovered.run(single_step=True)
    assert not result.completed and recovered.blackboard.current_step == 'deliver_custom'
    assert next_runnable_iteration_number(j.issue / 'inspect_custom') == 2
    assert len(j.manager.calls) == 1 and j.manager.deliveries == 0
    assert j.effects == ['prepare', 'after']
    assert json.loads((j.iteration / 'iteration.json').read_text())['workspace_completion']['consumed'] == 3


@pytest.mark.parametrize('single_step', [False, True])
@pytest.mark.parametrize('surface', ['native', 'readiness'])
@pytest.mark.parametrize('mode', ['baton', 'legacy'])
def test_issue646_blk005_rejected_required_gate_preserves_original_iteration(execution_context, mode, surface, single_step):
    context = dict(execution_context, delivery_endpoint={"route": "local", "source_branch": "main"})
    issue = Path(context["root"]) / '.cafe/issues/probe'
    graph = {'playbook': {'id': 'native-probe'}, 'steps': {
        'inspect_custom': {'skill': 'custom', 'role': 'reviewer', 'output_artifact': 'review_result',
           'execution': ({'review_policy': 'single_native', 'review_evidence_artifact': 'native_review.json'} if surface == 'native' else {'checkpoints': ['before_delivery'], 'delivery_evidence_artifact': 'ready.json'}),
           'on': {'await_agent': '_done'},
           **({'behavior': {'completion': 'baton'}} if mode == 'baton' else {})}}}
    metadata = {'iteration': 1, 'end_time': '2026-10-13T10:00:00+08:00',
                'workflow_completion_trusted': False, 'session_id': 'original', 'cli': 'codex',
                'model': 'original', 'workspace_completion': {'consumed': 3}}
    def provider(step, declaration, state, **kwargs):
        directory = issue / step / 'iteration_001'; directory.mkdir(parents=True, exist_ok=True)
        (directory / 'iteration.json').write_text(json.dumps(metadata))
        output = directory / 'output.md'; output.write_text('Saved review output requiring native verification\n')
        if surface == 'readiness':
            from cafe.core.execution_checkpoints import checkpoint
            saved = checkpoint(context, 'before_delivery', round_id='saved-round', parent_id='original')
            saved['passed'] = False
            (directory / 'ready.json').write_text(json.dumps({'authority_digest': context['authority_digest'], 'endpoint': context['delivery_endpoint'], 'checkpoint': saved}))
        # The gate must reject unavailable required native or failed checkpoint evidence.
        (issue / 'next_step.txt').write_text('{"version":1,"intent":"await_agent"}')
        return StepExecutionResult(response='', artifacts={'review_result': str(output)})
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=provider, execution_context=context)
    context["identity"] = {"issue_name": "probe", "workflow_id": runtime.blackboard.workflow_id}
    runtime._notify_new_human_task = lambda task: None
    result = runtime.run(start_step='inspect_custom', single_step=single_step)
    saved = json.loads((issue / 'inspect_custom/iteration_001/iteration.json').read_text())
    assert not result.completed and result.final_status_code == ('NATIVE_REVIEW_BLOCKED' if surface == 'native' else 'DELIVERY_READINESS_BLOCKED')
    assert saved.get('workflow_completion_trusted') is False
    assert next_runnable_iteration_number(issue / 'inspect_custom') == 1


@pytest.mark.parametrize('single_step', [False, True])
@pytest.mark.parametrize('mode', ['baton', 'legacy'])
def test_issue646_blk005_marker_failure_publishes_no_terminal_success(journey, monkeypatch, mode, single_step):
    j = journey([CORRECTED], mode=mode, workspace=False)
    real_provider = j.manager.execute
    def provider(*args, **kwargs):
        result = real_provider(*args, **kwargs)
        path = j.iteration / 'iteration.json'; metadata = json.loads(path.read_text())
        metadata['workflow_completion_trusted'] = False
        path.write_text(json.dumps(metadata))
        return result
    j.manager.execute = provider
    import cafe.core.workflow_runtime as runtime_module
    real_write = runtime_module.atomic_write_bytes
    def fail(path, payload):
        if path == j.iteration / 'iteration.json':
            raise OSError('injected completion marker write failure')
        return real_write(path, payload)
    monkeypatch.setattr(runtime_module, 'atomic_write_bytes', fail)
    with pytest.raises(OSError): j.runtime.run(start_step='inspect_custom', single_step=single_step)
    state = j.runtime.blackboard_store.load_read_only()
    events = list(j.runtime.blackboard_store.audit.iter_records(state.workflow_id))
    completions = [e for e in events if e['step'] == 'inspect_custom' and e['event_type'] in {'step_completed', 'single_step_completed'}]
    from tests.unit.test_workflow_progress_renderer import _module
    status = _module()._runtime_progress(j.issue, j.playbook)[0]['inspect_custom']
    assert not completions and status != 'completed'


@pytest.mark.parametrize('mode,status_bytes', [
    ('baton', 0), ('legacy', 0), ('baton', 130_630), ('legacy', 60_000)])
@pytest.mark.parametrize('single_step', [False, True])
@pytest.mark.parametrize('boundary', ['marker_after', 'audit_before', 'audit_after'])
def test_issue646_blk005_interrupted_publication_reenters_without_reexecuting_work(journey, monkeypatch, mode, single_step, boundary, status_bytes):
    from cafe.core.audit_events import AuditEventStore
    from tests.unit.test_workflow_progress_renderer import _module
    import cafe.core.workflow_runtime as runtime_module
    j = journey([CORRECTED], mode=mode, workspace=False)
    callbacks = []
    j.runtime._workflow_event_callback = lambda event: callbacks.append(event)
    provider = j.manager.execute
    def retained(*args, **kwargs):
        result = provider(*args, **kwargs)
        path = j.iteration / 'iteration.json'; data = json.loads(path.read_text())
        data.update(workflow_completion_trusted=False, workspace_completion={'consumed': 3},
                    workspace_publication={'original': {'status': 'unknown'}})
        path.write_text(json.dumps(data))
        if status_bytes:
            (j.issue / 'next_step.txt').write_text(json.dumps({
                'version': 1, 'to_owner': 'agent', 'to_step': 'deliver_custom',
                'intent': 'await_agent', 'status_code': 'S' * status_bytes}))
        return result
    j.manager.execute = retained
    write, commit = runtime_module.atomic_write_bytes, AuditEventStore.commit
    failed = False
    def fail_write(path, payload):
        nonlocal failed
        write(path, payload)
        if boundary == 'marker_after' and path == j.iteration / 'iteration.json' and not failed:
            failed = True; raise OSError('marker committed before return failure')
    def fail_audit(self, workflow_id, record):
        nonlocal failed
        if record['event_type'] in {'step_completed', 'single_step_completed'} and not failed:
            failed = True
            if boundary == 'audit_after': commit(self, workflow_id, record)
            raise OSError('terminal audit publication boundary')
        return commit(self, workflow_id, record)
    monkeypatch.setattr(runtime_module, 'atomic_write_bytes', fail_write)
    if boundary != 'marker_after': monkeypatch.setattr(AuditEventStore, 'commit', fail_audit)
    with pytest.raises(OSError): j.runtime.run(start_step='inspect_custom', single_step=single_step)
    expected_next = 2 if boundary == 'audit_after' else 1
    assert next_runnable_iteration_number(j.issue / 'inspect_custom') == expected_next
    rendered = _module()._runtime_progress(j.issue, j.playbook)[0]['inspect_custom']
    assert (rendered == 'completed') is (boundary == 'audit_after')
    assert not any(e.get('event_type') == 'phase_terminal' for e in callbacks)
    monkeypatch.setattr(runtime_module, 'atomic_write_bytes', write)
    monkeypatch.setattr(AuditEventStore, 'commit', commit)
    # Existing legal completion reentry must use saved artifacts, not the provider.
    resumed = BlackboardWorkflowRuntime(issue_dir=j.issue, playbook=j.playbook,
        executor=j.executor.execute_step, workflow_event_callback=lambda event: callbacks.append(event))
    result = resumed.run(start_step='inspect_custom', single_step=True)
    assert not result.completed
    assert resumed.blackboard.current_step == 'deliver_custom'
    assert len(j.manager.calls) == 1 and j.manager.deliveries == 0
    assert j.effects == ['prepare', 'after']
    saved = json.loads((j.iteration / 'iteration.json').read_text())
    assert saved['session_id'] == 'exact-report-session'
    assert saved['workspace_completion']['consumed'] == 3
    assert saved['workspace_publication'] == {'original': {'status': 'unknown'}}
    assert next_runnable_iteration_number(j.issue / 'inspect_custom') == 2
    events = list(resumed.blackboard_store.audit.iter_records(resumed.blackboard.workflow_id))
    assert sum(e['step'] == 'inspect_custom' and e['event_type'] in {'step_completed', 'single_step_completed'} for e in events) == 1
    if not status_bytes:
        assert sum(e.get('event_type') == 'phase_terminal' and e.get('step') == 'inspect_custom' for e in callbacks) == 1
    elif mode == 'baton':
        terminal = next(e for e in events if e['step'] == 'inspect_custom'
                        and e['event_type'] in {'step_completed', 'single_step_completed'})
        size = resumed.blackboard_store.audit._path(terminal['sequence']).stat().st_size
        assert 262_080 < size <= 262_144


@pytest.mark.parametrize('case', ['malformed', 'oversize', 'serialized_oversize', 'read_failure'])
def test_issue646_blk005_public_read_and_capacity_refusal_preserves_original_without_success(journey, monkeypatch, case):
    import cafe.core.workflow_runtime as module
    from tests.unit.test_workflow_progress_renderer import _module
    j = journey([CORRECTED], workspace=False)
    publish = j.runtime._record_step_completion
    original = []
    def seed(**kwargs):
        path = j.iteration / 'iteration.json'
        data = json.loads(path.read_text()); data['workflow_completion_trusted'] = False
        data['workspace_completion'] = {'consumed': 3}
        if case == 'malformed': payload = b'{invalid'
        elif case == 'oversize': payload = json.dumps(data).encode() + b' ' * 1_048_577
        elif case == 'serialized_oversize':
            data['padding'] = ''
            data['padding'] = 'x' * (1_048_576 - len(json.dumps(data, separators=(',', ':')).encode()))
            payload = json.dumps(data, separators=(',', ':')).encode()
        else: payload = json.dumps(data).encode()
        path.write_bytes(payload); original.append(payload)
        if case == 'read_failure':
            reader = module.load_restart_context
            def unreadable(candidate):
                if candidate == path: raise OSError('saved control read failure')
                return reader(candidate)
            monkeypatch.setattr(module, 'load_restart_context', unreadable)
        return publish(**kwargs)
    monkeypatch.setattr(j.runtime, '_record_step_completion', seed)
    with pytest.raises((OSError, RuntimeError)): j.runtime.run(start_step='inspect_custom', single_step=True)
    assert (j.iteration / 'iteration.json').read_bytes() == original[0]
    assert not any(e['event_type'] in {'step_completed', 'single_step_completed'} for e in
        j.runtime.blackboard_store.audit.iter_records(j.runtime.blackboard.workflow_id))
    if case == 'malformed':
        # The established reader refuses malformed source rather than rendering
        # any status; refusal must not be counted as successful completion.
        with pytest.raises(ValueError): _module()._runtime_progress(j.issue, j.playbook)
    else:
        assert _module()._runtime_progress(j.issue, j.playbook)[0]['inspect_custom'] != 'completed'


@pytest.mark.parametrize('boundary', ['before', 'after'])
def test_issue646_blk005_optional_receipt_compaction_failure_keeps_committed_completion(journey, monkeypatch, boundary):
    import cafe.core.workflow_runtime as module
    from tests.unit.test_workflow_progress_renderer import _module
    j = journey([CORRECTED], workspace=False)
    provider = j.manager.execute
    def retained(*args, **kwargs):
        result = provider(*args, **kwargs)
        path = j.iteration / 'iteration.json'; data = json.loads(path.read_text())
        data['workflow_completion_trusted'] = False; path.write_text(json.dumps(data))
        return result
    j.manager.execute = retained
    write = module.atomic_write_bytes
    def fail(path, payload):
        compacting = path == j.iteration / 'iteration.json' and 'workflow_completion_trusted' not in json.loads(payload)
        if compacting:
            if boundary == 'after': write(path, payload)
            raise OSError('optional receipt compaction boundary')
        write(path, payload)
    monkeypatch.setattr(module, 'atomic_write_bytes', fail)
    j.runtime.run(start_step='inspect_custom', single_step=True)
    assert j.runtime.blackboard.current_step == 'deliver_custom'
    assert next_runnable_iteration_number(j.issue / 'inspect_custom') == 2
    assert _module()._runtime_progress(j.issue, j.playbook)[0]['inspect_custom'] == 'completed'
    assert len(j.manager.calls) == 1
    if boundary == 'before':
        # A changed original control invalidates a retained receipt at both
        # consumers; an old completed audit cannot conceal that conflict.
        path = j.iteration / 'iteration.json'; data = json.loads(path.read_text())
        data['session_id'] = 'another-session'; path.write_text(json.dumps(data))
        assert next_runnable_iteration_number(j.issue / 'inspect_custom') == 1
        assert _module()._runtime_progress(j.issue, j.playbook)[0]['inspect_custom'] != 'completed'
