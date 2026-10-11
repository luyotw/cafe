"""U4–U8/U12 and I3–I5: authoritative composition, provenance, authority and graph."""

from copy import deepcopy
from pathlib import Path

from cafe.authoring import apply, decode_request, prepare

FIXTURE = Path(__file__).parents[1] / "fixtures/authoring/pair.yaml"


def pair():
    return decode_request(FIXTURE.read_text())


def test_missing_required_tools_are_exact_advisory_proposals(tmp_path):
    request = pair()
    request["companions"][0]["declaration"]["workflow"]["required_tools"] = ["Read"]
    result = prepare(request, root=tmp_path)
    assert result.status == "rejected"
    diagnostic = next(d for d in result.diagnostics if d["code"] == "missing_required_tool")
    assert (diagnostic["skill"], diagnostic["step"], diagnostic["field"]) == (
        "cafe-observe",
        "observe",
        "allowed_tools",
    )
    assert result.proposals[0]["operation"]["value"] == "Read"
    assert request["declaration"]["steps"]["observe"]["allowed_tools"] == []
    request["declaration"]["steps"]["observe"]["allowed_tools"] = ["Read"]
    assert prepare(request, root=tmp_path).status == "ready"


def test_required_prompt_input_needs_binding_and_reachable_producer(tmp_path):
    request = pair()
    request["companions"][0]["declaration"]["workflow"]["prompt_inputs"] = [
        {"placeholder": "record_file", "artifacts": ["record"], "required": True}
    ]
    request["declaration"]["steps"]["observe"]["input_artifacts"] = ["record"]
    result = prepare(request, root=tmp_path)
    assert any(
        d["code"] == "missing_producer" and d["skill"] == "cafe-observe" for d in result.diagnostics
    )
    producer = deepcopy(request["companions"][0])
    producer["target"] = ".cafe/skills/cafe-collect/SKILL.md"
    producer["declaration"]["name"] = "cafe-collect"
    producer["declaration"]["workflow"].pop("prompt_inputs")
    request["companions"].append(producer)
    request["declaration"]["entry_point"] = "collect"
    request["declaration"]["steps"]["collect"] = {
        "skill": "cafe-collect",
        "role": "observer",
        "input_artifacts": [],
        "output_artifact": "record",
        "allowed_tools": [],
        "on": {"await_agent": "observe"},
    }
    assert prepare(request, root=tmp_path).status == "ready"


def test_all_iteration_candidates_and_overlays_are_checked(tmp_path):
    request = pair()
    secondary = deepcopy(request["companions"][0])
    secondary["target"] = ".cafe/skills/cafe-second/SKILL.md"
    secondary["declaration"]["name"] = "cafe-second"
    secondary["declaration"]["workflow"]["required_tools"] = ["Write"]
    request["companions"].append(secondary)
    request["declaration"]["steps"]["observe"]["skill"] = {
        "1": "cafe-observe",
        "default": "cafe-second",
    }
    assert prepare(request, root=tmp_path).status == "rejected"
    request["declaration"]["steps"]["observe"]["allowed_tools"] = ["Write"]
    assert prepare(request, root=tmp_path).status == "ready"
    request["declaration"]["skills"]["workflow"]["shared"] = ["cafe-second"]
    result = prepare(request, root=tmp_path)
    assert result.status == "ready", result.diagnostics
    assert result.artifact_summary[request["target"]]["contributors"]["observe:cafe-observe"] == [
        "cafe-observe",
        "cafe-second",
    ]


def test_mandatory_gate_summary_does_not_change_issue_state(tmp_path):
    request = pair()
    request["companions"][0]["declaration"]["workflow"]["human_tasks"] = [
        {
            "id": "review",
            "pattern": "confirm_output",
            "prompt": "Confirm the observation.",
            "input_schema": "decision",
            "decisions": [{"id": "agree", "label": "Agree", "requires_feedback": True}],
        }
    ]
    step = request["declaration"]["steps"]["observe"]
    step["on"]["confirm_output"] = "observe"
    step["human_tasks"] = [
        {
            "trigger": "confirm_output",
            "task_id": "review",
            "outcomes": {"agree": "_done"},
            "feedback_delivery": {
                "artifact": "comment",
                "source_kind": "review",
                "todo_source": "feedback",
                "todo_id_prefix": "COMMENT",
            },
        }
    ]
    state = tmp_path / ".cafe/issues/x/blackboard.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"unchanged":true}')
    result = apply(request, root=tmp_path)
    assert result.status == "applied", result.diagnostics
    gates = result.confirmation_gates[request["target"]]
    assert gates["after"]["mandatory"] == ["observe"]
    assert gates["stale_stop_contracts"]
    assert state.read_text() == '{"unchanged":true}'


def test_new_dead_ends_and_missing_intents_block_apply(tmp_path):
    for changes in ({"on": {}}, {"valid_intents": ["need_permission"]}):
        request = pair()
        request["declaration"]["steps"]["observe"].update(changes)
        result = prepare(request, root=tmp_path)
        assert result.status == "rejected"
        assert any(d["code"] == "graph_invalid" for d in result.diagnostics)


def test_existing_skill_selection_cannot_implicitly_import_tool_defaults(tmp_path):
    request = pair()
    request["companions"][0]["declaration"]["workflow"]["step_defaults"] = {
        "version": 1,
        "values": {"allowed_tools": ["Write"]},
    }
    request["declaration"]["steps"]["observe"].pop("allowed_tools")
    # Defaults are explicitly author-declared in this transaction.
    assert apply(request, root=tmp_path).status == "applied"
    second = pair()
    second.pop("companions")
    second["target"] = ".cafe/playbooks/another.yaml"
    second["declaration"]["playbook"]["id"] = "another"
    second["declaration"]["steps"]["observe"].pop("allowed_tools")
    result = prepare(second, root=tmp_path)
    assert any(d["code"] == "implicit_authority" for d in result.diagnostics)


def test_custom_todo_producer_and_overlay_contract_are_required(tmp_path):
    request = pair()
    producer = deepcopy(request["companions"][0])
    producer["target"] = ".cafe/skills/cafe-compose/SKILL.md"
    producer["declaration"]["name"] = "cafe-compose"
    request["companions"].append(producer)
    request["declaration"]["entry_point"] = "compose"
    request["declaration"]["steps"]["compose"] = {
        "skill": "cafe-compose",
        "role": "observer",
        "input_artifacts": [],
        "output_artifact": "agenda",
        "allowed_tools": [],
        "on": {"await_agent": "observe"},
    }
    request["declaration"]["steps"]["observe"]["input_artifacts"] = ["agenda"]
    workflow = request["companions"][0]["declaration"]["workflow"]
    workflow["prompt_inputs"] = [
        {"placeholder": "agenda_file", "artifacts": ["agenda"], "required": True}
    ]
    workflow["checklist_overlay"] = {
        "variants": [
            {
                "when": {},
                "sections": [{"todo_projection": {"artifact": "agenda", "source": "agenda"}}],
            }
        ]
    }
    assert any(
        d["code"] == "incomplete_todo_contract" for d in prepare(request, root=tmp_path).diagnostics
    )
    producer["references"] = {
        "references/output.md": (
            "## Todo List\n- [ ] `AGENDA-001` — Source: `agenda` — "
            "Work: Observe. — Closure: Recorded. — Evidence: Record.\n"
        )
    }
    assert prepare(request, root=tmp_path).status == "ready"
    workflow["checklist_overlay"]["variants"][0]["sections"][0]["todo_projection"] = {
        "artifact": "causal_todo",
        "causal": True,
    }
    assert any(
        d["code"] == "missing_causal_route" for d in prepare(request, root=tmp_path).diagnostics
    )
    request["declaration"]["steps"]["compose"]["behavior"] = {
        "feedback_routes": {
            "observe": {
                "artifact": "agenda",
                "source_kind": "analysis",
                "todo_source": "agenda",
                "todo_id_prefix": "AGENDA",
            }
        }
    }
    assert prepare(request, root=tmp_path).status == "ready"


def test_primary_variant_defaults_need_equivalent_effective_contracts(tmp_path):
    request = pair()
    second = deepcopy(request["companions"][0])
    second["target"] = ".cafe/skills/cafe-second/SKILL.md"
    second["declaration"]["name"] = "cafe-second"
    second["declaration"]["workflow"]["step_defaults"] = {
        "version": 1,
        "values": {"allowed_tools": ["Read"]},
    }
    request["companions"].append(second)
    request["declaration"]["steps"]["observe"]["skill"] = {
        "1": "cafe-observe",
        "default": "cafe-second",
    }
    request["declaration"]["steps"]["observe"].pop("allowed_tools")
    assert prepare(request, root=tmp_path).status == "rejected"
    request["declaration"]["steps"]["observe"]["allowed_tools"] = []
    assert prepare(request, root=tmp_path).status == "ready"
    request["declaration"]["steps"]["observe"]["skill"] = "cafe-observe"
    request["declaration"]["skills"]["workflow"]["shared"] = ["cafe-second"]
    result = prepare(request, root=tmp_path)
    assert result.status == "ready"
    assert (
        result.confirmation_gates[request["target"]]["authority_after"]["observe"]["allowed_tools"]
        == []
    )


def test_phase_patch_revalidates_alias_selected_playbook(tmp_path):
    core = tmp_path / "src/cafe/core/playbook.py"
    core.parent.mkdir(parents=True)
    core.write_text("# CAFE source fixture with an isolated builtin catalog\n")
    request = pair()
    phase = request["companions"][0]
    phase["target"] = ".cafe/skills/cafe-develop/SKILL.md"
    phase["declaration"]["name"] = "cafe-develop"
    request["declaration"]["steps"]["observe"]["skill"] = "develop"
    assert apply(request, root=tmp_path).status == "applied"
    patch = {
        "version": 1,
        "target": phase["target"],
        "mode": "patch",
        "operations": [
            {"op": "upsert", "path": ["metadata", "workflow", "required_tools"], "value": "Write"}
        ],
    }
    result = prepare(patch, root=tmp_path)
    assert result.status == "rejected"
    assert any(
        d["code"] == "missing_required_tool" and d["step"] == "observe" for d in result.diagnostics
    )


def test_required_workspace_requires_reachable_workspace_producer(tmp_path):
    request = pair()
    step = request["declaration"]["steps"]["observe"]
    step.update(input_artifacts=["checkout"], workspace_input_artifact="checkout")
    result = prepare(request, root=tmp_path)
    assert any(
        d["code"] == "missing_workspace_producer" and d["field"] == "workspace_input_artifact"
        for d in result.diagnostics
    )
    producer = deepcopy(request["companions"][0])
    producer["target"] = ".cafe/skills/cafe-checkout/SKILL.md"
    producer["declaration"]["name"] = "cafe-checkout"
    request["companions"].append(producer)
    request["declaration"]["entry_point"] = "checkout"
    request["declaration"]["steps"]["checkout"] = {
        "skill": "cafe-checkout",
        "role": "observer",
        "input_artifacts": [],
        "output_artifact": "checkout",
        "allowed_tools": [],
        "on": {"await_agent": "observe"},
    }
    assert prepare(request, root=tmp_path).status == "rejected"
    request["declaration"]["steps"]["checkout"]["workspace_artifact"] = "checkout"
    request["declaration"]["steps"]["checkout"]["output_artifact"] = "checkout_report"
    assert prepare(request, root=tmp_path).status == "ready"


def test_missing_optional_ordinary_artifact_is_reported(tmp_path):
    request = pair()
    request["declaration"]["steps"]["observe"]["input_artifacts"] = ["prior_record"]
    result = prepare(request, root=tmp_path)
    assert any(
        d["code"] == "unbound_artifact" and d["severity"] == "info" for d in result.diagnostics
    )
