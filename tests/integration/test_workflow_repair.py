"""issue646 I2–I4/I7/I9: public repair never replays execution or decisions."""
import json
import pytest
from typer.testing import CliRunner
from cafe.ui.cli import app


@pytest.mark.parametrize('execute', [False, True])
def test_removed_iteration_repair_refuses_without_touching_saved_evidence(tmp_path, monkeypatch, execute):
    from cafe.core.blackboard import BlackboardStore

    issue = tmp_path / '.cafe/issues/removed-repair'
    store = BlackboardStore(issue)
    store.load_or_create('compose_custom', playbook_id='custom')
    directory = issue / 'compose_custom/iteration_001'
    directory.mkdir(parents=True)
    (directory / 'iteration.json').write_text(json.dumps({
        'iteration': 1, 'session_id': 'original', 'streaming_log': [{'text': 'saved'}],
    }))
    (directory / 'streaming.jsonl').write_text('{"text":"saved"}\n')
    before = {str(p.relative_to(issue)): p.read_bytes() for p in issue.rglob('*') if p.is_file()}
    monkeypatch.chdir(tmp_path)
    args = ['repair', '--issue', issue.name, '--kind', 'iteration',
            '--step', 'compose_custom', '--iteration', '1']
    result = CliRunner().invoke(app, args + (['--execute'] if execute else []))
    assert result.exit_code == 2 and json.loads(result.stdout)['status'] == 'refused'
    assert {str(p.relative_to(issue)): p.read_bytes() for p in issue.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('single_step', [False, True])
def test_issue646_blk002_real_retry_completion_survives_runtime_hop_reset(tmp_path, single_step):
    from cafe.agents.executor import AgentExecutionError
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.core.human_task_records import HumanTaskRecordStore
    from tests.unit.test_workflow_progress_renderer import _module
    issue = tmp_path / '.cafe/issues/hop-case'
    graph = {'playbook': {'id': 'custom'}, 'steps': {
        'alpha': {'skill': 'custom', 'role': 'writer', 'behavior': {'completion': 'baton'},
                  'output_artifact': 'a', 'on': {'await_agent': 'beta'}},
        'beta': {'skill': 'custom', 'role': 'writer', 'behavior': {'completion': 'baton'},
                 'output_artifact': 'b', 'on': {'await_agent': '_done'}}}}
    failed = False
    def execute(step, definition, state, **kwargs):
        nonlocal failed
        directory = issue / step / 'iteration_001'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'iteration.json').write_text(json.dumps({'iteration': 1,
            'session_id': 'original', 'cli': 'codex', 'model': 'original'}))
        if step == 'beta' and not failed:
            failed = True
            raise AgentExecutionError('provider boundary failure', error_type='execution_error')
        path = directory / 'output.md'; path.write_text('Saved bounded result\n')
        (issue / 'next_step.txt').write_text('{"version":1,"intent":"await_agent"}')
        return StepExecutionResult(response='', artifacts={definition['output_artifact']: str(path)})
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=execute)
    runtime._notify_new_human_task = lambda task: None
    assert not runtime.run(start_step='alpha').completed
    render = lambda: _module()._runtime_progress(issue, graph)[0]['beta']
    assert render() == 'blocked'
    records = HumanTaskRecordStore(issue); task = records.tasks()[0]
    # Separate simulated user decision; repair/rendering never answers a task.
    records.complete(workflow_id=task.workflow_id, task_id=task.id,
        payload={'decision': 'retry', 'continuation': 'beta'}, source='fixture-user')
    assert render() == 'blocked'
    resumed = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=execute)
    assert resumed.run(start_step='beta', single_step=single_step).completed
    assert render() == 'completed'


from tests.unit.test_workflow_repair import interrupted, interrupt, repair_interrupted


@pytest.mark.parametrize('boundary', ['baton', 'task', 'owner'])
def test_i3_public_orphan_recovery_is_discoverable_and_typed(interrupted, monkeypatch, boundary):
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.core.human_tasks import HumanTaskPolicy, HumanTaskCompletion, validate_human_task_completion
    runtime, directory, controls, calls = interrupted
    original = {'baton': BlackboardStore.write_handoff_contract,
                'task': HumanTaskRecordStore.materialize_with_status,
                'owner': BlackboardStore.set_current_step}[boundary]
    failed = False
    def fail_after(self, *args, **kwargs):
        nonlocal failed
        result = original(self, *args, **kwargs)
        if not failed:
            failed = True
            raise OSError('after durable write')
        return result
    owner_type = HumanTaskRecordStore if boundary == 'task' else BlackboardStore
    name = {'baton': 'write_handoff_contract', 'task': 'materialize_with_status', 'owner': 'set_current_step'}[boundary]
    monkeypatch.setattr(owner_type, name, fail_after)
    with pytest.raises(OSError): interrupt(runtime)
    monkeypatch.chdir(runtime.issue_dir.parents[2])
    result = CliRunner().invoke(app, ['repair', '--issue', 'interrupted-case', '--kind', 'interruption',
        '--step', 'compose_custom', '--iteration', '1', '--execute'])
    assert result.exit_code == 0 and json.loads(result.stdout)['status'] == 'repaired'
    records = HumanTaskRecordStore(runtime.issue_dir)
    task = records.tasks()[0]
    policy = HumanTaskPolicy.model_validate(task.expected_result)
    typed = validate_human_task_completion(policy, {'decision': 'retry'})
    assert isinstance(typed, HumanTaskCompletion) and typed.decision == 'retry'
    assert not records.results() and not calls
    assert BlackboardStore(runtime.issue_dir).load_read_only().current_step == 'user'
    assert repair_interrupted(interrupted)['status'] == 'no-op'


def test_i5_real_interruption_pending_task_retry_and_completion_progress(interrupted):
    from tests.unit.test_workflow_progress_renderer import _module
    runtime = interrupted[0]
    interrupt(runtime)
    graph = {'steps': runtime.steps}
    render = lambda: _module()._runtime_progress(runtime.issue_dir, graph)[0]['compose_custom']
    assert render() == 'blocked'
    runtime.blackboard_store.record_event(runtime.blackboard, 'step_started',
        {'step': 'compose_custom', 'attempt': 2, 'iteration': 2})
    assert render() == 'in_progress'
    runtime.blackboard_store.record_event(runtime.blackboard, 'step_completed',
        {'step': 'compose_custom', 'attempt': 2, 'iteration': 2})
    assert render() == 'completed'


from tests.integration.test_workflow_artifact_correction import journey, CORRECTED


@pytest.mark.parametrize('missing_first', [True, False])
def test_i6_custom_declared_producer_validates_full_checkpoint_before_handoff(journey, missing_first):
    from cafe.core.execution_checkpoints import checkpoint
    from cafe.core.blackboard import BlackboardStore
    j = journey([CORRECTED], workspace=False)
    context = {'version': 1, 'root': str(j.repo), 'baseline_commit': __import__('subprocess').check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=j.repo, text=True).strip(), 'paths': ['owned.txt'], 'preexisting': [],
        'authority_digest': 'a' * 64, 'revision': 1,
        'identity': {'issue_name': 'correction', 'workflow_id': j.runtime.blackboard.workflow_id},
        'delivery_endpoint': {'route': 'local', 'source_branch': 'main'}}
    declaration = {'checkpoints': ['before_delivery'], 'delivery_evidence_artifact': 'bespoke_ready.json'}
    j.runtime.steps['inspect_custom']['execution'] = declaration
    j.runtime.execution_context = context
    execute = j.manager.execute
    saved = checkpoint(context, 'before_delivery', round_id='delivery-round', parent_id='parent')
    def provider(*args, **kwargs):
        result = execute(*args, **kwargs)
        if kwargs.get('phase_name') != 'deliver_custom':
            artifact = {'authority_digest': context['authority_digest'], 'endpoint': context['delivery_endpoint'],
                        'checkpoint': {'receipt_id': saved['receipt_id']} if missing_first and len(j.manager.calls) == 1 else saved}
            (j.iteration / 'bespoke_ready.json').write_text(json.dumps(artifact))
        return result
    j.manager.execute = provider
    j.runtime.run(start_step='inspect_custom', single_step=True)
    assert len(j.manager.calls) == (2 if missing_first else 1)
    readiness = json.loads((j.issue / 'execution_delivery.json').read_text())
    assert readiness['checkpoint'] == saved
    assert not __import__('cafe.core.human_task_records', fromlist=['HumanTaskRecordStore']).HumanTaskRecordStore(j.issue).tasks()
    assert BlackboardStore(j.issue).load_read_only().current_step == 'deliver_custom'


@pytest.fixture
def saved_delivery(journey):
    import subprocess, yaml
    from cafe.core.execution_checkpoints import checkpoint
    j = journey([CORRECTED], workspace=False)
    context = {'version': 1, 'root': str(j.repo), 'baseline_commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=j.repo, text=True).strip(), 'paths': ['owned.txt'], 'preexisting': [],
        'authority_digest': 'a' * 64, 'revision': 1,
        'identity': {'issue_name': 'correction', 'workflow_id': j.runtime.blackboard.workflow_id},
        'delivery_endpoint': {'route': 'local', 'source_branch': 'main'}}
    declaration = {'checkpoints': ['before_delivery'], 'delivery_evidence_artifact': 'bespoke_ready.json'}
    j.runtime.steps['inspect_custom']['execution'] = declaration
    source = j.repo / '.cafe/playbooks/correction.yaml'
    definition = yaml.safe_load(source.read_text())
    definition['steps']['inspect_custom']['execution'] = declaration
    source.write_text(yaml.safe_dump(definition))
    j.runtime.execution_context = context
    saved = checkpoint(context, 'before_delivery', round_id='inspect_custom', parent_id='exact-report-session')
    execute = j.manager.execute
    def provider(*args, **kwargs):
        result = execute(*args, **kwargs)
        readiness = {'authority_digest': context['authority_digest'], 'endpoint': context['delivery_endpoint'], 'checkpoint': saved}
        (j.iteration / 'bespoke_ready.json').write_text(json.dumps(readiness))
        (j.iteration / 'delivery_checkpoint.json').write_text(json.dumps(saved))
        records = [{'type': 'thread.started', 'thread_id': 'exact-report-session'},
                   {'type': 'turn.completed', 'usage': {}}, {'type': 'stderr_diagnostics', 'returncode': 0}]
        (j.iteration / 'streaming.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        return result
    j.manager.execute = provider
    j.runtime.run(start_step='inspect_custom', single_step=True)
    # A process-stop fixture retains verified completed artifacts and its legal
    # outbound handoff, while the producing owner update/readiness is omitted.
    j.runtime.blackboard_store.set_current_step(j.runtime.blackboard, 'inspect_custom')
    readiness = json.loads((j.iteration / 'bespoke_ready.json').read_text())
    readiness['checkpoint'] = {'receipt_id': saved['receipt_id']}
    (j.iteration / 'bespoke_ready.json').write_text(json.dumps(readiness))
    context_path = j.issue / 'saved-execution-context.json'
    context_path.write_text(json.dumps(context))
    return j, context_path, saved


def saved_repair(saved_delivery, execute=True):
    from cafe.core.workflow_repair import repair_workflow
    j, context_path, _ = saved_delivery
    return repair_workflow(j.issue, kind='readiness', step='inspect_custom', iteration=1,
                           execute=execute, execution_context_file=context_path)


def test_i7_saved_complete_evidence_repairs_without_any_new_invocation(saved_delivery):
    from cafe.core.blackboard import BlackboardStore
    j, _, saved = saved_delivery
    count = len(j.manager.calls), j.manager.deliveries
    assert saved_repair(saved_delivery, False)['status'] == 'proposed'
    assert saved_repair(saved_delivery)['status'] == 'repaired'
    assert json.loads((j.iteration / 'bespoke_ready.json').read_text())['checkpoint'] == saved
    assert BlackboardStore(j.issue).load_read_only().current_step == 'deliver_custom'
    assert (len(j.manager.calls), j.manager.deliveries) == count
    assert saved_repair(saved_delivery)['status'] == 'no-op'


@pytest.mark.parametrize('defect', ['terminal', 'exit', 'stale', 'authority', 'endpoint', 'ambiguous', 'checklist'])
def test_i7_saved_evidence_refusal_preserves_sources_and_no_replay(saved_delivery, defect):
    j, context_path, saved = saved_delivery
    if defect == 'terminal': (j.iteration / 'streaming.jsonl').write_text('{"type":"turn.started"}\n')
    elif defect == 'exit':
        p = j.iteration / 'streaming.jsonl'; p.write_text(p.read_text().replace('"returncode": 0', '"returncode": 1'))
    elif defect == 'stale': (j.repo / 'owned.txt').write_text('changed')
    elif defect in ['authority', 'endpoint']:
        data = json.loads(context_path.read_text())
        data['authority_digest' if defect == 'authority' else 'delivery_endpoint'] = 'b' * 64 if defect == 'authority' else {'route': 'other'}
        context_path.write_text(json.dumps(data))
    elif defect == 'ambiguous':
        other = dict(saved, receipt_id='different')
        (j.iteration / 'other_checkpoint.json').write_text(json.dumps(other))
    elif defect == 'checklist': (j.iteration / 'checklist.md').write_text('[ ] Required gate\n')
    before = (j.iteration / 'bespoke_ready.json').read_bytes(), (j.issue / 'next_step.txt').read_bytes()
    calls = len(j.manager.calls)
    assert saved_repair(saved_delivery)['status'] == 'refused'
    assert ((j.iteration / 'bespoke_ready.json').read_bytes(), (j.issue / 'next_step.txt').read_bytes()) == before
    assert len(j.manager.calls) == calls


@pytest.mark.parametrize('boundary', ['readiness_after', 'completed_before', 'completed_after'])
def test_i7_saved_readiness_partial_write_reentry_keeps_original_provenance(saved_delivery, monkeypatch, boundary):
    from cafe.core import workflow_repair
    from cafe.core.audit_events import AuditEventStore
    j, _, saved = saved_delivery
    replace, commit = workflow_repair.atomic_write_bytes, AuditEventStore.commit
    failed = False
    def write(path, content):
        nonlocal failed
        replace(path, content)
        if boundary == 'readiness_after' and path == j.iteration / 'bespoke_ready.json' and not failed:
            failed = True; raise OSError('after readiness replacement')
    def audit(self, workflow_id, record):
        nonlocal failed
        if boundary.startswith('completed') and record['event_type'] == 'readiness_repair_completed' and not failed:
            failed = True
            if boundary.endswith('after'): commit(self, workflow_id, record)
            raise OSError('completion audit boundary')
        return commit(self, workflow_id, record)
    monkeypatch.setattr(workflow_repair, 'atomic_write_bytes', write)
    monkeypatch.setattr(AuditEventStore, 'commit', audit)
    assert saved_repair(saved_delivery)['status'] == 'interrupted'
    assert saved_repair(saved_delivery)['status'] in {'repaired', 'no-op'}
    assert saved_repair(saved_delivery)['status'] == 'no-op'
    assert json.loads((j.iteration / 'bespoke_ready.json').read_text())['checkpoint'] == saved
    assert j.manager.deliveries == 0


def test_i7_pending_real_decision_is_never_answered_by_readiness_repair(saved_delivery):
    from cafe.core.human_task_records import HumanTaskRecordStore
    j = saved_delivery[0]
    j.runtime._pause_for_agent_execution_interruption(current_step='inspect_custom', reason='agent_error', runtime='fixture')
    records = HumanTaskRecordStore(j.issue)
    before = (j.issue / 'human_tasks.json').read_bytes()
    assert saved_repair(saved_delivery)['status'] == 'refused'
    assert not records.results() and (j.issue / 'human_tasks.json').read_bytes() == before


def test_i7_saved_readiness_public_cli_stops_at_handoff(saved_delivery):
    j, context, _ = saved_delivery
    result = CliRunner().invoke(app, ['repair', '--issue', 'correction', '--kind', 'readiness',
        '--step', 'inspect_custom', '--iteration', '1', '--execution-context-file', str(context), '--execute'])
    assert result.exit_code == 0 and json.loads(result.stdout)['to_step'] == 'deliver_custom'
    assert j.manager.deliveries == 0


from tests.unit.test_pr_delivery import publication, unknown_publication, historical_publication
from tests.unit.test_file_scope import repository


@pytest.mark.parametrize('historical', [False, True])
def test_i9_public_readonly_reconciliation_preserves_original_unknown_and_no_replay(
    request, unknown_publication, historical, monkeypatch):
    # Use one isolated repository per invocation; the historical positive case
    # is supplied through its owning approval fixture in the separate test below.
    if historical:
        from cafe.core.blackboard import BlackboardStore
        p = unknown_publication
        record = json.loads((p['issue'] / 'delivery_result.json').read_text())
        store = BlackboardStore(p['issue']); state = store.load_or_create('ship_custom')
        store.append_capability_receipt(state, dict(record['receipt'], request=record['request']))
        (p['issue'] / 'delivery_result.json').write_text(json.dumps({k: record[k] for k in ['delivered', 'status', 'authority_digest', 'commit']}))
    p = unknown_publication
    monkeypatch.chdir(p['root'])
    args = ['repair', '--issue', 'pr-case', '--kind', 'pr-result', '--step', 'ship_custom',
        '--iteration', '1', '--execution-context-file', str(p['issue'] / 'saved-context.json'), '--execute']
    before = (p['issue'] / 'next_step.txt').read_bytes()
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0 and json.loads(result.stdout)['status'] == 'verified'
    assert CliRunner().invoke(app, args).exit_code == 0
    assert (p['issue'] / 'next_step.txt').read_bytes() == before
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


def test_i9_public_historical_approval_receipt_is_not_consumed_or_answered(historical_publication, monkeypatch):
    p = historical_publication
    monkeypatch.chdir(p['root'])
    before = (p['issue'] / 'human_tasks.json').read_bytes()
    args = ['repair', '--issue', 'pr-case', '--kind', 'pr-result', '--step', 'ship_custom',
        '--iteration', '1', '--execution-context-file', str(p['issue'] / 'saved-context.json'), '--execute']
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0 and json.loads(result.stdout)['status'] == 'verified'
    assert (p['issue'] / 'human_tasks.json').read_bytes() == before
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


def test_i9_original_context_external_saved_path_keeps_original_binding(unknown_publication, monkeypatch):
    p = unknown_publication
    context = p['root'].parent / 'original-context.json'
    context.write_bytes((p['issue'] / 'saved-context.json').read_bytes())
    monkeypatch.chdir(p['root'])
    result = CliRunner().invoke(app, ['repair', '--issue', 'pr-case', '--kind', 'pr-result',
        '--step', 'ship_custom', '--iteration', '1', '--execution-context-file', str(context), '--execute'])
    assert result.exit_code == 0 and json.loads(result.stdout)['status'] == 'verified'
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


@pytest.mark.parametrize('sequence', [('mismatch', 'verified', 'mismatch'),
    ('verified', 'mismatch', 'verified'), ('unresolved', 'verified', 'unresolved')])
def test_issue646_blk003_public_reobservation_applies_current_outcome_without_replay(unknown_publication, monkeypatch, sequence):
    import subprocess
    p = unknown_publication
    path = p['issue'] / 'delivery_result.json'
    original = json.loads(path.read_text())
    args = ['repair', '--issue', 'pr-case', '--kind', 'pr-result', '--step', 'ship_custom',
            '--iteration', '1', '--execution-context-file', str(p['issue'] / 'saved-context.json'), '--execute']
    unavailable = False
    def read(argv, **kwargs):
        if unavailable and (argv[:3] == ['gh', 'pr', 'view'] or argv[:2] == ['gh', 'api']):
            raise subprocess.CalledProcessError(8, argv)
        return p['transport'](argv, **kwargs)
    monkeypatch.setattr(subprocess, 'run', read)
    monkeypatch.chdir(p['root'])
    for status in sequence:
        unavailable = status == 'unresolved'
        p['cli']['headRefName'] = 'different' if status == 'mismatch' else 'feature'
        result = CliRunner().invoke(app, args)
        assert result.exit_code == {'verified': 0, 'mismatch': 5, 'unresolved': 3}[status]
        assert json.loads(result.stdout)['status'] == status
        saved = json.loads(path.read_text())
        assert {k: v for k, v in saved.items() if k != 'reconciliation'} == original
        assert saved['reconciliation']['effective_delivered'] is (status == 'verified')
        assert saved['reconciliation']['effective_status'] == {'verified': 'succeeded', 'mismatch': 'mismatch', 'unresolved': 'unknown'}[status]
    assert len(saved['reconciliation']['observations']) == 2
    before = path.read_bytes()
    repeated = CliRunner().invoke(app, args)
    assert json.loads(repeated.stdout)['status'] == 'no-op'
    assert repeated.exit_code == {'verified': 0, 'mismatch': 5, 'unresolved': 3}[sequence[-1]]
    assert path.read_bytes() == before
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


def test_issue646_blk004_public_history_limit_plus_one_preserves_admitted_reentry(unknown_publication, monkeypatch):
    p = unknown_publication
    path = p['issue'] / 'delivery_result.json'
    original = json.loads(path.read_text())
    args = ['repair', '--issue', 'pr-case', '--kind', 'pr-result', '--step', 'ship_custom',
            '--iteration', '1', '--execution-context-file', str(p['issue'] / 'saved-context.json'), '--execute']
    monkeypatch.chdir(p['root'])
    for index in range(128):
        p['cli']['headRefName'] = f'different-{index}'
        assert CliRunner().invoke(app, args).exit_code == 5
    admitted = path.read_bytes()
    assert len(json.loads(admitted)['reconciliation']['observations']) == 128
    p['cli']['headRefName'] = 'different-128'
    for _ in range(2):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 2 and json.loads(result.stdout)['status'] == 'refused'
        assert path.read_bytes() == admitted
    p['cli']['headRefName'] = 'different-127'
    repeated = CliRunner().invoke(app, args)
    assert json.loads(repeated.stdout)['status'] == 'no-op' and repeated.exit_code == 5
    assert path.read_bytes() == admitted
    # A retained earlier observation remains applicable at the exact bound.
    p['cli']['headRefName'] = 'different-0'
    assert CliRunner().invoke(app, args).exit_code == 5
    saved = json.loads(path.read_text())
    assert len(saved['reconciliation']['observations']) == 128
    assert {k: v for k, v in saved.items() if k != 'reconciliation'} == original
    assert sum(kind == 'publish' for kind, _ in p['calls']) == 1


def test_i7_verified_saved_repair_supersedes_original_interruption_progress(saved_delivery):
    from tests.unit.test_workflow_progress_renderer import _module
    j = saved_delivery[0]
    j.runtime.blackboard_store.record_event(j.runtime.blackboard, 'step_interrupted',
        {'step': 'inspect_custom', 'iteration': 1, 'reason': 'readiness_omission'})
    render = lambda: _module()._runtime_progress(j.issue, {'steps': j.runtime.steps})[0]['inspect_custom']
    assert render() == 'blocked'
    assert saved_repair(saved_delivery)['status'] == 'repaired'
    assert render() == 'completed'


def test_i6_genuine_human_permission_handoff_is_not_machine_readiness_correction(journey):
    import subprocess
    from cafe.core.human_task_records import HumanTaskRecordStore
    j = journey([(CORRECTED, 'need_permission')], human='need_permission', workspace=False)
    context = {'version': 1, 'root': str(j.repo), 'baseline_commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=j.repo, text=True).strip(), 'paths': ['owned.txt'], 'preexisting': [],
        'authority_digest': 'a' * 64, 'revision': 1,
        'identity': {'issue_name': 'correction', 'workflow_id': j.runtime.blackboard.workflow_id},
        'delivery_endpoint': {'route': 'local'}}
    j.runtime.steps['inspect_custom']['execution'] = {'checkpoints': ['before_delivery'], 'delivery_evidence_artifact': 'bespoke_ready.json'}
    j.runtime.steps['inspect_custom']['human_tasks'] = [{'trigger': 'need_permission', 'task_id': 'decision', 'outcomes': {'feedback': 'inspect_custom'}}]
    j.runtime.execution_context = context
    j.runtime.run(start_step='inspect_custom')
    assert len(j.manager.calls) == 1
    tasks = HumanTaskRecordStore(j.issue).tasks()
    assert len(tasks) == 1 and tasks[0].trigger == 'need_permission'
    assert not (j.issue / 'execution_delivery.json').exists()
