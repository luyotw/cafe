"""Correction contracts remain strict and graph-derived."""

import pytest
from pydantic import ValidationError

from cafe.core.artifact_revisions import ArtifactRevisionStore, StaleArtifactRevision
from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.human_task_corrections import CorrectionRequest, HumanTaskCorrectionService
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.packet_io import sha256_bytes
from cafe.driver.proxy import assess_correction_takeover, submit_authorized_correction
from cafe.core.human_tasks import HumanTaskBinding, HumanTaskCorrection
from cafe.core.playbook import PlaybookDefinition
from cafe.workflow_execution.worker_launch import WorkerLaunchStore


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


def test_bootstrap_binds_a_first_correction_to_existing_artifact_bytes(tmp_path) -> None:
    store = ArtifactRevisionStore(tmp_path)
    current = store.bootstrap("brief", content="published")
    assert store.replace(
        "brief", base_hash=current.sha256, content="corrected", operation_id="op-1"
    ).sha256 != current.sha256
    with pytest.raises(StaleArtifactRevision):
        store.bootstrap("brief", content="different")


def test_correction_journal_reuses_the_recorded_manifest(tmp_path) -> None:
    store = ArtifactRevisionStore(tmp_path)
    prepared = store.prepare("op-1", [{"kind": "artifact", "id": "review"}])
    assert prepared["state"] == "prepared"
    assert store.receipt("op-1", {"kind": "artifact", "id": "review"})["id"] == "review"
    assert store.commit("op-1")["state"] == "committed"
    assert store.prepare("op-1", [{"kind": "different", "id": "new"}])["state"] == "committed"


def test_correction_service_uses_the_pending_task_contract_not_caller_authority(tmp_path) -> None:
    source = tmp_path / "brief.md"
    source.write_text("", encoding="utf-8")
    blackboard_store = BlackboardStore(tmp_path)
    blackboard = blackboard_store.load_or_create("review")
    blackboard_store.put_artifact(
        blackboard,
        ArtifactEntry(
            name="brief", kind=ArtifactKind.DOCUMENT, version=1,
            updated_by="writer", path="brief.md",
        ),
    )
    records = HumanTaskRecordStore(tmp_path)
    task = records.materialize(
        workflow_id="workflow",
        step="review",
        iteration=1,
        trigger="confirm_output",
        policy_id="output-review",
        prompt="Review",
        expected_result={
            "input_schema": "decision",
            "correction": {"artifacts": ["brief"], "allow_driver_proxy": False},
        },
        continuations={"revise": "draft"},
        assignee_type="user",
    )
    service = HumanTaskCorrectionService(tmp_path)
    request = CorrectionRequest(
        workflow_id="workflow",
        task_id=task.id,
        artifact="brief",
        base_hash=sha256_bytes(b""),
        content="replacement",
        operation_id="correction-1",
        actor="user",
        manifest=({"kind": "artifact", "id": "review"},),
        completion_payload={"task": "output-review", "continuation": "draft"},
    )
    assert service.apply(request).revision.artifact == "brief"
    assert records.get_result(task.id).payload["continuation"] == "draft"

    other = records.materialize(
        workflow_id="workflow",
        step="review",
        iteration=2,
        trigger="confirm_output",
        policy_id="output-review",
        prompt="Review",
        expected_result={"input_schema": "decision"},
        continuations={"revise": "draft"},
        assignee_type="user",
    )
    with pytest.raises(ValueError, match="does not permit"):
        service.apply(
            CorrectionRequest(
                workflow_id="workflow", task_id=other.id, artifact="brief", base_hash=None,
                content="forged", operation_id="correction-2", actor="user",
                manifest=({"kind": "artifact", "id": "review"},),
            )
        )


@pytest.mark.parametrize("field", ["bounded", "clear", "reversible", "within_scope", "no_new_authority"])
def test_proxy_takeover_requires_every_suitability_predicate(field: str) -> None:
    evidence = {name: True for name in ("bounded", "clear", "reversible", "within_scope", "no_new_authority")}
    evidence[field] = False
    result = assess_correction_takeover(**evidence)
    assert result.suitable is False
    assert result.reason == field


def test_driver_proxy_requires_task_bound_authorization_and_assessment(tmp_path) -> None:
    source = tmp_path / "brief.md"
    source.write_text("base", encoding="utf-8")
    board_store = BlackboardStore(tmp_path)
    board = board_store.load_or_create("review")
    board_store.put_artifact(board, ArtifactEntry(
        name="brief", kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path="brief.md"
    ))
    records = HumanTaskRecordStore(tmp_path)
    task = records.materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review",
        expected_result={"input_schema": "decision", "correction": {
            "artifacts": ["brief"], "allow_driver_proxy": True,
            "driver_authorization": {"id": "authorized-by-user"},
        }}, continuations={"revise": "draft"}, assignee_type="user",
    )
    with pytest.raises(ValueError, match="authorization"):
        submit_authorized_correction(
            issue_dir=tmp_path, workflow_id="workflow", task_id=task.id, artifact="brief",
            base_hash=sha256_bytes(b"base"), content="replacement", operation_id="proxy-1",
            authorization_id="forged", suitability={name: True for name in (
                "bounded", "clear", "reversible", "within_scope", "no_new_authority"
            )}, manifest=({"kind": "artifact", "id": "review"},),
        )
    result = submit_authorized_correction(
        issue_dir=tmp_path, workflow_id="workflow", task_id=task.id, artifact="brief",
        base_hash=sha256_bytes(b"base"), content="replacement", operation_id="proxy-1",
        authorization_id="authorized-by-user", suitability={name: True for name in (
            "bounded", "clear", "reversible", "within_scope", "no_new_authority"
        )}, manifest=({"kind": "artifact", "id": "review"},),
    )
    assert result.revision.artifact == "brief"
    correction = records.get_result(task.id).payload["correction"]
    assert correction["actor"] == "driver_on_behalf_of_user"
    assert correction["authorization_id"] == "authorized-by-user"
    assert correction["validation"]["suitability"]["bounded"] is True


def test_correction_invalidates_queued_worker_before_receipting(tmp_path) -> None:
    source = tmp_path / "brief.md"
    source.write_text("base", encoding="utf-8")
    board_store = BlackboardStore(tmp_path)
    board = board_store.load_or_create("review")
    board_store.put_artifact(board, ArtifactEntry(
        name="brief", kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path="brief.md"
    ))
    task = HumanTaskRecordStore(tmp_path).materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review",
        expected_result={"input_schema": "decision", "correction": {"artifacts": ["brief"]}},
        continuations={"revise": "draft"}, assignee_type="user",
    )
    worker = WorkerLaunchStore(tmp_path).start()
    result = HumanTaskCorrectionService(tmp_path).apply(CorrectionRequest(
        workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
        content="replacement", operation_id="worker-fence", actor="user",
        manifest=({"kind": "worker", "id": worker["worker_id"]},),
    ))
    assert WorkerLaunchStore(tmp_path).get(worker["worker_id"])["status"] == "stale"
    journal = ArtifactRevisionStore(tmp_path).journal(result.operation_id)
    assert journal["receipts"] == [{"kind": "worker", "id": worker["worker_id"]}]
