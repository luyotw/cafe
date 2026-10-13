"""issue646 U3–U8/U13/U16: explicit repairs preserve original authority."""
import json
import os
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore


@pytest.fixture
def legacy(tmp_path):
    issue = tmp_path / '.cafe' / 'issues' / 'repair-case'
    store = BlackboardStore(issue)
    state = store.load_or_create('compose_custom', playbook_id='custom')
    store.ensure_baton(state)
    directory = issue / 'compose_custom' / 'iteration_001'
    directory.mkdir(parents=True)
    records = [{'sequence': i, 'text': 'x' * 600} for i in range(2366)]
    controls = {'iteration': 1, 'session_id': 'original-session', 'model': 'original-model',
                'artifact_correction': {'consumed': 2}, 'checkpoint': {'receipt_id': 'original'},
                'approval': {'id': 'original'}, 'streaming_log': records, 'padding': ''}
    overhead = len(json.dumps(controls, ensure_ascii=False, indent=2).encode())
    controls['padding'] = 'x' * (1_712_660 - overhead)
    path = directory / 'iteration.json'
    path.write_text(json.dumps(controls, ensure_ascii=False, indent=2))
    raw = directory / 'streaming.jsonl'
    raw.write_text(''.join(json.dumps(r) + '\n' for r in records))
    return issue, path, raw, controls


def repair(legacy, execute=True, **kwargs):
    from cafe.core.workflow_repair import repair_workflow
    return repair_workflow(legacy[0], kind='iteration', step='compose_custom', iteration=1,
                           execute=execute, **kwargs)


def events(issue):
    from cafe.core.audit_events import AuditEventStore
    state = json.loads((issue / 'blackboard.json').read_text())
    return list(AuditEventStore(issue).iter_records(state['workflow_id']))


def test_u3_u5_incident_exact_repair_preview_and_completed_noop(legacy):
    issue, path, raw, controls = legacy
    original, raw_bytes, identity = path.read_bytes(), raw.read_bytes(), raw.stat().st_ino
    assert len(original) == 1_712_660
    assert repair(legacy, execute=False)['status'] == 'proposed'
    assert path.read_bytes() == original and not events(issue)
    result = repair(legacy)
    assert result['status'] == 'repaired'
    assert json.loads(path.read_text()) == {k: v for k, v in controls.items() if k != 'streaming_log'}
    assert raw.read_bytes() == raw_bytes and raw.stat().st_ino == identity
    proof = events(issue)
    assert [e['event_type'] for e in proof] == ['iteration_repair_prepared', 'iteration_repair_completed']
    assert proof[0]['data']['record_count'] == 2366
    assert proof[0]['data']['original_sha256'] != proof[0]['data']['target_sha256']
    final = path.read_bytes(), events(issue)
    assert repair(legacy)['status'] == 'no-op'
    assert (path.read_bytes(), events(issue)) == final


@pytest.mark.parametrize('defect', ['missing', 'reordered', 'truncated', 'extra', 'malformed',
    'duplicate_key', 'symlink', 'hardlink', 'overload', 'deep', 'nonregular'])
def test_u3_u4_refusals_preserve_original_bytes(legacy, defect):
    issue, path, raw, controls = legacy
    if defect == 'missing': raw.unlink()
    elif defect == 'reordered': raw.write_text('\n'.join(reversed(raw.read_text().splitlines())) + '\n')
    elif defect == 'truncated': raw.write_bytes(raw.read_bytes()[:-10])
    elif defect == 'extra':
        with raw.open('a') as handle: handle.write('{}\n')
    elif defect == 'malformed': raw.write_text('{bad\n')
    elif defect == 'duplicate_key': raw.write_text('{"sequence":0,"sequence":1}\n')
    elif defect == 'symlink':
        moved = raw.with_suffix('.saved'); raw.rename(moved); raw.symlink_to(moved)
    elif defect == 'hardlink': os.link(raw, raw.with_suffix('.alias'))
    elif defect == 'nonregular': raw.unlink(); raw.mkdir()
    elif defect == 'overload':
        controls['padding'] = 'x' * 1_048_576; path.write_text(json.dumps(controls))
    elif defect == 'deep': path.write_text('{"streaming_log":' + '[' * 65 + '0' + ']' * 65 + '}')
    before = path.read_bytes()
    assert repair(legacy)['status'] == 'refused'
    assert path.read_bytes() == before and not events(issue)


@pytest.mark.parametrize('boundary', ['prepared_before', 'prepared_after', 'replace_after',
                                      'completed_before', 'completed_after'])
def test_u5_every_audit_replacement_failure_recovers_same_proof(legacy, monkeypatch, boundary):
    from cafe.core import workflow_repair
    from cafe.core.audit_events import AuditEventStore
    issue, path, raw, _ = legacy
    original, raw_bytes = path.read_bytes(), raw.read_bytes()
    commit = AuditEventStore.commit
    replace = workflow_repair.atomic_write_bytes
    failed = False
    def audit_failure(self, workflow_id, record):
        nonlocal failed
        selected = ('prepared' in boundary and record['event_type'] == 'iteration_repair_prepared') or (
            'completed' in boundary and record['event_type'] == 'iteration_repair_completed')
        if selected and not failed:
            failed = True
            if boundary.endswith('after'): commit(self, workflow_id, record)
            raise OSError('injected durable boundary')
        return commit(self, workflow_id, record)
    def replacement_failure(target, payload):
        nonlocal failed
        replace(target, payload)
        if target == path and boundary == 'replace_after' and not failed:
            failed = True
            raise OSError('crash after metadata replacement')
    monkeypatch.setattr(AuditEventStore, 'commit', audit_failure)
    monkeypatch.setattr(workflow_repair, 'atomic_write_bytes', replacement_failure)
    assert repair(legacy)['status'] == 'interrupted'
    if boundary.startswith('prepared'): assert path.read_bytes() == original
    assert raw.read_bytes() == raw_bytes
    result = repair(legacy)
    assert result['status'] in {'repaired', 'no-op'}
    proofs = events(issue)
    assert len([e for e in proofs if e['event_type'] == 'iteration_repair_prepared']) == 1
    assert len([e for e in proofs if e['event_type'] == 'iteration_repair_completed']) == 1
    assert proofs[0]['data']['operation_id'] == proofs[-1]['data']['operation_id']
    assert repair(legacy)['status'] == 'no-op'


@pytest.mark.parametrize('changed', ['raw_bytes', 'raw_inode', 'metadata'])
def test_u5_prepared_reentry_refuses_changed_identity(legacy, monkeypatch, changed):
    from cafe.core import workflow_repair
    original_write = workflow_repair.atomic_write_bytes
    def stop(target, payload):
        if target == legacy[1]: raise OSError('before replacement')
        original_write(target, payload)
    monkeypatch.setattr(workflow_repair, 'atomic_write_bytes', stop)
    assert repair(legacy)['status'] == 'interrupted'
    monkeypatch.setattr(workflow_repair, 'atomic_write_bytes', original_write)
    if changed == 'raw_bytes': legacy[2].write_text('{}\n')
    elif changed == 'raw_inode':
        content = legacy[2].read_bytes(); legacy[2].unlink(); legacy[2].write_bytes(content)
    else: legacy[1].write_text('{}')
    before = legacy[1].read_bytes()
    assert repair(legacy)['status'] == 'refused'
    assert legacy[1].read_bytes() == before


@pytest.mark.parametrize('resource', ['metadata', 'raw', 'record', 'count'])
def test_u3_exceptional_resource_bounds_are_finite(legacy, resource):
    if resource == 'metadata': legacy[1].write_bytes(b' ' * (16 * 1024 * 1024 + 1))
    elif resource == 'raw':
        with legacy[2].open('wb') as handle: handle.truncate(32 * 1024 * 1024 + 1)
    elif resource == 'record': legacy[2].write_bytes(b'x' * (1_048_576 + 1) + b'\n')
    else:
        legacy[1].write_text(json.dumps({'streaming_log': [{}] * 100001}))
    before = legacy[1].read_bytes()
    assert repair(legacy)['status'] == 'refused'
    assert legacy[1].read_bytes() == before


@pytest.fixture
def interrupted(tmp_path):
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    issue = tmp_path / '.cafe/issues/interrupted-case'
    playbook = {'playbook': {'id': 'bespoke'}, 'steps': {
        'compose_custom': {'skill': 'custom', 'role': 'author', 'on': {'await_agent': '_done'}}}}
    calls = []
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=playbook,
        executor=lambda **kw: calls.append('agent'))
    runtime._notify_new_human_task = lambda task: None
    directory = issue / 'compose_custom/iteration_001'
    directory.mkdir(parents=True)
    controls = {'iteration': 1, 'session_id': 'same-session', 'model': 'same-model',
        'cli': 'codex', 'workspace_completion': {'consumed': 2},
        'checkpoint': {'receipt_id': 'genuine'}, 'approval': {'id': 'original'}}
    (directory / 'iteration.json').write_text(json.dumps(controls))
    return runtime, directory, controls, calls


def interrupt(runtime):
    return runtime._pause_for_agent_execution_interruption(
        current_step='compose_custom', reason='agent_error', runtime='fixture')


def repair_interrupted(j, execute=True):
    from cafe.core.workflow_repair import repair_workflow
    return repair_workflow(j[0].issue_dir, kind='interruption', step='compose_custom',
                           iteration=1, execute=execute)


def test_u6_fallible_context_preparation_publishes_no_handoff(interrupted):
    runtime, directory, _, _ = interrupted
    path = directory / 'iteration.json'
    path.write_text(json.dumps({'unrelated': 'x' * 1_048_576}))
    original = (runtime.issue_dir / 'next_step.txt').read_bytes()
    from cafe.core.human_task_records import HumanTaskRecordStore
    with pytest.raises((RuntimeError, ValueError)):
        interrupt(runtime)
    assert (runtime.issue_dir / 'next_step.txt').read_bytes() == original
    assert not HumanTaskRecordStore(runtime.issue_dir).tasks()
    assert runtime.blackboard.current_step == 'compose_custom'


@pytest.mark.parametrize('boundary', ['prepared', 'baton', 'task_before', 'task_after', 'owner', 'pause'])
def test_u7_partial_interruption_converges_original_handoff_without_execution(interrupted, monkeypatch, boundary):
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.human_task_records import HumanTaskRecordStore
    runtime, directory, controls, calls = interrupted
    failed = False
    log, write, materialize, owner = BlackboardStore.log_event, BlackboardStore.write_handoff_contract, HumanTaskRecordStore.materialize_with_status, BlackboardStore.set_current_step
    def after_log(self, state, step, kind, message, *args, **kwargs):
        nonlocal failed
        value = log(self, state, step, kind, message, *args, **kwargs)
        if not failed and ((boundary == 'prepared' and kind == 'agent_interruption_prepared') or (boundary == 'pause' and kind == 'workflow_paused')):
            failed = True; raise OSError('audit stop')
        return value
    def after_baton(self, state, contract):
        nonlocal failed
        value = write(self, state, contract)
        if boundary == 'baton' and not failed:
            failed = True; raise OSError('baton stop')
        return value
    def task_boundary(self, **kwargs):
        nonlocal failed
        if boundary == 'task_before' and not failed:
            failed = True; raise OSError('before task')
        value = materialize(self, **kwargs)
        if boundary == 'task_after' and not failed:
            failed = True; raise OSError('after task')
        return value
    def after_owner(self, state, step):
        nonlocal failed
        value = owner(self, state, step)
        if boundary == 'owner' and not failed:
            failed = True; raise OSError('owner stop')
        return value
    monkeypatch.setattr(BlackboardStore, 'log_event', after_log)
    monkeypatch.setattr(BlackboardStore, 'write_handoff_contract', after_baton)
    monkeypatch.setattr(HumanTaskRecordStore, 'materialize_with_status', task_boundary)
    monkeypatch.setattr(BlackboardStore, 'set_current_step', after_owner)
    with pytest.raises(OSError): interrupt(runtime)
    before = json.loads((directory / 'iteration.json').read_text())
    result = repair_interrupted(interrupted)
    assert result['status'] == 'repaired'
    records = HumanTaskRecordStore(runtime.issue_dir)
    assert len(records.tasks()) == 1 and not records.results()
    task = records.tasks()[0]
    state = BlackboardStore(runtime.issue_dir).load_read_only()
    assert state.current_step == 'user' and state.handoff_contract.from_step == 'compose_custom'
    assert task.handoff_key.endswith(state.handoff_contract.created_at)
    after = json.loads((directory / 'iteration.json').read_text())
    assert all(after[k] == v for k, v in controls.items())
    assert after['agent_interruption']['id'] == state.handoff_contract.created_at
    assert not calls
    assert repair_interrupted(interrupted)['status'] == 'no-op'
    assert len(records.tasks()) == 1


@pytest.mark.parametrize('terminal', ['completed', 'cancelled'])
def test_u7_terminal_decisions_never_reopen(interrupted, terminal):
    from cafe.core.human_task_records import HumanTaskRecordStore
    runtime = interrupted[0]
    interrupt(runtime)
    records = HumanTaskRecordStore(runtime.issue_dir)
    task = records.tasks()[0]
    if terminal == 'cancelled':
        records.cancel(workflow_id=task.workflow_id, task_id=task.id, reason='simulated user cancellation')
    else:
        # This explicit simulated user action is separate from repair.
        records.complete(workflow_id=task.workflow_id, task_id=task.id,
                         payload={'decision': 'retry', 'continuation': 'compose_custom'}, source='fixture-user')
    before = (runtime.issue_dir / 'human_tasks.json').read_bytes()
    assert repair_interrupted(interrupted)['status'] == 'refused'
    assert (runtime.issue_dir / 'human_tasks.json').read_bytes() == before


def test_u8_live_or_uncertain_worker_refuses_without_writes(legacy):
    path = legacy[0] / '.workflow-worker-launches.json'
    path.write_text(json.dumps({'attempts': {'one': {'status': 'starting'}}}))
    before = legacy[1].read_bytes()
    assert repair(legacy)['status'] == 'refused'
    assert legacy[1].read_bytes() == before and not events(legacy[0])


def test_u8_real_cross_process_advancement_lease_refuses(legacy):
    import subprocess, sys
    lock = legacy[0] / '.workflow-advancement.lock'
    script = "import fcntl,sys; h=open(sys.argv[1],'a'); fcntl.flock(h,fcntl.LOCK_EX); print('held',flush=True); sys.stdin.read()"
    child = subprocess.Popen([sys.executable, '-u', '-c', script, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'held'
        original = legacy[1].read_bytes()
        assert repair(legacy)['status'] == 'refused'
        assert legacy[1].read_bytes() == original and not events(legacy[0])
    finally:
        child.communicate('', timeout=5)
    assert child.returncode == 0


def test_u7_reentered_normal_interruption_reuses_pending_identity(interrupted):
    from cafe.core.human_task_records import HumanTaskRecordStore
    runtime = interrupted[0]
    interrupt(runtime)
    first = runtime.blackboard.handoff_contract.to_dict()
    interrupt(runtime)
    assert runtime.blackboard.handoff_contract.to_dict() == first
    assert len(HumanTaskRecordStore(runtime.issue_dir).tasks()) == 1


def test_u8_workspace_lease_contention_is_bounded_refusal(legacy):
    import subprocess, sys
    lock = legacy[0].parents[2] / '.cafe/workspace-use.lock'
    script = "import fcntl,sys; h=open(sys.argv[1],'a'); fcntl.flock(h,fcntl.LOCK_EX); print('held',flush=True); sys.stdin.read()"
    child = subprocess.Popen([sys.executable, '-u', '-c', script, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'held'
        command = 'import json,sys; from pathlib import Path; from cafe.core.workflow_repair import repair_workflow; print(json.dumps(repair_workflow(Path(sys.argv[1]),kind="iteration",step="compose_custom",iteration=1,execute=True)))'
        result = subprocess.run([sys.executable, '-B', '-c', command, str(legacy[0])], capture_output=True, text=True, timeout=2)
        assert result.returncode == 0 and json.loads(result.stdout)['status'] == 'refused'
        assert not events(legacy[0])
    finally:
        child.communicate('', timeout=5)
    assert child.returncode == 0


@pytest.mark.parametrize('size', [995_000, 1_048_576, 1_048_577])
def test_u1_u3_legacy_report_control_keeps_original_admission_above_workspace_max(legacy, size):
    controls = dict(legacy[3], workspace_completion={'consumed': 2}, artifact_correction={'consumed': 1})
    controls['padding'] = ''
    slim = {k: v for k, v in controls.items() if k != 'streaming_log'}
    overhead = len(json.dumps(slim, ensure_ascii=False, indent=2).encode())
    controls['padding'] = 'x' * (size - overhead)
    legacy[1].write_text(json.dumps(controls, ensure_ascii=False, indent=2))
    before = legacy[1].read_bytes()
    result = repair(legacy)
    if size <= 1_048_576:
        assert result['status'] == 'repaired'
        assert legacy[1].stat().st_size == size
        assert json.loads(legacy[1].read_text())['workspace_completion'] == {'consumed': 2}
    else:
        assert result['status'] == 'refused' and legacy[1].read_bytes() == before


def test_u8_terminal_launch_with_live_or_reused_pid_remains_uncertain(legacy):
    (legacy[0] / '.workflow-worker-launches.json').write_text(json.dumps({
        'attempts': {'old': {'status': 'stopped', 'pid': os.getpid()}}}))
    before = legacy[1].read_bytes()
    assert repair(legacy)['status'] == 'refused'
    assert legacy[1].read_bytes() == before and not events(legacy[0])


@pytest.mark.parametrize('field,value', [('iteration', 2), ('step_name', 'another'), ('workflow_id', 'another')])
def test_u4_metadata_selection_conflicts_refuse_without_mutation(legacy, field, value):
    data = dict(legacy[3]); data[field] = value
    legacy[1].write_text(json.dumps(data))
    before = legacy[1].read_bytes()
    assert repair(legacy)['status'] == 'refused'
    assert legacy[1].read_bytes() == before and not events(legacy[0])


@pytest.mark.parametrize('missing_task', [False, True])
def test_u7_legacy_handoff_preserves_sealed_workflow_locale_and_identity(interrupted, missing_task):
    from cafe.core.human_task_records import HumanTaskRecordStore
    runtime = interrupted[0]
    runtime.blackboard.conversation_locale = 'zh-TW'
    runtime.blackboard_store.save(runtime.blackboard)
    interrupt(runtime)
    original_task = HumanTaskRecordStore(runtime.issue_dir).tasks()[0]
    audit = runtime.blackboard_store.audit
    for record in list(audit.iter_records(runtime.blackboard.workflow_id)):
        if record['event_type'] == 'agent_interruption_prepared' or missing_task and record['event_type'] in {'agent_execution_task_materialized', 'workflow_paused'}:
            audit._path(record['sequence']).unlink()
    if missing_task:
        (runtime.issue_dir / 'human_tasks.json').unlink()
    runtime.blackboard_store.set_current_step(runtime.blackboard, 'compose_custom')
    result = repair_interrupted(interrupted)
    assert result['status'] == 'repaired'
    task = HumanTaskRecordStore(runtime.issue_dir).tasks()[0]
    assert task.expected_result == original_task.expected_result
    assert task.handoff_key == original_task.handoff_key
    if not missing_task: assert task.id == original_task.id
    assert not HumanTaskRecordStore(runtime.issue_dir).results()


def test_u7_prepared_absent_context_can_recover_without_inventing_control_fields(interrupted, monkeypatch):
    from cafe.core.audit_events import AuditEventStore
    runtime, directory, _, _ = interrupted
    (directory / 'iteration.json').unlink()
    commit = AuditEventStore.commit
    failed = False
    def stop(self, workflow_id, record):
        nonlocal failed
        commit(self, workflow_id, record)
        if record['event_type'] == 'agent_interruption_prepared' and not failed:
            failed = True; raise OSError('prepared before first context write')
    monkeypatch.setattr(AuditEventStore, 'commit', stop)
    with pytest.raises(OSError): interrupt(runtime)
    assert not (directory / 'iteration.json').exists()
    assert repair_interrupted(interrupted)['status'] == 'repaired'
    saved = json.loads((directory / 'iteration.json').read_text())
    assert set(saved) == {'agent_interruption', 'workflow_completion_trusted'}


def test_u8_unsupported_lock_platform_refuses_before_repair_writes(legacy, monkeypatch):
    from cafe.workflow_execution import workflow_hosting
    monkeypatch.setattr(workflow_hosting, 'fcntl', None)
    monkeypatch.setattr(workflow_hosting, 'msvcrt', None)
    original = legacy[1].read_bytes()
    assert repair(legacy)['status'] == 'refused'
    assert legacy[1].read_bytes() == original and not events(legacy[0])
