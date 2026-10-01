"""U05/U12/U13/I05/I07: public evidence transactions and recoverable capture."""
import hashlib
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


def _model(name, fingerprint='v1', url='https://provider.test/models'):
    now = datetime.now(timezone.utc).isoformat()
    return {'provider': 'fixture', 'model': name, 'version': '2026-10-01', 'assessed_at': now,
            'sources': [{'url': url, 'retrieved_at': now, 'fingerprint': fingerprint}],
            'workloads': ['implementation'], 'reasoning': 'standard',
            'capability_bands': {'coding': 'fixture'}, 'limitations': ['Fixture evidence only.']}


def _refresh(cli, root, name, **kwargs):
    evidence = root / (name + '.json')
    evidence.write_text(json.dumps(_model(name, **kwargs)))
    return ['evidence', 'refresh', '--category', 'models', '--project-root', str(root),
            '--cache-dir', str(root / 'cache'), '--evidence-file', str(evidence)]


def test_refresh_shared_source_invalidates_old_dependents_in_normal_discovery(tmp_path, capsys):
    cli = load_kickoff_module('prepare_kickoff')
    for name in ('one', 'two'):
        assert cli.main(_refresh(cli, tmp_path, name)) == 0
    assert cli.main(_refresh(cli, tmp_path, 'unrelated', url='https://provider.test/other')) == 0
    assert cli.main(_refresh(cli, tmp_path, 'one', fingerprint='v2')) == 0
    capsys.readouterr()
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'schema_version': 1, 'project_root': str(tmp_path), 'issue_name': 'sample'}))
    assert cli.main(['discover', '--request-file', str(request), '--cache-dir', str(tmp_path / 'cache'),
                     '--config-dir', str(tmp_path / 'config')]) == 0
    result = json.loads(capsys.readouterr().out)
    models = {m['identity']['model']: m for m in result['models']}
    assert models['one']['status'] == models['unrelated']['status'] == 'hit'
    assert models['two']['status'] == 'miss' and models['two']['diagnostics']
    assert models['two']['assessment'] is None


@pytest.mark.parametrize('operation', ['refresh', 'clear'])
def test_concurrent_public_maintenance_preserves_each_selected_scope(tmp_path, monkeypatch, operation):
    cli = load_kickoff_module('prepare_kickoff')
    assert cli.main(_refresh(cli, tmp_path, 'old')) == 0
    first = _refresh(cli, tmp_path, 'one')
    second = (_refresh(cli, tmp_path, 'two') if operation == 'refresh' else
              ['evidence', 'clear', '--category', 'models', '--project-root', str(tmp_path),
               '--cache-dir', str(tmp_path / 'cache'), '--key', 'fixture:old:2026-10-01'])
    # Force the previous unlocked read/replace race; a transaction has no such read.
    original_read = cli.VersionedJsonStore.read
    barrier = Barrier(2)
    def synchronized_read(store):
        records = original_read(store)
        barrier.wait(timeout=5)
        return records
    monkeypatch.setattr(cli.VersionedJsonStore, 'read', synchronized_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(cli.main, args) for args in (first, second)]
        assert [f.result(timeout=10) for f in futures] == [0, 0]
    records = json.loads((tmp_path / 'cache/models-v1.json').read_text())['evidence']
    expected = {'fixture:one:2026-10-01'}
    if operation == 'refresh':
        expected |= {'fixture:two:2026-10-01', 'fixture:old:2026-10-01'}
    assert set(records) == expected


def test_capture_failure_preserves_draft_and_previously_referenced_report(tmp_path, monkeypatch, capsys):
    cli = load_kickoff_module('prepare_kickoff')
    request, report = tmp_path / 'draft.json', tmp_path / 'report.json'
    report.write_text('{"old": true}\n')
    request.write_text(json.dumps({'schema_version': 1, 'formatter_inputs': {'delivery_contract': {'outcome': 'keep me'}},
                                  'preflight_files': {'update': str(report)}}))
    before = request.read_bytes()
    original = report.read_bytes()
    raw = '{"new": true}\n'
    argv = ['capture-report', '--request-file', str(request), '--kind', 'update', '--report-output', str(report),
            '--checked-at', datetime.now(timezone.utc).isoformat()]
    # Inject a failure at the request publication boundary, whether old in-place or atomic.
    write = Path.write_text
    import os
    replace = os.replace
    def interrupted_write(path, text, *a, **kw):
        if path == request:
            write(path, text[:12], *a, **kw)
            raise OSError('injected short request write')
        return write(path, text, *a, **kw)
    def interrupted_replace(src, dst):
        if Path(dst) == request:
            raise OSError('injected request publication failure')
        return replace(src, dst)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'write_text', interrupted_write)
        patch.setattr(os, 'replace', interrupted_replace)
        patch.setattr(cli.sys, 'stdin', io.StringIO(raw))
        assert cli.main(argv) == 2
    capsys.readouterr()
    assert request.read_bytes() == before
    assert report.read_bytes() == original
    monkeypatch.setattr(cli.sys, 'stdin', io.StringIO(raw))
    assert cli.main(argv) == 0
    captured = json.loads(capsys.readouterr().out)
    result = json.loads(request.read_text())
    assert result['formatter_inputs'] == json.loads(before)['formatter_inputs']
    assert Path(result['preflight_files']['update']).read_text() == raw
    assert captured['report_file'] == result['preflight_files']['update']
