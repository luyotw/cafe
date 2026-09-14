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
        entry, output = _write_route_artifact(
            issue_dir,
            producer=producer,
            artifact_name=route.artifact,
            source=route.todo_source,
            prefix=route.todo_id_prefix,
        )
        store = BlackboardStore(issue_dir)
        state = store.load_or_create(destination, playbook_id=playbook_name)
        state.handoff_contract = None
        state.events.append(
            EventEntry(
                timestamp="2026-06-01T00:00:00+00:00",
                step=producer,
                event_type="transition",
                message="persisted route",
                data={
                    "from": producer,
                    "to": destination,
                    "source_artifact": entry.to_dict(),
                },
            )
        )
        store.save(state)
        restarted = store.load_or_create(destination, playbook_id=playbook_name)
        restarted.handoff_contract = None
        resolved = GenericWorkflowStepExecutor._add_causal_todo_artifact(
            {route.artifact: entry}, restarted, playbook=playbook
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
