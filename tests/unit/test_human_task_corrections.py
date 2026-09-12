"""Correction contracts remain strict and graph-derived."""

import pytest
from pydantic import ValidationError

from cafe.core.artifact_revisions import ArtifactRevisionStore, StaleArtifactRevision
from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.event_dispatches import EventDispatchFenceStore
from cafe.core.human_task_corrections import CorrectionRequest, HumanTaskCorrectionService
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.packet_io import sha256_bytes
from cafe.driver.proxy import assess_correction_takeover, record_user_driver_authorization, submit_authorized_correction
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
    assert playbook.downstream_artifacts("brief") == ("review", "approval")
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
        manifest=({"kind": "artifact", "id": "brief"},),
        completion_payload={"task": "output-review", "continuation": "draft"},
    )
    applied = service.apply(request)
    assert applied.revision.artifact == "brief"
    assert service.apply(request) == applied
    assert records.get_result(task.id).payload["continuation"] == "draft"
    published = BlackboardStore(tmp_path).load_or_create("review").artifacts["brief"]
    assert published.path.startswith("artifact_revisions/brief/")
    assert published.version == 2

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
                manifest=({"kind": "artifact", "id": "brief"},),
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
            )}, manifest=({"kind": "artifact", "id": "brief"},),
        )
    result = submit_authorized_correction(
        issue_dir=tmp_path, workflow_id="workflow", task_id=task.id, artifact="brief",
        base_hash=sha256_bytes(b"base"), content="replacement", operation_id="proxy-1",
        authorization_id="authorized-by-user", suitability={name: True for name in (
            "bounded", "clear", "reversible", "within_scope", "no_new_authority"
        )}, manifest=({"kind": "artifact", "id": "brief"},),
    )
    assert result.revision.artifact == "brief"
    correction = records.get_result(task.id).payload["correction"]
    assert correction["actor"] == "driver_on_behalf_of_user"
    assert correction["authorization_id"] == "authorized-by-user"
    assert correction["validation"]["suitability"]["bounded"] is True


def test_proxy_correction_resumes_on_the_persisted_graph_agent_edge(tmp_path) -> None:
    project = tmp_path / "project"
    issue_dir = project / ".cafe" / "issues" / "463"
    playbooks = project / ".cafe" / "playbooks"
    playbooks.mkdir(parents=True)
    (playbooks / "custom.yaml").write_text(
        """playbook: {id: custom}
steps:
  draft: {skill: review, role: writer, output_artifact: brief, on: {await_agent: review}}
  review: {skill: review, role: reviewer, output_artifact: review, on: {await_agent: approve}}
  approve:
    skill: cafe-review
    role: owner
    assignee_type: human
    human_tasks: [{trigger: initial, task_id: clarification-feedback, outcomes: {submit: _done}}]
    on: {await_agent: _done}
""",
        encoding="utf-8",
    )
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: custom\n", encoding="utf-8")
    source = issue_dir / "brief.md"
    source.write_text("base", encoding="utf-8")
    board_store = BlackboardStore(issue_dir)
    board = board_store.load_or_create("review", playbook_id="custom")
    board_store.put_artifact(board, ArtifactEntry(
        name="brief", kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path="brief.md"
    ))
    records = HumanTaskRecordStore(issue_dir)
    task = records.materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review", expected_result={"input_schema": "decision", "correction": {
            "artifacts": ["brief"], "allow_driver_proxy": True,
            "driver_authorization": {"id": "authorized-by-user"},
        }}, continuations={"revise": "review"}, assignee_type="user",
    )
    worker = WorkerLaunchStore(issue_dir).start()
    driver_dir = issue_dir / "driver"
    driver_dir.mkdir()
    (driver_dir / "dispatch_state.json").write_text(
        '{"events":{"event-1":{"status":"routing"}}}', encoding="utf-8"
    )

    result = submit_authorized_correction(
        issue_dir=issue_dir, workflow_id="workflow", task_id=task.id, artifact="brief",
        base_hash=sha256_bytes(b"base"), content="replacement", operation_id="proxy-resume",
        authorization_id="authorized-by-user", suitability={name: True for name in (
            "bounded", "clear", "reversible", "within_scope", "no_new_authority"
        )}, manifest=({"kind": "artifact", "id": "brief"},),
    )

    resumed = BlackboardStore(issue_dir).load_or_create("review")
    assert resumed.current_step == "approve"
    assert resumed.handoff_contract.to_step == "approve"
    assert resumed.handoff_contract.to_owner.value == "agent"
    assert WorkerLaunchStore(issue_dir).get(worker["worker_id"])["status"] == "stale"
    assert EventDispatchFenceStore(issue_dir).is_fenced("event-1")
    assert {frozenset(entry.items()) for entry in ArtifactRevisionStore(issue_dir).journal(result.operation_id)["receipts"]} >= {
        frozenset({("kind", "worker"), ("id", worker["worker_id"])}),
        frozenset({("kind", "dispatch"), ("id", "event-1")}),
        frozenset({("kind", "continuation"), ("id", "workflow")}),
    }
    assert any(event.event_type == "continuation_invalidated_for_correction" for event in resumed.events)


def test_driver_manifest_cannot_expand_the_task_scoped_mutation_set(tmp_path) -> None:
    source = tmp_path / "brief.md"
    unrelated = tmp_path / "unrelated.md"
    source.write_text("base", encoding="utf-8")
    unrelated.write_text("keep", encoding="utf-8")
    board_store = BlackboardStore(tmp_path)
    board = board_store.load_or_create("review")
    for name, path in (("brief", "brief.md"), ("unrelated", "unrelated.md")):
        board_store.put_artifact(board, ArtifactEntry(
            name=name, kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path=path
        ))
    task = HumanTaskRecordStore(tmp_path).materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review", expected_result={"input_schema": "decision", "correction": {
            "artifacts": ["brief"], "allow_driver_proxy": True,
            "driver_authorization": {"id": "authorized-by-user"},
        }}, continuations={"revise": "draft"}, assignee_type="user",
    )

    submit_authorized_correction(
        issue_dir=tmp_path, workflow_id="workflow", task_id=task.id, artifact="brief",
        base_hash=sha256_bytes(b"base"), content="replacement", operation_id="proxy-scope",
        authorization_id="authorized-by-user", suitability={name: True for name in (
            "bounded", "clear", "reversible", "within_scope", "no_new_authority"
        )}, manifest=(
            {"kind": "artifact", "id": "brief"},
            {"kind": "artifact", "id": "unrelated"},
        ),
    )

    assert BlackboardStore(tmp_path).load_or_create("review").artifacts["unrelated"].path == "unrelated.md"


def test_user_authorization_is_durable_and_task_scoped(tmp_path) -> None:
    records = HumanTaskRecordStore(tmp_path)
    task = records.materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review",
        expected_result={"input_schema": "decision", "correction": {
            "artifacts": ["brief"], "allow_driver_proxy": True,
        }}, continuations={"revise": "draft"}, assignee_type="user",
    )
    assert record_user_driver_authorization(
        issue_dir=tmp_path, workflow_id="workflow", task_id=task.id, authorization_id="user-consent"
    ) == "user-consent"
    assert records.get_task(task.id).expected_result["correction"]["driver_authorization"] == {"id": "user-consent"}
    with pytest.raises(ValueError):
        record_user_driver_authorization(
            issue_dir=tmp_path, workflow_id="workflow", task_id=task.id, authorization_id="other"
        )


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


def test_active_worker_rejects_before_correction_bootstrap(tmp_path) -> None:
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
    workers = WorkerLaunchStore(tmp_path)
    worker = workers.start()
    workers.mark(worker["worker_id"], "running")

    with pytest.raises(ValueError, match="crossed"):
        HumanTaskCorrectionService(tmp_path).apply(CorrectionRequest(
            workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
            content="replacement", operation_id="active-worker", actor="user",
            manifest=({"kind": "worker", "id": worker["worker_id"]},),
        ))

    assert not (tmp_path / "artifact_revisions").exists()
    assert HumanTaskRecordStore(tmp_path).get_task(task.id).status.value == "pending"


def test_correction_fences_a_preexisting_event_dispatch_before_receipting(tmp_path) -> None:
    source = tmp_path / "brief.md"
    source.write_text("base", encoding="utf-8")
    board_store = BlackboardStore(tmp_path)
    board = board_store.load_or_create("review")
    board_store.put_artifact(board, ArtifactEntry(
        name="brief", kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path="brief.md"
    ))
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir()
    (driver_dir / "dispatch_state.json").write_text(
        '{"events":{"event-1":{"status":"routing"}}}', encoding="utf-8"
    )
    task = HumanTaskRecordStore(tmp_path).materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review",
        expected_result={"input_schema": "decision", "correction": {"artifacts": ["brief"]}},
        continuations={"revise": "draft"}, assignee_type="user",
    )

    result = HumanTaskCorrectionService(tmp_path).apply(CorrectionRequest(
        workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
        content="replacement", operation_id="dispatch-fence", actor="user",
        manifest=({"kind": "dispatch", "id": "event-1"},),
    ))

    assert EventDispatchFenceStore(tmp_path).is_fenced("event-1")
    assert ArtifactRevisionStore(tmp_path).journal(result.operation_id)["receipts"] == [
        {"kind": "dispatch", "id": "event-1"}
    ]


def test_correction_rejects_an_unimplemented_executable_invalidator_before_mutation(tmp_path) -> None:
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

    with pytest.raises(ValueError):
        HumanTaskCorrectionService(tmp_path).apply(CorrectionRequest(
            workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
            content="replacement", operation_id="dispatch-fence", actor="user",
            manifest=({"kind": "dispatch", "id": "event-1"},),
        ))

    assert HumanTaskRecordStore(tmp_path).get_task(task.id).status.value == "pending"
    assert BlackboardStore(tmp_path).load_or_create("review").artifacts["brief"].path == "brief.md"


def test_correction_rejects_an_invalid_operation_id_before_bootstrap(tmp_path) -> None:
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

    with pytest.raises(ValueError):
        HumanTaskCorrectionService(tmp_path).apply(CorrectionRequest(
            workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
            content="replacement", operation_id="../invalid", actor="user",
            manifest=({"kind": "artifact", "id": "brief"},),
        ))

    assert not (tmp_path / "artifact_revisions" / "index.json").exists()
    assert HumanTaskRecordStore(tmp_path).get_task(task.id).status.value == "pending"


def test_correction_removes_a_stale_downstream_artifact_pointer_before_receipting(tmp_path) -> None:
    source = tmp_path / "brief.md"
    downstream = tmp_path / "summary.md"
    source.write_text("base", encoding="utf-8")
    downstream.write_text("stale", encoding="utf-8")
    board_store = BlackboardStore(tmp_path)
    board = board_store.load_or_create("review")
    board_store.put_artifact(board, ArtifactEntry(
        name="brief", kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path="brief.md"
    ))
    board_store.put_artifact(board, ArtifactEntry(
        name="summary", kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path="summary.md"
    ))
    task = HumanTaskRecordStore(tmp_path).materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review",
        expected_result={"input_schema": "decision", "correction": {"artifacts": ["brief"]}},
        continuations={"revise": "draft"}, assignee_type="user",
    )

    result = HumanTaskCorrectionService(tmp_path).apply(CorrectionRequest(
        workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
        content="replacement", operation_id="artifact-fence", actor="user",
        manifest=(
            {"kind": "artifact", "id": "brief"},
            {"kind": "artifact", "id": "summary"},
        ),
    ))

    current = BlackboardStore(tmp_path).load_or_create("review")
    assert current.artifacts["brief"].path == result.revision.path
    assert "summary" not in current.artifacts
    assert ArtifactRevisionStore(tmp_path).journal(result.operation_id)["receipts"] == [
        {"kind": "artifact", "id": "brief"},
        {"kind": "artifact", "id": "summary"},
    ]


def test_recovery_replays_a_durable_prepared_correction_once(tmp_path) -> None:
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
        expected_result={"input_schema": "decision", "correction": {"artifacts": ["brief"]}},
        continuations={"revise": "draft"}, assignee_type="user",
    )
    store = ArtifactRevisionStore(tmp_path)
    store.prepare("recover-1", [{"kind": "artifact", "id": "brief"}], context={
        "workflow_id": "workflow", "task_id": task.id, "artifact": "brief",
        "base_hash": sha256_bytes(b"base"), "content": "replacement", "actor": "user",
        "proxy_authorization_id": None, "suitability": {}, "completion_payload": {},
    })
    result = HumanTaskCorrectionService(tmp_path).recover("recover-1")
    assert result.operation_id == "recover-1"
    assert records.get_result(task.id) is not None
    assert HumanTaskCorrectionService(tmp_path).recover("recover-1").revision == result.revision


def test_recovery_finishes_after_revision_replacement_without_rechecking_old_base(tmp_path, monkeypatch) -> None:
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
        expected_result={"input_schema": "decision", "correction": {"artifacts": ["brief"]}},
        continuations={"revise": "draft"}, assignee_type="user",
    )
    service = HumanTaskCorrectionService(tmp_path)
    original_complete = service.tasks.complete

    def interrupted_complete(**_kwargs):
        raise OSError("simulated interruption after publication")

    monkeypatch.setattr(service.tasks, "complete", interrupted_complete)
    with pytest.raises(OSError):
        service.apply(CorrectionRequest(
            workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
            content="replacement", operation_id="recover-after-replace", actor="user",
            manifest=({"kind": "artifact", "id": "brief"},),
        ))
    monkeypatch.setattr(service.tasks, "complete", original_complete)

    recovered = HumanTaskCorrectionService(tmp_path).recover("recover-after-replace")
    assert recovered.revision.sha256 == sha256_bytes(b"replacement")
    assert records.get_task(task.id).status.value == "completed"


def test_recovery_publishes_graph_handoff_after_task_completion_interruption(tmp_path, monkeypatch) -> None:
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
        expected_result={"input_schema": "decision", "correction": {"artifacts": ["brief"]}},
        continuations={"revise": "draft"}, assignee_type="user",
    )
    service = HumanTaskCorrectionService(tmp_path)
    monkeypatch.setattr(service, "_schedule_graph_continuation", lambda _task: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError, match="stop"):
        service.apply(CorrectionRequest(
            workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
            content="replacement", operation_id="recover-handoff", actor="user",
            manifest=({"kind": "artifact", "id": "brief"},),
        ))

    observed_states: list[str] = []
    original = HumanTaskCorrectionService._schedule_graph_continuation

    def observe_schedule(self, recovered_task):
        observed_states.append(ArtifactRevisionStore(tmp_path).journal("recover-handoff")["state"])
        return original(self, recovered_task)

    monkeypatch.setattr(HumanTaskCorrectionService, "_schedule_graph_continuation", observe_schedule)
    HumanTaskCorrectionService(tmp_path).recover("recover-handoff")

    assert observed_states == ["applying"]
    assert ArtifactRevisionStore(tmp_path).journal("recover-handoff")["state"] == "committed"


def test_recovery_replays_an_artifact_invalidation_interrupted_before_its_receipt(tmp_path, monkeypatch) -> None:
    source = tmp_path / "brief.md"
    stale = tmp_path / "summary.md"
    source.write_text("base", encoding="utf-8")
    stale.write_text("stale", encoding="utf-8")
    board_store = BlackboardStore(tmp_path)
    board = board_store.load_or_create("review")
    for name, path in (("brief", "brief.md"), ("summary", "summary.md")):
        board_store.put_artifact(board, ArtifactEntry(
            name=name, kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path=path
        ))
    records = HumanTaskRecordStore(tmp_path)
    task = records.materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review",
        expected_result={"input_schema": "decision", "correction": {"artifacts": ["brief"]}},
        continuations={"revise": "draft"}, assignee_type="user",
    )
    service = HumanTaskCorrectionService(tmp_path)
    original_receipt = service.revisions.receipt

    def interrupted_receipt(*_args, **_kwargs):
        raise OSError("simulated interruption after invalidation")

    monkeypatch.setattr(service.revisions, "receipt", interrupted_receipt)
    with pytest.raises(OSError):
        service.apply(CorrectionRequest(
            workflow_id="workflow", task_id=task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
            content="replacement", operation_id="recover-after-invalidate", actor="user",
            manifest=({"kind": "artifact", "id": "summary"},),
        ))
    monkeypatch.setattr(service.revisions, "receipt", original_receipt)

    HumanTaskCorrectionService(tmp_path).recover("recover-after-invalidate")
    assert "summary" not in BlackboardStore(tmp_path).load_or_create("review").artifacts
    assert records.get_task(task.id).status.value == "completed"


def test_correction_supersedes_downstream_pending_task_before_receipting(tmp_path) -> None:
    source = tmp_path / "brief.md"
    source.write_text("base", encoding="utf-8")
    board_store = BlackboardStore(tmp_path)
    board = board_store.load_or_create("review")
    board_store.put_artifact(board, ArtifactEntry(
        name="brief", kind=ArtifactKind.DOCUMENT, version=1, updated_by="writer", path="brief.md"
    ))
    records = HumanTaskRecordStore(tmp_path)
    correction_task = records.materialize(
        workflow_id="workflow", step="review", iteration=1, trigger="confirm_output",
        policy_id="output-review", prompt="Review",
        expected_result={"input_schema": "decision", "correction": {"artifacts": ["brief"]}},
        continuations={"revise": "draft"}, assignee_type="user",
    )
    stale_task = records.materialize(
        workflow_id="workflow", step="approve", iteration=1, trigger="confirm_output",
        policy_id="approval", prompt="Approve", expected_result={"input_schema": "decision"},
        continuations={"submit": "done"}, assignee_type="user",
    )
    HumanTaskCorrectionService(tmp_path).apply(CorrectionRequest(
        workflow_id="workflow", task_id=correction_task.id, artifact="brief", base_hash=sha256_bytes(b"base"),
        content="replacement", operation_id="task-fence", actor="user",
        manifest=({"kind": "human_task", "id": stale_task.id},),
    ))
    assert records.get_task(stale_task.id).status.value == "cancelled"
    assert records.get_wait_state(stale_task.id).released_at is not None
