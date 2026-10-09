"""U01-U03/U10/U14-U16, I01/I02/I05/I06: corrected public preparation seams."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'unit'))
from _kickoff_test_support import load_kickoff_module
from test_kickoff_prefill import _project as _base_project

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kickoff_inputs
from test_kickoff_preparation import _formatter_inputs

pytestmark = [pytest.mark.release_extended, pytest.mark.usefixtures("isolated_global_catalog")]


@pytest.fixture(scope="module")
def review_catalog(tmp_path_factory):
    """The review journeys create the same playbook in separate project roots."""
    from cafe.catalogs.resolver import CatalogResolver

    root = tmp_path_factory.mktemp("review-catalog-project")
    _project(root)
    resolver = CatalogResolver(
        project_root=root,
        global_root=tmp_path_factory.mktemp("review-global") / ".cafe",
    )
    catalog = load_kickoff_module("kickoff_catalog").discover_index(
        project_root=root,
        global_root=resolver.global_root,
        builtin_root=resolver.builtin_root,
        cache_file=tmp_path_factory.mktemp("review-catalog-cache") / "catalog.json",
    )
    playbook = (root / ".cafe/playbooks/example.yaml").read_bytes()
    return playbook, catalog


@pytest.fixture(autouse=True)
def reuse_unchanged_catalog_within_journey(monkeypatch, review_catalog):
    """Preference and evidence changes do not alter the playbook catalog."""
    load = kickoff_inputs._load_local_module
    catalogs = {}
    template_playbook, template_catalog = review_catalog

    def load_with_catalog(name):
        module = load(name)
        if name != "kickoff_catalog":
            return module

        def discover_index(**kwargs):
            key = tuple(Path(kwargs[field]).resolve() for field in ("project_root", "global_root", "builtin_root"))
            project = key[0]
            playbook = project / ".cafe/playbooks/example.yaml"
            if playbook.exists() and playbook.read_bytes() == template_playbook:
                catalog = copy.deepcopy(template_catalog)
                for candidate in catalog["candidates"]:
                    if candidate["source"] == "project":
                        candidate["path"] = str(project / ".cafe/playbooks" / Path(candidate["path"]).name)
                return catalog
            if key not in catalogs:
                catalogs[key] = module.discover_index(**kwargs)
            return copy.deepcopy(catalogs[key])

        return SimpleNamespace(discover_index=discover_index)

    monkeypatch.setattr(kickoff_inputs, "_load_local_module", load_with_catalog)


def _project(root):
    request = _base_project(root)
    playbook = root / '.cafe/playbooks/example.yaml'
    playbook.write_text(playbook.read_text().replace('conversation_locale: ja-JP',
        'conversation_locale: ja-JP, applicability: {summary: Custom writing, use_when: [outline], avoid_when: [deployment]}'))
    return request


def _call(cli, capsys, args):
    code = cli.main(args)
    return code, json.loads(capsys.readouterr().out)


def _settings(root):
    return ['--config-dir', str(root / 'config'), '--cache-dir', str(root / 'cache')]


def _save_preference(cli, capsys, root, project, key, value, scope='repository'):
    code, result = _call(cli, capsys, ['preferences', 'set', '--project-root', str(project),
        '--config-dir', str(root / 'config'), '--scope', scope, '--key', key,
        '--value-json', json.dumps(value), '--reuse'])
    assert code == 0, result


@pytest.mark.release_smoke
def test_source_backed_draft_requires_reassessment_before_render_after_change(tmp_path, capsys):
    """Legacy command templates never become current action authority."""
    from datetime import datetime, timezone

    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'
    project.mkdir()
    _project(project)
    source = project / 'README.md'
    source.write_text('Use local commit.\n')
    evidence = tmp_path / 'evidence.json'
    record = {'target': 'local', 'stable_conventions': ['Local commit.'],
        'sources': [{'path': 'README.md', 'fingerprint': hashlib.sha256(source.read_bytes()).hexdigest()}],
        'delivery_template': {'deliver': [['git', 'commit']], 'deliver_description': ['Commit.']}}
    evidence.write_text(json.dumps(record))
    args = ['evidence', 'refresh', '--category', 'delivery', '--project-root', str(project),
        '--cache-dir', str(tmp_path / 'cache'), '--evidence-file', str(evidence)]
    code, rejected = _call(cli, capsys, args)
    assert code == 3 and rejected['diagnostic'] == 'obsolete_manager_delivery_template'
    record.pop('delivery_template')
    evidence.write_text(json.dumps(record))
    assert _call(cli, capsys, args)[0] == 0
    module = load_kickoff_module('kickoff_delivery')
    refreshed = module.refresh_delivery({}, evidence=record, project_root=project, now=datetime.now(timezone.utc))
    source.write_text('Commit is no longer the delivery route.\n')
    assessment = module.assess_delivery(refreshed['record'], project_root=project, now=datetime.now(timezone.utc))
    assert assessment['status'] == 'miss' and assessment['delivery_template'] is None
    assert not (project / '.cafe/issues').exists()


@pytest.mark.parametrize('input_source,expected', [('inferred', 'ja-JP'), ('explicit', 'en-US')])
def test_saved_locale_precedes_inference_through_public_render(tmp_path, capsys, input_source, expected):
    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'; project.mkdir()
    request = _project(project)
    values = _formatter_inputs('new')
    values.update(project_root=str(project), playbook_id='example', effective_locale='en-US',
        locale_source=input_source, phase_chain=['outline=codex:configured-model'],
        user_required=[], manager_confirmable=[], proactive_review_decision=['outline=not_required'],
        cleanup=[], cleanup_description=[], capability_choice=[],
        current_checkout=True, manager_mode='unattended')
    values.pop('worktree')
    request['formatter_inputs'] = values
    path, draft, proposal = (tmp_path / name for name in ('request.json', 'draft.json', 'proposal.md'))
    path.write_text(json.dumps(request))
    _save_preference(cli, capsys, tmp_path, project, 'conversation.locale', 'fr-FR', scope='user')
    _save_preference(cli, capsys, tmp_path, project, 'conversation.locale', 'ja-JP')
    code, report = _call(cli, capsys, ['assemble', '--request-file', str(path), '--draft-output', str(draft), *_settings(tmp_path)])
    assert code == 0, report
    assert json.loads(draft.read_text())['formatter_inputs']['effective_locale'] == expected
    assert _call(cli, capsys, ['render', '--request-file', str(draft), '--output', str(proposal), *_settings(tmp_path)])[0] == 0
    assert expected in proposal.read_text()


def test_saved_families_reach_draft_and_current_values_override_without_authority(tmp_path, capsys):
    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'; project.mkdir()
    request = _project(project)
    saved = {
        'worktree.convention': str(tmp_path / 'worktrees/{issue_name}'),
        'phase.chains': {'roles': {'writer': ['codex:role-model', 'claude:fallback-model']}},
        'confirmation.assignments': {'user_required': [], 'manager_confirmable': []},
        'review.decisions': {'outline': 'not_required'},
        'cleanup.convention': {'cleanup': [], 'cleanup_description': []},
    }
    for key, value in saved.items():
        _save_preference(cli, capsys, tmp_path, project, key, value)
    draft = tmp_path / 'draft.json'
    code, report = _call(cli, capsys, ['draft', '--project-root', str(project), '--issue-name', 'new',
        '--playbook-id', 'example', '--output', str(draft), *_settings(tmp_path)])
    assert code == 3
    fields = json.loads(draft.read_text())['formatter_inputs']
    assert fields['worktree'] == str(tmp_path / 'worktrees/new')
    assert fields['phase_chain'] == ['outline=codex:role-model,claude:fallback-model']
    assert fields['cleanup'] == []
    assert 'deliver' not in fields
    assert fields['user_required'] == fields['manager_confirmable'] == []
    assert fields['proactive_review_decision'] == ['outline=not_required']
    assert all(report['preferences'][key]['scope'] == 'repository' for key in saved)
    assert fields['delivery_contract']['permissions'] == []
    # Complete an actual proposal; suitability is still a current Manager judgment.
    values = _formatter_inputs('new')
    data = json.loads(draft.read_text())
    for key in ('delivery_contract', 'update_preflight', 'catalog_preflight'):
        data['formatter_inputs'][key] = values[key]
    data['formatter_inputs']['manager_mode'] = 'unattended'
    data['formatter_inputs'].pop('event_manager', None)
    draft.write_text(json.dumps(data))
    assert _call(cli, capsys, ['render', '--request-file', str(draft), '--output', str(tmp_path / 'proposal.md'), *_settings(tmp_path)])[0] == 0
    # Current empty and false choices do not fall back to preferences.
    request['formatter_inputs'] = {'current_checkout': True, 'cleanup': [],
                                  'phase_chain': ['outline=codex:current-model']}
    path = tmp_path / 'explicit.json'; path.write_text(json.dumps(request))
    code, report = _call(cli, capsys, ['assemble', '--request-file', str(path), *_settings(tmp_path)])
    fields = report['formatter_draft']
    assert 'worktree' not in fields and fields['current_checkout'] is True
    assert fields['cleanup'] == [] and 'deliver' not in fields
    assert fields['phase_chain'] == ['outline=codex:current-model']
    assert not (project / '.cafe/issues').exists()


def test_incompatible_saved_selectors_and_gates_remain_actionable(tmp_path, capsys):
    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'; project.mkdir()
    request = _project(project)
    _save_preference(cli, capsys, tmp_path, project, 'phase.chains', {'steps': {'absent': ['codex:model']}})
    _save_preference(cli, capsys, tmp_path, project, 'confirmation.assignments', {'mandatory_task': False})
    path = tmp_path / 'request.json'; path.write_text(json.dumps(request))
    code, report = _call(cli, capsys, ['assemble', '--request-file', str(path), *_settings(tmp_path)])
    assert code == 3
    for key in ('phase.chains', 'confirmation.assignments'):
        assert report['preferences'][key]['diagnostic']
        assert any(key in item['requirement'] for item in report['missing_decisions'])


def test_saved_actions_use_effective_default_checkout_and_clear_reveals_user(tmp_path, capsys):
    """Neither user nor repository preferences may restore obsolete delivery commands."""
    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'
    project.mkdir()
    _project(project)
    for scope in ('user', 'repository'):
        code, result = _call(cli, capsys, ['preferences', 'set', '--project-root', str(project),
            '--config-dir', str(tmp_path / 'config'), '--scope', scope, '--key', 'delivery.convention',
            '--value-json', json.dumps({'deliver': [['git', 'status']], 'deliver_description': ['Inspect.']}), '--reuse'])
        assert code == 2 and 'obsolete' in result['message']
        assert not (project / '.cafe/issues').exists()


def test_saved_language_does_not_rewrite_resumed_workflow(tmp_path, capsys):
    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'; project.mkdir()
    request = _project(project)
    state = project / '.cafe/issues/new/blackboard.json'
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({'workflow_id': 'existing', 'conversation_locale': 'de-DE',
                                'conversation_locale_source': 'explicit'}))
    before = state.read_bytes()
    _save_preference(cli, capsys, tmp_path, project, 'conversation.locale', 'fr-FR')
    request['formatter_inputs'] = {'effective_locale': 'en-US', 'locale_source': 'inferred'}
    path = tmp_path / 'request.json'; path.write_text(json.dumps(request))
    _, report = _call(cli, capsys, ['assemble', '--request-file', str(path), *_settings(tmp_path)])
    assert report['formatter_draft']['effective_locale'] == 'de-DE'
    assert state.read_bytes() == before


def test_saved_action_preserves_current_description(tmp_path, capsys):
    """A description cannot restore a removed Manager delivery command field."""
    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'
    project.mkdir()
    request = _project(project)
    request['current_explicit_inputs'] = {'deliver_description': ['Current exact target explanation.']}
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request))
    code, report = _call(cli, capsys, ['assemble', '--request-file', str(path), *_settings(tmp_path)])
    assert code == 3 and 'unknown_formatter_field:deliver_description' in report['diagnostics']
    assert not (project / '.cafe/issues').exists()


def test_incompatible_preferences_survive_draft_roundtrip_until_resolution(tmp_path, capsys):
    """U01/U02/U14/I01/I06: generated defaults cannot resolve a rejected preference."""
    key = 'phase.chains'
    invalid = {'steps': {'absent': ['codex:model']}}
    fields = ['phase_chain']
    resolution = {'phase_chain': ['outline=codex:chosen']}
    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'; project.mkdir()
    request = _project(project)
    _save_preference(cli, capsys, tmp_path, project, key, invalid)
    request['formatter_inputs'] = {'manager_mode': 'unattended', 'cleanup': []}
    for field in fields:
        request['formatter_inputs'].pop(field, None)
    initial, draft, proposal = (tmp_path / name for name in ('initial.json', 'draft.json', 'proposal.md'))
    initial.write_text(json.dumps(request))
    code, first = _call(cli, capsys, ['assemble', '--request-file', str(initial), '--draft-output', str(draft), *_settings(tmp_path)])
    assert code == 3 and first['preferences'][key]['diagnostic']
    data = json.loads(draft.read_text())
    complete = _formatter_inputs('new')
    for field in ('delivery_contract', 'update_preflight', 'catalog_preflight'):
        data['formatter_inputs'][field] = complete[field]
    draft.write_text(json.dumps(data))
    render = ['render', '--request-file', str(draft), '--output', str(proposal), *_settings(tmp_path)]
    code, blocked = _call(cli, capsys, render)
    assert code == 3 and not proposal.exists()
    assert any(key in row['requirement'] for row in blocked['missing_decisions'])
    assert json.loads(draft.read_text())['formatter_inputs']['delivery_contract'] == complete['delivery_contract']
    # Clearing the rejected preference is also a genuine resolution. No stale
    # error flag or generated fallback needs a manual request repair.
    assert _call(cli, capsys, ['preferences', 'clear', '--project-root', str(project),
        '--config-dir', str(tmp_path / 'config'), '--scope', 'repository', '--key', key])[0] == 0
    if key == 'delivery.convention':
        # Delivery has no policy default; clearing leaves that real decision open.
        assert _call(cli, capsys, render)[0] == 3
    else:
        code, result = _call(cli, capsys, render)
        assert code == 0, result
        proposal.unlink()
    _save_preference(cli, capsys, tmp_path, project, key, invalid)
    for field in fields:
        data['formatter_inputs'].pop(field, None)
    data['current_explicit_inputs'] = resolution
    draft.write_text(json.dumps(data))
    code, result = _call(cli, capsys, render)
    assert code == 0, result
    assert proposal.read_text() and not (project / '.cafe/issues').exists()


def test_null_action_slots_use_saved_conventions_through_public_render(tmp_path, capsys):
    """Cleanup placeholders still reuse conventions without granting delivery authority."""
    cli = load_kickoff_module('prepare_kickoff')
    project = tmp_path / 'project'
    project.mkdir()
    request = _project(project)
    _save_preference(cli, capsys, tmp_path, project, 'cleanup.convention', {'cleanup': [], 'cleanup_description': []})
    request['formatter_inputs'] = {'cleanup': None, 'manager_mode': 'unattended'}
    values = _formatter_inputs('new')
    for key in ('delivery_contract', 'update_preflight', 'catalog_preflight'):
        request['formatter_inputs'][key] = values[key]
    path, draft, proposal = (tmp_path / name for name in ('request.json', 'draft.json', 'proposal.md'))
    path.write_text(json.dumps(request))
    code, result = _call(cli, capsys, ['assemble', '--request-file', str(path), '--draft-output', str(draft), *_settings(tmp_path)])
    assert code == 0, result
    fields = json.loads(draft.read_text())['formatter_inputs']
    assert fields['cleanup'] == [] and 'deliver' not in fields
    assert _call(cli, capsys, ['render', '--request-file', str(draft), '--output', str(proposal), *_settings(tmp_path)])[0] == 0
    request['current_explicit_inputs'] = {'cleanup': []}
    request['formatter_inputs'].pop('cleanup')
    path.write_text(json.dumps(request))
    _, current = _call(cli, capsys, ['assemble', '--request-file', str(path), *_settings(tmp_path)])
    assert current['formatter_draft']['cleanup'] == [] and 'deliver' not in current['formatter_draft']
