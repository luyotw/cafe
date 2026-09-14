"""Production-boundary journeys for normalized correction and artifact contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore, EventEntry
from cafe.core.git import GitOperations
from cafe.core.playbook import resolve_step_behavior
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.types import AgentCLI, TokenUsage
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.verification import run_verification


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


def test_custom_named_step_publication_handoff_restart_and_consumer_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the executor/runtime seams for a current custom-name workflow."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".cafe/\n", encoding="utf-8")
    (repo / ".cafe").mkdir()
    (repo / ".cafe" / "phases.yaml").write_text(
        "emit:\n  name: custom-agent\n  clis:\n    - cli: codex\n      model: test-model\n"
        "consume:\n  name: custom-agent\n  clis:\n    - cli: codex\n      model: test-model\n",
        encoding="utf-8",
    )
    agent_dir = repo / ".cafe" / "agents" / "producer"
    agent_dir.mkdir(parents=True)
    (agent_dir / "custom-agent.md").write_text(
        "---\nname: custom-agent\ndescription: test\n---\n\nExecute the custom step.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    (repo / "tracked.txt").write_text("current\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "current"], cwd=repo, check=True, capture_output=True)

    builtin_root = tmp_path / "builtin"
    for skill_name in ("custom-producer", "custom-consumer"):
        skill_dir = builtin_root / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: test\n---\n\n"
            "## Role\nRun the declared custom workflow step.\n\n"
            "## Handoff\nWrite the result for the next declared step.\n",
            encoding="utf-8",
        )
    loader = SkillLoader(
        project_root=repo,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    loader.discover()
    phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(
            loader, project_root=repo, home_dir=tmp_path / "home"
        ),
    )

    class ProductionAgent:
        def __init__(self) -> None:
            self.agent = SimpleNamespace(
                config=SimpleNamespace(cli=AgentCLI.CODEX, session_id="custom-session", model=None)
            )
            self.calls = 0

        def get_agent(self, _name: str) -> SimpleNamespace:
            return self.agent

        def execute(self, _name: str, _prompt: str, *, streaming_output_file=None, **_kwargs):
            self.calls += 1
            assert streaming_output_file is not None
            iteration_dir = Path(streaming_output_file).parent
            output = iteration_dir / "output.md"
            if self.calls == 1:
                output.write_text(
                    "## Todo List\n"
                    "- [ ] `CUST-001` — Source: `custom_review` — Work: preserve custom route — "
                    "Closure: consumed after restart — Evidence: production journey\n",
                    encoding="utf-8",
                )
            else:
                output.write_text("# Consumer result\n", encoding="utf-8")
            run_verification(
                output_file=output,
                command=[sys.executable, "-c", "print('custom journey')"],
                scope="targeted",
                cwd=repo,
            )
            checklist = iteration_dir / "checklist.md"
            checklist.write_text(
                "\n".join(
                    line.replace("[ ]", "[x]", 1) if line.startswith("[ ]") else line
                    for line in checklist.read_text(encoding="utf-8").splitlines()
                )
                + "\n",
                encoding="utf-8",
            )
            return "confirmed", TokenUsage(), [], [], [], None

    playbook = {
        "playbook": {"id": "custom-contract"},
        "roles": {"producer": {"default_agent": "custom-agent"}},
        "steps": {
            "emit": {
                "skill": "custom-producer",
                "role": "producer",
                "output_artifact": "evidence_bundle",
                "workspace_artifact": "verified_state",
                "valid_intents": ["confirmed"],
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
                "on": {"confirmed": "consume"},
            },
            "consume": {
                "skill": "custom-consumer",
                "role": "producer",
                "input_artifacts": ["evidence_bundle", "verified_state"],
                "workspace_input_artifact": "verified_state",
                "output_artifact": "consumer_result",
                "valid_intents": ["confirmed"],
                "on": {"confirmed": "_done"},
            },
        },
    }
    issue_dir = repo / ".cafe" / "issues" / "custom-contract"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("emit", playbook_id="custom-contract")
    manager = ProductionAgent()
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="custom-contract",
        playbook=playbook,
        generic_phase=phase,
        agent_manager=manager,
        git_ops=GitOperations(repo),
        role_agent_map={"producer": "custom-agent"},
    )
    produced = executor.execute_step("emit", playbook["steps"]["emit"], state)
    assert set(produced.artifacts) == {"evidence_bundle", "verified_state"}

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    )
    runtime.blackboard = state
    runtime._store_artifacts(produced.artifacts, produced.artifact_metadata)
    runtime._emit_transition(
        current_step="emit",
        next_step="consume",
        status_code="confirmed",
        source="integration.custom_production_path",
        runtime="test",
    )

    restarted = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    )
    consumer_state = restarted.blackboard
    consumed = executor.execute_step("consume", playbook["steps"]["consume"], consumer_state)

    assert consumed.agent_invoked is True
    assert manager.calls == 2
    assert consumer_state.artifacts["verified_state"].kind == ArtifactKind.WORKSPACE
    assert consumer_state.events[-1].event_type in {"handoff_contract", "transition", "decision"}


def test_bounded_v02_mixed_code_record_rebuilds_and_restarts_for_legacy_consumer(
    tmp_path: Path,
) -> None:
    """Legacy mixed records remain readable without current-workspace certification."""
    issue_dir = tmp_path / ".cafe" / "issues" / "legacy-contract"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    output = iteration_dir / "output.md"
    output.write_text("legacy development summary\n", encoding="utf-8")
    (iteration_dir / "artifact.json").write_text(
        json.dumps(
            {
                "name": "code",
                "kind": "workspace",
                "version": 1,
                "updated_by": "develop",
                "path": "develop/iteration_001/output.md",
                "summary": "legacy development summary",
                "base_sha": "abc1234",
                "head_sha": "def5678",
            }
        ),
        encoding="utf-8",
    )
    store = BlackboardStore(issue_dir)
    rebuilt = store.rebuild_from_iterations(initial_step="review")
    assert rebuilt.artifacts["code"].kind == ArtifactKind.WORKSPACE
    assert rebuilt.artifacts["code"].path.endswith("develop/iteration_001/output.md")

    playbook = {
        "playbook": {"id": "legacy-contract"},
        "steps": {
            "review": {
                "input_artifacts": ["code"],
                "output_artifact": "review_feedback",
            }
        },
    }
    first_restart = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=object()
    )
    second_restart = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=object()
    )
    executor = GenericWorkflowStepExecutor.__new__(GenericWorkflowStepExecutor)
    inputs = executor._step_input_artifacts(
        playbook["steps"]["review"], second_restart.blackboard
    )

    assert first_restart.blackboard.artifacts["code"].summary == "legacy development summary"
    assert inputs["code"].kind == ArtifactKind.WORKSPACE
    assert "workspace_input_artifact" not in playbook["steps"]["review"]
