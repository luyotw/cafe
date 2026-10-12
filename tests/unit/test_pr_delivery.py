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
