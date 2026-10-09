"""Final rendered contracts carry the exact optional project preference offer."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_global_catalog")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from _kickoff_test_support import load_kickoff_module
from test_kickoff_prefill import _project

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kickoff_preparation import _formatter_inputs


@pytest.fixture
def ready_request(tmp_path):
    root = tmp_path / "project"
    request = _project(root)
    playbook = root / ".cafe/playbooks/example.yaml"
    playbook.write_text(playbook.read_text().replace(
        "conversation_locale: ja-JP",
        "conversation_locale: ja-JP, applicability: {summary: Writing, use_when: [outline], avoid_when: [deployment]}"
    ).replace("await_agent: _done", "await_agent: compose") +
        "  compose: {role: writer, assignee_type: agent, skill: custom-step, on: {await_agent: _done}}\n")
    config = root / ".cafe/phases.yaml"
    config.write_text(config.read_text() +
        "compose: {name: Writer, role: writer, clis: [{cli: codex, model: configured-model}]}\n")
    values = _formatter_inputs("new")
    request["formatter_inputs"] = {k: values[k] for k in ("delivery_contract", "update_preflight", "catalog_preflight")}
    request["formatter_inputs"].update(cleanup=[], manager_mode="unattended",
                                       effective_locale="zh-TW", locale_source="explicit")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    return root, path


def test_final_confirmation_and_selected_remember_use_same_snapshot(ready_request, tmp_path, capsys):
    root, request = ready_request
    cli = load_kickoff_module("prepare_kickoff")
    config = tmp_path / "config"
    common = ["--config-dir", str(config), "--cache-dir", str(tmp_path / "cache")]
    output = tmp_path / "proposal.md"
    render = ["render", "--request-file", str(request), "--output", str(output), *common]
    assert cli.main(render) == 0
    receipt = json.loads(capsys.readouterr().out)
    text = output.read_text()
    assert "下次沿用" in text
    assert text.count("請確認上述完整契約") == 1
    assert "**確認**：啟動本次流程，不儲存偏好" in text
    assert "**確認並記住**" in text
    assert text.index("下次沿用") < text.index("請確認上述完整契約") < text.index("### Workflow progress")
    offer_path = Path(receipt["preference_offer_file"])
    offer = json.loads(offer_path.read_text())
    assert "permissions" not in {e["key"] for e in offer["entries"]}
    assert not list(config.rglob("*.json"))
    remember = ["preferences", "remember", "--offer-file", str(offer_path), "--project-root", str(root),
                "--config-dir", str(config), "--select", "phase.chains/outline"]
    assert cli.main(remember) == 0
    assert json.loads(capsys.readouterr().out)["stored"] is False
    assert cli.main([*remember, "--reuse"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == ["phase.chains/outline"]
    assert cli.main(["preferences", "inspect", "--scope", "repository", "--project-root", str(root),
                     "--config-dir", str(config)]) == 0
    records = json.loads(capsys.readouterr().out)["preferences"]
    assert set(records) == {"phase.chains"}
    assert records["phase.chains"]["value"] == {"steps": {"outline": ["codex:configured-model"]}}
    # Saving a displayed model does not invalidate the other displayed choices.
    all_entries = [*remember[:-1], "*", "--reuse"]
    assert cli.main(all_entries) == 0
    capsys.readouterr()
    assert cli.main(render) == 0
    newer = json.loads(capsys.readouterr().out)
    assert Path(newer["preference_offer_file"]) != offer_path
    assert json.loads(offer_path.read_text()) == offer
    assert "下次沿用" not in output.read_text()
    assert not (root / ".cafe/issues").exists()


def test_invalid_optional_template_keeps_valid_contract_renderable(ready_request, tmp_path, capsys):
    _, path = ready_request
    request = json.loads(path.read_text())
    request["preference_templates"] = {"cleanup.convention": {"cleanup": [["close", "new"]],
                                                            "cleanup_description": ["Close new"]}}
    path.write_text(json.dumps(request))
    cli = load_kickoff_module("prepare_kickoff")
    assert cli.main(["render", "--request-file", str(path), "--config-dir", str(tmp_path / "config"),
                     "--cache-dir", str(tmp_path / "cache")]) == 0
    rendered = json.loads(capsys.readouterr().out)["render"]
    assert rendered["status"] == "rendered"
    assert "cleanup.convention" in rendered["preference_offer"]["problems"]
    assert "暫不可儲存" not in rendered["output"]
    assert "cleanup.convention" not in rendered["output"]
    assert "preference_offer" not in rendered["proposal"]


def test_direct_formatter_has_same_confirmation_and_explicit_offer_output(tmp_path, monkeypatch, capsys):
    formatter = load_kickoff_module("format_kickoff_contract")
    inputs = load_kickoff_module("kickoff_inputs")
    values = _formatter_inputs("direct-preference-offer")
    snapshot = tmp_path / "offer.json"
    monkeypatch.setattr(sys, "argv", ["format_kickoff_contract.py", *inputs.formatter_argv(values),
        "--preference-config-dir", str(tmp_path / "config"), "--preference-offer-output", str(snapshot)])
    assert formatter.main() == 0
    text = capsys.readouterr().out
    assert "確認並記住" in text
    offer = json.loads(snapshot.read_text())
    assert "phase.chains/develop" in {e["id"] for e in offer["entries"]}
    assert not list((tmp_path / "config").rglob("*.json"))


def test_cached_template_missing_context_does_not_block_explicit_current_actions(ready_request, tmp_path, capsys):
    root, request = ready_request
    cli = load_kickoff_module("prepare_kickoff")
    source = root / "README.md"
    source.write_text("Deliver using an explicit issue ID.")
    record = {"target": "local", "stable_conventions": ["Deliver by issue ID"],
              "sources": [{"path": "README.md", "fingerprint": hashlib.sha256(source.read_bytes()).hexdigest()}],
              "delivery_template": {"deliver": [["ship", "{issue_id}"]], "deliver_description": ["Deliver {issue_id}"]}}
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(record))
    assert cli.main(["evidence", "refresh", "--category", "delivery", "--project-root", str(root),
                     "--cache-dir", str(tmp_path / "cache"), "--evidence-file", str(evidence)]) == 3
    rejected = capsys.readouterr()
    assert "obsolete_manager_delivery_template" in rejected.out
    assert cli.main(["render", "--request-file", str(request), "--config-dir", str(tmp_path / "config"),
                     "--cache-dir", str(tmp_path / "cache")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["render"]["proposal"]["delivery_contract"]["closeout_plan"]["deliver"] == []
