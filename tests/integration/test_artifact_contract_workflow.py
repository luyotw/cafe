"""Production-boundary journeys for normalized correction and artifact contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore, EventEntry
from cafe.core.playbook import resolve_step_behavior
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader


DOMAIN_ROUTES = ("editorial", "research", "incident")


def _write_route_artifact(
    issue_dir: Path,
    *,
    producer: str,
    artifact_name: str,
    source: str,
    prefix: str,
) -> tuple[ArtifactEntry, Path]:
    iteration = issue_dir / producer / "iteration_001"
    iteration.mkdir(parents=True)
    output = iteration / "output.md"
    output.write_text(
        "## Todo List\n"
        f"- [ ] `{prefix}-001` — Source: `{source}` — Work: preserve identity — "
        "Closure: verified — Evidence: targeted route test\n",
        encoding="utf-8",
    )
    entry = ArtifactEntry(
        name=artifact_name,
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by=producer,
        path=str(output),
        content_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
    )
    (iteration / "artifact.json").write_text(
        json.dumps(entry.to_dict()), encoding="utf-8"
    )
    return entry, output


def _publish_route_through_runtime(
    issue_dir: Path,
    *,
    playbook: dict,
    producer: str,
    destination: str,
    artifact_name: str,
    source: str,
    prefix: str,
) -> tuple[ArtifactEntry, Path]:
    """Publish one producer output through the same runtime seams as a step."""
    producer_dir = issue_dir / producer
    iteration_dir = producer_dir / "iteration_001"
    iteration_dir.mkdir(parents=True)
    output = iteration_dir / "output.md"
    output.write_text(
        "## Todo List\n"
        f"- [ ] `{prefix}-001` — Source: `{source}` — Work: preserve identity — "
        "Closure: verified — Evidence: targeted route test\n",
        encoding="utf-8",
    )

    publisher = GenericWorkflowStepExecutor.__new__(GenericWorkflowStepExecutor)
    publisher.phase_dir = producer_dir
    publisher.iteration = 1
    publisher.phase_name = producer
    initial = BlackboardStore(issue_dir).load_or_create(producer, playbook_id=playbook["playbook"]["id"])
    entry = publisher._write_artifact_record(
        blackboard_state=initial,
        output_key=artifact_name,
        output_path=str(output),
        updated_by=producer,
    )
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=object(),
    )
    runtime.blackboard.current_step = producer
    runtime._store_artifacts(
        {artifact_name: str(output)},
        {artifact_name: entry.to_dict()},
    )
    runtime._emit_transition(
        current_step=producer,
        next_step=destination,
        status_code="await_agent",
        source="integration.production_path",
        runtime="test",
    )
    persisted = BlackboardStore(issue_dir).load_or_create(
        destination,
        playbook_id=playbook["playbook"]["id"],
    )
    assert persisted.artifacts[artifact_name].content_sha256 == entry.content_sha256
    assert any(
        event.event_type == "transition"
        and event.data.get("source_artifact", {}).get("content_sha256") == entry.content_sha256
        for event in persisted.events
    )
    return entry, output


@pytest.mark.parametrize("playbook_name", DOMAIN_ROUTES)
def test_every_declared_domain_route_survives_persisted_process_resume(
    tmp_path: Path, playbook_name: str
) -> None:
    """Exercise all eight real route declarations through the runtime resolver."""
    playbook = PlaybookLoader().load(playbook_name, strict=True)
    routes = []
    for producer, raw_step in playbook["steps"].items():
        behavior = resolve_step_behavior(playbook, producer)
        for destination, route in (behavior.feedback_routes or {}).items():
            routes.append((producer, destination, route))
    assert routes

    for index, (producer, destination, route) in enumerate(routes):
        issue_dir = tmp_path / playbook_name / str(index)
        entry, output = _publish_route_through_runtime(
            issue_dir,
            playbook=playbook,
            producer=producer,
            destination=destination,
            artifact_name=route.artifact,
            source=route.todo_source,
            prefix=route.todo_id_prefix,
        )
        restarted = BlackboardStore(issue_dir).load_or_create(
            destination,
            playbook_id=playbook_name,
        )
        resolved = GenericWorkflowStepExecutor._add_causal_todo_artifact(
            {route.artifact: restarted.artifacts[route.artifact]},
            restarted,
            playbook=playbook,
        )

        projection = resolved["causal_todo"]
        assert projection.path == output
        assert projection.version == 1
        assert projection.items[0].item_id == f"{route.todo_id_prefix}-001"


def test_custom_artifact_names_and_legacy_summary_are_boundary_distinct(tmp_path: Path) -> None:
    issue_dir = tmp_path / "custom-contract"
    playbook = {
        "steps": {
            "emit": {
                "output_artifact": "evidence_bundle",
                "behavior": {
                    "feedback_routes": {
                        "consume": {
                            "artifact": "evidence_bundle",
                            "source_kind": "custom_review",
                            "todo_source": "custom_review",
                            "todo_id_prefix": "CUST",
                        }
                    }
                },
            },
            "consume": {"input_artifacts": ["evidence_bundle"]},
        }
    }
    entry, output = _write_route_artifact(
        issue_dir,
        producer="emit",
        artifact_name="evidence_bundle",
        source="custom_review",
        prefix="CUST",
    )
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("consume", playbook_id="custom-contract")
    state.events.append(
        EventEntry(
            timestamp="2026-06-01T00:00:00+00:00",
            step="emit",
            event_type="transition",
            message="custom route",
            data={"from": "emit", "to": "consume", "source_artifact": entry.to_dict()},
        )
    )
    store.save(state)

    resolved = GenericWorkflowStepExecutor._add_causal_todo_artifact(
        {"evidence_bundle": entry}, store.load_or_create("consume"), playbook=playbook
    )
    assert resolved["causal_todo"].path == output
    assert resolved["causal_todo"].artifact == "evidence_bundle"

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook={"playbook": {"id": "custom-contract"}, "steps": {"consume": {}}},
        executor=object(),
    )
    runtime._store_artifacts({"summary_doc": str(output)}, {})
    assert runtime.blackboard.artifacts["summary_doc"].kind == ArtifactKind.DOCUMENT
