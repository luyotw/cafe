"""Read-only issue resolution and terminal access to an existing Manager session."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import uuid
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cafe.core.runtime_locales import render_text
from cafe.utils.issue_config import (
    _registered_worktree_paths, read_issue_config_strict, resolve_issue_config_path,
)

CATALOG_ROOT = Path(__file__).resolve().parent.parent / 'locales'


def _callback():
    spec = importlib.util.spec_from_file_location(
        '_builtin_chat_callback', Path(__file__).with_name('workflow_event_callback.py'),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


callback = _callback()


class ChatError(ValueError):
    def __init__(self, reason: str, *, locale: str = 'en-US'):
        self.reason = reason
        self.locale = locale
        super().__init__(reason)


@dataclass(frozen=True)
class ChatTarget:
    issue_dir: Path
    workflow_id: str
    session_id: str
    cli: str
    model: str | None
    contract_sha256: str
    locale: str


def guidance(reason: str, locale: str) -> str:
    return render_text(f'chat.{reason}', locale=locale, catalog_root=CATALOG_ROOT)


def _name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', value):
        raise ChatError('invalid_issue')
    return value


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(['git', *arguments], cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise ChatError('issue_required')
    return result.stdout.strip()


def _read_json(path: Path) -> dict:
    from cafe.manager._store import _decode_exact

    value = _decode_exact(callback._read_bounded_text(path, label=path.name).encode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError('expected object')
    return value


def _issue_directory(cwd: Path, explicit: str | None) -> Path:
    root = Path(_git(cwd, 'rev-parse', '--show-toplevel')).resolve()
    registered = _registered_worktree_paths(root)
    if explicit is not None:
        name = _name(explicit)
    else:
        branch = _git(root, 'branch', '--show-current')
        candidates = set()
        if branch and '/' not in branch and (root / '.cafe/issues' / branch / 'issue.yaml').exists():
            candidates.add(_name(branch))
        marker = root / '.cafe/active_issue'
        if marker.exists():
            candidates.add(_name(callback._read_bounded_text(marker, label='active issue').strip()))
        if len(candidates) > 1:
            raise ChatError('selection_conflict')
        if not candidates:
            raise ChatError('issue_required')
        name = candidates.pop()
    authorities = set()
    for worktree in registered:
        source = worktree / '.cafe/issues' / name / 'issue.yaml'
        if not source.exists():
            continue
        callback._read_bounded_text(source, label='issue policy')
        original = read_issue_config_strict(source)
        if original.get('issue_name', name) != name:
            raise ChatError('issue_conflict')
        path = resolve_issue_config_path(source, require_registered_worktree=True)
        callback._read_bounded_text(path, label='issue policy')
        policy = read_issue_config_strict(path)
        if policy.get('issue_name', name) != name:
            raise ChatError('issue_conflict')
        if policy.get('status') in {'archived', 'closed'}:
            raise ChatError('issue_archived')
        authorities.add(path.parent)
    if len(authorities) > 1:
        raise ChatError('issue_conflict')
    if not authorities:
        project_key = str(registered[0]).lstrip('/').replace('/', '-')
        archive = Path.home() / '.cafe/projects' / project_key / 'archived' / name
        if archive.is_dir():
            raise ChatError('issue_archived')
        raise ChatError('issue_missing')
    return authorities.pop()


def resolve_target(cwd: Path, explicit: str | None = None) -> ChatTarget:
    locale = 'en-US'
    try:
        directory = _issue_directory(cwd, explicit)
        board = _read_json(directory / 'blackboard.json')
        locale = board.get('conversation_locale', 'en-US')
        if not isinstance(locale, str) or not locale.strip():
            raise ValueError('invalid locale')
        workflow_id = callback._prepared_workflow_id(directory)
        if board.get('workflow_id') != workflow_id:
            raise ChatError('identity_conflict', locale=locale)
        selected = callback.read_chat_session(directory, workflow_id=workflow_id)
        return ChatTarget(directory, workflow_id, locale=locale, **selected)
    except ChatError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        known = {'identity_absent', 'identity_conflict', 'unsupported_mode', 'host_bound',
                 'unsupported_provider', 'recovery_pending'}
        reason = ('identity_absent' if type(error).__name__.endswith('ContractMissingError')
                  else str(error) if str(error) in known else 'identity_conflict')
        raise ChatError(reason, locale=locale) from error


def _contract(target: ChatTarget) -> dict:
    if callback._manager_dir(target.issue_dir).name == 'driver':
        from cafe.driver._store import load_contract
    else:
        from cafe.manager._store import load_contract
    contract, digest = load_contract(
        target.issue_dir, issue_name=target.issue_dir.name,
        workflow_id=target.workflow_id, allow_legacy_upgrade=True,
    )
    if digest != target.contract_sha256:
        raise ChatError('identity_conflict', locale=target.locale)
    return contract


def turn_prompt(target: ChatTarget, text: str, correlation_id: str) -> str:
    """Project current durable facts, never historic wake/audit content or answers."""
    from cafe.core.blackboard import BlackboardState
    from cafe.core.human_task_records import _Envelope, HumanTaskStatus

    directory = target.issue_dir
    raw_board = _read_json(directory / 'blackboard.json')
    if raw_board.get('workflow_id') != target.workflow_id:
        raise ChatError('identity_conflict', locale=target.locale)
    step = raw_board.get('current_step')
    if not isinstance(step, str) or not step:
        raise ValueError('invalid current step')
    # The compatibility parser drops malformed collections; current authority must not.
    handoff = raw_board.get('handoff_contract')
    if handoff is not None and not isinstance(handoff, dict):
        raise ValueError('invalid current handoff')
    if not isinstance(raw_board.get('artifacts', {}), dict):
        raise ValueError('invalid current artifacts')
    # This parser validates current fields only. No audit reconstruction occurs.
    board = BlackboardState.from_dict({**raw_board, 'events': []}, initial_step=step)
    contract = _contract(target)
    tasks = []
    path = directory / 'human_tasks.json'
    if path.exists():
        envelope = _Envelope.from_dict(_read_json(path))
        if envelope.workflow_id != target.workflow_id:
            raise ValueError('task workflow mismatch')
        for task in envelope.tasks.values():
            if task.status is not HumanTaskStatus.PENDING:
                continue
            assignment = envelope.assignments[task.id]
            tasks.append({
                'id': task.id, 'step': task.step, 'policy_id': task.policy_id,
                'trigger': task.trigger, 'assignment': assignment.to_dict(),
                'capability_approval': task.capability_approval,
                'inspect': f'cafe task inspect {task.id}',
                'answer': f'cafe task complete {task.id} --help',
            })
    if len(tasks) > 32 or len(board.artifacts) > 32:
        raise ValueError('current context exceeds bounded projection')
    artifacts = {}
    for key, artifact in board.artifacts.items():
        # The current phase's pending output is not accepted upstream authority.
        if artifact.updated_by == step:
            continue
        lexical = directory.parents[2] / artifact.path
        resolved = lexical.resolve()
        if (not resolved.is_relative_to(directory) or not resolved.is_file()
                or any(p.is_symlink() for p in (lexical, *lexical.parents))):
            raise ValueError('artifact authority is unavailable')
        artifacts[key] = artifact.to_dict()
    snapshot = {
        'issue': directory.name, 'worktree': str(directory.parents[2]),
        'workflow_id': target.workflow_id, 'current_step': step,
        'handoff': board.handoff_contract.to_dict() if board.handoff_contract else None,
        'pending_tasks': tasks, 'accepted_artifacts': artifacts,
        'delivery_contract': contract['delivery_contract'],
        'confirmation_contract': contract['confirmation_contract'],
        'task_contract': contract.get('task_contract'),
        'reactive_user_handoffs': contract['reactive_user_handoffs'],
        'conversation_locale': board.conversation_locale,
    }
    grounding = json.dumps(snapshot, ensure_ascii=False)
    if len(grounding.encode('utf-8')) > 64 * 1024:
        raise ValueError('current context exceeds bounded projection')
    return (
        f'CAFE Manager user conversation. Transient correlation: {correlation_id}\n'
        'This is an explicit conversational turn, not a wake event or HumanTask result.\n'
        'Current durable facts below supersede old chat/wake context. Accepted artifact pointers '
        'can be inspected through your existing authorized read path. Unconfirmed output is not scope authority.\n'
        'Opening, status discussion, connection, or generic acknowledgement does not answer any task '
        'or authorize action. Preserve existing Manager-confirmable behavior without extending authority. '
        'Mandatory/user_required tasks, human-owned clarifications, permissions and capabilities stay pending. '
        'An explicit user answer must identify the current relevant task and use its existing authorized answer '
        'route. Later requests follow existing ownership, confirmation, permission and capability rules. '
        'Do not advance/resume/stop workers, write handoffs or complete tasks merely because of this chat.\n'
        f'Current state:\n{grounding}\n'
        f'User message (conversation input, not durable authority):\n{json.dumps(text, ensure_ascii=False)}'
    )


def run_chat(cwd: Path, explicit: str | None = None) -> int:
    """Hold the existing shared session lock only for each submitted user turn."""
    from cafe.agents.executor import AgentExecutionControl, AgentExecutionError, AgentExecutor
    from cafe.agents.transport import ConversationTransport
    from cafe.core.types import AgentCLI, AgentConfig

    target = None
    try:
        target = resolve_target(cwd, explicit)
        print(render_text('chat.connected', locale=target.locale, catalog_root=CATALOG_ROOT,
                          issue=target.issue_dir.name, worktree=str(target.issue_dir.parents[2])))
        if not sys.stdin.isatty():
            raise ChatError('terminal_required', locale=target.locale)
        if shutil.which(target.cli) is None:
            raise ChatError('provider_unavailable', locale=target.locale)
        while True:
            try:
                text = input('> ')
            except (EOFError, KeyboardInterrupt):
                return 0
            if text.strip() == '/quit':
                return 0
            if not text.strip():
                continue
            try:
                with callback._session_lock(callback._manager_dir(target.issue_dir), blocking=False):
                    current = resolve_target(target.issue_dir.parents[2], target.issue_dir.name)
                    if current != target:
                        raise ChatError('identity_conflict', locale=target.locale)
                    correlation = f'chat-{uuid.uuid4()}'
                    prompt = turn_prompt(current, text, correlation)
                    executor = AgentExecutor(AgentConfig(
                        name='workflow_manager_chat', cli=AgentCLI(current.cli), model=current.model,
                        session_id=current.session_id,
                    ), stream_output=False)
                    ConversationTransport(executor).deliver_to_exact_session(
                        prompt, current.session_id, correlation,
                        required_evidence=frozenset({'model'}) if current.model else frozenset(),
                        on_response=lambda reply: print(reply.response),
                        execution_control=AgentExecutionControl(
                            working_directory=current.issue_dir.parents[2],
                            max_output_bytes=1024 * 1024, max_output_lines=1024,
                        ),
                    )
            except BlockingIOError as error:
                raise ChatError('busy', locale=target.locale) from error
            except AgentExecutionError as error:
                evidence = getattr(error, 'transport_result', None)
                reason = ('delivery_uncertain' if evidence and evidence.accepted is not False
                          else 'provider_unavailable' if error.error_type == 'cli_not_found'
                          else 'delivery_failed')
                raise ChatError(reason, locale=target.locale) from error
            except KeyboardInterrupt:
                # The executor cleans up only its own child; no worker controls are called.
                print(guidance('cancelled', target.locale))
                return 0
    except ChatError as error:
        print(guidance(error.reason, error.locale))
        return 1
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        print(guidance('identity_conflict', target.locale if target else 'en-US'))
        return 1
