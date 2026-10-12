"""issue646 I2–I4/I7/I9: public repair never replays execution or decisions."""
import json
import pytest
from typer.testing import CliRunner
from cafe.ui.cli import app
from tests.unit.test_workflow_repair import legacy, events


def invoke(legacy, monkeypatch, execute=True):
    monkeypatch.chdir(legacy[0].parents[2])
    args = ['repair', '--issue', 'repair-case', '--kind', 'iteration',
            '--step', 'compose_custom', '--iteration', '1']
    return CliRunner().invoke(app, args + (['--execute'] if execute else []))


@pytest.mark.parametrize('boundary', ['prepared_before', 'prepared_after', 'replace_after',
                                      'completed_before', 'completed_after'])
def test_i2_public_crash_reentry_preserves_original_provenance(legacy, monkeypatch, boundary):
    from cafe.core import workflow_repair
    from cafe.core.audit_events import AuditEventStore
    original = legacy[1].read_bytes()
    raw = legacy[2].read_bytes(), legacy[2].stat().st_ino
    assert invoke(legacy, monkeypatch, False).exit_code == 0
    assert legacy[1].read_bytes() == original and not events(legacy[0])
    commit, replace = AuditEventStore.commit, workflow_repair.atomic_write_bytes
    failed = False
    def audit(self, workflow_id, record):
        nonlocal failed
        selected = boundary.startswith('prepared') and record['event_type'].endswith('_prepared') or boundary.startswith('completed') and record['event_type'].endswith('_completed')
        if selected and not failed:
            failed = True
            if boundary.endswith('after'): commit(self, workflow_id, record)
            raise OSError('audit boundary injection')
        return commit(self, workflow_id, record)
    def write(path, payload):
        nonlocal failed
        replace(path, payload)
        if path == legacy[1] and boundary == 'replace_after' and not failed:
            failed = True
            raise OSError('replacement boundary injection')
    monkeypatch.setattr(AuditEventStore, 'commit', audit)
    monkeypatch.setattr(workflow_repair, 'atomic_write_bytes', write)
    result = invoke(legacy, monkeypatch)
    assert result.exit_code == 4 and json.loads(result.stdout)['status'] == 'interrupted'
    if boundary.startswith('prepared'): assert legacy[1].read_bytes() == original
    assert (legacy[2].read_bytes(), legacy[2].stat().st_ino) == raw
    assert invoke(legacy, monkeypatch).exit_code == 0
    final = invoke(legacy, monkeypatch)
    assert final.exit_code == 0 and json.loads(final.stdout)['status'] == 'no-op'
    proof = events(legacy[0])
    assert len(proof) == 2 and proof[0]['data']['operation_id'] == proof[1]['data']['operation_id']
    from cafe.core.blackboard import BlackboardStore
    BlackboardStore(legacy[0]).load_read_only()


def test_i2_public_refusal_and_readonly_preview_preserve_every_source(legacy, monkeypatch):
    original = legacy[1].read_bytes()
    legacy[2].unlink()
    result = invoke(legacy, monkeypatch)
    assert result.exit_code == 2 and json.loads(result.stdout)['status'] == 'refused'
    assert legacy[1].read_bytes() == original and not events(legacy[0])
