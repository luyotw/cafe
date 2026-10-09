"""Production-boundary journeys for normalized correction and artifact contracts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.git import GitOperations
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.playbook import resolve_step_behavior
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.types import AgentCLI, TokenUsage
from cafe.core.workspace_artifact import build_workspace_artifact
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.verification import run_verification


DOMAIN_ROUTES = ("editorial", "research", "incident")


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_runtime_returns_report_format_rejection_to_same_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repair_succeeds: bool
) -> None:
    """Exercise real step execution, correction, publication and recovery routing."""
    repo = tmp_path / "report-retry"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)
    config = repo / ".cafe"
    config.mkdir()
    (config / "strategic_context.yaml").write_text("version: 1\n", encoding="utf-8")
    (config / "phases.yaml").write_text(
        "inspect:\n  name: Inspector\n  clis:\n    - cli: codex\n      model: test-model\n",
        encoding="utf-8",
    )
    skill = config / "skills" / "report"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: report\ndescription: Inspect and report\n"
        "---\n\nWrite your report to {output_file} and submit the ordinary baton.\n",
        encoding="utf-8",
    )
    agent_file = config / "agents" / "reviewer" / "Inspector.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text(
        "---\nname: Inspector\ndescription: Report inspection results\n---\n\nInspect.\n",
        encoding="utf-8",
    )
    issue_dir = config / "issues" / "format-rejection"
    iteration = issue_dir / "inspect" / "iteration_001"
    playbook = {
        "playbook": {"id": "format-rejection"},
        "roles": {"reviewer": {"default_agent": "Inspector"}},
        "steps": {
            "inspect": {
                "skill": "report",
                "role": "reviewer",
                "output_artifact": "report",
                "behavior": {"completion": "baton"},
                "allowed_tools": ["Read", "Write"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    class ReportAgent:
        def __init__(self):
            self.agent = SimpleNamespace(
                config=SimpleNamespace(native_review_configuration=None,
                    cli=AgentCLI.CODEX, session_id="report-session", model="test-model"
                )
            )
            self.calls = []
            self.metadata = []

        def get_agent(self, _name):
            return self.agent

        def get_last_cli(self):
            return AgentCLI.CODEX

        def get_last_session_id(self):
            return "report-session"

        def execute(self, name, prompt, *, continuation=None, **kwargs):
            self.calls.append((name, prompt, continuation, kwargs.get("allowed_tools")))
            assert not (iteration / "artifact.json").exists()
            state = BlackboardStore(issue_dir).load_or_create("inspect")
            assert state.artifacts == {}
            assert not any(e.event_type == "transition" for e in state.events)
            assert HumanTaskRecordStore(issue_dir).tasks() == ()
            assert (iteration / "checklist.md").read_bytes() == b""
            self.metadata.append(json.loads((iteration / "iteration.json").read_text()))
            report = "# Inspection\n\n## Todo List\n\nNo actionable work.\n"
            if len(self.calls) == 1 or not repair_succeeds:
                report += "\n# Earlier inspection\n\n## Todo List\n\nNo actionable work.\n"
            (iteration / "output.md").write_text(report, encoding="utf-8")
            (issue_dir / "next_step.txt").write_text(
                json.dumps({"version": 1, "intent": "await_agent"}), encoding="utf-8"
            )
            return "", TokenUsage(), [], [], [], None

    manager = ReportAgent()
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="format-rejection",
        playbook=playbook,
        generic_phase=GenericPhase(
            SkillLoader(project_root=repo, global_root=tmp_path / "global")
        ),
        agent_manager=manager,
        git_ops=GitOperations(repo),
        role_agent_map={"reviewer": "Inspector"},
    )
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor.execute_step
    )

    result = runtime.run(start_step="inspect")

    assert len(manager.calls) == (2 if repair_succeeds else 3)
    for name, prompt, continuation, allowed_tools in manager.calls[1:]:
        assert name == "Inspector"
        assert "exactly one '## Todo List' section" in prompt
        assert continuation.session_id == "report-session"
        assert allowed_tools == manager.calls[0][3]
    for metadata in manager.metadata:
        assert metadata["effective_checklist"] == manager.metadata[0]["effective_checklist"]
        assert metadata["effective_checklist"]["gates"] == []
        assert metadata["model"] == "test-model"
    assert (iteration / "checklist.md").read_bytes() == b""
    assert not (issue_dir / "inspect" / "iteration_002").exists()
    tasks = HumanTaskRecordStore(issue_dir).tasks()
    persisted = BlackboardStore(issue_dir).load_or_create("inspect")
    if repair_succeeds:
        assert result.completed is True
        assert tasks == ()
        assert persisted.artifacts["report"].version == 1
        assert (iteration / "artifact.json").exists()
        assert any(e.event_type == "step_completed" for e in persisted.events)
    else:
        assert result.completed is False
        assert persisted.artifacts == {}
        assert not (iteration / "artifact.json").exists()
        assert len(tasks) == 1
        assert HumanTaskRecordStore(issue_dir).get_assignment(tasks[0].id).assignee_type == "user"
        assert tasks[0].policy_id == "agent-execution-interrupted"
        assert tasks[0].continuations == {
            "retry": "inspect", "retry_fresh_session": "inspect"
        }


def _run_runtime_worker(repo: Path, worker: str, args: list[str]) -> dict:
    """Run one workflow leg in a fresh interpreter and return its result."""
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(project_root), environment.get("PYTHONPATH", "")) if path
    )
    code = (
        "import sys\n"
        "from tests.integration.artifact_contract_runtime_workers import "
        f"{worker}\n"
        f"{worker}(*sys.argv[1:])\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout.strip().splitlines()[-1])


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
    store.record_event(
        state, "transition",
        {"step": "emit", "from": "emit", "to": "consume", "source_artifact": entry.to_dict()},
    )

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
        prompt_inputs = (
            "  prompt_inputs:\n"
            "    - artifacts: [evidence_bundle]\n"
            "      placeholder: custom_document\n"
            "      required: true\n"
            "    - artifacts: [evidence_bundle]\n"
            "      placeholder: custom_correction\n"
            "      required: true\n"
            "    - artifacts: [verified_state]\n"
            "      placeholder: custom_workspace\n"
            "      required: true\n"
            if skill_name == "custom-consumer"
            else "  prompt_inputs:\n"
            "    - artifacts: [evidence_bundle]\n"
            "      placeholder: custom_evidence\n"
            "      required: false\n"
        )
        (skill_dir / "SKILL.md").write_text(
            (
                f"---\nname: {skill_name}\ndescription: test\nversion: 1.0.0\n"
                "workflow:\n"
                "  execution_profile:\n"
                "    workload: implementation\n"
                "    reasoning: standard\n"
                "    risk_domains: [workflow]\n"
                "    fallback_strength: equivalent_or_stronger\n"
                + prompt_inputs
                + "---\n\n"
                "## Role\nRun the declared custom workflow step.\n\n"
                "## Handoff\nWrite next-step baton for this result; the runtime updates the blackboard.\n"
            ),
            encoding="utf-8",
        )
    playbook_definition = {
        "playbook": {"id": "custom-contract"},
        "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
        "commands": {"prepare": {"prompt_for_spec_plan_config": False}},
        "roles": {"producer": {"default_agent": "custom-agent"}},
        "steps": {
            "emit": {
                "skill": "custom-producer",
                "role": "producer",
                "output_artifact": "evidence_bundle",
                "workspace_artifact": "verified_state",
                "valid_intents": ["await_agent"],
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
                "on": {"await_agent": "consume"},
            },
            "consume": {
                "skill": "custom-consumer",
                "role": "producer",
                "input_artifacts": ["evidence_bundle", "verified_state"],
                "workspace_input_artifact": "verified_state",
                "output_artifact": "consumer_result",
                "valid_intents": ["await_agent"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    playbook_definition["playbook"]["applicability"] = {
        "summary": "custom artifact contract journey",
        "use_when": ["testing custom workflow contracts"],
        "avoid_when": ["testing built-in workflow names"],
    }
    (repo / ".cafe" / "playbooks" / "custom-contract.yaml").parent.mkdir(
        parents=True, exist_ok=True
    )
    (repo / ".cafe" / "playbooks" / "custom-contract.yaml").write_text(
        yaml.safe_dump(playbook_definition, sort_keys=False), encoding="utf-8"
    )
    playbook = PlaybookLoader(
        project_root=repo,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    ).load("custom-contract", strict=True)
    issue_dir = repo / ".cafe" / "issues" / "custom-contract"
    first = _run_runtime_worker(
        repo,
        "run_custom_runtime_process",
        [str(repo), str(issue_dir), str(builtin_root), str(tmp_path / "global"), "emit"],
    )
    second = _run_runtime_worker(
        repo,
        "run_custom_runtime_process",
        [str(repo), str(issue_dir), str(builtin_root), str(tmp_path / "global"), "consume"],
    )

    assert first["completed"] is False
    assert first["final_step"] == "emit"
    assert first["calls"] == 1
    assert second["completed"] is True, second
    assert second["final_step"] == "consume"
    assert second["calls"] == 1
    consumer_state = BlackboardStore(issue_dir).load_or_create(
        "consume", playbook_id="custom-contract"
    )
    assert consumer_state.artifacts["verified_state"].kind == ArtifactKind.WORKSPACE
    assert consumer_state.events[-1].event_type in {
        "handoff_contract", "transition", "decision", "workflow_completed"
    }


def test_bounded_v02_mixed_code_record_rebuilds_and_restarts_for_legacy_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy records load through declared files and execute a restarted consumer."""
    repo = tmp_path / "legacy-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".cafe/\n", encoding="utf-8")
    (repo / ".cafe").mkdir()
    (repo / ".cafe" / "phases.yaml").write_text(
        "review:\n  name: legacy-consumer-agent\n  clis:\n    - cli: codex\n      model: test-model\n",
        encoding="utf-8",
    )
    agent_dir = repo / ".cafe" / "agents" / "operator"
    agent_dir.mkdir(parents=True)
    (agent_dir / "legacy-consumer-agent.md").write_text(
        "---\nname: legacy-consumer-agent\ndescription: legacy test agent\n---\n\nConsume the legacy record.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    (repo / "tracked.txt").write_text("legacy\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "legacy base"], cwd=repo, check=True, capture_output=True)

    builtin_root = tmp_path / "builtin"
    skill_dir = builtin_root / "skills" / "legacy-consumer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: legacy-consumer\ndescription: legacy consumer\nversion: 1.0.0\n"
        "workflow:\n"
        "  prompt_inputs:\n"
        "    - artifacts: [code]\n"
        "      placeholder: legacy_artifact\n"
        "      required: true\n"
        "---\n\n"
        "## Role\nConsume the legacy artifact.\n\n"
        "## Handoff\nWrite next-step baton for this result; the runtime updates the blackboard.\n",
        encoding="utf-8",
    )
    playbook_dir = repo / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True)
    (playbook_dir / "legacy-contract.yaml").write_text(
        yaml.safe_dump(
            {
                "playbook": {"id": "legacy-contract"},
                "roles": {"operator": {"default_agent": "legacy-consumer-agent"}},
                "steps": {
                    "review": {
                        "skill": "legacy-consumer",
                        "role": "operator",
                        "input_artifacts": ["code"],
                        "output_artifact": "review_feedback",
                        "valid_intents": ["await_agent"],
                        "on": {"await_agent": "_done"},
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    issue_dir = repo / ".cafe" / "issues" / "legacy-contract"
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
    store.save(rebuilt)
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {"version": 1, "to_owner": "agent", "to_step": "review", "intent": "await_agent"}
        ),
        encoding="utf-8",
    )

    loader = PlaybookLoader(
        project_root=repo,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    playbook = loader.load("legacy-contract")
    resumed = _run_runtime_worker(
        repo,
        "run_legacy_runtime_process",
        [str(repo), str(issue_dir), str(builtin_root), str(tmp_path / "global")],
    )

    resumed_state = BlackboardStore(issue_dir).load_or_create(
        "review", playbook_id="legacy-contract"
    )
    assert resumed_state.artifacts["code"].summary == "legacy development summary"
    assert "workspace_input_artifact" not in playbook["steps"]["review"]
    assert resumed["completed"] is True
    assert resumed["final_step"] == "review"
    assert resumed["calls"] == 1
    assert (issue_dir / "review" / "iteration_001" / "output.md").exists()
    assert not (issue_dir / "develop" / "iteration_001" / "workspace.json").exists()


def test_post_use_workspace_mismatch_prevents_all_publication_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-agent mismatch prevents artifacts, handoff, and transition acceptance."""
    repo = tmp_path / "post-use-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".cafe/\n", encoding="utf-8")
    (repo / ".cafe").mkdir()
    (repo / ".cafe" / "phases.yaml").write_text(
        "consume:\n  name: contaminator\n  clis:\n    - cli: codex\n      model: test-model\n",
        encoding="utf-8",
    )
    builtin_root = tmp_path / "builtin"
    skill_dir = builtin_root / "skills" / "post-use-consumer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: post-use-consumer\ndescription: post-use test consumer\nversion: 1.0.0\n"
        "workflow:\n"
        "  execution_profile:\n"
        "    workload: implementation\n"
        "    reasoning: standard\n"
        "    risk_domains: [workspace]\n"
        "    fallback_strength: equivalent_or_stronger\n"
        "---\n\n"
        "## Role\nRun the consumer.\n\n"
        "## Handoff\nWrite next-step baton for this result; the runtime updates the blackboard.\n",
        encoding="utf-8",
    )
    agent_dir = repo / ".cafe" / "agents" / "consumer"
    agent_dir.mkdir(parents=True)
    (agent_dir / "contaminator.md").write_text(
        "---\nname: contaminator\ndescription: post-use test agent\n---\n\nRun the consumer.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "tracked.txt").write_text("current\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "current"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    issue_dir = repo / ".cafe" / "issues" / "post-use"
    receipt_output = issue_dir / "receipt" / "iteration_001" / "output.md"
    receipt_output.parent.mkdir(parents=True)
    run_verification(
        output_file=receipt_output,
        command=[sys.executable, "-c", "print('post-use baseline')"],
        scope="targeted",
        cwd=repo,
    )
    workspace = build_workspace_artifact(
        repo=repo,
        name="verified_state",
        version=1,
        base_sha=base,
        head_sha=head,
        receipt_outputs=[receipt_output],
        producer_step="producer",
    )
    producer_workspace = issue_dir / "producer" / "iteration_001" / "workspace.json"
    producer_workspace.parent.mkdir(parents=True)
    producer_workspace.write_text(json.dumps(workspace.to_dict()), encoding="utf-8")
    state = BlackboardStore(issue_dir).load_or_create("consume", playbook_id="post-use")
    state.artifacts["verified_state"] = ArtifactEntry(
        name="verified_state",
        kind=ArtifactKind.WORKSPACE,
        version=1,
        updated_by="producer",
        path=str(producer_workspace),
        base_sha=base,
        head_sha=head,
    )
    BlackboardStore(issue_dir).save(state)
    initial_handoff = (
        state.handoff_contract.to_dict()
        if state.handoff_contract is not None
        else None
    )

    class ContaminatingAgent:
        def __init__(self) -> None:
            self.agent = SimpleNamespace(
                config=SimpleNamespace(native_review_configuration=None, cli=AgentCLI.CODEX, session_id="post-use", model=None)
            )

        def get_agent(self, _name: str) -> SimpleNamespace:
            return self.agent

        def execute(self, _name: str, _prompt: str, *, streaming_output_file=None, **_kwargs):
            output_file = Path(streaming_output_file).parent / "output.md"
            output_file.write_text("# contaminated result\n", encoding="utf-8")
            (repo / "tracked.txt").write_text("contaminated\n", encoding="utf-8")
            return "await_agent", TokenUsage(), [], [], [], None

    playbook = {
        "playbook": {"id": "post-use"},
        "roles": {"consumer": {"default_agent": "contaminator"}},
        "steps": {
            "consume": {
                "skill": "post-use-consumer",
                "role": "consumer",
                "input_artifacts": ["verified_state"],
                "workspace_input_artifact": "verified_state",
                "valid_intents": ["await_agent"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="post-use",
        playbook=playbook,
        generic_phase=GenericPhase(
            SkillLoader(
                project_root=repo,
                global_root=tmp_path / "global",
                builtin_root=builtin_root,
            )
        ),
        agent_manager=ContaminatingAgent(),
        git_ops=GitOperations(repo),
        role_agent_map={"consumer": "contaminator"},
    )

    with pytest.raises(ValueError, match="workspace"):
        executor.execute_step("consume", playbook["steps"]["consume"], state)

    iteration_dir = issue_dir / "consume" / "iteration_001"
    assert not (iteration_dir / "artifact.json").exists()
    assert not (iteration_dir / "workspace.json").exists()
    persisted = BlackboardStore(issue_dir).load_or_create("consume", playbook_id="post-use")
    assert not any(event.event_type == "transition" for event in persisted.events)
    assert (
        persisted.handoff_contract.to_dict()
        if persisted.handoff_contract is not None
        else None
    ) == initial_handoff
