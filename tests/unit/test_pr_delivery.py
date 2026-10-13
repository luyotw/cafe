"""issue646 U14–U16/I8/I9: authorized PR observations never replay publication."""
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from tests.unit.test_file_scope import repository, git


@pytest.fixture
def publication(repository, monkeypatch):
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.git_delivery import remote_identity
    from cafe.core.execution_checkpoints import checkpoint
    from cafe.core.capabilities import HOST_CAPABILITY_ADAPTERS
    root, baseline = repository
    git(root, 'checkout', '-qb', 'feature')
    git(root, 'remote', 'add', 'origin', 'https://github.com/example/project.git')
    issue = root / '.cafe/issues/pr-case'
    store = BlackboardStore(issue)
    state = store.load_or_create('ship_custom', playbook_id='custom')
    store.ensure_baton(state)
    output = issue / 'ship_custom/iteration_001/pr.md'
    output.parent.mkdir(parents=True)
    output.write_text('# Authorized delivery\n')
    context = {'version': 1, 'root': str(root), 'baseline_commit': baseline, 'paths': ['allowed'],
        'preexisting': [], 'authority_digest': 'a' * 64, 'revision': 1,
        'identity': {'issue_name': 'pr-case', 'workflow_id': state.workflow_id},
        'delivery_endpoint': {'route': 'pr', 'remote': 'origin', 'source_branch': 'feature',
            'target_branch': 'main', 'remote_identity': remote_identity(root, 'origin', 'pr'),
            'effects': ['push', 'pr']}}
    saved = checkpoint(context, 'before_delivery', round_id='ship_custom', parent_id='original-parent')
    (issue / 'execution_delivery.json').write_text(json.dumps({'authority_digest': context['authority_digest'],
        'endpoint': context['delivery_endpoint'], 'checkpoint': saved}))
    (issue / 'saved-context.json').write_text(json.dumps(context))
    url = 'https://github.com/example/project/pull/1'
    cli = {'url': url, 'number': 1, 'headRefName': 'feature', 'baseRefName': 'main',
           'headRefOid': git(root, 'rev-parse', 'HEAD'), 'state': 'OPEN',
           'headRepository': {'nameWithOwner': ''}}
    rest = {'html_url': url, 'number': 1, 'state': 'open', 'merged': False,
        'head': {'ref': 'feature', 'sha': cli['headRefOid'], 'repo': {'full_name': 'example/project'}},
        'base': {'ref': 'main', 'repo': {'full_name': 'example/project'}}}
    calls, receipts = [], []
    def publish(**kwargs):
        calls.append(('publish', kwargs['request'].args))
        receipts.append(json.loads((issue / 'delivery_result.json').read_text()))
        return {'pr_url': url, 'pr_number': '1', 'action': 'created'}, None
    monkeypatch.setitem(HOST_CAPABILITY_ADAPTERS, 'sync_pr', publish)
    original = subprocess.run
    def transport(argv, **kwargs):
        if argv[0] == 'gh':
            calls.append(('read', list(argv)))
            if argv[1:3] == ['repo', 'view']: return subprocess.CompletedProcess(argv, 0, 'example/project\n', '')
            if argv[1:3] == ['pr', 'view']: return subprocess.CompletedProcess(argv, 0, json.dumps(cli), '')
            if argv[1] == 'api': return subprocess.CompletedProcess(argv, 0, json.dumps(rest), '')
            raise AssertionError('unexpected GitHub mutation or read')
        return original(argv, **kwargs)
    monkeypatch.setattr(subprocess, 'run', transport)
    return dict(root=root, issue=issue, output=output, context=context, cli=cli, rest=rest,
                calls=calls, before=receipts, transport=transport, url=url)


def publisher(p, **kwargs):
    from cafe.core.pr_delivery import publish_resolved_pr
    return publish_resolved_pr(p['issue'], p['root'], p['output'], context=p['context'],
                               before_dispatch=lambda: None, **kwargs)


def test_u14_i8_missing_cli_identity_uses_matching_trusted_rest(publication):
    p = publication
    result = publisher(p)
    assert result['delivered'] and result['status'] == 'succeeded'
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1
    assert any(kind == 'read' and args[1] == 'api' for kind, args in p['calls'])
    original = p['before'][0]
    assert original['request']['args']['head_oid'] == p['cli']['headRefOid']
    assert original['head_repository'] == original['base_repository'] == 'example/project'
    assert original['endpoint'] == p['context']['delivery_endpoint']
    assert original['attempt_id']
    with pytest.raises(ValueError): publisher(p)
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


@pytest.mark.parametrize('field', ['url', 'number', 'head_repo', 'base_repo', 'source', 'base', 'sha', 'state'])
def test_u14_mismatched_observation_cannot_be_overwritten_by_supplement(publication, field):
    p = publication
    if field == 'url': p['cli']['url'] = 'https://github.com/other/project/pull/1'
    elif field == 'number': p['cli']['number'] = 2
    elif field == 'head_repo': p['cli']['headRepository']['nameWithOwner'] = 'other/project'
    elif field == 'base_repo': p['rest']['base']['repo']['full_name'] = 'other/project'
    elif field == 'source': p['cli']['headRefName'] = 'other'
    elif field == 'base': p['cli']['baseRefName'] = 'other'
    elif field == 'sha': p['cli']['headRefOid'] = 'b' * 40
    elif field == 'state': p['cli']['state'] = 'CLOSED'
    with pytest.raises(ValueError): publisher(p)
    saved = json.loads((p['issue'] / 'delivery_result.json').read_text())
    assert saved['status'] == 'unknown' and saved['receipt']['success']
    assert saved['verification']['status'] == 'mismatch'


def test_u15_receipt_is_durable_before_observation_failure(publication, monkeypatch):
    p = publication
    def fail(argv, **kwargs):
        if argv[:3] == ['gh', 'pr', 'view'] or argv[:2] == ['gh', 'api']:
            raise subprocess.CalledProcessError(9, argv, stderr='unavailable')
        return p['transport'](argv, **kwargs)
    monkeypatch.setattr(subprocess, 'run', fail)
    try: publisher(p)
    except subprocess.CalledProcessError: pass
    saved = json.loads((p['issue'] / 'delivery_result.json').read_text())
    assert saved['status'] == 'unknown' and saved['receipt']['success']
    assert saved['receipt']['outputs']['pr_url'] == p['url']
    assert saved['original_attempt']['status'] == 'unknown'
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


@pytest.mark.parametrize('allowed', [False, True])
def test_u14_merged_only_passes_original_confirmed_condition(allowed):
    from cafe.core.pr_delivery import verify_pr_identity
    expected = {'url': 'https://github.com/example/project/pull/1', 'number': 1,
        'head_repository': 'example/project', 'base_repository': 'example/project',
        'source_branch': 'feature', 'target_branch': 'main', 'head_oid': 'a' * 40,
        'allowed_states': ['OPEN', 'MERGED'] if allowed else ['OPEN']}
    rest = {'html_url': expected['url'], 'number': 1, 'state': 'closed', 'merged': True,
        'head': {'ref': 'feature', 'sha': 'a' * 40, 'repo': {'full_name': 'example/project'}},
        'base': {'ref': 'main', 'repo': {'full_name': 'example/project'}}}
    assert verify_pr_identity(expected, {}, rest)['status'] == ('verified' if allowed else 'mismatch')


def pr_repair(p, execute=True):
    from cafe.core.workflow_repair import repair_workflow
    return repair_workflow(p['issue'], kind='pr-result', step='ship_custom', iteration=1,
        execute=execute, execution_context_file=p['issue'] / 'saved-context.json')


@pytest.fixture
def unknown_publication(publication, monkeypatch):
    p = publication
    def unavailable(argv, **kwargs):
        if argv[:3] == ['gh', 'pr', 'view'] or argv[:2] == ['gh', 'api']:
            raise subprocess.CalledProcessError(8, argv)
        return p['transport'](argv, **kwargs)
    monkeypatch.setattr(subprocess, 'run', unavailable)
    publisher(p)
    monkeypatch.setattr(subprocess, 'run', p['transport'])
    return p


def test_u16_bound_unknown_reconciliation_is_additive_and_idempotent(unknown_publication):
    p = unknown_publication
    path = p['issue'] / 'delivery_result.json'
    original = json.loads(path.read_text())
    count = sum(kind == 'publish' for kind, _ in p['calls'])
    assert pr_repair(p, False)['status'] == 'verified'
    assert json.loads(path.read_text()) == original
    assert pr_repair(p)['status'] == 'verified'
    saved = json.loads(path.read_text())
    assert saved['status'] == 'unknown' and saved['delivered'] is False
    assert saved['reconciliation']['original'] == original
    assert saved['reconciliation']['effective_status'] == 'succeeded'
    bytes_after = path.read_bytes()
    assert pr_repair(p)['status'] == 'no-op'
    assert path.read_bytes() == bytes_after
    assert sum(kind == 'publish' for kind, _ in p['calls']) == count


@pytest.fixture
def historical_publication(publication, monkeypatch):
    from cafe.core.capabilities import load_capability_registry, default_capability_definition_dirs
    from cafe.core.capability_approvals import CapabilityApprovalService
    p = publication
    registry = dict(load_capability_registry(default_capability_definition_dirs(p['root'])))
    registry['cafe.pr.publish'] = registry['cafe.pr.publish'].model_copy(update={'approval': 'required'})
    pending = publisher(p, registry=registry)
    service = CapabilityApprovalService(issue_dir=p['issue'], workflow_id=p['context']['identity']['workflow_id'], step='delivery', iteration=1)
    approval = service.inspect(pending['task_id'])
    # Genuine original approval execution receipt is saved by the owning service.
    service.record_decision(pending['task_id'], {'decision': 'approve',
        'workflow_id': p['context']['identity']['workflow_id'], 'task_id': pending['task_id'],
        'request_fingerprint': approval['fingerprint'], 'correlation_id': approval['correlation_id']})
    def unavailable(argv, **kwargs):
        if argv[:3] == ['gh', 'pr', 'view'] or argv[:2] == ['gh', 'api']:
            raise subprocess.CalledProcessError(8, argv)
        return p['transport'](argv, **kwargs)
    monkeypatch.setattr(subprocess, 'run', unavailable)
    publisher(p, registry=registry, approval_task_id=pending['task_id'], correlation_id=approval['correlation_id'])
    monkeypatch.setattr(subprocess, 'run', p['transport'])
    record = json.loads((p['issue'] / 'delivery_result.json').read_text())
    original = {key: record[key] for key in ['delivered', 'status', 'authority_digest', 'commit']}
    (p['issue'] / 'delivery_result.json').write_text(json.dumps(original))
    p.update(original=original, approval_task_id=pending['task_id'])
    return p


def test_u16_historical_four_field_record_uses_genuine_independent_approval_receipt(historical_publication):
    p = historical_publication
    assert set(json.loads((p['issue'] / 'delivery_result.json').read_text())) == {'delivered', 'status', 'authority_digest', 'commit'}
    original_tasks = (p['issue'] / 'human_tasks.json').read_bytes()
    result = pr_repair(p)
    assert result['status'] == 'verified'
    saved = json.loads((p['issue'] / 'delivery_result.json').read_text())
    assert saved['reconciliation']['original'] == p['original']
    assert saved['reconciliation']['association']['receipt_source']['task_id'] == p['approval_task_id']
    assert (p['issue'] / 'human_tasks.json').read_bytes() == original_tasks
    assert pr_repair(p)['status'] == 'no-op'
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


@pytest.mark.parametrize('defect', ['absent_receipt', 'authority', 'commit', 'request', 'workflow',
    'remote', 'source', 'base', 'correlation', 'fingerprint', 'pr_number', 'ambiguous'])
def test_u16_historical_binding_conflicts_refuse_without_guessing_intent(historical_publication, defect):
    p = historical_publication
    path = p['issue'] / 'human_tasks.json'
    data = json.loads(path.read_text())
    task = data['tasks'][p['approval_task_id']] if isinstance(data['tasks'], dict) else next(t for t in data['tasks'] if t['id'] == p['approval_task_id'])
    approval = task['capability_approval']
    if defect == 'absent_receipt': approval.pop('receipt')
    elif defect == 'authority':
        old = json.loads((p['issue'] / 'delivery_result.json').read_text());old['authority_digest'] = 'b' * 64
        (p['issue'] / 'delivery_result.json').write_text(json.dumps(old))
    elif defect == 'commit':
        old = json.loads((p['issue'] / 'delivery_result.json').read_text());old['commit'] = 'b' * 40
        (p['issue'] / 'delivery_result.json').write_text(json.dumps(old))
    elif defect == 'workflow': approval['receipt']['workflow_id'] = 'other'
    elif defect == 'correlation': approval['receipt']['correlation_id'] = 'other'
    elif defect == 'fingerprint': approval['receipt']['request_fingerprint'] = 'b' * 64
    elif defect == 'pr_number': approval['receipt']['execution']['outputs']['pr_number'] = '2'
    elif defect == 'ambiguous':
        # Two independently attributed genuine-looking receipts cannot be picked by convenience.
        copy = deepcopy(task); copy['id'] = 'other-task'; copy['capability_approval']['receipt']['task_id'] = 'other-task'
        if isinstance(data['tasks'], dict): data['tasks']['other-task'] = copy
        else: data['tasks'].append(copy)
    else:
        key = {'request': 'output', 'remote': 'remote', 'source': 'source_branch', 'base': 'base'}[defect]
        approval['receipt']['request']['args'][key] = 'other'
    path.write_text(json.dumps(data))
    before = (p['issue'] / 'delivery_result.json').read_bytes(), path.read_bytes()
    assert pr_repair(p)['status'] == 'refused'
    assert ((p['issue'] / 'delivery_result.json').read_bytes(), path.read_bytes()) == before
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


@pytest.mark.parametrize('observation', ['unavailable', 'malformed', 'source', 'base_repo', 'sha'])
def test_u16_observation_unknown_or_mismatch_is_explicit_without_replay(unknown_publication, monkeypatch, observation):
    p = unknown_publication
    if observation == 'source': p['rest']['head']['ref'] = 'other'
    elif observation == 'base_repo': p['rest']['base']['repo']['full_name'] = 'other/project'
    elif observation == 'sha': p['rest']['head']['sha'] = 'b' * 40
    else:
        def fail(argv, **kwargs):
            if argv[:3] == ['gh', 'pr', 'view'] or argv[:2] == ['gh', 'api']:
                if observation == 'malformed': return subprocess.CompletedProcess(argv, 0, '{broken', '')
                raise subprocess.CalledProcessError(9, argv)
            return p['transport'](argv, **kwargs)
        monkeypatch.setattr(subprocess, 'run', fail)
    result = pr_repair(p)
    assert result['status'] == ('unresolved' if observation in ['unavailable', 'malformed'] else 'mismatch')
    saved = json.loads((p['issue'] / 'delivery_result.json').read_text())
    assert saved['status'] == 'unknown' and saved['delivered'] is False
    assert not saved['reconciliation']['effective_delivered']
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


def test_u16_historical_independent_capability_store_proves_original_receipt(unknown_publication):
    from cafe.core.blackboard import BlackboardStore
    p = unknown_publication
    path = p['issue'] / 'delivery_result.json'
    record = json.loads(path.read_text())
    store = BlackboardStore(p['issue'])
    state = store.load_or_create('ship_custom')
    # Persist genuine original request and actual owner-produced receipt through
    # an existing store, independently of the old minimal attempt record.
    store.append_capability_receipt(state, dict(record['receipt'], request=record['request']))
    original = {k: record[k] for k in ['delivered', 'status', 'authority_digest', 'commit']}
    path.write_text(json.dumps(original))
    assert pr_repair(p)['status'] == 'verified'
    assert json.loads(path.read_text())['reconciliation']['original'] == original
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


@pytest.mark.parametrize('boundary', ['before', 'after'])
def test_u16_result_write_failure_reentry_retains_same_attempt(unknown_publication, monkeypatch, boundary):
    from cafe.core import workflow_repair
    p = unknown_publication
    write = workflow_repair.atomic_write_bytes
    failed = False
    def injection(path, payload):
        nonlocal failed
        if path == p['issue'] / 'delivery_result.json' and not failed:
            failed = True
            if boundary == 'after': write(path, payload)
            raise OSError('result persistence interruption')
        write(path, payload)
    monkeypatch.setattr(workflow_repair, 'atomic_write_bytes', injection)
    assert pr_repair(p)['status'] == 'interrupted'
    assert pr_repair(p)['status'] == ('verified' if boundary == 'before' else 'no-op')
    assert pr_repair(p)['status'] == 'no-op'
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


def test_u16_original_commit_binding_survives_unrelated_current_head(unknown_publication):
    p = unknown_publication
    original = p['cli']['headRefOid']
    git(p['root'], 'commit', '--allow-empty', '-qm', 'later unrelated bookkeeping')
    assert git(p['root'], 'rev-parse', 'HEAD') != original
    result = pr_repair(p)
    assert result['status'] == 'verified', result
    saved = json.loads((p['issue'] / 'delivery_result.json').read_text())
    assert saved['commit'] == original and saved['reconciliation']['association']['commit'] == original
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1
