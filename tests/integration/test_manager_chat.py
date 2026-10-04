"""Plan I1/I3: public composed command selects durable worktree authority."""
import pytest
from typer.testing import CliRunner

from tests.fixtures.manager_chat import registered_issue, repository, snapshot


def test_worktree_and_explicit_root_open_and_exit_without_mutation(tmp_path, monkeypatch):
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    directory = registered_issue(repo)
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    for cwd, arguments in [(directory.parents[2], []), (repo, ['--issue', 'topic'])]:
        monkeypatch.chdir(cwd)
        before = snapshot(cwd)
        result = CliRunner().invoke(app, ['manager', 'chat', *arguments], input='/quit\n')
        assert result.exit_code == 0, result.output
        assert 'topic' in result.output
        assert snapshot(cwd) == before


def test_public_command_missing_issue_gives_selection_action(tmp_path, monkeypatch):
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'missing'])
    assert result.exit_code != 0
    assert '--issue' in result.output


import json
import os
import select
import subprocess
import sys
from pathlib import Path

from tests.fixtures.manager_chat import adapter, issue, mutate
from tests.unit.test_conversation_transport import codex_reply, provider_process


@pytest.fixture(autouse=True)
def terminal_io(monkeypatch):
    from click.testing import _NamedTextIOWrapper
    monkeypatch.setattr(_NamedTextIOWrapper, 'isatty', lambda self: True)
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/codex')
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)


@pytest.mark.parametrize('fallback', [False, True])
def test_user_turns_preserve_active_identity_and_refresh_grounding(tmp_path, monkeypatch, provider_process, fallback):
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    directory = registered_issue(repo, fallback=fallback)
    monkeypatch.chdir(repo)
    model = 'exact-fallback' if fallback else None
    launch = provider_process(codex_reply('topic-codex', model))
    process = launch.return_value
    prompts = []
    before_manager = snapshot(directory.parents[2])
    def deliver(command, **kwargs):
        prompts.append(command)
        assert Path(kwargs['cwd']) == directory.parents[2]
        process.stdout.readline.side_effect = [json.dumps(r)+'\n' for r in codex_reply('topic-codex', model)] + ['']
        if len(prompts) == 1:
            mutate(directory / 'blackboard.json', lambda b: b.update(current_step='inspect_custom'))
        return process
    launch.side_effect = deliver
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\nthanks\n/quit\n')
    assert result.exit_code == 0, result.output
    assert result.output.count('verified reply') == 2
    assert len(prompts) == 2
    assert 'inspect_custom' in prompts[1][prompts[1].index('resume') + 2]
    for command in prompts:
        assert command[command.index('resume') + 1] == 'topic-codex'
        assert ('--model' in command) == fallback
        if fallback: assert command[command.index('--model')+1] == model
    after = snapshot(directory.parents[2])
    board_key = '.cafe/issues/topic/blackboard.json'
    assert {k:v for k,v in after.items() if k != board_key} == {k:v for k,v in before_manager.items() if k != board_key}


@pytest.mark.parametrize('trigger', ['confirm_output', 'need_clarification', 'need_permission'])
def test_conversation_does_not_answer_current_human_boundary(tmp_path, monkeypatch, provider_process, trigger):
    from cafe.manager.cli import app
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.core.task_inbox import TaskInboxService
    from cafe.manager.task_authority import decide_task_authority
    from cafe.manager._store import load_contract
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    target = adapter().resolve_target(repo, 'topic')
    task = HumanTaskRecordStore(directory).materialize(
        workflow_id=target.workflow_id, step='build_custom', iteration=1, trigger=trigger,
        policy_id='boundary', prompt='Explicit user response required', expected_result={'type': 'feedback'},
        continuations={'continue': 'build_custom'}, assignee_type='user',
        capability_approval={'capability_id': 'test'} if trigger == 'need_permission' else None,
    )
    # Simulated durable worker state is never touched by chat.
    (directory / 'worker.json').write_text(json.dumps({'pid': 123456, 'state': 'running'}))
    (repo / '.cafe/active_issue').write_text('topic')
    monkeypatch.chdir(repo)
    before = snapshot(repo)
    launch = provider_process(codex_reply('topic-codex'))
    process = launch.return_value
    def deliver(command, **kwargs):
        assert task.id in command[command.index('resume')+2]
        process.stdout.readline.side_effect = [json.dumps(r)+'\n' for r in codex_reply('topic-codex')] + ['']
        return process
    launch.side_effect = deliver
    for text in ['/quit\n', 'status?\nok\n/quit\n']:
        result = CliRunner().invoke(app, ['manager', 'chat'], input=text)
        assert result.exit_code == 0, result.output
        assert snapshot(repo) == before
    detail = TaskInboxService(repo / '.cafe').inspect(task.id).to_dict()
    contract, _ = load_contract(directory)
    decision = decide_task_authority(task=detail, contract=contract, current_task_id=task.id)
    assert decision['resolution_owner'] == 'user_required'
    assert decision['allowed'] is False
    assert launch.call_count == 2


@pytest.mark.parametrize('damage', ['before_acceptance', 'after_acceptance', 'wrong_session', 'provider_missing'])
def test_failed_resume_preserves_identity_and_has_no_replay(tmp_path, monkeypatch, provider_process, damage):
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    monkeypatch.chdir(repo)
    records = codex_reply('wrong' if damage == 'wrong_session' else 'topic-codex')
    if damage == 'before_acceptance': records = records[:1]
    if damage == 'after_acceptance': records = records[:3]
    launch = provider_process(records, returncode=1 if damage == 'before_acceptance' else 0)
    if damage == 'provider_missing': monkeypatch.setattr('shutil.which', lambda _: None)
    before = snapshot(repo)
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='hello\n')
    assert result.exit_code != 0
    assert 'verified reply' not in result.output
    assert snapshot(repo) == before
    assert launch.call_count == (0 if damage == 'provider_missing' else 1)
    if damage == 'after_acceptance': assert 'retry' in result.output


def test_cross_process_callback_lock_is_busy_then_idle_chat_releases_it(tmp_path, monkeypatch):
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    monkeypatch.chdir(repo)
    script = "import fcntl,sys; f=open(sys.argv[1], 'a+'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.readline()"
    real_popen = subprocess.Popen
    def process_boundary(command, **kwargs):
        if command[0] == 'codex':
            pytest.fail('busy/idle chat must never launch a provider')
        return real_popen(command, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', process_boundary)
    child = subprocess.Popen([sys.executable, '-c', script, str(directory / 'manager/session.lock')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline().strip() == 'ready'
        result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n')
        assert result.exit_code != 0
        assert 'busy' in result.output
    finally:
        child.communicate('\n', timeout=5)
    def idle_input(_prompt):
        with adapter('workflow_event_callback')._session_lock(directory / 'manager', blocking=False):
            pass
        return '/quit'
    monkeypatch.setattr('builtins.input', idle_input)
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'])
    assert result.exit_code == 0, result.output


def test_cancelling_provider_child_preserves_worker_and_releases_lock(tmp_path, monkeypatch, provider_process):
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    monkeypatch.chdir(repo)
    launch = provider_process(codex_reply('topic-codex'))
    # Terminal Ctrl-C interrupts the consumer, not the background pipe reader.
    def interrupted_read(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr('cafe.agents.process_output.ProcessOutput.readline', interrupted_read)
    before = snapshot(repo)
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n')
    assert result.exit_code == 0, result.output
    assert launch.call_count == 1
    launch.return_value.terminate.assert_called_once()
    assert snapshot(repo) == before
    with adapter('workflow_event_callback')._session_lock(directory / 'manager', blocking=False):
        pass


def test_canonical_help_and_declared_entrypoint_keep_generic_help_separate():
    import tomllib
    from cafe.manager.cli import app, main
    from cafe.ui.cli import app as generic_app
    root = Path(__file__).parents[2]
    declaration = tomllib.loads((root / 'pyproject.toml').read_text())
    assert declaration['project']['scripts']['cafe'] == f'{main.__module__}:main'
    runner = CliRunner()
    assert runner.invoke(app, ['manager', 'chat', '--help']).exit_code == 0
    assert '--issue' in runner.invoke(app, ['manager', 'chat', '--help']).output
    assert runner.invoke(generic_app, ['manager', 'chat', '--help']).exit_code != 0
    assert runner.invoke(app, ['version']).exit_code == 0


def test_custom_phase_role_manager_and_workflow_manager_have_distinct_sessions(tmp_path, monkeypatch, provider_process):
    import yaml
    from cafe.manager.cli import app
    from cafe.core.session import SessionManager
    from cafe.core.types import AgentCLI
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    from tests.fixtures.manager_chat import git
    git(repo, 'checkout', '-b', 'topic')
    monkeypatch.chdir(repo)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    playbooks = repo / '.cafe/playbooks'
    playbooks.mkdir()
    (playbooks / 'custom.yaml').write_text(yaml.safe_dump({
        'playbook': {'id': 'custom', 'name': 'Custom workflow'},
        'roles': {'manager': {'default_agent': 'PhaseAgent'}},
        'steps': {'build_custom': {'type': 'skill', 'role': 'manager', 'skill': 'cafe-develop',
                                  'on': {'await_agent': 'build_custom'}}},
    }))
    from cafe.playbooks.loader import PlaybookLoader
    PlaybookLoader(project_root=repo).load('custom')
    mutate(directory / 'blackboard.json', lambda b: b.update(playbook_id='custom'))
    (directory / 'issue.yaml').write_text('issue_name: topic\nplaybook: custom\n')
    (repo / '.cafe/phases.yaml').write_text(yaml.safe_dump({'build_custom': {
        'name': 'PhaseAgent', 'role': 'manager', 'clis': [{'cli': 'codex', 'model': 'phase-only'}],
    }}))
    SessionManager().save_session('PhaseAgent', AgentCLI.CODEX, 'phase-session', 'topic', 'build_custom')
    launch = provider_process(codex_reply('phase-session', 'phase-only'))
    phase = CliRunner().invoke(app, ['chat', 'manager', '--phase', 'build_custom', '--prompt', 'phase status'])
    assert phase.exit_code == 0, phase.output
    assert launch.call_count == 1
    command = launch.call_args.args[0]
    assert command[command.index('resume')+1] == 'phase-session'
    launch = provider_process(codex_reply('topic-codex'))
    launch.reset_mock()
    workflow = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='manager status\n/quit\n')
    assert workflow.exit_code == 0, workflow.output
    assert launch.call_count == 1
    command = launch.call_args.args[0]
    assert command[command.index('resume')+1] == 'topic-codex'
    assert '--model' not in command


def test_composed_public_main_retains_owner_module_on_checkout_reexecution(monkeypatch, tmp_path):
    from cafe.manager import cli as composed
    from cafe.ui import cli as generic
    monkeypatch.setattr(generic, '_check_dependencies', lambda: None)
    monkeypatch.delenv('CAFE_SKIP_ENTRYPOINT_CHECK', raising=False)
    monkeypatch.setattr(generic, '_resolve_repo_entrypoint_mismatch', lambda **_: (tmp_path, tmp_path/'expected', tmp_path/'actual'))
    monkeypatch.setattr(sys, 'argv', ['cafe', 'manager', 'chat', '--issue', 'selected'])
    class Reexecuted(BaseException): pass
    commands = []
    def replace(_executable, command, _environment):
        commands.append(command)
        raise Reexecuted
    monkeypatch.setattr(os, 'execvpe', replace)
    with pytest.raises(Reexecuted): composed.main()
    assert commands[0][1:] == ['-m', 'cafe.manager.cli', 'manager', 'chat', '--issue', 'selected']


def test_checkout_module_help_and_canonical_documentation_agree():
    root = Path(__file__).parents[2]
    environment = {**os.environ, 'PYTHONPATH': str(root / 'src')}
    result = subprocess.run([sys.executable, '-m', 'cafe.manager.cli', 'manager', 'chat', '--help'],
                            cwd=root, env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert '--issue' in result.stdout
    for name in ['README.md', 'docs/manager-chat.md', 'src/cafe/data/skills/use-cafe-workflow/references/running_workflow.md']:
        text = (root / name).read_text()
        assert 'cafe manager chat' in text
        assert 'cafe task inspect' in text and 'cafe task complete' in text


@pytest.mark.parametrize('field,value', [
    ('handoff_contract', []), ('handoff_contract', 'invalid'),
    ('artifacts', []), ('artifacts', 'invalid'),
])
def test_malformed_current_authority_never_submits_or_repairs(
    tmp_path, monkeypatch, provider_process, field, value,
):
    """Plan U4/U6, I3: damaged authority fails through the public command."""
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    mutate(directory / 'blackboard.json', lambda board: board.update({field: value}))
    monkeypatch.chdir(repo)
    before = snapshot(repo)
    launch = provider_process(codex_reply('topic-codex'))
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n/quit\n')
    assert result.exit_code != 0
    assert 'verified reply' not in result.output
    launch.assert_not_called()
    assert snapshot(repo) == before


@pytest.mark.parametrize('absent', [True, False])
def test_optional_current_authority_stays_valid(tmp_path, monkeypatch, provider_process, absent):
    """Plan U6/I2: missing and explicit empty current facts remain legitimate."""
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    def empty(board):
        if absent:
            board.pop('handoff_contract', None)
            board.pop('artifacts', None)
        else:
            board.update(handoff_contract=None, artifacts={})
    mutate(directory / 'blackboard.json', empty)
    monkeypatch.chdir(repo)
    before = snapshot(repo)
    launch = provider_process(codex_reply('topic-codex'))
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n/quit\n')
    assert result.exit_code == 0, result.output
    assert 'verified reply' in result.output
    assert launch.call_count == 1
    assert snapshot(repo) == before


@pytest.mark.parametrize('transition', ['board', 'task_created', 'task_changed'])
def test_worker_transition_during_grounding_never_submits_mixed_facts(
    tmp_path, monkeypatch, provider_process, transition,
):
    """Plan U6/I2/I4: real durable transitions are detected at the read boundary."""
    from cafe.manager.cli import app
    from cafe.manager.cli import _builtin_chat
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.human_task_records import HumanTaskRecordStore
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    store = BlackboardStore(directory)
    board = store.load_or_create('build_custom')
    task_store = HumanTaskRecordStore(directory)
    def materialize(step):
        return task_store.materialize(
            workflow_id=board.workflow_id, step=step, iteration=1, trigger='confirm_output',
            policy_id='boundary', prompt='Explicit user response required',
            expected_result={'type': 'feedback'}, continuations={'continue': step}, assignee_type='user',
        )
    if transition == 'task_changed':
        materialize('build_custom')
    monkeypatch.chdir(repo)
    launch = provider_process(codex_reply('topic-codex'))
    chat = _builtin_chat()
    original_read = chat.callback._read_bounded_text
    board_reads = 0
    after_transition = None
    def read(path, **kwargs):
        nonlocal board_reads, after_transition
        content = original_read(path, **kwargs)
        if Path(path) == directory / 'blackboard.json':
            board_reads += 1
            # Initial selection and revalidation precede the prompt's first read.
            if board_reads == 3:
                if transition == 'board':
                    board.current_step = 'inspect_custom'
                    store.save(board)
                    materialize('inspect_custom')
                elif transition == 'task_created':
                    # Wait for the first tasks observation to test creation after absence.
                    pass
                if transition == 'board':
                    after_transition = snapshot(repo)
        if transition == 'task_changed' and Path(path) == directory / 'human_tasks.json' and after_transition is None:
            materialize('inspect_custom')
            after_transition = snapshot(repo)
        if transition == 'task_created' and board_reads == 4 and after_transition is None:
            materialize('inspect_custom')
            after_transition = snapshot(repo)
        return content
    monkeypatch.setattr(chat.callback, '_read_bounded_text', read)
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n/quit\n')
    assert after_transition is not None
    assert result.exit_code != 0
    assert 'verified reply' not in result.output
    launch.assert_not_called()
    assert snapshot(repo) == after_transition


def test_noncompleting_provider_turn_is_timed_out_and_releases_chat_lock(
    tmp_path, monkeypatch, provider_process,
):
    """Plan U9/I3/I6: exercise the real executor timer from the public caller."""
    from threading import Event, Timer
    from cafe.manager.cli import app
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    monkeypatch.chdir(repo)
    launch = provider_process(codex_reply('topic-codex')[:2])
    process = launch.return_value
    terminated = Event()
    records = iter([json.dumps(record) + '\n' for record in codex_reply('topic-codex')[:2]])
    def read():
        record = next(records, None)
        if record is not None:
            return record
        assert terminated.wait(1), 'Provider was not terminated within the test deadline'
        return ''
    process.stdout.readline.side_effect = read
    process.terminate.side_effect = terminated.set
    durations = []
    timers = []
    def timer(duration, callback):
        durations.append(duration)
        # Accelerate only the clock boundary; production executor starts and cleans it up.
        value = Timer(0.025, callback)
        timers.append(value)
        return value
    monkeypatch.setattr('cafe.agents.executor.Timer', timer)
    before = snapshot(repo)
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n')
    try:
        assert len(durations) == 1 and 0 < durations[0] <= 60
        assert result.exit_code != 0
        assert 'verified reply' not in result.output
        assert launch.call_count == 1
        process.terminate.assert_called_once()
        process.wait.assert_called()
        assert snapshot(repo) == before
        with adapter('workflow_event_callback')._session_lock(directory / 'manager', blocking=False):
            pass
        from cafe.manager._store import contract_lock
        with contract_lock(directory, blocking=False):
            pass
    finally:
        terminated.set()
        for value in timers:
            value.cancel()
            value.join(timeout=1)


@pytest.mark.parametrize('writer', ['settings', 'reconfirmation'])
@pytest.mark.parametrize('fallback', [False, True])
def test_public_contract_writer_cannot_replace_authority_during_submission(
    tmp_path, monkeypatch, provider_process, writer, fallback,
):
    """Plan U3/U6/I2/I3: real public writers obey the chat submission boundary."""
    from datetime import datetime, timezone
    from threading import Event, Thread
    from cafe.manager.cli import app
    from cafe.manager import ReplaceConfirmedContract, replace_confirmed_contract, update_manager_settings
    from cafe.manager._store import load_contract
    repo = repository(tmp_path / 'repo')
    directory = issue(repo, fallback=fallback)
    monkeypatch.chdir(repo)
    contract, digest = load_contract(directory)
    target = adapter().resolve_target(repo, 'topic')
    dispatch = directory / 'manager' / adapter('workflow_event_callback').DISPATCH_STATE_FILENAME
    original_identity = dispatch.read_bytes()
    started, finished = Event(), Event()
    errors = []
    def replace():
        started.set()
        try:
            if writer == 'settings':
                update_manager_settings(
                    issue_dir=directory, issue_name='topic', workflow_id=target.workflow_id,
                    manager={'mode': 'unattended'}, expected_contract_sha256=digest,
                )
            else:
                proposal = {key: contract[key] for key in [
                    'delivery_contract', 'locales', 'confirmation_contract', 'reactive_user_handoffs',
                    'task_contract', 'phases', 'proactive_review', 'manager', 'checkout',
                ]}
                proposal['manager'] = {'mode': 'unattended'}
                replace_confirmed_contract(ReplaceConfirmedContract(
                    issue_dir=directory, issue_name='topic', workflow_id=target.workflow_id,
                    confirmed_by='user', confirmed_at=datetime.now(timezone.utc), proposal=proposal,
                    expected_predecessor_sha256=digest, kind='user_reconfirmation',
                ))
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()
    thread = Thread(target=replace, daemon=True)
    launch = provider_process(codex_reply('topic-codex', 'exact-fallback' if fallback else None))
    process = launch.return_value
    records = iter(codex_reply('topic-codex', 'exact-fallback' if fallback else None))
    def read():
        record = next(records, None)
        if record is None:
            return ''
        if record['type'] == 'item.completed':
            assert finished.wait(1), 'Writer still waits while the provider is replying'
        return json.dumps(record) + '\n'
    process.stdout.readline.side_effect = read
    def deliver(command, **kwargs):
        # Run the actual public writer after final grounding and at the Popen boundary.
        thread.start()
        assert started.wait(1)
        assert not finished.wait(0.1), 'Contract replacement crossed provider submission'
        assert load_contract(directory)[1] == digest
        return process
    launch.side_effect = deliver
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n/quit\n')
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors == []
    assert result.exit_code == 0, result.output
    assert 'verified reply' in result.output
    assert launch.call_count == 1
    assert load_contract(directory)[1] != digest
    assert dispatch.read_bytes() == original_identity
    again = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n')
    assert again.exit_code != 0
    assert launch.call_count == 1
    with adapter('workflow_event_callback')._session_lock(directory / 'manager', blocking=False):
        pass


@pytest.mark.parametrize('authority', ['manager', 'driver', 'dual'])
def test_busy_contract_writer_rejects_chat_without_waiting_for_replacement(
    tmp_path, monkeypatch, provider_process, authority,
):
    """Plan U9/I6: contention uses the existing lock and remains a bounded failure."""
    from threading import Event, Thread
    from cafe.manager.cli import app
    from cafe.manager._store import contract_lock
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    monkeypatch.chdir(repo)
    if authority != 'manager':
        from tests.fixtures.manager_chat import legacy_chat_authority
        legacy_chat_authority(directory, dual=authority == 'dual')
        from cafe.driver._store import contract_lock
    acquired, release = Event(), Event()
    def hold():
        with contract_lock(directory):
            acquired.set()
            release.wait(1)
    holder = Thread(target=hold, daemon=True)
    holder.start()
    assert acquired.wait(1)
    launch = provider_process(codex_reply('topic-codex'))
    before = snapshot(repo)
    try:
        result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n/quit\n')
        assert result.exit_code != 0
        assert not release.is_set()
        launch.assert_not_called()
        assert snapshot(repo) == before
    finally:
        release.set()
        holder.join(timeout=2)
    assert not holder.is_alive()
    again = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n/quit\n')
    assert again.exit_code == 0, again.output
    assert launch.call_count == 1
    assert snapshot(repo) == before


@pytest.mark.parametrize('authority,fallback,operation', [
    ('manager', False, 'identical'), ('manager', True, 'identical'),
    ('manager', False, 'update'), ('driver', False, 'identical'),
    ('driver', False, 'update'), ('dual', False, 'identical'),
])
def test_resumed_provider_can_complete_existing_public_settings_operation(
    tmp_path, monkeypatch, authority, fallback, operation,
):
    """Plan U7/U9, I2/I3/I6: actual child executes the existing public writer."""
    from threading import Timer
    from cafe.manager.cli import app
    from tests.fixtures.manager_chat import legacy_chat_authority
    repo = repository(tmp_path / 'repo')
    directory = issue(repo, fallback=fallback)
    if authority != 'manager':
        legacy_chat_authority(directory, dual=authority == 'dual')
    monkeypatch.chdir(repo)
    role = 'manager' if authority == 'manager' else 'driver'
    contract = json.loads((directory / role / 'contract.json').read_text())
    current = contract[role]
    settings = current if operation == 'identical' else {'mode': 'unattended'}
    status = 'unchanged' if operation == 'identical' else 'saved'
    model = 'exact-fallback' if fallback else None
    script = f'''
import json
from typer.testing import CliRunner
from cafe.manager.cli import app
identity = {{'type': 'thread.started', 'thread_id': 'topic-codex'}}
if {model!r} is not None:
    identity['model'] = {model!r}
print(json.dumps(identity), flush=True)
print(json.dumps({{'type': 'turn.started'}}), flush=True)
result = CliRunner().invoke(app, ['settings', 'update', 'topic', '--set',
    {'manager=' + json.dumps(settings)!r}, '--json'])
assert result.exit_code == 0, result.output
assert json.loads(result.output)['status'] == {status!r}
print(json.dumps({{'type': 'item.completed', 'item': {{'type': 'agent_message',
    'text': {'settings ' + status!r}}}}}), flush=True)
print(json.dumps({{'type': 'turn.completed', 'usage': {{'input_tokens': 2, 'output_tokens': 3}}}}), flush=True)
'''
    actual_popen = subprocess.Popen
    children = []
    source = Path(__file__).resolve().parents[2] / 'src'
    def provider(command, **kwargs):
        if command[0] != 'codex':
            return actual_popen(command, **kwargs)
        assert command[command.index('resume') + 1] == 'topic-codex'
        assert ('--model' in command) == fallback
        if fallback:
            assert command[command.index('--model') + 1] == model
        kwargs['env'] = {**kwargs['env'], 'PYTHONPATH': str(source)}
        child = actual_popen([sys.executable, '-c', script], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', provider)
    def timer(duration, callback):
        assert 0 < duration <= 60
        return Timer(2, callback)
    monkeypatch.setattr('cafe.agents.executor.Timer', timer)
    before = snapshot(repo)
    try:
        result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'],
            input='Apply these Manager settings through the existing authorized settings route.\n/quit\n')
        assert result.exit_code == 0, result.output
        assert result.output.count('settings ' + status) == 1
        assert len(children) == 1
        assert children[0].poll() is not None
        after = snapshot(repo)
        contract_key = f'.cafe/issues/topic/{role}/contract.json'
        if operation == 'identical':
            assert after == before
        else:
            assert after[contract_key] != before[contract_key]
            assert {key: value for key, value in after.items() if key != contract_key} == {
                key: value for key, value in before.items() if key != contract_key
            }
        with adapter('workflow_event_callback')._session_lock(directory / role, blocking=False):
            pass
        if role == 'manager':
            from cafe.manager._store import contract_lock
        else:
            from cafe.driver._store import contract_lock
        with contract_lock(directory, blocking=False):
            pass
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)


@pytest.mark.parametrize('authority', ['manager', 'driver', 'dual'])
@pytest.mark.parametrize('alias', ['dangling', 'existing', 'hardlink', 'open_swap', 'role_swap', 'issue_swap'])
def test_unsafe_lock_alias_rejects_before_outside_creation_or_submission(
    tmp_path, monkeypatch, provider_process, authority, alias,
):
    """Plan U2/U4, I1/I3/I6: actual lock opens must remain under selected authority."""
    from cafe.manager.cli import app
    from tests.fixtures.manager_chat import legacy_chat_authority
    repo = repository(tmp_path / 'repo')
    directory = issue(repo)
    if authority != 'manager':
        legacy_chat_authority(directory, dual=authority == 'dual')
    role = 'manager' if authority == 'manager' else 'driver'
    lock = directory / role / 'contract.lock'
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'contract.lock'
    if alias in {'existing', 'hardlink'}:
        target.write_text('Unrelated existing content')
    if alias == 'issue_swap':
        (outside / role).mkdir()
    node = directory if alias == 'issue_swap' else directory / role
    outside_before = {str(path.relative_to(outside)): path.read_bytes()
                      for path in outside.rglob('*') if path.is_file()}
    expected = None
    def retarget():
        nonlocal expected
        if expected is not None:
            return
        if alias in {'role_swap', 'issue_swap'}:
            node.rename(node.with_name(node.name + '-original'))
            node.symlink_to(outside, target_is_directory=True)
        else:
            lock.unlink()
            if alias == 'hardlink':
                lock.hardlink_to(target)
            else:
                lock.symlink_to(target)
        expected = snapshot(repo)
    if alias in {'dangling', 'existing', 'hardlink'}:
        retarget()
    else:
        # Substitute only filesystem open boundaries, after the preliminary checks.
        actual_path_open, actual_os_open = Path.open, os.open
        def path_open(path, *args, **kwargs):
            if Path(path) == lock:
                retarget()
            return actual_path_open(path, *args, **kwargs)
        def native_open(path, flags, *args, **kwargs):
            name = Path(path).name
            at_node = name == node.name and flags & os.O_DIRECTORY
            at_lock = name == 'contract.lock'
            if (alias == 'open_swap' and at_lock) or (alias != 'open_swap' and at_node):
                retarget()
            return actual_os_open(path, flags, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', path_open)
        monkeypatch.setattr(os, 'open', native_open)
    monkeypatch.chdir(repo)
    launch = provider_process(codex_reply('topic-codex'))
    result = CliRunner().invoke(app, ['manager', 'chat', '--issue', 'topic'], input='status?\n/quit\n')
    assert expected is not None
    assert result.exit_code != 0
    launch.assert_not_called()
    assert {str(path.relative_to(outside)): path.read_bytes()
            for path in outside.rglob('*') if path.is_file()} == outside_before
    assert snapshot(repo) == expected
