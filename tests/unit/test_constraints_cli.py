"""Public read-only inspection and generated docs: U8, I3/I5."""
import json
from typer.testing import CliRunner
from cafe.ui.cli import app
from cafe.constraints import Context, resolve, material_digest


def test_catalog_show_and_contextual_json_share_runtime_facts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner=CliRunner()
    catalog=runner.invoke(app,['constraints','list','--json'])
    assert catalog.exit_code==0, catalog.output
    assert len(json.loads(catalog.stdout)['entries'])==9
    result=runner.invoke(app,['constraints','list','--cli','gemini','--json'])
    assert result.exit_code==0, result.output
    payload=json.loads(result.stdout)
    idle=next(e for e in payload['entries'] if e['id']=='agent.stdout-idle')
    assert idle['boundary']['limits'][0]['value']==600
    assert payload['digest']==material_digest(resolve(Context(cli='gemini',provider='google')))
    shown=runner.invoke(app,['constraints','show','agent.stdout-idle','--cli','gemini','--json'])
    assert json.loads(shown.stdout)['entries']==[idle]
    assert not (tmp_path/'.cafe').exists()
    assert runner.invoke(app,['constraints','show','missing','--json']).exit_code!=0
    assert runner.invoke(app,['constraints','list','--cli','invalid','--json']).exit_code!=0


def test_document_generation_check_and_package_discovery_outside_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner=CliRunner()
    page=tmp_path/'known.md'
    generated=runner.invoke(app,['constraints','docs','render','--output',str(page)])
    assert generated.exit_code==0,generated.output
    assert runner.invoke(app,['constraints','docs','check',str(page)]).exit_code==0
    page.write_text(page.read_text()+'drift\n')
    assert runner.invoke(app,['constraints','docs','check',str(page)]).exit_code!=0
