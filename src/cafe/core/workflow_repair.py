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
            raise ValueError('unsupported repair kind')
        if not execute:
            _quiescent(issue)
            return operation()
        with _guard(issue): return operation()
    except (ValueError, FileNotFoundError, NotADirectoryError, UnicodeError, json.JSONDecodeError) as error:
        return {'status': 'refused', 'reason': str(error), 'next_action': 'inspect original evidence and resolve the stated prerequisite'}
    except OSError as error:
        return {'status': 'interrupted', 'reason': str(error), 'next_action': 'explicitly reenter under the same selection; verify durable bytes and audit'}
