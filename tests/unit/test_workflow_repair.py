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
