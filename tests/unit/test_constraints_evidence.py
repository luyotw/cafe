"""Resume/freshness compatibility: U5/U9, I4."""
import copy
import json

from cafe.constraints import Context, load_registry
from cafe.constraints.evidence import snapshot, compare_snapshot
from cafe.constraints.registry import parse_registry
from cafe.constraints import resolver


def changed_registry(value=None, title=None):
    data=load_registry().model_dump(mode='json')
    e=next(e for e in data['entries'] if e['id']=='agent.stdout-idle')
    if value: e['variants'][0]['boundary']['limits'][0]['value']=value
    if title: e['title']=title
    return parse_registry(json.dumps(data))


def test_legacy_unknown_material_change_and_metadata_preservation(monkeypatch):
    before=snapshot(Context())
    assert compare_snapshot(None,before)=='unknown'
    assert compare_snapshot(before,before)=='same_semantics'
    registry=changed_registry(title='New editorial title')
    monkeypatch.setattr(resolver,'load_registry',lambda:registry)
    assert compare_snapshot(before,snapshot(Context()))=='same_semantics'
    registry=changed_registry(value=333)
    assert compare_snapshot(before,snapshot(Context()))=='material_change'


def test_manager_public_freshness_observes_constraints_and_legacy_absence(tmp_path, monkeypatch):
    from datetime import datetime,timezone
    from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract
    from cafe.manager._store import load_contract
    from cafe.manager._schema import freshness_semantic_facts,validate_contract
    from cafe.manager._freshness import compare_freshness,Freshness
    from cafe.manager.constraints import refresh_constraints
    from tests.unit.test_manager_contract_application import _manager_proposal
    activate_confirmed_contract(ActivateConfirmedContract(issue_dir=tmp_path,issue_name='custom',workflow_id='w',
        confirmed_by='user',confirmed_at=datetime(2026,10,5,tzinfo=timezone.utc),proposal=_manager_proposal()))
    contract,_=load_contract(tmp_path)
    facts={'semantic_facts':freshness_semantic_facts(contract),'runtime_constraints':refresh_constraints(contract)}
    assert compare_freshness(contract,facts)==Freshness.SAME_SEMANTICS
    legacy=copy.deepcopy(contract)
    legacy['provenance'].pop('runtime_constraints')
    validate_contract(legacy)
    assert compare_freshness(legacy,facts)==Freshness.UNKNOWN
    registry=changed_registry(value=333)
    monkeypatch.setattr(resolver,'load_registry',lambda:registry)
    facts['runtime_constraints']=refresh_constraints(contract)
    assert compare_freshness(contract,facts)==Freshness.MATERIAL_CHANGE
    assert contract['reactive_user_handoffs']['need_permission']=='user_required'


def test_public_manager_entry_refreshes_instead_of_trusting_cached_facts(tmp_path, monkeypatch):
    from datetime import datetime,timezone
    from cafe.manager import ActivateConfirmedContract,activate_confirmed_contract,ManagerEntryRequest,evaluate_manager_entry
    from cafe.manager._store import load_contract
    from cafe.manager._schema import freshness_semantic_facts
    from tests.unit.test_manager_contract_application import _manager_proposal
    activate_confirmed_contract(ActivateConfirmedContract(issue_dir=tmp_path,issue_name='custom',workflow_id='w',
        confirmed_by='user',confirmed_at=datetime(2026,10,5,tzinfo=timezone.utc),proposal=_manager_proposal()))
    contract,_=load_contract(tmp_path)
    request=ManagerEntryRequest(tmp_path,'custom','w',{'semantic_facts':freshness_semantic_facts(contract)})
    assert evaluate_manager_entry(request).freshness.value=='same_semantics'
    registry=changed_registry(value=333)
    monkeypatch.setattr(resolver,'load_registry',lambda:registry)
    assert evaluate_manager_entry(request).freshness.value=='material_change'


def test_preflight_runtime_identity_tracks_selected_material_values(monkeypatch):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).parents[2]/'src/cafe/data/skills/use-cafe-workflow/scripts/preflight_cache.py'
    spec=importlib.util.spec_from_file_location('constraint_preflight',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before=module._runtime_fingerprint([('codex','model')])['digest']
    registry=changed_registry(title='Metadata')
    monkeypatch.setattr(resolver,'load_registry',lambda:registry)
    assert module._runtime_fingerprint([('codex','model')])['digest']==before
    registry=changed_registry(value=333)
    assert module._runtime_fingerprint([('codex','model')])['digest']!=before
