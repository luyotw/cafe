"""issue646 I2–I4/I7/I9: public repair never replays execution or decisions."""
import json
import pytest
from typer.testing import CliRunner
from cafe.ui.cli import app


@pytest.mark.parametrize('kind', ['iteration', 'readiness'])
@pytest.mark.parametrize('execute', [False, True])
def test_removed_repair_refuses_without_touching_saved_evidence(tmp_path, monkeypatch, execute, kind):
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
    args = ['repair', '--issue', issue.name, '--kind', kind,
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


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('orphan_owner', [False, True])
def test_workflow_recovers_missing_original_task_before_user_wait_or_agent(journey, monkeypatch, orphan_owner, background):
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.human_task_records import HumanTaskRecordStore
    j = journey([CORRECTED], workspace=False)
    materialize = HumanTaskRecordStore.materialize_with_status
    def stop(*args, **kwargs):
        raise OSError('task materialization boundary')
    monkeypatch.setattr(HumanTaskRecordStore, 'materialize_with_status', stop)
    with pytest.raises(OSError):
        j.runtime._pause_for_agent_execution_interruption(current_step='inspect_custom',
            reason='agent_error', runtime='original')
    monkeypatch.setattr(HumanTaskRecordStore, 'materialize_with_status', materialize)
    if orphan_owner:
        j.runtime.blackboard_store.set_current_step(j.runtime.blackboard, 'user')
    args = ['workflow', '--execute', '--issue', 'correction', '--playbook', 'correction']
    if background:
        from cafe.workflow_execution.worker_launch import WorkerLaunchStore
        launches = WorkerLaunchStore(j.issue)
        launch = launches.start()
        launches.mark(launch['worker_id'], 'started')
        args += ['--internal-worker-id', launch['worker_id'], '--internal-worker-token', launch['worker_token']]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.stdout
    assert 'Recovered original interruption task' in result.stdout
    records = HumanTaskRecordStore(j.issue)
    assert len(records.tasks()) == 1 and not records.results()
    state = BlackboardStore(j.issue).load_read_only()
    assert state.current_step == 'user'
    assert records.tasks()[0].handoff_key.endswith(state.handoff_contract.created_at)
    assert not j.manager.calls and not j.manager.deliveries
