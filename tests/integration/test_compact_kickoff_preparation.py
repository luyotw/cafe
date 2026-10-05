"""I9/U9: public staged preparation and complete observation invariants."""

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys

import pytest

from tests.unit.test_compact_contract import compact_request, compact_proposal, activate
from tests.unit._kickoff_test_support import load_kickoff_module, SCRIPT_ROOT


def test_confirmed_compact_inputs_override_changed_defaults_and_selected_mode(compact_request, compact_proposal, tmp_path):
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    first = activate(issue, compact_proposal)
    contract_bytes = (issue / "manager/contract.json").read_bytes()
    changed = deepcopy(compact_request)
    changed.pop("compact_inputs")
    changed.pop("playbook_id")
    (root / ".cafe/phases.yaml").write_text("invalid new defaults")
    owner = load_kickoff_module("kickoff_inputs")
    discovery = owner.discover_kickoff(changed, config_dir=tmp_path / "prefs", cache_dir=tmp_path / "cache")
    assembled = owner.assemble_kickoff(changed, discovery=discovery)
    assert assembled["status"] == "ready", assembled
    assert assembled["proposal"] == compact_proposal
    assert (issue / "manager/contract.json").read_bytes() == contract_bytes
    assert not (issue / "execution_context.json").exists()


def test_missing_configured_chain_is_a_focused_gap(compact_request, tmp_path):
    owner = load_kickoff_module("kickoff_inputs")
    compact_request["compact_inputs"].pop("phases")
    discovered = owner.discover_kickoff(compact_request, config_dir=tmp_path / "prefs", cache_dir=tmp_path / "cache")
    assembled = owner.assemble_kickoff(compact_request, discovery=discovered)
    assert assembled["status"] == "incomplete"
    assert any(gap["requirement"].startswith("phase_chain:") for gap in assembled["missing_decisions"])
    assert assembled["proposal"] is None


@pytest.mark.parametrize("path_count", [2, 1024])
def test_rendered_proposal_activates_only_with_explicit_user_provenance(compact_request, tmp_path, path_count):
    from cafe.core.blackboard import BlackboardStore
    from cafe.manager.api import confirmed_contract_snapshot
    from datetime import datetime, timezone
    if path_count == 1024:
        compact_request["compact_inputs"]["files"] = [f"new/file-{i}.py" for i in range(path_count)]
    root = Path(compact_request["project_root"])
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(compact_request))
    output = tmp_path / "proposal.md"
    rendered = subprocess.run([sys.executable, str(SCRIPT_ROOT / "prepare_kickoff.py"), "render",
        "--request-file", str(request_file), "--config-dir", str(tmp_path / "prefs"),
        "--cache-dir", str(tmp_path / "cache"), "--output", str(output)], capture_output=True, text=True, timeout=30)
    assert rendered.returncode == 0, rendered.stderr
    proposal_file = output.with_suffix(".proposal.json")
    assert proposal_file.is_file()
    issue = root / ".cafe/issues/sample"
    board = BlackboardStore(issue).load_or_create("build", playbook_id="selected")
    assert confirmed_contract_snapshot(issue) is None
    command = [sys.executable, str(SCRIPT_ROOT / "activate_compact.py"), "--issue-dir", str(issue),
        "--proposal-file", str(proposal_file), "--confirmed-at", datetime.now(timezone.utc).isoformat()]
    rejected = subprocess.run([*command, "--confirmed-by", "manager"], capture_output=True, text=True, timeout=30)
    assert rejected.returncode != 0 and confirmed_contract_snapshot(issue) is None
    activated = subprocess.run([*command, "--confirmed-by", "user"], capture_output=True, text=True, timeout=30)
    assert activated.returncode == 0 and json.loads(activated.stdout)["status"] == "activated"
    assert confirmed_contract_snapshot(issue)["identity"]["workflow_id"] == board.workflow_id
    from cafe.manager.file_scope import execution_scope_projection
    from cafe.core.execution_artifacts import bounded_execution_json
    from cafe.core.execution_checkpoints import load_execution_context
    context_file = issue / "execution_context.json"
    context_file.write_bytes(bounded_execution_json(execution_scope_projection(issue, root)))
    assert load_execution_context(context_file)["paths"] == compact_request["compact_inputs"]["files"]


def test_excessive_literal_path_list_is_rejected_before_ready_proposal(compact_request, tmp_path):
    owner = load_kickoff_module("kickoff_inputs")
    compact_request["compact_inputs"]["files"] = [f"{number:04}" + "x" * 251 for number in range(1024)]
    discovery = owner.discover_kickoff(compact_request, config_dir=tmp_path / "prefs", cache_dir=tmp_path / "cache")
    assembled = owner.assemble_kickoff(compact_request, discovery=discovery)
    assert assembled["status"] != "ready"
    assert assembled.get("proposal") is None


def test_path_count_plus_one_is_rejected_by_public_producer(compact_request, tmp_path):
    owner = load_kickoff_module("kickoff_inputs")
    compact_request["compact_inputs"]["files"] = [f"new/file-{i}.py" for i in range(1025)]
    discovery = owner.discover_kickoff(compact_request, config_dir=tmp_path / "prefs", cache_dir=tmp_path / "cache")
    assembled = owner.assemble_kickoff(compact_request, discovery=discovery)
    assert assembled["status"] != "ready" and assembled.get("proposal") is None
