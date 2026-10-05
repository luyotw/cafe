"""U3/I1/I2: compact authority persists through confirmed activation and CAS."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_compact_kickoff import compact_request
from _kickoff_test_support import load_kickoff_module
from cafe.manager.api import (
    ActivateConfirmedContract,
    ReplaceConfirmedContract,
    activate_confirmed_contract,
    replace_confirmed_contract,
    confirmed_contract_snapshot,
)


@pytest.fixture
def compact_proposal(compact_request, tmp_path):
    owner = load_kickoff_module("kickoff_inputs")
    report = owner.discover_kickoff(
        compact_request, config_dir=tmp_path / "config", cache_dir=tmp_path / "cache"
    )
    return owner.assemble_kickoff(compact_request, discovery=report)["proposal"]


def activate(tmp_path, proposal, confirmed_by="user"):
    return activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=tmp_path,
            issue_name="sample",
            workflow_id="workflow",
            confirmed_by=confirmed_by,
            confirmed_at=datetime.now(timezone.utc),
            proposal=proposal,
        )
    )


def test_compact_initial_confirmation_and_revision_checked_expansion(tmp_path, compact_proposal):
    with pytest.raises(ValueError):
        activate(tmp_path, compact_proposal, confirmed_by="manager")
    first = activate(tmp_path, compact_proposal)
    contract = confirmed_contract_snapshot(tmp_path)
    assert contract["contract_mode"] == "compact"
    assert set(contract["file_scope"]["paths"]) == {"app.py", "tests/test_app.py"}
    expanded = deepcopy(compact_proposal)
    expanded["file_scope"]["paths"].append("extra.py")
    command = ReplaceConfirmedContract(
        tmp_path,
        "sample",
        "workflow",
        "user",
        datetime.now(timezone.utc),
        expanded,
        first.contract_sha256,
        "user_reconfirmation",
    )
    second = replace_confirmed_contract(command)
    assert second.revision == 2
    assert (
        confirmed_contract_snapshot(tmp_path)["file_scope"]["baseline_commit"]
        == compact_proposal["file_scope"]["baseline_commit"]
    )
    with pytest.raises(ValueError):
        replace_confirmed_contract(command)
    moved = deepcopy(expanded)
    moved["file_scope"]["baseline_commit"] = "a" * 40
    with pytest.raises(ValueError):
        replace_confirmed_contract(
            ReplaceConfirmedContract(
                tmp_path,
                "sample",
                "workflow",
                "user",
                datetime.now(timezone.utc),
                moved,
                second.contract_sha256,
                "user_reconfirmation",
            )
        )


@pytest.mark.parametrize(
    "paths",
    [
        ["../outside"],
        ["/absolute"],
        ["src/"],
        ["src/*.py"],
        ["a", "a"],
        ["src/../a"],
        ["a\\b"],
        ["."],
    ],
)
def test_compact_approval_paths_are_literal_files(tmp_path, compact_proposal, paths):
    compact_proposal["file_scope"]["paths"] = paths
    with pytest.raises(ValueError):
        activate(tmp_path, compact_proposal)


def test_compact_authority_cannot_hide_changed_delivery_or_execution_in_scope_expansion(
    tmp_path, compact_proposal
):
    first = activate(tmp_path, compact_proposal)
    changed = deepcopy(compact_proposal)
    changed["file_scope"]["paths"].append("extra.py")
    changed["delivery_contract"]["target_branch"] = "other"
    with pytest.raises(ValueError):
        replace_confirmed_contract(
            ReplaceConfirmedContract(
                tmp_path,
                "sample",
                "workflow",
                "user",
                datetime.now(timezone.utc),
                changed,
                first.contract_sha256,
                "scope_expansion",
            )
        )
