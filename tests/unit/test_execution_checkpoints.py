"""U6/I1-I4/I8: generic, current-snapshot execution checkpoints."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_file_scope import repository


@pytest.fixture
def execution_context(repository):
    root, baseline = repository
    return {
        "version": 1,
        "root": str(root),
        "baseline_commit": baseline,
        "paths": ["allowed"],
        "preexisting": [],
        "authority_digest": "a" * 64,
        "revision": 1,
        "identity": {"issue_name": "sample", "workflow_id": "workflow"},
    }


@pytest.mark.parametrize("boundary", ["before_review", "resume", "before_delivery"])
def test_checkpoint_binds_current_content_authority_and_boundary(execution_context, boundary):
    from cafe.core.execution_checkpoints import checkpoint, require_checkpoint

    receipt = checkpoint(execution_context, boundary, round_id="round-1", parent_id="parent")
    assert receipt["passed"]
    require_checkpoint(execution_context, receipt, boundary)
    with pytest.raises(ValueError):
        require_checkpoint({**execution_context, "revision": 2}, receipt, boundary)
    with pytest.raises(ValueError):
        require_checkpoint({**execution_context, "authority_digest": "b" * 64}, receipt, boundary)
    (Path(execution_context["root"]) / "allowed").write_text("changed")
    with pytest.raises(ValueError):
        require_checkpoint(execution_context, receipt, boundary)


def test_scope_violation_and_missing_receipt_block(execution_context):
    from cafe.core.execution_checkpoints import checkpoint, require_checkpoint

    (Path(execution_context["root"]) / "outside").write_text("changed")
    receipt = checkpoint(execution_context, "before_review", round_id="round-1", parent_id="parent")
    assert not receipt["passed"]
    assert receipt["findings"][0]["path"] == "outside"
    for missing in (None, {}, receipt):
        with pytest.raises(ValueError):
            require_checkpoint(execution_context, missing, "before_review")


def test_runtime_checks_before_custom_named_work(execution_context):
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    root = Path(execution_context["root"])
    calls = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=root / ".cafe/issues/sample",
        playbook={
            "playbook": {"id": "custom"},
            "roles": {"operator": {}},
            "steps": {
                "repair_anything": {
                    "role": "operator",
                    "skill": "plain",
                    "execution": {"checkpoints": ["resume"]},
                    "on": {"await_agent": "_done"},
                }
            },
        },
        executor=lambda *a, **k: calls.append(a),
        execution_context=execution_context,
    )
    (root / "outside").write_text("out of scope")
    result = runtime.run()
    assert not calls
    assert not result.completed
    assert result.final_status_code == "EXECUTION_CHECKPOINT_BLOCKED"


@pytest.mark.parametrize("candidate", ["same", "different", "outside_restored", "replaced"])
def test_checkpoint_binds_selected_commit_and_its_history(execution_context, candidate):
    import os
    import subprocess
    from cafe.core.execution_checkpoints import checkpoint, require_checkpoint

    root = Path(execution_context["root"])
    baseline = execution_context["baseline_commit"]
    receipt = checkpoint(execution_context, "before_review", round_id="round", parent_id="parent")
    env = {**os.environ, "GIT_INDEX_FILE": str(root / ".git/candidate-index")}
    def git(*args, input=None):
        return subprocess.run(["git", "-C", str(root), *args], env=env, input=input,
            capture_output=True, text=True, check=True, timeout=20).stdout.strip()
    git("read-tree", baseline)
    parent = baseline
    if candidate != "same":
        blob = git("hash-object", "-w", "--stdin", input="unreviewed")
        path = "allowed" if candidate in {"different", "replaced"} else "outside"
        git("update-index", "--cacheinfo", "100644," + blob + "," + path)
        if candidate == "outside_restored":
            parent = git("commit-tree", git("write-tree"), "-p", parent, "-m", "outside edit")
            git("read-tree", baseline)
    selected = git("commit-tree", git("write-tree"), "-p", parent, "-m", "selected candidate")
    if candidate == "replaced":
        view = git("commit-tree", git("rev-parse", baseline + "^{tree}"), "-p", baseline,
                   "-m", "reviewed replacement view")
        git("replace", selected, view)
    # Current HEAD/workspace still passes. A separate immutable candidate must
    # match the reviewed implementation and cumulative allowed history itself.
    assert git("rev-parse", "HEAD") == baseline
    assert (root / "allowed").read_text() == "initial"
    require_checkpoint(execution_context, receipt, "before_review")
    if candidate == "same":
        require_checkpoint(execution_context, receipt, "before_review", source_revision=selected)
    else:
        with pytest.raises(ValueError):
            require_checkpoint(execution_context, receipt, "before_review", source_revision=selected)


@pytest.mark.parametrize("field", ["authority_digest", "revision", "identity", "boundary",
                                  "receipt_id", "round_id", "parent_id"])
def test_shared_observation_preserves_each_receipts_authority_and_identity(execution_context, field):
    from copy import deepcopy
    from cafe.core.execution_checkpoints import checkpoint, observe_checkpoint, require_checkpoint

    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id="parent")
    observation = observe_checkpoint(execution_context)
    require_checkpoint(execution_context, receipt, "before_review", observation=observation)
    changed = deepcopy(receipt)
    changed[field] = "" if field.endswith("_id") else "other"
    with pytest.raises(ValueError):
        require_checkpoint(execution_context, changed, "before_review", observation=observation)
    with pytest.raises(ValueError):
        require_checkpoint({**execution_context, "revision": 2}, receipt, "before_review", observation=observation)
    with pytest.raises(ValueError):
        require_checkpoint(execution_context, receipt, "before_review", observation=observation,
                           source_revision=execution_context["baseline_commit"])


@pytest.mark.parametrize('defect', [None, 'receipt_only', 'missing', 'failed', 'boundary',
    'authority', 'revision', 'identity', 'content', 'endpoint', 'required_review', 'shape'])
def test_issue646_u11_complete_readiness_contract(execution_context, defect):
    from copy import deepcopy
    from cafe.core.execution_checkpoints import checkpoint, prepare_delivery_readiness, validate_delivery_readiness
    context = dict(execution_context, delivery_endpoint={'route': 'local', 'source_branch': 'main'})
    saved = checkpoint(context, 'before_delivery', round_id='delivery-round', parent_id='parent')
    readiness = prepare_delivery_readiness(context, saved)
    candidate = deepcopy(readiness)
    if defect == 'receipt_only': candidate['checkpoint'] = {'receipt_id': saved['receipt_id']}
    elif defect == 'missing': candidate.pop('checkpoint')
    elif defect == 'failed': candidate['checkpoint']['passed'] = False
    elif defect == 'boundary': candidate['checkpoint']['boundary'] = 'before_review'
    elif defect == 'authority': candidate['authority_digest'] = 'b' * 64
    elif defect == 'revision': candidate['checkpoint']['revision'] = 2
    elif defect == 'identity': candidate['checkpoint']['identity'] = {'workflow_id': 'different'}
    elif defect == 'content': (Path(context['root']) / 'allowed').write_text('changed')
    elif defect == 'endpoint': candidate['endpoint'] = {'route': 'other'}
    elif defect == 'shape': candidate['checkpoint'].pop('findings')
    elif defect == 'required_review': context['review_policy'] = 'single_native'
    if defect:
        with pytest.raises(ValueError): validate_delivery_readiness(context, candidate)
    else:
        assert validate_delivery_readiness(context, candidate) == readiness
        assert readiness['checkpoint'] == saved


def test_issue646_u12_public_consumer_routes_machine_omission_to_specific_recovery(execution_context):
    import json
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.core.human_task_records import HumanTaskRecordStore
    context = dict(execution_context, delivery_endpoint={'route': 'local'})
    issue = Path(context['root']) / '.cafe/issues/sample'
    graph = {'playbook': {'id': 'custom'}, 'steps': {'ship_bespoke': {
        'skill': 'custom', 'role': 'author', 'behavior': {'completion': 'baton'},
        'execution': {'checkpoints': ['before_delivery'], 'delivery_evidence_artifact': 'bespoke.json'},
        'on': {'await_agent': '_done'}}}}
    def provider(*args, **kwargs):
        directory = issue / 'ship_bespoke/iteration_001'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'iteration.json').write_text(json.dumps({'iteration': 1, 'session_id': 'original'}))
        (directory / 'output.md').write_text('Saved terminal work\n')
        (directory / 'bespoke.json').write_text(json.dumps({'authority_digest': context['authority_digest'],
            'endpoint': context['delivery_endpoint'], 'checkpoint': {'receipt_id': 'reference-only'}}))
        (issue / 'next_step.txt').write_text(json.dumps({'version': 1, 'intent': 'await_agent'}))
        return StepExecutionResult(response='', artifacts={'report': str(directory / 'output.md')})
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=provider, execution_context=context)
    result = runtime.run(start_step='ship_bespoke', single_step=True)
    assert not result.completed
    task = HumanTaskRecordStore(issue).tasks()[0]
    assert task.policy_id == 'agent-execution-interrupted'
    assert json.loads((issue / 'ship_bespoke/iteration_001/iteration.json').read_text())['agent_interruption']['reason'] == 'agent_delivery_readiness_evidence'


@pytest.mark.parametrize('review_status', ['passing', 'missing', 'blocking', 'wrong_parent'])
def test_issue646_u11_readiness_preserves_required_independent_native_review(execution_context, review_status):
    from datetime import datetime, timezone
    from cafe.core.execution_checkpoints import checkpoint, validate_delivery_readiness
    context = dict(execution_context, delivery_endpoint={'route': 'local'}, review_policy='single_native',
                   review_configuration={'cli': 'codex', 'model': 'review-model', 'read_only': True})
    review_checkpoint = checkpoint(context, 'before_review', round_id='review-round', parent_id='parent')
    findings = [{'severity': 'blocking', 'detail': 'Unresolved invariant'}] if review_status == 'blocking' else []
    invocation = {'parent_id': 'other' if review_status == 'wrong_parent' else 'parent',
        'reviewer_id': 'independent', 'configuration': context['review_configuration'], 'terminal': 'result',
        'exit_status': 0, 'findings': findings, 'targeted_tests': ['targeted invariant passes'], 'result_reference': 'native/result'}
    observed = dict(invocation, parent_id='parent', receipt_id=review_checkpoint['receipt_id'],
                    observed_at=datetime.now(timezone.utc).isoformat())
    review = {'version': 1, 'round_id': 'review-round', 'checkpoint': review_checkpoint,
        'invocations': [invocation], 'native_observations': {'version': 1, 'parent_id': 'parent', 'observations': [observed]}}
    saved = checkpoint(context, 'before_delivery', round_id='delivery-round', parent_id='parent')
    readiness = {'authority_digest': context['authority_digest'], 'endpoint': context['delivery_endpoint'], 'checkpoint': saved}
    if review_status == 'passing':
        assert validate_delivery_readiness(context, readiness, review_evidence=review) == readiness
    else:
        with pytest.raises(ValueError):
            validate_delivery_readiness(context, readiness, review_evidence=None if review_status == 'missing' else review)


@pytest.mark.parametrize('defect', [None, 'absent', 'ambiguous', 'session', 'reference',
    'content', 'authority', 'endpoint', 'review', 'pending', 'iteration', 'workflow'])
def test_automatic_checkpoint_restoration_requires_unique_current_original_proof(execution_context, defect):
    import json
    from cafe.core.execution_checkpoints import checkpoint, load_or_restore_delivery_readiness
    from cafe.core.workspace_lock import workspace_execution_lock
    context = dict(execution_context, delivery_endpoint={'route': 'local'})
    issue = Path(context['root']) / '.cafe/issues/sample'
    directory = issue / 'custom/iteration_001'
    directory.mkdir(parents=True)
    saved = checkpoint(context, 'before_delivery', round_id='custom', parent_id='original')
    path = directory / 'ready.json'
    readiness = {'authority_digest': context['authority_digest'], 'endpoint': context['delivery_endpoint'],
                 'checkpoint': {'receipt_id': saved['receipt_id']}}
    (directory / 'iteration.json').write_text(json.dumps({'iteration': 1, 'session_id': 'original'}))
    (directory / 'delivery_checkpoint.json').write_text(json.dumps(saved))
    if defect == 'absent': (directory / 'delivery_checkpoint.json').unlink()
    elif defect == 'ambiguous':
        (directory / 'other_checkpoint.json').write_text(json.dumps(dict(saved, receipt_id='different')))
    elif defect == 'session':
        (directory / 'iteration.json').write_text(json.dumps({'iteration': 1, 'session_id': 'other'}))
    elif defect in {'iteration', 'workflow'}:
        metadata = {'iteration': 2 if defect == 'iteration' else 1, 'session_id': 'original'}
        if defect == 'workflow': metadata['workflow_id'] = 'other'
        (directory / 'iteration.json').write_text(json.dumps(metadata))
    elif defect == 'reference': readiness['checkpoint']['receipt_id'] = 'different'
    elif defect == 'content': (Path(context['root']) / 'allowed').write_text('changed')
    elif defect == 'authority': readiness['authority_digest'] = 'other'
    elif defect == 'endpoint': readiness['endpoint'] = {'route': 'other'}
    elif defect == 'review': context['review_policy'] = 'single_native'
    elif defect == 'pending':
        from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
        runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook={
            'playbook': {'id': 'custom'}, 'steps': {'custom': {'skill': 'custom', 'role': 'writer'}}}, executor=None)
        runtime._notify_new_human_task = lambda task: None
        runtime._pause_for_agent_execution_interruption(current_step='custom', reason='agent_error', runtime='fixture')
    path.write_text(json.dumps(readiness))
    original = path.read_bytes()
    with workspace_execution_lock(Path(context['root'])):
        if defect:
            with pytest.raises(ValueError): load_or_restore_delivery_readiness(context, path)
            assert path.read_bytes() == original
        else:
            result = load_or_restore_delivery_readiness(context, path)
            assert result['checkpoint'] == saved
            assert json.loads(path.read_text()) == result
            assert load_or_restore_delivery_readiness(context, path) == result


def test_consumer_restores_checkpoint_without_replaying_provider(execution_context):
    import json
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.core.execution_checkpoints import checkpoint
    from cafe.core.human_task_records import HumanTaskRecordStore
    context = dict(execution_context, delivery_endpoint={'route': 'local'})
    issue = Path(context['root']) / '.cafe/issues/sample'
    graph = {'playbook': {'id': 'custom'}, 'steps': {'custom': {
        'skill': 'custom', 'role': 'author', 'behavior': {'completion': 'baton'},
        'output_artifact': 'report',
        'execution': {'checkpoints': ['before_delivery'], 'delivery_evidence_artifact': 'ready.json'},
        'on': {'await_agent': '_done'}}}}
    calls = []
    saved = checkpoint(context, 'before_delivery', round_id='custom', parent_id='original')
    def provider(*args, **kwargs):
        calls.append('provider')
        directory = issue / 'custom/iteration_001'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'iteration.json').write_text(json.dumps({'iteration': 1, 'session_id': 'original'}))
        (directory / 'output.md').write_text('Completed original result\n')
        (directory / 'delivery_checkpoint.json').write_text(json.dumps(saved))
        (directory / 'ready.json').write_text(json.dumps({'authority_digest': context['authority_digest'],
            'endpoint': context['delivery_endpoint'], 'checkpoint': {'receipt_id': saved['receipt_id']}}))
        (issue / 'next_step.txt').write_text(json.dumps({'version': 1, 'intent': 'await_agent'}))
        return StepExecutionResult(response='', artifacts={'report': str(directory / 'output.md')})
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=provider, execution_context=context)
    assert runtime.run(start_step='custom', single_step=True).completed
    assert not HumanTaskRecordStore(issue).tasks()
    assert calls == ['provider']
    assert json.loads((issue / 'execution_delivery.json').read_text())['checkpoint'] == saved


def test_checkpoint_restoration_recovers_a_write_that_persisted_before_failure(execution_context, monkeypatch):
    import json
    from cafe.core import packet_io
    from cafe.core.execution_checkpoints import checkpoint, load_or_restore_delivery_readiness
    from cafe.core.workspace_lock import workspace_execution_lock
    context = dict(execution_context, delivery_endpoint={'route': 'local'})
    directory = Path(context['root']) / '.cafe/issues/sample/custom/iteration_001'
    directory.mkdir(parents=True)
    saved = checkpoint(context, 'before_delivery', round_id='custom', parent_id='original')
    (directory / 'iteration.json').write_text(json.dumps({'iteration': 1, 'session_id': 'original'}))
    source = directory / 'delivery_checkpoint.json'
    source.write_text(json.dumps(saved))
    path = directory / 'ready.json'
    path.write_text(json.dumps({'authority_digest': context['authority_digest'],
        'endpoint': context['delivery_endpoint'], 'checkpoint': {'receipt_id': saved['receipt_id']}}))
    original_write = packet_io.atomic_write_bytes
    def write(path, content):
        original_write(path, content)
        raise OSError('after persisted write')
    monkeypatch.setattr(packet_io, 'atomic_write_bytes', write)
    original = source.read_bytes()
    with workspace_execution_lock(Path(context['root'])):
        with pytest.raises(OSError): load_or_restore_delivery_readiness(context, path)
        assert load_or_restore_delivery_readiness(context, path)['checkpoint'] == saved
    assert source.read_bytes() == original
