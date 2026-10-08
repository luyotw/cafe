"""U7–U10: cleanup-only fresh authority and exact legacy compatibility."""

from copy import deepcopy

import pytest

from cafe.manager.delivery import normalize_delivery_contract, phase_owned_graph
from cafe.playbooks.loader import PlaybookLoader
from tests.fixtures.delivery_contract import delivery_contract
from tests.unit._kickoff_test_support import load_kickoff_module


def test_new_phase_owned_contract_has_only_cleanup_authority():
    old = delivery_contract()
    old["closeout_plan"]["deliver"] = [{"argv": ["gh", "pr", "merge", "--merge"]}]
    original = deepcopy(old)
    assert normalize_delivery_contract(old) == original
    fresh = delivery_contract()
    fresh["schema_version"] = 5
    fresh["closeout_plan"] = {"cleanup": []}
    assert normalize_delivery_contract(fresh) == fresh
    fresh["closeout_plan"]["deliver"] = []
    with pytest.raises(ValueError):
        normalize_delivery_contract(fresh)
    assert old == original


def test_kickoff_schema_does_not_offer_manager_delivery():
    schema = load_kickoff_module("kickoff_inputs").request_schema()
    assert "deliver" not in schema["formatter_fields"]
    assert "deliver_description" not in schema["formatter_fields"]
    assert "deliver" not in schema["required_formatter_fields"]
    assert "deliver" not in schema["input_template"]


def test_phase_ownership_is_declared_and_survives_renaming(tmp_path):
    model = PlaybookLoader(project_root=tmp_path, global_root=tmp_path).load_model("direct").model
    assert phase_owned_graph(model)
    graph = model.model_dump(mode="json")
    graph["steps"]["ship"] = graph["steps"].pop("deliver")
    assert phase_owned_graph(graph)
    assert not phase_owned_graph(
        PlaybookLoader(project_root=tmp_path, global_root=tmp_path).load_model("streamlined").model
    )


def test_progress_requires_cleanup_only_for_fresh_contract_and_rejects_duplicate_delivery(tmp_path):
    renderer = load_kickoff_module("render_workflow_progress")
    graph = PlaybookLoader(project_root=tmp_path, global_root=tmp_path).load("direct")
    contract = {
        "delivery_contract": {"schema_version": 5},
        "proactive_review": {"phase_decisions": []},
    }
    text = renderer.render_progress(
        playbook=graph, contract=contract, manager_state={"cleanup": "pending"}
    )
    assert text.count("deliver") == 1
    with pytest.raises(ValueError):
        renderer.render_progress(
            playbook=graph,
            contract=contract,
            manager_state={"deliver": "pending", "cleanup": "pending"},
        )


def test_legacy_unbound_merge_pauses_without_changing_contract():
    from cafe.manager.delivery import validate_legacy_delivery_binding

    argv = ["gh", "pr", "merge", "--merge"]
    original = list(argv)
    with pytest.raises(ValueError):
        validate_legacy_delivery_binding(argv)
    assert argv == original
    validate_legacy_delivery_binding(
        [
            "gh",
            "pr",
            "merge",
            "23",
            "--repo",
            "owner/repo",
            "--merge",
            "--match-head-commit",
            "a" * 40,
        ]
    )


def test_phase_owned_cleanup_cannot_repeat_local_integration():
    from cafe.manager.delivery import CleanupCloseoutPlan

    with pytest.raises(ValueError):
        CleanupCloseoutPlan.model_validate({"cleanup": [{"argv": ["cafe", "close"]}]})
    CleanupCloseoutPlan.model_validate({"cleanup": [{"argv": ["cafe", "close", "--archive-only"]}]})


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "-C", "/tmp/repo", "merge", "feature"],
        ["/usr/bin/gh", "--repo", "owner/repo", "pr", "merge", "23"],
    ],
)
def test_cleanup_options_cannot_restore_phase_operations(argv):
    from cafe.manager.delivery import CleanupCloseoutPlan

    with pytest.raises(ValueError):
        CleanupCloseoutPlan.model_validate({"cleanup": [{"argv": argv}]})
