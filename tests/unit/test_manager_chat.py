"""Plan U1–U5: deterministic authority, exact identity and actionable limitations."""
import json
from pathlib import Path

import pytest

from tests.fixtures.manager_chat import adapter, git, issue, mutate, registered_issue, repository, snapshot


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    return repository(tmp_path / 'repo')


def test_explicit_selection_reaches_registered_authority_without_writes(repo):
    selected = registered_issue(repo)
    before = snapshot(repo)
    target = adapter().resolve_target(repo, 'topic')
    assert target.issue_dir == selected
    assert target.session_id == 'topic-codex'
    assert target.model is None
    assert snapshot(repo) == before


def test_consistent_worktree_default_and_conflicting_marker(repo):
    selected = registered_issue(repo)
    chat = adapter()
    assert chat.resolve_target(selected.parents[2]).issue_dir == selected
    (selected.parents[1] / 'active_issue').write_text('other')
    with pytest.raises(chat.ChatError) as error:
        chat.resolve_target(selected.parents[2])
    assert error.value.reason == 'selection_conflict'
    assert chat.resolve_target(selected.parents[2], 'topic').issue_dir == selected


@pytest.mark.parametrize('name', ['../topic', '/topic', '.', '..', 'a/b', 'a\\b', ''])
def test_invalid_explicit_names_never_escape_repository(repo, name):
    chat = adapter()
    with pytest.raises(chat.ChatError) as error:
        chat.resolve_target(repo, name)
    assert error.value.reason == 'invalid_issue'


def test_root_never_selects_unrelated_or_newest_issue(repo):
    issue(repo, 'one')
    issue(repo, 'two')
    chat = adapter()
    with pytest.raises(chat.ChatError) as error:
        chat.resolve_target(repo)
    assert error.value.reason == 'issue_required'
    (repo / '.cafe/active_issue').write_text('one')
    assert chat.resolve_target(repo).issue_dir.name == 'one'


@pytest.mark.parametrize('damage,reason', [
    ('missing', 'identity_absent'), ('workflow', 'identity_conflict'),
    ('route', 'identity_conflict'), ('digest', 'identity_conflict'),
    ('provenance', 'identity_conflict'), ('host', 'host_bound'),
    ('legacy', 'identity_conflict'), ('invalid', 'identity_conflict'),
])
def test_invalid_identity_preserves_every_record(repo, damage, reason):
    directory = issue(repo)
    # Use the callback-owned filename, not a fixture-only convention.
    path = directory / 'manager' / adapter('workflow_event_callback').DISPATCH_STATE_FILENAME
    if damage == 'missing':
        path.unlink()
    elif damage == 'invalid':
        path.write_text('{')
    else:
        def change(state):
            if damage == 'workflow': state['workflow_id'] = 'stale'
            elif damage == 'route': state['entries'][0]['cli'] = 'claude'
            elif damage == 'digest': state['contract_sha256'] = '0' * 64
            elif damage == 'provenance': state['entries'][0]['session']['acquired_at'] = ''
            elif damage == 'host': state['entries'][0]['session']['source'] = 'host_session'
            elif damage == 'legacy': state['entries'][0].pop('cli')
        mutate(path, change)
    before = snapshot(repo)
    chat = adapter()
    with pytest.raises(chat.ChatError) as error:
        chat.resolve_target(repo, 'topic')
    assert error.value.reason == reason
    assert snapshot(repo) == before


def test_active_fallback_uses_current_model_and_not_primary(repo):
    directory = issue(repo, fallback=True)
    target = adapter().resolve_target(repo, 'topic')
    assert target.session_id == 'topic-codex'
    assert target.model == 'exact-fallback'
    assert target.issue_dir == directory


@pytest.mark.parametrize('mode', ['attached', 'unattended'])
def test_other_modes_return_to_origin_without_writes(repo, mode):
    issue(repo, mode=mode)
    chat = adapter()
    before = snapshot(repo)
    with pytest.raises(chat.ChatError) as error:
        chat.resolve_target(repo, 'topic')
    assert error.value.reason == 'unsupported_mode'
    assert 'origin' in chat.guidance(error.value.reason, 'en-US').lower()
    assert snapshot(repo) == before


@pytest.mark.parametrize('damage', ['unreadable', 'stale_pointer', 'competing', 'archived', 'dual'])
def test_issue_authority_failures_are_read_only(repo, damage):
    directory = registered_issue(repo)
    if damage == 'unreadable': (directory / 'issue.yaml').write_text('[')
    elif damage == 'stale_pointer': mutate_path = repo / '.cafe/issues/topic/issue.yaml'; mutate_path.write_text('worktree_path: /missing')
    elif damage == 'competing': issue(repo, 'topic')
    elif damage == 'archived':
        (directory / 'issue.yaml').write_text('issue_name: topic\nstatus: archived\n')
    elif damage == 'dual':
        driver = directory / 'driver'
        driver.mkdir()
        (driver / 'contract.json').write_text('{}')
    chat = adapter()
    before = snapshot(repo)
    with pytest.raises(chat.ChatError):
        chat.resolve_target(repo, 'topic')
    assert snapshot(repo) == before


def test_guidance_catalogs_have_matching_reasons_and_actions():
    from cafe.core.runtime_locales import load_catalogs
    chat = adapter()
    catalogs = load_catalogs(chat.CATALOG_ROOT)
    assert set(catalogs['en-US']) == set(catalogs['zh-TW'])
    for reason in ['identity_absent', 'identity_conflict', 'busy', 'provider_unavailable', 'host_bound', 'unsupported_mode', 'unsupported_provider']:
        assert chat.guidance(reason, 'en-US')
        assert chat.guidance(reason, 'zh-TW')


from tests.unit.test_conversation_transport import codex_reply, provider_process


def terminal(monkeypatch, values):
    turns = iter(values)
    def read(_prompt):
        value = next(turns, EOFError())
        if isinstance(value, BaseException):
            raise value
        return value
    monkeypatch.setattr('builtins.input', read)
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/codex')


@pytest.mark.parametrize('values', [['', ' ', '/quit'], [EOFError()], [KeyboardInterrupt()]])
def test_idle_chat_has_no_provider_or_durable_effect(repo, monkeypatch, provider_process, values):
    issue(repo)
    terminal(monkeypatch, values)
    before = snapshot(repo)
    launch = provider_process([])
    chat = adapter()
    assert chat.run_chat(repo, 'topic') == 0
    assert snapshot(repo) == before
    launch.assert_not_called()


def test_each_turn_is_grounded_in_current_state_and_keeps_identity(repo, monkeypatch, provider_process, capsys):
    directory = issue(repo)
    terminal(monkeypatch, ['status?', 'thanks', '/quit'])
    launch = provider_process(codex_reply('topic-codex'))
    prompts = []
    process = launch.return_value
    def execute(command, **kwargs):
        prompts.append(command)
        # The provider child must use the selected issue worktree.
        assert Path(kwargs['cwd']) == directory.parents[2]
        assert command[command.index('-C') + 1] == str(directory.parents[2])
        process.stdout.readline.side_effect = [json.dumps(r)+'\n' for r in codex_reply('topic-codex')] + ['']
        if len(prompts) == 1:
            mutate(directory / 'blackboard.json', lambda b: b.update(current_step='inspect_custom'))
        return process
    launch.side_effect = execute
    chat = adapter()
    assert chat.run_chat(repo, 'topic') == 0
    assert len(prompts) == 2
    for command in prompts:
        assert command[command.index('resume')+1] == 'topic-codex'
        assert '--model' not in command
    assert 'build_custom' in prompts[0][prompts[0].index('resume')+2]
    assert 'inspect_custom' in prompts[1][prompts[1].index('resume')+2]
    assert capsys.readouterr().out.count('verified reply') == 2


def test_grounding_retains_pending_tasks_and_confirmed_constraints(repo):
    from cafe.core.human_task_records import HumanTaskRecordStore
    directory = issue(repo)
    chat = adapter()
    target = chat.resolve_target(repo, 'topic')
    store = HumanTaskRecordStore(directory)
    task = store.materialize(workflow_id=target.workflow_id, step='build_custom', iteration=1,
        trigger='need_permission', policy_id='permission', prompt='Approve?',
        expected_result={'type': 'feedback'}, continuations={'continue': 'build_custom'},
        assignee_type='user')
    before = snapshot(repo)
    prompt = chat.turn_prompt(target, 'ok', 'correlation-1')
    assert task.id in prompt
    assert 'cafe task inspect' in prompt and 'cafe task complete' in prompt
    assert 'No paid services.' in prompt and 'Automatic publication.' in prompt
    assert target.workflow_id in prompt
    assert 'ok' in prompt
    assert snapshot(repo) == before


def test_busy_chat_uses_callback_lock_without_waiting(repo, monkeypatch, provider_process):
    directory = issue(repo)
    terminal(monkeypatch, ['status?'])
    launch = provider_process([])
    callback = adapter('workflow_event_callback')
    with callback._session_lock(directory / 'manager'):
        chat = adapter()
        assert chat.run_chat(repo, 'topic') != 0
    launch.assert_not_called()
    with callback._session_lock(directory / 'manager', blocking=False):
        pass


def test_cancellation_releases_turn_lock_and_never_replays(repo, monkeypatch, provider_process):
    directory = issue(repo)
    terminal(monkeypatch, ['status?'])
    launch = provider_process([])
    launch.side_effect = KeyboardInterrupt
    before = snapshot(repo)
    assert adapter().run_chat(repo, 'topic') == 0
    assert launch.call_count == 1
    assert snapshot(repo) == before
    with adapter('workflow_event_callback')._session_lock(directory / 'manager', blocking=False):
        pass


@pytest.mark.parametrize('damage,reason', [
    ('unacquired', 'identity_absent'), ('other_provider', 'unsupported_provider'),
    ('outstanding', 'recovery_pending'), ('phase_only', 'identity_absent'),
    ('duplicate', 'identity_conflict'),
])
def test_uncertain_or_unrelated_sessions_never_supply_the_current_target(repo, damage, reason):
    directory = issue(repo, primary='claude' if damage == 'other_provider' else 'codex')
    callback = adapter('workflow_event_callback')
    path = directory / 'manager' / callback.DISPATCH_STATE_FILENAME
    if damage == 'phase_only':
        from cafe.core.session import SessionManager
        from cafe.core.types import AgentCLI
        SessionManager(str(directory / 'sessions')).save_session('phase', AgentCLI.CODEX, 'phase-only')
        (directory / 'manager/contract.json').unlink()
        path.unlink()
    elif damage == 'duplicate':
        path.write_text(path.read_text().replace('"active_index": 0', '"active_index": 0, "active_index": 0'))
    else:
        def change(state):
            if damage == 'unacquired': state['entries'][0]['session'] = None
            elif damage == 'outstanding':
                state['events']['event-1'] = {
                    'event': {'event_id': 'event-1', 'workflow_id': state['workflow_id'], 'sequence': 1, 'occurred_at': '2026-10-03T00:00:00Z'},
                    'starting_index': 0, 'status': 'routing', 'attempts': [],
                    'accepted_index': None, 'takeover': None, 'recovery_pending': False,
                }
        mutate(path, change)
    chat = adapter()
    before = snapshot(repo)
    with pytest.raises(chat.ChatError) as error:
        chat.resolve_target(repo, 'topic')
    assert error.value.reason == reason
    assert snapshot(repo) == before


def test_archived_only_issue_is_never_selected(repo, tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', lambda: home)
    archive = home / '.cafe/projects' / str(repo).lstrip('/').replace('/', '-') / 'archived/topic'
    archive.mkdir(parents=True)
    (archive / 'issue.yaml').write_text('issue_name: topic')
    chat = adapter()
    with pytest.raises(chat.ChatError) as error:
        chat.resolve_target(repo, 'topic')
    assert error.value.reason == 'issue_archived'


def test_current_accepted_artifacts_and_locale_ground_each_turn(repo):
    directory = issue(repo, locale='zh-TW')
    artifact = directory / 'accepted/iteration_001/output.md'
    artifact.parent.mkdir(parents=True)
    artifact.write_text('Confirmed source')
    mutate(directory / 'blackboard.json', lambda b: b.update(artifacts={'accepted': {
        'name': 'accepted', 'kind': 'document', 'version': 1, 'updated_by': 'accepted',
        'path': str(artifact.relative_to(repo)),
    }}))
    target = adapter().resolve_target(repo, 'topic')
    assert target.locale == 'zh-TW'
    prompt = adapter().turn_prompt(target, '狀態？', 'turn-1')
    assert str(artifact.relative_to(repo)) in prompt
    assert 'zh-TW' in prompt


def test_noninteractive_open_sends_nothing(repo, monkeypatch, provider_process):
    issue(repo)
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    launch = provider_process([])
    before = snapshot(repo)
    assert adapter().run_chat(repo, 'topic') != 0
    launch.assert_not_called()
    assert snapshot(repo) == before
