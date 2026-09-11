"""Correction contracts remain strict and graph-derived."""

import pytest
from pydantic import ValidationError

from cafe.core.artifact_revisions import ArtifactRevisionStore, StaleArtifactRevision
from cafe.driver.proxy import assess_correction_takeover
from cafe.core.human_tasks import HumanTaskBinding, HumanTaskCorrection
from cafe.core.playbook import PlaybookDefinition


def _playbook() -> dict:
    return {
        "playbook": {"id": "custom"},
        "steps": {
            "draft": {
                "skill": "draft", "role": "writer", "output_artifact": "brief",
                "on": {"await_agent": "review"},
            },
            "review": {
                "skill": "review", "role": "reviewer", "input_artifacts": ["brief"],
                "output_artifact": "review", "on": {"await_agent": "approve"},
                "human_tasks": [{
                    "trigger": "confirm_output", "task_id": "output-review",
                    "correction": {"artifacts": ["brief"], "allow_driver_proxy": True},
                }],
            },
            "approve": {
                "skill": "approve", "role": "owner", "assignee_type": "human",
                "input_artifacts": ["review"], "output_artifact": "approval",
                "human_tasks": [{"trigger": "initial", "task_id": "approval", "outcomes": {"submit": "_done"}}],
                "on": {"await_agent": "_done"},
            },
        },
    }


def test_correction_declaration_is_explicit_and_strict() -> None:
    declaration = HumanTaskCorrection.model_validate(
        {"artifacts": ["brief"], "allow_driver_proxy": True}
    )
    assert declaration.artifacts == ("brief",)
    assert declaration.allow_driver_proxy is True

    with pytest.raises(ValidationError):
        HumanTaskBinding.model_validate(
            {"trigger": "confirm_output", "task_id": "review", "correction": {"artifacts": ["brief"], "actor": "user"}}
        )


def test_playbook_derives_dependency_closure_and_next_human_gate() -> None:
    playbook = PlaybookDefinition.model_validate(_playbook())
    assert playbook.correction_targets("review", "confirm_output") == ("brief",)
    assert playbook.downstream_steps("brief") == ("review", "approve")
    assert playbook.next_human_gate("review") == "approve"


def test_playbook_rejects_undeclared_correction_artifact() -> None:
    payload = _playbook()
    payload["steps"]["review"]["human_tasks"][0]["correction"]["artifacts"] = ["unknown"]
    with pytest.raises(ValidationError):
        PlaybookDefinition.model_validate(payload)


def test_artifact_revision_is_immutable_and_replays_one_operation(tmp_path) -> None:
    store = ArtifactRevisionStore(tmp_path)
    first = store.replace("brief", base_hash=None, content="first", operation_id="op-1")
    replay = store.replace("brief", base_hash=None, content="first", operation_id="op-1")
    assert replay == first
    assert store.read(first.path) == "first"

    second = store.replace("brief", base_hash=first.sha256, content="second", operation_id="op-2")
    assert store.read(first.path) == "first"
    assert store.read(second.path) == "second"
    with pytest.raises(StaleArtifactRevision):
        store.replace("brief", base_hash=first.sha256, content="third", operation_id="op-3")


@pytest.mark.parametrize("field", ["bounded", "clear", "reversible", "within_scope", "no_new_authority"])
def test_proxy_takeover_requires_every_suitability_predicate(field: str) -> None:
    evidence = {name: True for name in ("bounded", "clear", "reversible", "within_scope", "no_new_authority")}
    evidence[field] = False
    result = assess_correction_takeover(**evidence)
    assert result.suitable is False
    assert result.reason == field
