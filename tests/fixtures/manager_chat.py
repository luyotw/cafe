"""Complete durable Manager chat fixtures and real registered Git worktrees."""
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from cafe.core.blackboard import BlackboardStore
from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract
from tests.fixtures.delivery_contract import delivery_contract

SCRIPTS = Path(__file__).parents[2] / 'src/cafe/data/skills/use-cafe-workflow/scripts'


def adapter(name='manager_chat'):
    key = f'chat_test_{name}'
    spec = importlib.util.spec_from_file_location(key, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def repository(root):
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'Initial')
    return root


def issue(root, name='topic', *, mode='event-driven', fallback=False, locale='en-US', primary='codex'):
    directory = root / '.cafe/issues' / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'issue.yaml').write_text(yaml.safe_dump({'issue_name': name}), encoding='utf-8')
    board = BlackboardStore(directory).load_or_create('build_custom')
    board.conversation_locale = locale
    BlackboardStore(directory).save(board)
    clis = [{'cli': 'claude'}, {'cli': 'codex', 'model': 'exact-fallback'}] if fallback else [{'cli': primary}]
    proposal = {
        'delivery_contract': delivery_contract(),
        'locales': {'conversation': {'value': locale, 'source': 'test'}},
        'confirmation_contract': {'user_required': ['build_custom'], 'manager_confirmable': [], 'mandatory_human_stops': ['build_custom']},
        'reactive_user_handoffs': {'need_clarification': 'user_required', 'need_permission': 'user_required', 'alignment_checkpoint': 'manager_resolvable_when_clear'},
        'task_contract': {'user_required': [], 'manager_confirmable': []},
        'phases': [{'name': 'build_custom', 'chain': [{'cli': 'codex', 'model': 'phase-only'}]}],
        'proactive_review': {'phase_decisions': [{'phase': 'build_custom', 'decision': 'not_required'}]},
        'manager': {'mode': mode, **({'poll_interval_seconds': 30} if mode == 'attached' else {}), **({'clis': clis} if mode == 'event-driven' else {})},
        'checkout': {'kind': 'current_checkout'},
    }
    activate_confirmed_contract(ActivateConfirmedContract(
        issue_dir=directory, issue_name=name, workflow_id=board.workflow_id,
        confirmed_by='user', confirmed_at=datetime(2026, 10, 3, tzinfo=timezone.utc), proposal=proposal,
    ))
    callback = adapter('workflow_event_callback')
    if mode == 'event-driven':
        config = callback._contract_callback_config(issue_dir=directory, issue_name=name, workflow_id=board.workflow_id)
        state = callback._load_or_initialize_dispatch_state(directory / 'manager', workflow_id=board.workflow_id, config=config)
        state['active_index'] = 1 if fallback else 0
        for entry in state['entries']:
            entry['session'] = {'id': f'{name}-{entry["cli"]}', 'source': 'provider', 'acquired_at': '2026-10-03T00:00:00Z'}
        callback._write_dispatch_state(directory / 'manager', state)
    return directory


def registered_issue(root, name='topic', **kwargs):
    worktree = root.parent / name
    git(root, 'worktree', 'add', '-b', name, str(worktree))
    directory = issue(worktree, name, **kwargs)
    inventory = root / '.cafe/issues' / name
    inventory.mkdir(parents=True)
    (inventory / 'issue.yaml').write_text(yaml.safe_dump({'issue_name': name, 'worktree_path': str(worktree)}))
    return directory


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in (root / '.cafe').rglob('*') if p.is_file() and p.name != 'session.lock'}


def mutate(path, change):
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record), encoding='utf-8')


def legacy_chat_authority(directory, *, dual=False):
    """Build a valid predecessor under its sole authoritative lock and session store."""
    import shutil
    from cafe.core.packet_io import canonical_json
    from cafe.driver._schema import build_initial_contract
    from cafe.driver._store import contract_lock
    from cafe.manager._store import load_contract
    current, _ = load_contract(directory)
    proposal = {key: current[key] for key in [
        'delivery_contract', 'locales', 'confirmation_contract', 'reactive_user_handoffs',
        'task_contract', 'phases', 'proactive_review', 'manager', 'checkout',
    ]}
    # Decode a copy before translating the historical names.
    proposal = json.loads(json.dumps(proposal))
    proposal['driver'] = proposal.pop('manager')
    for field in ['confirmation_contract', 'task_contract']:
        proposal[field]['driver_confirmable'] = proposal[field].pop('manager_confirmable')
    proposal['reactive_user_handoffs']['alignment_checkpoint'] = 'driver_resolvable_when_clear'
    legacy = build_initial_contract(
        proposal=proposal, issue_name=directory.name, workflow_id=current['identity']['workflow_id'],
        confirmed_by='user', confirmed_at='2026-10-03T00:00:00+00:00',
    )
    driver = directory / 'driver'
    driver.mkdir()
    (driver / 'contract.json').write_bytes(canonical_json(legacy))
    if not dual:
        shutil.rmtree(directory / 'manager')
    callback = adapter('workflow_event_callback')
    config = callback._contract_callback_config(
        issue_dir=directory, issue_name=directory.name, workflow_id=current['identity']['workflow_id'],
    )
    state = callback._load_or_initialize_dispatch_state(
        driver, workflow_id=current['identity']['workflow_id'], config=config,
    )
    for entry in state['entries']:
        entry['session'] = {'id': f'{directory.name}-{entry["cli"]}', 'source': 'provider',
                            'acquired_at': '2026-10-03T00:00:00Z'}
    callback._write_dispatch_state(driver, state)
    with contract_lock(directory):
        pass
