"""Explicit, guarded machine-evidence repair; never executes workflow work.

Normal readers retain their limits. Repairs preserve original handoff,
checkpoint and publication evidence without replaying completed work.
"""
from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

from cafe.core.audit_events import AuditEventStore
from cafe.core.blackboard import BlackboardState, BlackboardStore
from cafe.core.packet_io import atomic_write_bytes, sha256_bytes
from cafe.core.workspace_lock import workspace_execution_lock, WorkspaceLeaseBusyError
from cafe.utils.file_lock import (
    _release_advancement_process_lock,
    _try_advancement_process_lock,
)

LEGACY_METADATA_BYTES = 16 * 1024 * 1024


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _json(payload):
    # Check nesting before decoding so malicious nesting cannot exhaust recursion.
    depth = 0
    quoted = escaped = False
    for char in payload.decode('utf-8'):
        if quoted:
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False
        elif char == '"': quoted = True
        elif char in '[{':
            depth += 1
            if depth > 64: raise ValueError('JSON nesting exceeds exceptional bound')
        elif char in ']}': depth -= 1
    return json.loads(payload, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def _identity(observed):
    return {key: getattr(observed, 'st_' + key) for key in ['dev', 'ino', 'size', 'mtime_ns', 'ctime_ns']}


def _safe_parents(path):
    for parent in [path, *path.parents]:
        if parent.is_symlink(): raise ValueError('symlinked repair path')


@contextmanager
def _regular(path, maximum):
    _safe_parents(path)
    if not hasattr(os, 'O_NOFOLLOW'): raise ValueError('safe descriptor support unavailable')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise ValueError('unsafe alias, nonregular file or exceptional size exceeded')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            yield handle, _identity(info)
        if _identity(os.fstat(fd)) != _identity(info) or _identity(path.stat()) != _identity(info):
            raise ValueError('repair evidence changed while reading')
    finally:
        os.close(fd)


def _read(path, maximum):
    with _regular(path, maximum) as (handle, identity):
        content = handle.read(maximum + 1)
        if len(content) > maximum: raise ValueError('exceptional size exceeded')
    return content, identity


def _snapshot(issue):
    content, _ = _read(issue / 'blackboard.json', LEGACY_METADATA_BYTES)
    raw = _json(content)
    if not isinstance(raw, dict) or not raw.get('workflow_id') or not raw.get('current_step'):
        raise ValueError('missing original workflow authority')
    return BlackboardState.from_dict(raw, initial_step=raw['current_step'])


def _quiescent(issue):
    path = issue / '.workflow-worker-launches.json'
    if not path.exists() and not path.is_symlink(): return
    raw = _json(_read(path, 1_048_576)[0])
    attempts = raw.get('attempts') if isinstance(raw, dict) else None
    if not isinstance(attempts, dict): raise ValueError('worker launch state is uncertain')
    for record in attempts.values():
        if not isinstance(record, dict) or record.get('status') not in {
            'stopped', 'completed', 'startup_failed', 'failed', 'exited'
        }:
            raise ValueError('live or uncertain worker launch owns execution')
        pid = record.get('pid')
        if pid is not None:
            if type(pid) is not int or pid <= 0: raise ValueError('registered worker PID is uncertain')
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                raise ValueError('registered worker liveness cannot be verified') from error
            else:
                raise ValueError('registered worker PID is still live or reused; ownership is uncertain')


@contextmanager
def _guard(issue):
    _safe_parents(issue)
    _safe_parents(issue / '.workflow-advancement.lock')
    try:
        lease = _try_advancement_process_lock(issue / '.workflow-advancement.lock')
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    if lease is None: raise ValueError('workflow advancement lease is busy')
    try:
        _quiescent(issue)
        # Preserve the runtime's advancement -> workspace -> store lock order.
        with workspace_execution_lock(issue.parents[2], blocking=False):
            _quiescent(issue)
            yield
    except WorkspaceLeaseBusyError as error:
        raise ValueError(str(error)) from error
    finally:
        _release_advancement_process_lock(lease)


def _safe_component(value):
    return (isinstance(value, str) and bool(value) and value.strip() == value
            and value not in {'.', '..'} and not any(char in value for char in ['/', '\\', '\x00']))


def _selected(issue, step, iteration):
    if not _safe_component(step):
        raise ValueError('explicit safe step selection required')
    if type(iteration) is not int or not 1 <= iteration <= 999:
        raise ValueError('explicit iteration in 1..999 required')
    directory = issue / step / f'iteration_{iteration:03d}'
    _safe_parents(directory)
    if not directory.is_dir(): raise ValueError('selected iteration does not exist')
    return directory


def _repair_events(audit, state, step, iteration, kinds):
    high = audit.high_water(state.workflow_id)
    if high > 100_000: raise ValueError('audit attribution exceeds bounded repair inspection')
    # Existing store writers may hydrate audit history. Bound that exceptional
    # repair workload before using them, without changing any normal store limit.
    total = 0
    for sequence in range(1, high + 1):
        path = audit._path(sequence)
        if path.is_symlink(): raise ValueError('unsafe audit evidence alias')
        if path.exists():
            total += path.stat().st_size
            if total > LEGACY_METADATA_BYTES:
                raise ValueError('audit attribution exceeds bounded repair evidence capacity')
    selected = []
    for sequence in range(1, high + 1):
        record = audit.read(state.workflow_id, sequence, bounded=True)
        if record and record['event_type'] in kinds and record['step'] == step and record['data'].get('iteration') == iteration:
            selected.append(record)
    return selected


def _event(audit, state, step, kind, proof):
    # The existing store reserves/commits the audit and advances its compact
    # projection. A durable-but-return-failed commit is found on repair reentry.
    BlackboardStore(audit.root.parent).log_event(state, step, kind, kind, proof)
    record = audit.read(state.workflow_id, state.applied_event_sequence, bounded=True)
    if not record or record['event_type'] != kind or record['data'] != proof:
        raise OSError('repair audit readback failed')
    return record


def repair_workflow(issue_dir: Path, *, kind: str, step: str, iteration: int,
                    execute: bool = False, progress: Callable[[dict], None] | None = None,
                    execution_context_file: Path | None = None) -> dict:
    """Preview by default; execution never dispatches an agent or publication."""
    if kind not in {'interruption', 'pr-result'}:
        return {'status': 'refused', 'reason': 'unsupported repair kind'}
    issue = Path(issue_dir).absolute()
    progress = progress or (lambda event: None)
    try:
        directory = _selected(issue, step, iteration)
        def operation():
            state = _snapshot(issue)
            if kind == 'interruption': return _interruption(issue, state, directory, step, iteration, execute)
            if kind == 'pr-result': return _pr_result(issue, state, directory, step, iteration, execute, execution_context_file)
            raise ValueError('unsupported repair kind')
        if not execute:
            _quiescent(issue)
            return operation()
        with _guard(issue): return operation()
    except (ValueError, FileNotFoundError, NotADirectoryError, UnicodeError, json.JSONDecodeError) as error:
        return {'status': 'refused', 'reason': str(error), 'next_action': 'inspect original evidence and resolve the stated prerequisite'}
    except OSError as error:
        return {'status': 'interrupted', 'reason': str(error), 'next_action': 'explicitly reenter under the same selection; verify durable bytes and audit'}


def reconcile_completed_feedback(
    issue_dir: Path, *, playbook: dict, step: str, iteration: int, execute: bool = False
) -> dict:
    """Settle an exact legacy consumer completion without replaying its work.

    This owner operation leaves tasks, handoffs and original iteration artifacts
    untouched. A saved transition must authenticate the still-current artifact;
    checked boxes or a provider exit alone cannot authorize reconciliation.
    """
    from cafe.core.checklist import load_materialization
    from cafe.core.phase_state_mixin import validated_completion_receipt
    from cafe.core.workflow_feedback import (
        WorkflowFeedbackError,
        WorkflowFeedbackLedger,
        feedback_todo_mappings,
        validate_completed_feedback_projection,
    )
    from cafe.utils.checklist_validator import completion_requires_checklist, validate_checklist

    issue = Path(issue_dir).absolute()
    try:
        directory = _selected(issue, step, iteration)

        def operation():
            state = _snapshot(issue)
            if state.playbook_id != playbook.get("playbook", {}).get("id"):
                raise ValueError("completed feedback playbook identity changed")
            output = directory / "output.md"
            for path in (
                output,
                directory / "iteration.json",
                directory / "checklist.md",
                directory / "workflow_feedback_batch.json",
            ):
                _read(path, 1_048_576)
            context = _json(_read(directory / "iteration.json", 1_048_576)[0])
            if not isinstance(context, dict):
                raise ValueError("original consumer control metadata is malformed")
            marker = context.get("workflow_completion_trusted", True)
            if (
                context.get("agent_invoked") is not True
                or not context.get("end_time")
                or context.get("workflow_id") != state.workflow_id
                or context.get("step_name") != step
                or type(context.get("iteration")) is not int
                or context.get("iteration") != iteration
                or (marker is not True and validated_completion_receipt(directory, context) is None)
            ):
                raise ValueError("original consumer completion is not trusted")
            name = playbook.get("steps", {}).get(step, {}).get("output_artifact")
            artifact = state.artifacts.get(name)
            if (
                artifact is None
                or artifact.updated_by != step
                or (issue.parents[2] / artifact.path).resolve() != output.resolve()
                or artifact.content_sha256 != sha256_bytes(output.read_bytes())
            ):
                raise ValueError("original completed feedback artifact changed")
            audit = AuditEventStore(issue)
            _repair_events(audit, state, step, iteration, set())  # existing bounded audit admission
            previous = None
            transitions = []
            for sequence in range(1, audit.high_water(state.workflow_id) + 1):
                record = audit.read(state.workflow_id, sequence, bounded=True)
                if not record or record["event_type"] == "workflow_event_callback_enqueued":
                    continue
                if (
                    record["event_type"] == "transition"
                    and record["data"].get("from") == step
                    and completion_requires_checklist(
                        baton_intent=record["data"].get("transition_intent")
                    )
                    and record["data"].get("source_artifact") == artifact.to_dict()
                    and previous is not None
                    and previous["step"] == step
                    and previous["event_type"] in {"step_completed", "single_step_completed"}
                ):
                    transitions.append(record)
                previous = record
            if (
                len(transitions) != 1
                or not validate_checklist(directory / "checklist.md").is_complete
            ):
                raise ValueError("original successful consumer handoff is not uniquely proven")
            ledger = WorkflowFeedbackLedger(issue)
            _read(ledger.path, 1_048_576)
            batch = _json(_read(directory / "workflow_feedback_batch.json", 1_048_576)[0])
            identities = tuple(entry["source_identity"] for entry in batch["entries"])
            mappings = feedback_todo_mappings(playbook, target_step=step)
            if load_materialization(
                directory / "iteration.json"
            ) is None or not validate_completed_feedback_projection(
                issue, step=step, output=output, source_identities=identities, mappings=mappings
            ):
                raise ValueError("original source-bound completion projection is missing")
            pending = ledger.pending(target_step=step)
            selected = [entry for entry in ledger.load() if entry.source_identity in identities]
            if len(selected) != len(identities):
                raise ValueError("completed feedback sources are missing or duplicated")
            proof = {
                "step": step,
                "source_identities": list(identities),
                "excluded_source_identities": [],
                "deferred_source_identities": [
                    entry.source_identity
                    for entry in pending
                    if entry.source_identity not in identities
                ],
                "recovered_from_iteration": str(directory.relative_to(issue)),
                "transition_id": transitions[0]["data"]["transition_id"],
                "artifact": artifact.to_dict(),
            }
            if not execute:
                return {"status": "proposed", "proof": proof}
            done, excluded = ledger.settle_or_reconcile_reviewed(identities, ())
            if len(done) != len(identities) or excluded:
                raise ValueError("completed feedback lifecycle conflicts with its completion")
            state = BlackboardStore(issue).load_or_create(state.current_step)
            if any(
                event.event_type == "workflow_feedback_delivered" and event.data == proof
                for event in state.events
            ):
                return {"status": "no-op", "proof": proof}
            _event(audit, state, step, "workflow_feedback_delivered", proof)
            return {"status": "repaired", "proof": proof}

        if not execute:
            _quiescent(issue)
            return operation()
        with _guard(issue):
            return operation()
    except (
        ValueError,
        KeyError,
        TypeError,
        FileNotFoundError,
        UnicodeError,
        WorkflowFeedbackError,
    ) as error:
        return {"status": "refused", "reason": str(error)}
    except OSError as error:
        return {"status": "interrupted", "reason": str(error)}


def _interruption(issue, state, directory, step, iteration, execute):
    from cafe.core.blackboard import HandoffContract, HandoffIntent, HandoffOwner
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.core.human_tasks import agent_execution_interrupted_human_task, AGENT_EXECUTION_INTERRUPTED_TRIGGER
    from cafe.core.restart_policy import load_restart_context, resolve_restart_policy
    from cafe.utils.issue_config import read_issue_config_strict

    path = directory / 'iteration.json'
    absent = not path.exists() and not path.is_symlink()
    content, identity = (None, None) if absent else _read(path, 1_048_576)
    context = load_restart_context(path)
    audit = AuditEventStore(issue)
    histories = _repair_events(audit, state, step, iteration, {'agent_interruption_prepared', 'interruption_repair_completed'})
    candidates = [record['data'] for record in histories if record['event_type'] == 'agent_interruption_prepared']
    marker = context.get('agent_interruption') or context.get('restart_preparation_failure')
    current = state.handoff_contract
    if candidates:
        # The latest original interruption supersedes older resolved decisions;
        # all source bindings still have to match the exact selected iteration.
        prepared = candidates[-1]
        contract = HandoffContract.from_dict(prepared['contract'])
        marker = context.get(prepared['marker_key']) if prepared.get('marker_key') else None
        if marker and marker.get('id') not in {contract.created_at, (prepared.get('marker') or {}).get('id')}:
            raise ValueError('interruption marker conflicts with prepared identity')
        observed_hash = sha256_bytes(content) if content is not None else None
        if observed_hash not in {prepared['context_original_sha256'], prepared['context_target_sha256']}:
            raise ValueError('interruption context changed since durable preparation')
    else:
        if absent: raise ValueError('absent legacy context has no durable pre-image proof')
        if (current is None or current.from_step != step or current.to_owner is not HandoffOwner.USER
                or current.intent is not HandoffIntent.MANUAL_HANDOFF
                or current.source != 'workflow.agent_execution_interrupted'):
            raise ValueError('no uniquely bound original interruption handoff')
        contract = current
        # Legacy orphan baton may predate the metadata marker; the actual
        # interrupted event must supply the original reason, never provider exit.
        record = audit.latest_record(state.workflow_id, {'step_interrupted'}, step=step)
        reason = marker.get('reason') if isinstance(marker, dict) else (record or {}).get('data', {}).get('reason')
        if not isinstance(reason, str) or not (reason.startswith('agent_') or reason == 'checklist_validation_failed'):
            raise ValueError('no genuine interruption evidence')
        if marker and (marker.get('id') != contract.created_at or marker.get('workflow_id') != state.workflow_id
                       or marker.get('step') != step or marker.get('iteration') != iteration):
            raise ValueError('original interruption marker is mismatched')
        config = issue / 'issue.yaml'
        policy, binding = agent_execution_interrupted_human_task(step_name=step,
            restart_policy=resolve_restart_policy(read_issue_config_strict(config) if config.exists() else {}),
            interruption_reason=reason)
        policy = policy.for_locale(state.conversation_locale)
        proposed = dict(context, agent_interruption=dict(id=contract.created_at, reason=reason,
                workflow_id=state.workflow_id, step=step, iteration=iteration), workflow_completion_trusted=False)
        prepared = dict(step=step, iteration=iteration, reason=reason, runtime='explicit-repair',
            contract=contract.to_dict(), context_original_sha256=sha256_bytes(content),
            context_target_sha256=sha256_bytes(json.dumps(proposed, ensure_ascii=False, indent=2).encode()),
            marker_key='agent_interruption', marker=proposed['agent_interruption'], policy_id=policy.id,
            prompt=policy.prompt, expected_result=policy.model_dump(mode='json'), continuations=binding.outcomes)
    if contract.from_step != step or contract.to_owner is not HandoffOwner.USER or contract.to_step != 'user':
        raise ValueError('prepared handoff is not the selected human interruption')
    key = ':'.join(['user-handoff', state.workflow_id, step, contract.intent.value, contract.created_at])
    records = HumanTaskRecordStore(issue)
    tasks = [task for task in records.tasks() if task.handoff_key == key]
    if len(tasks) > 1 or any(task.status is not HumanTaskStatus.PENDING for task in tasks):
        raise ValueError('matching task is ambiguous or terminal; decisions cannot be reopened')
    if tasks and any(task.workflow_id != state.workflow_id or task.step != step or task.iteration != iteration
                     or task.expected_result != prepared['expected_result'] or task.continuations != prepared['continuations'] for task in tasks):
        raise ValueError('pending task conflicts with original preparation')
    if any(task.workflow_id == state.workflow_id and task.step == step and task.iteration == iteration
           and task.status is HumanTaskStatus.PENDING and task.handoff_key != key for task in records.tasks()):
        raise ValueError('another pending decision owns this iteration')
    target = dict(context)
    if prepared.get('marker_key'): target[prepared['marker_key']] = prepared['marker']
    target['workflow_completion_trusted'] = False
    payload = json.dumps(target, ensure_ascii=False, indent=2).encode()
    if len(payload) > 1_048_576 or sha256_bytes(payload) != prepared['context_target_sha256']:
        raise ValueError('reconstructed interruption target does not match original preparation')
    completion = [record for record in histories if record['event_type'] == 'interruption_repair_completed'
                  and record['data'].get('handoff_key') == key]
    coherent = (len(tasks) == 1 and state.current_step == 'user' and current is not None
                and current.to_dict() == contract.to_dict() and content == payload
                and _json(_read(issue / 'next_step.txt', 4096)[0]) == contract.to_next_step_dict())
    if coherent and completion:
        if len(completion) != 1: raise ValueError('ambiguous interruption completion proof')
        if execute and state.applied_event_sequence < completion[0]['sequence']:
            BlackboardStore(issue).load_or_create(step, playbook_id=state.playbook_id)
        return {'status': 'no-op', 'task_id': tasks[0].id}
    if not execute: return {'status': 'proposed', 'handoff_key': key}
    if absent:
        if path.exists() or path.is_symlink(): raise ValueError('absent original context changed')
    elif _read(path, 1_048_576) != (content, identity): raise ValueError('interruption evidence changed')
    if not candidates: _event(audit, state, step, 'agent_interruption_prepared', prepared)
    store = BlackboardStore(issue)
    with records.transaction():
        tasks = [task for task in records.tasks() if task.handoff_key == key]
        if len(tasks) > 1 or any(task.status is not HumanTaskStatus.PENDING for task in tasks):
            raise ValueError('task changed or became terminal')
        atomic_write_bytes(path, payload)
        task = records.materialize_with_status(workflow_id=state.workflow_id, step=step, iteration=iteration,
            trigger=AGENT_EXECUTION_INTERRUPTED_TRIGGER, policy_id=prepared['policy_id'], prompt=prepared['prompt'],
            expected_result=prepared['expected_result'], continuations=prepared['continuations'], assignee_type='user',
            handoff_key=key).task
        store.write_handoff_contract(state, contract)
        store.set_current_step(state, 'user')
        _event(audit, state, step, 'interruption_repair_completed',
               {'iteration': iteration, 'handoff_key': key, 'task_id': task.id, 'reason': prepared['reason']})
    return {'status': 'repaired', 'task_id': task.id, 'handoff_key': key}


def recover_pending_interruption(issue):
    """Recover only the current unfinished original preparation on execution entry."""
    from cafe.core.blackboard import HandoffContract, HandoffOwner
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.utils.file_lock import advancement_execution_lock

    issue = Path(issue).absolute()
    if not (issue / 'blackboard.json').exists():
        return None
    with advancement_execution_lock(issue) as acquired:
        if acquired:
            _quiescent(issue)
        with workspace_execution_lock(issue.parents[2], blocking=False):
            state = _snapshot(issue)
            record = AuditEventStore(issue).latest_record(state.workflow_id, {'agent_interruption_prepared'})
            if record is None:
                return None
            prepared = record['data']
            step, iteration = prepared['step'], prepared['iteration']
            contract = HandoffContract.from_dict(prepared['contract'])
            key = ':'.join(['user-handoff', state.workflow_id, step, contract.intent.value, contract.created_at])
            tasks = HumanTaskRecordStore(issue).tasks()
            matching = [task for task in tasks if task.handoff_key == key]
            if any(task.status is not HumanTaskStatus.PENDING for task in matching):
                return None
            current = state.handoff_contract
            if state.current_step not in {step, 'user'} or current is None:
                return None
            same = current.to_dict() == contract.to_dict()
            preceding = (state.current_step == step and current.from_step == step
                and current.to_owner is HandoffOwner.AGENT and current.to_step == step
                and current.created_at < contract.created_at)
            if not same and not preceding:
                return None
            directory = _selected(issue, step, iteration)
            if any(p.name > directory.name for p in directory.parent.glob('iteration_[0-9][0-9][0-9]')):
                return None
            # A coherent pending handoff is already published. Leave its legacy
            # metadata and any configured-choice upgrade to normal user routing.
            if (len(matching) == 1 and same and state.current_step == 'user'
                    and matching[0].workflow_id == state.workflow_id
                    and matching[0].step == step and matching[0].iteration == iteration
                    and matching[0].expected_result == prepared['expected_result']
                    and matching[0].continuations == prepared['continuations']
                    and _json(_read(issue / 'next_step.txt', 4096)[0]) == contract.to_next_step_dict()):
                return None
            if any(task.status is HumanTaskStatus.PENDING and task.handoff_key != key for task in tasks):
                raise ValueError('another pending decision owns the workflow')
            result = _interruption(issue, state, directory, step, iteration, True)
            return dict(result, step=step, reason=prepared['reason'])


def _original_context(issue, state, source):
    from cafe.core.execution_checkpoints import context_digest
    if source is None: raise ValueError('original saved --execution-context-file is required')
    path = Path(source).absolute()
    content, _ = _read(path, 256 * 1024)
    context = _json(content)
    context_digest(context)
    if (context.get('identity', {}).get('workflow_id') != state.workflow_id
            or context.get('identity', {}).get('issue_name') != issue.name
            or Path(context.get('root', '')).resolve() != issue.parents[2].resolve()):
        raise ValueError('saved context belongs to another original execution')
    return context, {'path': str(path.relative_to(issue)) if path.is_relative_to(issue) else str(path), 'sha256': sha256_bytes(content)}


def _pr_result(issue, state, directory, step, iteration, execute, context_file):
    from cafe.core.pr_delivery import prepare_pr_reconciliation
    from cafe.core.execution_artifacts import bounded_execution_json
    context, context_source = _original_context(issue, state, context_file)
    for source in [issue / 'human_tasks.json', issue / 'capability_receipts.json']:
        if source.exists() or source.is_symlink(): _read(source, LEGACY_METADATA_BYTES)
    path = issue / 'delivery_result.json'
    content, identity = _read(path, 256 * 1024)
    record = _json(content)
    if not isinstance(record, dict): raise ValueError('original publication result is malformed')
    original = {key: value for key, value in record.items() if key != 'reconciliation'}
    association, observation = prepare_pr_reconciliation(issue, issue.parents[2], context, original, directory)
    association['context_source'] = context_source
    fingerprint = sha256_bytes(json.dumps([association, observation], sort_keys=True).encode())
    previous = record.get('reconciliation')
    if previous is not None and (not isinstance(previous, dict) or previous.get('original') != original
                                or previous.get('association') != association):
        raise ValueError('existing reconciliation conflicts with original publication association')
    history = previous.get('observations', []) if previous else []
    if not isinstance(history, list) or any(not isinstance(item, dict) or not isinstance(item.get('fingerprint'), str) for item in history):
        raise ValueError('saved PR observation history is malformed')
    observations = list(history)
    if len(observations) > 128: raise ValueError('read-only observation history exceeds bounded record capacity')
    found = [item for item in observations if item.get('fingerprint') == fingerprint]
    if len(found) > 1: raise ValueError('ambiguous observation identity')
    status = observation['status']
    outcome = {'verified': 'succeeded', 'unresolved': 'unknown', 'mismatch': 'mismatch'}[status]
    # Historical equality does not identify the currently applied observation.
    # Legacy records applied their last appended observation; retain their
    # history and track subsequent revisits separately without duplicate entries.
    current = previous.get('current_fingerprint', observations[-1]['fingerprint'] if observations else None) if previous else None
    if (found and current == fingerprint and previous.get('effective_status') == outcome
            and previous.get('effective_delivered') is (status == 'verified')):
        return {'status': 'no-op', 'effective_status': outcome, 'association_id': association['id'],
                'exit_code': {'verified': 0, 'unresolved': 3, 'mismatch': 5}[status]}
    if not found and len(observations) >= 128:
        raise ValueError('new observation would exceed bounded record capacity')
    if not execute: return {'status': status, 'association_id': association['id'], 'preview': True}
    if _read(path, 256 * 1024) != (content, identity): raise ValueError('original result changed while observing')
    if not found:
        observations.append({'fingerprint': fingerprint, 'observed_at': datetime.now().astimezone().isoformat(), **observation})
    result = dict(original, reconciliation={'original': original, 'original_sha256': previous['original_sha256'] if previous else sha256_bytes(content),
        'association': association, 'observations': observations, 'current_fingerprint': fingerprint,
        'effective_status': outcome, 'effective_delivered': status == 'verified'})
    atomic_write_bytes(path, bounded_execution_json(result))
    return {'status': status, 'association_id': association['id'], 'effective_status': outcome}
