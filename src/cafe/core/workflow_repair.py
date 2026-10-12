"""Explicit, guarded machine-evidence repair; never executes workflow work.

Normal readers retain their limits. Exceptional legacy verification is bounded
and preserves its verified pre-image in the existing audit before replacement.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

from cafe.core.audit_events import AuditEventStore
from cafe.core.blackboard import BlackboardState, BlackboardStore
from cafe.core.packet_io import atomic_write_bytes, sha256_bytes
from cafe.core.workspace_lock import workspace_execution_lock
from cafe.workflow_execution.workflow_hosting import (
    _release_advancement_process_lock,
    _try_advancement_process_lock,
)

LEGACY_METADATA_BYTES = 16 * 1024 * 1024
LEGACY_RAW_BYTES = 32 * 1024 * 1024
LEGACY_RECORDS = 100_000
LEGACY_RECORD_BYTES = 1_048_576


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


@contextmanager
def _guard(issue):
    _safe_parents(issue)
    _safe_parents(issue / '.workflow-advancement.lock')
    lease = _try_advancement_process_lock(issue / '.workflow-advancement.lock')
    if lease is None: raise ValueError('workflow advancement lease is busy')
    try:
        _quiescent(issue)
        # Preserve the runtime's advancement -> workspace -> store lock order.
        with workspace_execution_lock(issue.parents[2]):
            _quiescent(issue)
            yield
    finally:
        _release_advancement_process_lock(lease)


def _selected(issue, step, iteration):
    if not isinstance(step, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', step):
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


def _raw_proof(path, embedded, progress):
    records = hashlib.sha256()
    byte_hash = hashlib.sha256()
    count = total = notified = 0
    with _regular(path, LEGACY_RAW_BYTES) as (handle, identity):
        while True:
            line = handle.readline(LEGACY_RECORD_BYTES + 1)
            if not line: break
            total += len(line)
            if len(line) > LEGACY_RECORD_BYTES or not line.endswith(b'\n') or total > LEGACY_RAW_BYTES:
                raise ValueError('raw record is oversized or truncated')
            decoded = _json(line)
            normalized = json.dumps(decoded, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            if embedded is not None and (count >= len(embedded) or normalized != json.dumps(embedded[count], sort_keys=True, ensure_ascii=False, separators=(',', ':'))):
                raise ValueError('available raw records do not exactly match embedded stream')
            records.update(json.dumps(decoded, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())
            records.update(b'\n')
            byte_hash.update(line)
            count += 1
            if count > LEGACY_RECORDS: raise ValueError('raw record count exceeds exceptional bound')
            if total - notified >= 1_048_576 or count % 1000 == 0:
                progress({'kind': 'verification_progress', 'bytes': total, 'records': count})
                notified = total
        if embedded is not None and count != len(embedded):
            raise ValueError('available raw stream is incomplete')
    return dict(raw_identity=identity, raw_sha256=byte_hash.hexdigest(), record_count=count,
                ordered_record_sha256=records.hexdigest())


def _iteration(issue, state, directory, step, iteration, execute, progress):
    path, raw_path = directory / 'iteration.json', directory / 'streaming.jsonl'
    content, identity = _read(path, LEGACY_METADATA_BYTES)
    metadata = _json(content)
    if not isinstance(metadata, dict): raise ValueError('iteration must be a mapping')
    audit = AuditEventStore(issue)
    records = _repair_events(audit, state, step, iteration, {'iteration_repair_prepared', 'iteration_repair_completed'})
    prepared = [r['data'] for r in records if r['event_type'] == 'iteration_repair_prepared']
    if len(prepared) > 1: raise ValueError('ambiguous prepared repair operation')
    original_sha = sha256_bytes(content)
    if prepared:
        proof = prepared[0]
        if proof.get('metadata_path') != str(path.relative_to(issue)) or proof.get('raw_path') != str(raw_path.relative_to(issue)):
            raise ValueError('prepared repair selection differs')
        raw = _raw_proof(raw_path, None, progress)
        if any(proof.get(k) != v for k, v in raw.items()): raise ValueError('prepared raw identity or content changed')
        if original_sha not in {proof['original_sha256'], proof['target_sha256']}:
            raise ValueError('metadata differs from both prepared pre-image and post-image')
        completed = [r for r in records if r['event_type'] == 'iteration_repair_completed']
        if completed:
            if len(completed) != 1 or completed[0]['data'] != proof or original_sha != proof['target_sha256']:
                raise ValueError('completion audit conflicts with current evidence')
            if state.applied_event_sequence < completed[0]['sequence']:
                if execute:
                    BlackboardStore(issue).load_or_create(step, playbook_id=state.playbook_id)
                return {'status': 'repaired' if execute else 'proposed', 'operation_id': proof['operation_id']}
            return {'status': 'no-op', 'operation_id': proof['operation_id']}
        if original_sha == proof['target_sha256']:
            if execute: _event(audit, state, step, 'iteration_repair_completed', proof)
            return {'status': 'repaired' if execute else 'proposed', 'operation_id': proof['operation_id']}
        if identity != proof['metadata_identity']: raise ValueError('prepared metadata identity changed')
    elif 'streaming_log' not in metadata:
        if len(content) > 1_048_576: raise ValueError('non-log metadata remains oversized')
        return {'status': 'no-op', 'reason': 'no duplicated stream; no verified migration claimed'}
    embedded = metadata.get('streaming_log')
    if not isinstance(embedded, list) or len(embedded) > LEGACY_RECORDS:
        raise ValueError('embedded stream is missing or exceeds exceptional bound')
    raw = _raw_proof(raw_path, embedded, progress)
    target = {k: v for k, v in metadata.items() if k != 'streaming_log'}
    workspace = any(k in metadata for k in ('workspace_completion', 'workspace_publication'))
    payload = (json.dumps(target, ensure_ascii=False, indent=2) + ('\n' if workspace else '')).encode()
    if len(payload) > (983_040 if workspace else 1_048_576):
        raise ValueError('non-log metadata exceeds its existing path-specific bound')
    candidate = dict(iteration=iteration, metadata_path=str(path.relative_to(issue)),
                     raw_path=str(raw_path.relative_to(issue)), metadata_identity=identity,
                     original_sha256=original_sha, target_sha256=sha256_bytes(payload),
                     removed_field='streaming_log', equality='ordered decoded records; complete EOF',
                     embedded_count=len(embedded), **raw)
    operation = sha256_bytes(json.dumps([state.workflow_id, step, candidate], sort_keys=True).encode())
    candidate['operation_id'] = operation
    if prepared and candidate != proof: raise ValueError('revalidated pre-image proof differs')
    proof = candidate
    if not execute: return {'status': 'proposed', 'operation_id': operation, 'record_count': len(embedded)}
    if not prepared: _event(audit, state, step, 'iteration_repair_prepared', proof)
    # Recheck both selected regular files after durable preparation and immediately before replacement.
    if _read(path, LEGACY_METADATA_BYTES) != (content, identity) or _raw_proof(raw_path, embedded, progress) != raw:
        raise ValueError('evidence changed before replacement')
    atomic_write_bytes(path, payload)
    if _read(path, 1_048_576)[0] != payload: raise OSError('metadata post-image readback failed')
    _event(audit, state, step, 'iteration_repair_completed', proof)
    return {'status': 'repaired', 'operation_id': operation, 'record_count': len(embedded)}


def repair_workflow(issue_dir: Path, *, kind: str, step: str, iteration: int,
                    execute: bool = False, progress: Callable[[dict], None] | None = None) -> dict:
    """Preview by default; execution never dispatches an agent or publication."""
    issue = Path(issue_dir).absolute()
    progress = progress or (lambda event: None)
    try:
        directory = _selected(issue, step, iteration)
        def operation():
            state = _snapshot(issue)
            if kind == 'iteration': return _iteration(issue, state, directory, step, iteration, execute, progress)
            if kind == 'interruption': return _interruption(issue, state, directory, step, iteration, execute)
            raise ValueError('unsupported repair kind')
        if not execute:
            _quiescent(issue)
            return operation()
        with _guard(issue): return operation()
    except (ValueError, FileNotFoundError, NotADirectoryError, UnicodeError, json.JSONDecodeError) as error:
        return {'status': 'refused', 'reason': str(error), 'next_action': 'inspect original evidence and resolve the stated prerequisite'}
    except OSError as error:
        return {'status': 'interrupted', 'reason': str(error), 'next_action': 'explicitly reenter under the same selection; verify durable bytes and audit'}


def _interruption(issue, state, directory, step, iteration, execute):
    from cafe.core.blackboard import HandoffContract, HandoffIntent, HandoffOwner
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.core.human_tasks import agent_execution_interrupted_human_task, AGENT_EXECUTION_INTERRUPTED_TRIGGER
    from cafe.core.restart_policy import load_restart_context, resolve_restart_policy
    from cafe.utils.issue_config import read_issue_config_strict

    path = directory / 'iteration.json'
    content, identity = _read(path, 1_048_576)
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
        if marker and marker.get('id') not in {contract.created_at, (prepared.get('marker') or {}).get('id')}:
            raise ValueError('interruption marker conflicts with prepared identity')
        if sha256_bytes(content) not in {prepared['context_original_sha256'], prepared['context_target_sha256']}:
            raise ValueError('interruption context changed since durable preparation')
    else:
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
        from dataclasses import replace
        policy = policy.model_copy(update={'prompt': policy.prompt})
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
    if _read(path, 1_048_576) != (content, identity): raise ValueError('interruption evidence changed')
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
