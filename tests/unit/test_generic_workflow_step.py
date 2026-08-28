"""Tests for direct workflow step execution."""

import json
from collections.abc import Iterator
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.hooks import HookResult
from cafe.core.resume_user_input import CONTINUE_USER_INPUT
from cafe.core.session_continuation import (
    SessionContinuation,
    SessionContinuationPolicy,
)
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import AgentCLI, AgentConfig, TokenUsage
from cafe.phases.generic_phase import GenericPhase, GenericPhaseExecution
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.utils.phase_config import PhaseStepModelResolution


@pytest.fixture(autouse=True)
def _default_runtime_context(tmp_path: Path, monkeypatch):
    """Give tests a configured strategic context so steps that opt into the
    alignment gate stay quiet unless a test sets up a real trigger. Tests that
    exercise the missing-context behavior delete this file explicitly."""
    config = tmp_path / ".cafe" / "strategic_context.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("version: 1\n", encoding="utf-8")

    from cafe.phases import generic_workflow_step

    real_loader = generic_workflow_step.load_phase_step_model

    def load_test_phase_config(*, step_name, local_path, repo_path=None):
        if any(path is not None and path.exists() for path in (local_path, repo_path)):
            return real_loader(step_name=step_name, local_path=local_path, repo_path=repo_path)
        return PhaseStepModelResolution(
            name=None,
            role=None,
            clis=(("codex", "gpt-5-test"),),
            model="gpt-5-test",
            source="test",
            chain=("test",),
            clis_source="test",
        )

    monkeypatch.setattr(generic_workflow_step, "load_phase_step_model", load_test_phase_config)


class FakeAgentManager:
    def __init__(self, response: str | list[str], on_execute=None) -> None:
        if isinstance(response, list):
            self._responses: Iterator[str] = iter(response)
        else:
            self._responses = iter([response])
        self.prompts: list[str] = []
        self.allowed_tools_calls: list[list[str] | None] = []
        self.allowed_directories_calls: list[list[str] | None] = []
        self.execute_call_count = 0
        self.preview_calls: list[list[str] | None] = []
        self.agent = SimpleNamespace(
            config=SimpleNamespace(cli=AgentCLI.CODEX, session_id="session-1", model=None)
        )
        self.on_execute = on_execute

    def get_agent(self, name: str) -> SimpleNamespace:
        return self.agent

    def preview_cli_command_args(
        self,
        agent_name: str,
        prompt: str,
        allowed_tools=None,
        allowed_directories=None,
    ) -> list[str]:
        args = ["-C", str(Path.cwd().resolve()), "-a", "never", "exec", "--json"]
        if self.agent.config.model:
            args.extend(["--model", self.agent.config.model])
        args.append(prompt)
        self.preview_calls.append(args)
        return args

    def preview_cli_environment(self, agent_name: str) -> dict[str, str]:
        return {"CODEX_HOME": str(Path.cwd().resolve() / ".codex")}

    def execute(
        self,
        agent_name: str,
        prompt: str,
        allowed_tools=None,
        allowed_directories=None,
        streaming_output_file=None,
        phase_name=None,
    ):
        self.prompts.append(prompt)
        self.allowed_tools_calls.append(list(allowed_tools) if allowed_tools is not None else None)
        self.allowed_directories_calls.append(
            list(allowed_directories) if allowed_directories is not None else None
        )
        self.execute_call_count += 1
        response = next(self._responses)
        if self.on_execute is not None:
            self.on_execute(
                prompt=prompt,
                response=response,
                streaming_output_file=streaming_output_file,
            )
        return response, TokenUsage(), [], [], [], None


class FakeGitOperations:
    def get_main_branch(self) -> str:
        return "main"

    def get_default_base_branch(self) -> str:
        return "main"

    def get_commits_between(self, base: str, head: str) -> str:
        return "abc123 test commit"


def _build_loader(tmp_path: Path) -> GenericPhase:
    skill_root = tmp_path / "builtin" / "skills"
    for name, body in {
        "cafe-spec": "## Role\nRead your agent file: {agent_file}\n\n## Context\n{blackboard_digest}\n",
        "cafe-plan": "Write plan to: {output_file}\n",
        "cafe-develop": "Implement the current request. {develop_file}\n",
        "cafe-workflow-common": "Read blackboard first.\n",
        "cafe-github_sync": "Shared GitHub sync helper.\n",
        "cafe-review": "Review the latest changes.\n",
        "cafe-pr": "Write PR content to: {output_file}\n",
        "synthesis": "Synthesize {evidence_file}.\n",
    }.items():
        skill_dir = skill_root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        workflow = ""
        if name == "cafe-develop":
            workflow = (
                "workflow:\n  prompt_inputs:\n"
                "    - artifacts: [code]\n      placeholder: develop_file\n      required: false\n"
            )
        if name == "synthesis":
            workflow = (
                "workflow:\n  prompt_inputs:\n"
                "    - artifacts: [research_notes]\n"
                "      placeholder: evidence_file\n"
                "      required: true\n"
                "  output_templates:\n"
                "    catalog: synthesis\n"
            )
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: desc\n{workflow}---\n\n{body}",
            encoding="utf-8",
        )
    synthesis_templates = skill_root / "synthesis" / "assets" / "templates"
    synthesis_templates.mkdir(parents=True, exist_ok=True)
    (synthesis_templates / "evidence.md").write_text("# Evidence\n", encoding="utf-8")
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(
            loader,
            project_root=tmp_path,
            home_dir=tmp_path / "home",
        ),
    )


def test_alignment_checkpoint_gate_pauses_before_agent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(exist_ok=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "roadmap.md").write_text("roadmap", encoding="utf-8")
    (tmp_path / ".cafe" / "strategic_context.yaml").write_text(
        """
version: 1
documents:
  roadmap:
    path: docs/roadmap.md
    status: exists
mandate:
  axes:
    product_scope:
      level: escalate
      grounds: [roadmap]
""",
        encoding="utf-8",
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-align-hook"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "hooks": {"prepare_input": ["AlignmentCheckpointGate"]},
                "alignment": {"affected_document_categories": ["roadmap"]},
                "on": {"await_agent": "_done", "alignment_checkpoint": "develop"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-align-hook",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        step_user_inputs={"develop": "This changes roadmap scope."},
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "alignment_checkpoint"
    assert agent_manager.prompts == []
    assert (issue_dir / "develop" / "iteration_001" / "alignment_request.json").exists()
    reloaded = BlackboardStore(issue_dir).load_or_create("develop")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.ALIGNMENT_CHECKPOINT
    assert "code" not in reloaded.artifacts


def test_generic_workflow_step_executor_writes_iteration_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-1"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-1",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )
    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert result.response == "confirmed"
    assert "spec" in result.artifacts
    iteration_dir = issue_dir / "spec" / "iteration_001"
    assert (iteration_dir / "iteration.json").exists()
    assert (iteration_dir / "checklist.md").exists()
    assert (iteration_dir / "output.md").exists()
    assert (iteration_dir / "artifact.json").exists()
    status_file = issue_dir / "spec" / "status.json"
    assert not status_file.exists()
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.DONE
    assert reloaded.handoff_contract.to_step == "done"
    assert reloaded.handoff_contract.intent == HandoffIntent.WORKFLOW_COMPLETE
    assert reloaded.handoff_contract.status_code == "confirmed"
    assert reloaded.handoff_contract.source == "workflow.status_transition_adapter"


def test_generic_step_passes_declared_read_only_guard_to_agent_manager(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-003: a custom step forwards its resolved guard to execution."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-declared-guard"
    playbook = {
        "playbook": {"id": "custom"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "build": {
                "skill": {"default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "behavior": {
                    "completion": "status_code",
                },
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("build")
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-declared-guard",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    executor.execute_step("build", playbook["steps"]["build"], state)

    assert agent_manager.execute_call_count == 1


def test_generic_step_forwards_declared_read_only_guard_on_checklist_retry(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-003: a custom step preserves its guard when checklist retrying."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-declared-guard-retry"
    checklist = issue_dir / "build" / "iteration_001" / "checklist.md"

    class ChecklistRetryManager(FakeAgentManager):
        def __init__(self) -> None:
            super().__init__(["confirmed", "confirmed"])

        def execute(self, *args, continuation=None, **kwargs):
            result = super().execute(*args, **kwargs)
            checklist.write_text(
                "[ ] complete task\n" if self.execute_call_count == 1 else "[x] complete task\n",
                encoding="utf-8",
            )
            return result

    playbook = {
        "playbook": {"id": "custom"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "build": {
                "skill": {"default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "behavior": {
                    "completion": "status_code",
                },
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("build")
    agent_manager = ChecklistRetryManager()
    generic_phase = _build_loader(tmp_path)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-declared-guard-retry",
        playbook=playbook,
        generic_phase=generic_phase,
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    def execute_with_confirmed_status(*args, **kwargs):
        response = kwargs["agent_executor"]("prompt")
        return GenericPhaseExecution(
            response=response,
            status_code=PhaseStatusCode.CONFIRMED,
            goto_target=None,
            agent_invoked=True,
        )

    monkeypatch.setattr(generic_phase, "execute", execute_with_confirmed_status)

    executor.execute_step("build", playbook["steps"]["build"], state)

    assert agent_manager.execute_call_count == 2


def test_resolve_agent_name_uses_phase_config_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(exist_ok=True)
    (tmp_path / ".cafe" / "phases.yaml").write_text(
        """
develop:
  name: PhaseDavid
  role: developer
  clis:
    - cli: codex
      model: gpt-5-test
""",
        encoding="utf-8",
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-1"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {"develop": {"skill": "develop", "role": "developer"}},
    }
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-1",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("await_agent"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    with (
        patch("cafe.phases.generic_workflow_step.get_repo_root", return_value=tmp_path),
        patch("cafe.phases.generic_workflow_step.get_git_toplevel", return_value=tmp_path),
    ):
        assert executor._resolve_agent_name("develop", playbook["steps"]["develop"]) == "PhaseDavid"


def test_generic_workflow_step_agent_written_baton_preserved(tmp_path: Path, monkeypatch) -> None:
    """When the agent writes a baton directly, the status-driven write is skipped."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-agent-baton"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    def on_execute(prompt, response, streaming_output_file=None):
        # Simulate agent writing a baton targeting "plan" instead of "_done"
        baton_path = issue_dir / "next_step.txt"
        baton_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "to_owner": "agent",
                    "to_step": "plan",
                    "intent": "await_agent",
                }
            ),
            encoding="utf-8",
        )

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-agent-baton",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed", on_execute=on_execute),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)
    assert result.response == "confirmed"

    # The agent's baton should be preserved (to_step=plan), not overwritten
    # by the status-driven transition (which would have set to_step=done)
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_step == "plan"
    assert reloaded.handoff_contract.to_owner == HandoffOwner.AGENT
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_generic_workflow_step_status_transition_writes_strict_baton_payload(
    tmp_path: Path, monkeypatch
) -> None:
    """Status-driven handoff write emits only the strict four-field baton payload."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-status-baton-payload"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "pm",
                "output_artifact": "plan",
            },
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-status-baton-payload",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)
    assert result.status_code == "confirmed"
    payload = json.loads((issue_dir / "next_step.txt").read_text(encoding="utf-8"))

    assert payload == {
        "version": 1,
        "to_owner": "agent",
        "to_step": "plan",
        "intent": "await_agent",
    }


def test_hybrid_portion_restores_canonical_control_files_after_agent_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-010: only the portion-local baton can survive a hybrid agent run."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "hybrid-control-files"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "mixed": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read", "Edit", "Write", "Bash"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
                "hybrid_portion": {"id": "draft"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("mixed")
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "to_owner": "agent",
                "to_step": "mixed",
                "intent": "await_agent",
            }
        ),
        encoding="utf-8",
    )
    original_baton = (issue_dir / "next_step.txt").read_text(encoding="utf-8")

    def on_execute(*, streaming_output_file: str | None, **_kwargs: object) -> None:
        assert streaming_output_file is not None
        (issue_dir / "next_step.txt").write_text("agent-controlled", encoding="utf-8")
        (issue_dir / "blackboard.json").write_text('{"current_step": "escaped"}', encoding="utf-8")
        portion_baton = Path(streaming_output_file).parent / "hybrid_portion_baton.json"
        portion_baton.write_text(
            json.dumps(
                {
                    "from_step": "mixed",
                    "to_owner": "agent",
                    "to_step": "mixed",
                    "intent": "await_agent",
                    "source": "hybrid_portion:mixed:draft",
                }
            ),
            encoding="utf-8",
        )

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="hybrid-control-files",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed", on_execute=on_execute),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    result = executor.execute_step("mixed", playbook["steps"]["mixed"], state)

    reloaded = BlackboardStore(issue_dir).load_or_create("mixed")
    assert (issue_dir / "next_step.txt").read_text(encoding="utf-8") == original_baton
    assert reloaded.current_step == "mixed"
    assert "escaped" not in (issue_dir / "blackboard.json").read_text(encoding="utf-8")
    assert result.events[-1]["type"] == "hybrid_portion_baton"


def test_hybrid_portion_replaces_control_file_symlink_without_following_it(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-010: hybrid rollback restores a replaced control path, not its target."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "hybrid-control-symlink"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "mixed": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read", "Edit", "Write", "Bash"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
                "hybrid_portion": {"id": "draft"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("mixed")
    protected_target = tmp_path / "protected-control-file"
    protected_target.write_text("must remain unchanged", encoding="utf-8")

    def on_execute(*, streaming_output_file: str | None, **_kwargs: object) -> None:
        assert streaming_output_file is not None
        control_path = issue_dir / "blackboard.json"
        control_path.unlink()
        control_path.symlink_to(protected_target)
        portion_baton = Path(streaming_output_file).parent / "hybrid_portion_baton.json"
        portion_baton.write_text(
            json.dumps(
                {
                    "from_step": "mixed",
                    "to_owner": "agent",
                    "to_step": "mixed",
                    "intent": "await_agent",
                    "source": "hybrid_portion:mixed:draft",
                }
            ),
            encoding="utf-8",
        )

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="hybrid-control-symlink",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed", on_execute=on_execute),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("mixed", playbook["steps"]["mixed"], state)

    assert protected_target.read_text(encoding="utf-8") == "must remain unchanged"
    assert not (issue_dir / "blackboard.json").is_symlink()
    assert store.load_or_create("mixed").current_step == "mixed"


def test_generic_workflow_step_writes_review_pause_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review-pause"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["ready_for_review", "confirmed"],
                "on": {"confirm_output": "spec", "await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review-pause",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("ready_for_review"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
        interactive=True,
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    # Interactive: READY_FOR_REVIEW hands off to user step for confirmation
    assert result.status_code == "ready_for_review"
    assert result.auto_continue is False
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.to_step == "user"
    assert reloaded.handoff_contract.intent == HandoffIntent.CONFIRM_OUTPUT


def test_generic_workflow_step_brief_ready_for_review_writes_confirm_output_contract(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-brief-confirm"
    playbook = {
        "playbook": {"id": "editorial"},
        "roles": {"editor": {"default_agent": "Roger"}},
        "steps": {
            "brief": {
                "skill": "spec_first",
                "role": "editor",
                "output_artifact": "brief",
                "allowed_tools": ["Read"],
                "valid_intents": ["ready_for_review", "confirmed"],
                "on": {"confirm_output": "brief", "await_agent": "draft"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("brief")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-brief-confirm",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("ready_for_review"),
        git_ops=FakeGitOperations(),
        role_agent_map={"editor": "Roger"},
        interactive=True,
    )

    result = executor.execute_step("brief", playbook["steps"]["brief"], state)

    assert result.status_code == "ready_for_review"
    reloaded = BlackboardStore(issue_dir).load_or_create("brief")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.CONFIRM_OUTPUT
    assert reloaded.handoff_contract.to_step == "user"


def test_generic_workflow_step_question_need_clarification_writes_clarification_contract(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-question-clarify"
    playbook = {
        "playbook": {"id": "research"},
        "roles": {"researcher": {"default_agent": "Morgan"}},
        "steps": {
            "question": {
                "skill": "spec_first",
                "role": "researcher",
                "output_artifact": "research_question_brief",
                "allowed_tools": ["Read"],
                "valid_intents": ["need_clarification", "confirmed"],
                "on": {"need_clarification": "question", "await_agent": "collect"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("question")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-question-clarify",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("need_clarification"),
        git_ops=FakeGitOperations(),
        role_agent_map={"researcher": "Morgan"},
        interactive=True,
    )
    executor._validate_produced_packet_contracts = MagicMock()

    result = executor.execute_step("question", playbook["steps"]["question"], state)

    assert result.status_code == "need_clarification"
    reloaded = BlackboardStore(issue_dir).load_or_create("question")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.NEED_CLARIFICATION
    assert reloaded.handoff_contract.to_step == "user"
    executor._validate_produced_packet_contracts.assert_not_called()


def test_generic_workflow_step_question_step_allows_questions_xml_edit(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-question-tools"
    playbook = {
        "playbook": {"id": "research"},
        "roles": {"researcher": {"default_agent": "Morgan"}},
        "steps": {
            "question": {
                "skill": "spec_first",
                "role": "researcher",
                "output_artifact": "research_question_brief",
                "allowed_tools": ["Read"],
                "valid_intents": ["need_clarification", "confirmed"],
                "on": {"need_clarification": "question", "await_agent": "collect"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("question")
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-question-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"researcher": "Morgan"},
    )

    executor.execute_step("question", playbook["steps"]["question"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert (
        "edit(./.cafe/issues/issue-question-tools/question/iteration_001/questions.xml)"
        in allowed_tools
    )


def test_generic_workflow_step_auto_confirms_review_in_non_interactive(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-auto-confirm"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}, "developer": {"default_agent": "David"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["ready_for_review", "confirmed"],
                "on": {"confirm_output": "spec", "await_agent": "plan"},
            },
            "plan": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "plan",
                "on": {"await_agent": "develop"},
            },
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-auto-confirm",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("ready_for_review"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
        interactive=False,
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    # Non-interactive: READY_FOR_REVIEW hands off to user step
    assert result.status_code == "ready_for_review"
    assert result.auto_continue is False
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_step == "user"
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER


def test_generic_workflow_step_does_not_retry_for_legacy_status_tokens(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-status-retry"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    agent_manager = FakeAgentManager(["ready_for_review", "confirmed"])
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-status-retry",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert len(agent_manager.prompts) == 1
    assert result.status_code is None


def test_generic_workflow_step_executor_uses_iteration_specific_skill_mapping(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-2"
    phase_dir = issue_dir / "spec" / "iteration_001"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / "iteration.json").write_text(
        '{"iteration": 1, "response": "confirmed", "end_time": "2026-04-09T00:00:00+08:00"}',
        encoding="utf-8",
    )

    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "plan"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    class RecordingPhase(GenericPhase):
        def __init__(self, loader, bridge):
            super().__init__(loader, skill_bridge=bridge)
            self.skill_names: list[str] = []

        def execute(self, **kwargs):
            self.skill_names.append(kwargs["skill_name"])
            return super().execute(**kwargs)

    state = BlackboardStore(issue_dir).load_or_create("spec")
    phase_for_loader = _build_loader(tmp_path)
    generic_phase = RecordingPhase(phase_for_loader.skill_loader, phase_for_loader.skill_bridge)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-2",
        playbook=playbook,
        generic_phase=generic_phase,
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert generic_phase.skill_names == ["plan"]


def test_generic_workflow_step_resolve_resume_user_input_uses_execution_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume"
    phase_dir = issue_dir / "develop"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / "iteration_001" / "iteration.json").parent.mkdir(parents=True, exist_ok=True)
    (phase_dir / "iteration_001" / "iteration.json").write_text(
        json.dumps({"iteration": 1, "cli": "gemini", "session_id": "session-abc"}),
        encoding="utf-8",
    )

    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "develop",
                "on": {"await_agent": "_done"},
            }
        },
    }

    class ResumeAwareManager(FakeAgentManager):
        def get_execution_config(self, agent_name: str, phase_name=None):
            return AgentConfig(
                name=agent_name,
                cli=AgentCLI.GEMINI,
                session_id="session-abc",
                model="gemini-model",
            )

    manager = ResumeAwareManager("ready_for_review")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-resume",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )
    executor.phase_dir = phase_dir
    executor.issue_dir = issue_dir
    executor.iteration = 1
    executor._step_agent_name = "David"

    resolved = executor._resolve_iteration_user_input("develop")
    assert resolved == CONTINUE_USER_INPUT


def test_generic_workflow_step_resume_user_input_rejects_different_execution_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-mismatch"
    phase_dir = issue_dir / "develop"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / "iteration_001" / "iteration.json").parent.mkdir(parents=True, exist_ok=True)
    (phase_dir / "iteration_001" / "iteration.json").write_text(
        json.dumps({"iteration": 1, "cli": "gemini", "session_id": "session-abc"}),
        encoding="utf-8",
    )

    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "develop",
                "on": {"await_agent": "_done"},
            }
        },
    }

    class ResumeAwareManager(FakeAgentManager):
        def get_execution_config(self, agent_name: str, phase_name=None):
            return AgentConfig(
                name=agent_name,
                cli=AgentCLI.COPILOT,
                session_id="session-different",
                model="copilot-model",
            )

    manager = ResumeAwareManager("ready_for_review")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-resume-mismatch",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )
    executor.phase_dir = phase_dir
    executor.issue_dir = issue_dir
    executor.iteration = 2
    executor._step_agent_name = "David"

    resolved = executor._resolve_iteration_user_input("develop")
    assert resolved == "workflow execute"


def test_first_iteration_declared_initial_task_uses_empty_optional_input(
    tmp_path: Path,
) -> None:
    """Optional first-run input follows the declaration, not a plan step name."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-initial-task"
    playbook = {
        "playbook": {"id": "custom"},
        "roles": {"writer": {"default_agent": "David"}},
        "steps": {
            "draft": {
                "skill": "cafe-plan",
                "role": "writer",
                "human_tasks": [{"trigger": "initial", "task_id": "optional-guide"}],
                "on": {"await_agent": "_done"},
            }
        },
    }
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-initial-task",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"writer": "David"},
    )
    executor.phase_dir = issue_dir / "draft"
    executor.iteration = 1

    assert executor._load_iteration_user_input_candidate("draft") == ""


def test_generic_workflow_step_executor_installs_workflow_common_and_phase_skill(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review-skill"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"reviewer": {"default_agent": "Richard"}},
        "skills": {
            "workflow": {
                "shared": ["cafe-workflow-common", "cafe-github_sync"],
                "roles": {
                    "reviewer": {"mode": "extend", "skills": ["cafe-workflow-common"]},
                },
                "steps": {
                    "review": {"mode": "extend", "skills": ["cafe-github_sync"]},
                },
            },
            "chat": {"shared": []},
        },
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "output_artifact": "review_feedback",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("review")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    generic_phase = _build_loader(tmp_path)
    installed_skill_names: list[str] = []
    original_install_skill = generic_phase.skill_bridge.install_skill

    def record_skill_install(name, cli, context=None):
        installed_skill_names.append(name)
        return original_install_skill(name, cli, context)

    monkeypatch.setattr(generic_phase.skill_bridge, "install_skill", record_skill_install)
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review-skill",
        playbook=playbook,
        generic_phase=generic_phase,
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"reviewer": "Richard"},
    )

    result = executor.execute_step("review", playbook["steps"]["review"], state)

    assert (tmp_path / ".codex" / "skills" / "cafe-workflow-common" / "SKILL.md").exists()
    assert (tmp_path / ".codex" / "skills" / "cafe-github_sync" / "SKILL.md").exists()
    assert (tmp_path / ".codex" / "skills" / "cafe-review" / "SKILL.md").exists()
    assert installed_skill_names == ["cafe-workflow-common", "cafe-github_sync", "review"]
    iteration_dir = issue_dir / "review" / "iteration_001"
    output_file = iteration_dir / "output.md"
    checklist_file = iteration_dir / "checklist.md"
    assert result.artifacts["review_feedback"] == str(output_file)
    assert output_file.exists()
    assert checklist_file.exists()

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "edit(./.cafe/issues/issue-review-skill/review/iteration_001/output.md)" in allowed_tools
    assert (
        "edit(./.cafe/issues/issue-review-skill/review/iteration_001/checklist.md)" in allowed_tools
    )
    assert (
        "write(./.cafe/issues/issue-review-skill/review/iteration_001/output.md)" in allowed_tools
    )
    assert (
        "write(./.cafe/issues/issue-review-skill/review/iteration_001/checklist.md)"
        in allowed_tools
    )
    assert "edit(./.cafe/issues/issue-review-skill/blackboard.json)" not in allowed_tools
    assert "edit(./.cafe/issues/issue-review-skill/next_step.txt)" in allowed_tools
    assert "write(./.cafe/issues/issue-review-skill/blackboard.json)" not in allowed_tools
    assert "write(./.cafe/issues/issue-review-skill/next_step.txt)" in allowed_tools

    prompt = agent_manager.prompts[-1]
    assert "Phase skill: $cafe-review" in prompt
    assert f"output_file={output_file}" in prompt
    assert f"checklist_file={checklist_file}" in prompt
    assert "blackboard_file=./.cafe/issues/issue-review-skill/blackboard.json" in prompt
    assert "next_step_file=./.cafe/issues/issue-review-skill/next_step.txt" in prompt


def test_generic_workflow_step_prompt_includes_latest_blackboard_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-handoff"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "skills": {
            "workflow": {
                "shared": ["cafe-workflow-common", "cafe-github_sync"],
            },
            "chat": {"shared": []},
        },
        "steps": {
            "develop": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    store.set_handoff_summary(
        state,
        "還要再實作 cafe skill rm，支援批次刪除、interactive 多選與 confirm。",
    )
    hidden_payload = "SHOULD_NOT_APPEAR_IN_BOUNDED_DIGEST" * 10_000
    store.log_event(
        state,
        "plan",
        "plan_confirmed",
        "Plan confirmed; continue to development.",
        {"full_prompt": hidden_payload},
    )
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-handoff",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert any(
        "Latest workflow handoff from blackboard:" in prompt for prompt in agent_manager.prompts
    )
    assert any("還要再實作 cafe skill rm" in prompt for prompt in agent_manager.prompts)
    prompt = agent_manager.prompts[-1]
    assert "Bounded blackboard digest:" in prompt
    assert '"event_type": "plan_confirmed"' in prompt
    assert hidden_payload not in prompt
    assert len(prompt) < 20_000
    installed_skill = (tmp_path / ".codex" / "skills" / "cafe-plan" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    expected_output = "./.cafe/issues/issue-handoff/develop/iteration_001/output.md"
    assert f"Write plan to: {expected_output}" in installed_skill
    assert "{output_file}" not in installed_skill


def test_generic_workflow_step_prompt_keeps_skill_invocations_only(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-skill-body"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "skills": {
            "workflow": {
                "shared": ["cafe-workflow-common", "cafe-github_sync"],
            },
            "chat": {"shared": []},
        },
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "output_artifact": "pr",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-skill-body",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    prompt = agent_manager.prompts[-1]
    assert "Shared skills:" in prompt
    assert "Phase skill: " in prompt
    assert "Shared skill instructions:" not in prompt
    assert "Phase skill instructions:" not in prompt
    assert "Read blackboard first." not in prompt
    assert "Write PR content to:" not in prompt


def test_generic_workflow_step_pr_prompt_overrides_external_state_guardrail(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-guardrail"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    store.set_handoff_summary(state, "原本的 pr script 有問題，我把 pr 砍掉了麻煩重發一次")
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-guardrail",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    prompt = agent_manager.prompts[-1]
    assert "Do not wait for, verify, or require a remote GitHub branch/PR" in prompt
    assert "Remote PR publish happens later in the host-side publish_output hook." in prompt
    assert (
        "Before updating the workflow baton, verify whether the requested state change has actually happened in files or external state relevant to this phase."
        not in prompt
    )


def test_generic_workflow_step_writes_pr_publish_request_contract(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-contract"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "output_artifact": "pr",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    issue_yaml = issue_dir / "issue.yaml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    issue_yaml.write_text("base_branch: v02\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-contract",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    publish_request = json.loads(
        (issue_dir / "pr" / "iteration_001" / "publish_request.json").read_text(encoding="utf-8")
    )
    capability_request = json.loads(
        (issue_dir / "pr" / "iteration_001" / "capability_request.json").read_text(encoding="utf-8")
    )
    assert publish_request["capability"] == "cafe.pr.publish"
    assert capability_request == publish_request
    assert publish_request["args"] == {
        "output": ".cafe/issues/issue-pr-contract/pr/iteration_001/output.md",
        "base": "v02",
    }
    assert publish_request["permissions"]["network"] == ["github.com", "api.github.com"]
    assert publish_request["permissions"]["writes"] == [
        ".cafe/issues/issue-pr-contract/pr/iteration_001/output.md",
        ".git",
        ".cafe/issues/issue-pr-contract",
    ]
    assert publish_request["effects"] == {
        "browser_open": [],
        "network_destinations": ["github.com", "api.github.com"],
        "writes": [
            ".cafe/issues/issue-pr-contract/pr/iteration_001/output.md",
            ".git",
            ".cafe/issues/issue-pr-contract",
        ],
    }
    assert publish_request["credentials"] == ["gh"]


def test_remote_pr_git_history_uses_fetched_remote_base(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-remote-history"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "base_branch: develop\npr:\n  auto_create: true\n",
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "behavior": {
                    "completion": "baton",
                    "context_providers": ["git_history"],
                },
                "output_artifact": "pr",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    git_ops = MagicMock()
    git_ops.ensure_remote_base_ancestor.return_value = "origin/develop"
    git_ops.get_commits_between.return_value = "abc123 direct bootstrap"
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-remote-history",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=git_ops,
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    git_ops.ensure_remote_base_ancestor.assert_called_once_with("develop", "HEAD")
    git_ops.get_commits_between.assert_called_once_with(
        base="origin/develop",
        head="HEAD",
    )


def test_generic_workflow_step_writes_exact_current_pr_browser_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-capability-contract"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "publish": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "capability_requests": ["cafe.browser.open"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("publish")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-capability-contract",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("publish", playbook["steps"]["publish"], state)

    capability_request = json.loads(
        (issue_dir / "publish" / "iteration_001" / "capability_request.json").read_text(
            encoding="utf-8"
        )
    )
    assert capability_request == {
        "capability": "cafe.browser.open",
        "args": {"target_ref": "current_pr"},
        "effects": {
            "browser_open": ["current_pr"],
            "writes": [],
            "network_destinations": [],
        },
        "credentials": [],
        "permissions": {},
    }
    assert not (issue_dir / "publish" / "iteration_001" / "publish_request.json").exists()


def test_generic_workflow_step_writes_multi_capability_request_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-capability-contract"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "publish": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "capability_requests": ["demo.first", "demo.second"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("publish")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-capability-contract",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("publish", playbook["steps"]["publish"], state)

    capability_request = json.loads(
        (issue_dir / "publish" / "iteration_001" / "capability_request.json").read_text(
            encoding="utf-8"
        )
    )
    assert capability_request == {
        "requests": [
            {
                "capability": "demo.first",
                "args": {},
                "effects": {
                    "browser_open": [],
                    "writes": [],
                    "network_destinations": [],
                },
                "credentials": [],
                "permissions": {},
            },
            {
                "capability": "demo.second",
                "args": {},
                "effects": {
                    "browser_open": [],
                    "writes": [],
                    "network_destinations": [],
                },
                "credentials": [],
                "permissions": {},
            },
        ]
    }
    assert not (issue_dir / "publish" / "iteration_001" / "publish_request.json").exists()


def test_generic_workflow_step_collects_clarification_before_next_agent_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-clarify"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["need_clarification", "confirmed"],
                "hooks": {"prepare_input": ["UserInputCollector"]},
                "on": {
                    "need_clarification": "spec",
                    "await_agent": "_done",
                },
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")

    def _write_questions_xml(
        *, response: str, streaming_output_file: str | None, **_: object
    ) -> None:
        if response != "need_clarification" or not streaming_output_file:
            return
        iteration_dir = Path(streaming_output_file).parent
        (iteration_dir / "questions.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="q1">
    <title>Which flow should we support first?</title>
    <options>
      <option>CLI only</option>
      <option>CLI and GitHub</option>
    </options>
  </question>
</questions>
""",
            encoding="utf-8",
        )

    agent_manager = FakeAgentManager(
        ["need_clarification", "confirmed"],
        on_execute=_write_questions_xml,
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-clarify",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    first_result = executor.execute_step("spec", playbook["steps"]["spec"], state)
    assert first_result.response == "need_clarification"
    assert first_result.auto_continue is False

    # Mirror the runtime which records a `step_completed` event after each step
    # so the second iteration's hooks can read the previous status from the
    # blackboard.
    store.record_event(
        state,
        "step_completed",
        {"step": "spec", "status_code": first_result.status_code},
    )

    with patch(
        "cafe.core.hooks.native.interactive_qa_flow",
        return_value="Q1: Which flow should we support first?\nA1: CLI only",
    ):
        second_result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert second_result.response == "confirmed"
    # In non-interactive mode the UserInputCollector does not auto-answer
    # need_clarification — the workflow stops at user step.
    assert any("No additional changes needed" not in prompt for prompt in agent_manager.prompts)


def test_initial_requirements_collection_does_not_auto_continue_clarification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-initial-input"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["need_clarification", "confirmed"],
                "hooks": {"prepare_input": ["GitHubIssueFetcher"]},
                "on": {
                    "need_clarification": "spec",
                    "await_agent": "_done",
                },
            }
        },
    }
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "spec:\n  input_method: manual\n",
        encoding="utf-8",
    )
    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-initial-input",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("need_clarification"),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
        step_user_inputs={"spec": "Initial issue text"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert result.status_code == "need_clarification"
    assert result.auto_continue is False
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.to_step == "user"


def test_generic_workflow_step_records_script_hook_events_to_blackboard(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-script-event"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "hooks": {"before_execute": ["ScriptHook"]},
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    class ScriptHook:
        name = "ScriptHook"

        def run(self, **kwargs):
            return HookResult(
                events=[
                    {
                        "type": "script_hook",
                        "step": "develop",
                        "skill": "develop",
                        "stage": "before_execute",
                        "script": "demo.sh",
                        "status": "success",
                        "exit_code": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "validation_errors": [],
                    }
                ]
            )

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))

    base_phase = _build_loader(tmp_path)
    phase_with_hook = GenericPhase(
        base_phase.skill_loader,
        hook_registry={"ScriptHook": ScriptHook},
        skill_bridge=base_phase.skill_bridge,
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-script-event",
        playbook=playbook,
        generic_phase=phase_with_hook,
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "confirmed"
    reloaded = BlackboardStore(issue_dir).load_or_create("develop")
    script_event = next(item for item in reloaded.events if item.event_type == "script_hook")
    assert script_event.data["script"] == "demo.sh"
    assert script_event.data["status"] == "success"


def test_generic_workflow_step_auto_continues_pause_statuses_in_interactive_mode(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review"
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    (spec_dir / "iteration.json").write_text(
        '{"iteration":1,"status_code":"confirmed"}',
        encoding="utf-8",
    )
    (issue_dir / "spec" / "status.json").write_text(
        '{"phase":"spec","status":"completed","status_code":"confirmed","iteration":1}',
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "plan": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "plan",
                "allowed_tools": ["Read"],
                "valid_intents": ["ready_for_review"],
                "on": {"confirm_output": "plan"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("plan")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("ready_for_review"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=True,
    )

    result = executor.execute_step("plan", playbook["steps"]["plan"], state)

    assert result.response == "ready_for_review"
    assert result.auto_continue is False


def test_generic_workflow_step_keeps_missing_status_without_continue_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-no-status"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-no-status",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager(["done without status", "confirmed"]),
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    result = executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert "done without status" in result.response
    assert result.status_code is None
    assert len(executor.agent_manager.prompts) == 1
    context_data = json.loads(
        (issue_dir / "spec" / "iteration_001" / "iteration.json").read_text(encoding="utf-8")
    )
    assert "status_code" not in context_data


def test_generic_workflow_step_does_not_recover_from_unchanged_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-stale-output"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed", "no_changes_needed"],
                "on": {"await_agent": "review"},
            }
        },
    }

    class FailingAgentManager(FakeAgentManager):
        def execute(self, *args, **kwargs):
            raise AgentExecutionError("Codex execution failed: thread/resume failed")

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-stale-output",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FailingAgentManager("unused"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    with pytest.raises(AgentExecutionError):
        executor.execute_step("develop", playbook["steps"]["develop"], state)


def test_generic_workflow_step_restores_spec_runtime_allowed_tools(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-spec-tools"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read", "Grep", "Glob", "WebFetch", "WebSearch"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done", "need_clarification": "spec"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-spec-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "ls" in allowed_tools
    assert "web_fetch" in allowed_tools
    assert "web_search" in allowed_tools
    assert "edit(./.cafe/issues/issue-spec-tools/spec/iteration_001/output.md)" in allowed_tools
    assert "edit(./.cafe/issues/issue-spec-tools/spec/iteration_001/checklist.md)" in allowed_tools
    assert "edit(./.cafe/issues/issue-spec-tools/spec/iteration_001/questions.xml)" in allowed_tools


def test_generic_workflow_step_uses_baton_only_tools_on_baton_error(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-spec-baton-retry"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read", "Grep", "Glob", "WebFetch", "WebSearch"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done", "need_clarification": "spec"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("spec")
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-spec-baton-retry",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
        step_user_inputs={"spec": "[BATON ERROR] fix baton only"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "ls" in allowed_tools
    assert "edit(./.cafe/issues/issue-spec-baton-retry/blackboard.json)" not in allowed_tools
    assert "edit(./.cafe/issues/issue-spec-baton-retry/next_step.txt)" in allowed_tools
    assert "write(./.cafe/issues/issue-spec-baton-retry/blackboard.json)" not in allowed_tools
    assert "write(./.cafe/issues/issue-spec-baton-retry/next_step.txt)" in allowed_tools
    assert (
        "edit(./.cafe/issues/issue-spec-baton-retry/spec/iteration_001/output.md)"
        not in allowed_tools
    )
    assert (
        "edit(./.cafe/issues/issue-spec-baton-retry/spec/iteration_001/checklist.md)"
        not in allowed_tools
    )
    assert (
        "edit(./.cafe/issues/issue-spec-baton-retry/spec/iteration_001/questions.xml)"
        not in allowed_tools
    )


def test_generic_workflow_step_restores_develop_runtime_allowed_tools(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-develop-tools"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": [
                    "Read",
                    "Edit",
                    "Write",
                    "Grep",
                    "Glob",
                    "Bash",
                    "WebFetch",
                    "WebSearch",
                ],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-develop-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("develop", playbook["steps"]["develop"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "edit" in allowed_tools
    assert "write" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "bash" in allowed_tools
    assert "ls" in allowed_tools
    assert "web_fetch" in allowed_tools
    assert "web_search" in allowed_tools


def test_generic_workflow_step_restores_review_runtime_allowed_tools(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-review-tools"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"reviewer": {"default_agent": "Richard"}},
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "behavior": {"runtime_tool_grants": ["web_research", "git_inspection"]},
                "output_artifact": "review_feedback",
                "allowed_tools": ["Read", "Grep", "Glob", "Bash(git:*)"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("review")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-review-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"reviewer": "Richard"},
    )

    executor.execute_step("review", playbook["steps"]["review"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "ls" in allowed_tools
    assert "web_fetch" in allowed_tools
    assert "web_search" in allowed_tools
    assert "bash(git:*)" in allowed_tools
    assert "bash(git log)" in allowed_tools
    assert "bash(git diff)" in allowed_tools
    assert "bash(git show)" in allowed_tools
    assert "bash(git status)" in allowed_tools
    assert "edit(./.cafe/issues/issue-review-tools/review/iteration_001/output.md)" in allowed_tools
    assert (
        "edit(./.cafe/issues/issue-review-tools/review/iteration_001/checklist.md)" in allowed_tools
    )


def test_generic_workflow_step_restores_pr_runtime_allowed_tools(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-tools"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "input_artifacts": ["spec", "plan", "review_feedback"],
                "output_artifact": "pr_result",
                "allowed_tools": [
                    "Read",
                    "Edit",
                    "Write",
                    "Grep",
                    "Glob",
                    "Bash",
                    "WebFetch",
                    "WebSearch",
                ],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    review_file = issue_dir / "review" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    review_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    review_file.write_text("# Review\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    store.set_artifact(state, "review_feedback", str(review_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-tools",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert "read" in allowed_tools
    assert "edit" in allowed_tools
    assert "write" in allowed_tools
    assert "grep" in allowed_tools
    assert "glob" in allowed_tools
    assert "bash" in allowed_tools
    assert "ls" in allowed_tools
    assert "web_fetch" in allowed_tools
    assert "web_search" in allowed_tools
    assert "edit(./.cafe/issues/issue-pr-tools/pr/iteration_001/output.md)" in allowed_tools
    assert "edit(./.cafe/issues/issue-pr-tools/pr/iteration_001/checklist.md)" in allowed_tools


def test_generic_workflow_step_pr_does_not_require_status_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-statusless"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Nick"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("pr")
    state.handoff_summary = "Reopen PR and complete the local artifact before host-side publish."
    state.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path="spec/iteration_001/output.md",
    )
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/iteration_001/output.md",
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-statusless",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("local artifact updated"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "Nick"},
    )

    captured: dict[str, object] = {}

    def fake_execute_agent_iteration(self, **kwargs):
        captured["require_status_code"] = kwargs["require_status_code"]
        return "local artifact updated", None

    executor._execute_agent_iteration = MethodType(fake_execute_agent_iteration, executor)

    result = executor.execute_step("pr", playbook["steps"]["pr"], state)

    assert captured["require_status_code"] is False
    assert result.status_code is None
    assert not (issue_dir / "pr" / "status.json").exists()


def test_generic_workflow_step_pr_does_not_parse_status_from_response(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-no-parse"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Nick"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "behavior": {"completion": "baton"},
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("pr")
    state.handoff_summary = "Refresh the local PR artifact only."
    state.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path="spec/iteration_001/output.md",
    )
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/iteration_001/output.md",
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-no-parse",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("unused"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "Nick"},
    )

    def fake_execute(*args, **kwargs):
        return GenericPhaseExecution(
            response="confirmed",
            status_code=None,
            goto_target=None,
            context_updates={},
            events=[],
        )

    executor.generic_phase.execute = fake_execute

    result = executor.execute_step("pr", playbook["steps"]["pr"], state)

    assert result.response == "confirmed"
    assert result.status_code is None
    assert all(event.get("type") != "handoff_intent" for event in result.events)


def test_generic_workflow_step_pr_aligns_baton_when_execute_returns_needs_changes(
    tmp_path: Path, monkeypatch
) -> None:
    """align_pr_baton_after_execution must run for pr even though require_status_code is False."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-baton-integrate"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Nick"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "on": {"manual_handoff": "develop"},
            },
            "develop": {"skill": "develop", "role": "developer", "allowed_tools": ["Read"]},
        },
    }
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "pr",
                "to_owner": "agent",
                "to_step": "pr",
                "intent": "await_agent",
                "status_code": "",
                "created_at": "2026-05-11T12:00:00+08:00",
                "source": "test",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state = BlackboardStore(issue_dir).load_or_create("pr")
    state.handoff_summary = "PR feedback requires follow-up."
    state.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path="spec/iteration_001/output.md",
    )
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/iteration_001/output.md",
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-baton-integrate",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("unused"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "Nick"},
    )

    def fake_execute(*args, **kwargs):
        return GenericPhaseExecution(
            response="needs_changes",
            status_code=PhaseStatusCode.NEEDS_CHANGES,
            goto_target=None,
            context_updates={},
            events=[],
        )

    executor.generic_phase.execute = fake_execute

    result = executor.execute_step("pr", playbook["steps"]["pr"], state)

    assert result.status_code == "needs_changes"
    reloaded = BlackboardStore(issue_dir).load_or_create("pr")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_step == "develop"
    assert reloaded.handoff_contract.to_owner == HandoffOwner.AGENT
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_generic_workflow_step_pr_prompt_uses_baton_wording(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-prompt"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "Nick"}},
        "steps": {
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    state = BlackboardStore(issue_dir).load_or_create("pr")
    state.handoff_summary = "Reopen PR and complete the local artifact before host-side publish."
    state.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path="spec/iteration_001/output.md",
    )
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/iteration_001/output.md",
    )
    agent_manager = FakeAgentManager("local artifact updated")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-pr-prompt",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "Nick"},
    )

    executor.execute_step("pr", playbook["steps"]["pr"], state)

    assert any("Before finishing this step" in prompt for prompt in agent_manager.prompts)
    assert any(
        "Do NOT finish this step until ALL checklist items are marked as [x]." in prompt
        for prompt in agent_manager.prompts
    )
    assert not any("Before returning a status code" in prompt for prompt in agent_manager.prompts)


def test_generic_workflow_step_applies_phase_specific_model_per_step(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "phases.yaml").write_text(
        "spec:\n  name: Roger\n  clis:\n    - cli: codex\n      model: gpt-5.4\n"
        "plan:\n  name: David\n  clis:\n    - cli: codex\n      model: claude-opus-4.6\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_git_toplevel", lambda: tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-models"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}, "developer": {"default_agent": "David"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "plan",
                "allowed_tools": ["Read"],
                "input_artifacts": ["spec"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")

    def _mark_checklist_complete(*, streaming_output_file, **kwargs) -> None:
        iteration_dir = Path(streaming_output_file).parent
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("- [x] completed\n", encoding="utf-8")

    agent_manager = FakeAgentManager(
        ["confirmed", "confirmed"],
        on_execute=_mark_checklist_complete,
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-models",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger", "developer": "David"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    store.set_artifact(state, "spec", str(spec_file))
    executor.execute_step("plan", playbook["steps"]["plan"], state)

    assert "--model" in (agent_manager.preview_calls[0] or [])
    assert "gpt-5.4" in (agent_manager.preview_calls[0] or [])
    assert "--model" in (agent_manager.preview_calls[1] or [])
    assert "claude-opus-4.6" in (agent_manager.preview_calls[1] or [])


def test_generic_workflow_step_applies_primary_model_from_phase_chain(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "phases.yaml").write_text(
        "spec:\n  name: Roger\n  clis:\n    - cli: claude\n      model: opus\n"
        "plan:\n  name: David\n  clis:\n    - cli: claude\n      model: opus\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_git_toplevel", lambda: tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-clis-models"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}, "developer": {"default_agent": "David"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "plan",
                "allowed_tools": ["Read"],
                "input_artifacts": ["spec"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")

    def _mark_checklist_complete(*, streaming_output_file, **kwargs) -> None:
        iteration_dir = Path(streaming_output_file).parent
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("- [x] completed\n", encoding="utf-8")

    agent_manager = FakeAgentManager(
        ["confirmed", "confirmed"],
        on_execute=_mark_checklist_complete,
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-clis-models",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger", "developer": "David"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    store.set_artifact(state, "spec", str(spec_file))
    executor.execute_step("plan", playbook["steps"]["plan"], state)

    assert "--model" in (agent_manager.preview_calls[0] or [])
    assert "opus" in (agent_manager.preview_calls[0] or [])
    assert "--model" in (agent_manager.preview_calls[1] or [])
    assert "opus" in (agent_manager.preview_calls[1] or [])


def test_generic_workflow_step_develop_confirmed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-develop-ready"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed", "need_clarification"],
                "on": {"await_agent": "review", "need_clarification": "develop"},
            },
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))

    def _mark_checklist_complete(*, streaming_output_file, **kwargs) -> None:
        iteration_dir = Path(streaming_output_file).parent
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("- [x] completed\n", encoding="utf-8")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-develop-ready",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager(
            "confirmed",
            on_execute=_mark_checklist_complete,
        ),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "confirmed"


# ---------------------------------------------------------------------------
# Tests for _get_allowed_directories merging (Task 3)
# ---------------------------------------------------------------------------


def _make_minimal_executor(tmp_path, **kwargs):
    """Build a GenericWorkflowStepExecutor with minimal config for dir-merge tests."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-dirs"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {},
        "steps": {},
    }
    return GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-dirs",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("ok"),
        git_ops=FakeGitOperations(),
        role_agent_map={},
        **kwargs,
    )


def test_producer_completion_does_not_gate_on_packet_metadata(tmp_path: Path) -> None:
    executor = _make_minimal_executor(tmp_path)
    executor.generic_phase.skill_loader = SkillLoader()
    executor.playbook = {
        "playbook": {"id": "packet-validation"},
        "steps": {
            "spec": {"skill": "cafe-spec", "output_artifact": "spec"},
            "develop": {
                "skill": "cafe-develop",
                "input_artifacts": ["spec"],
            },
        },
    }
    output = tmp_path / "spec.md"
    output.write_text(
        "# Spec\n\nGOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001\n\n"
        "## Downstream Contract\n\n"
        "- Contract-Version: `2`\n"
        "- Artifact-Kind: `spec`\n",
        encoding="utf-8",
    )

    executor._validate_produced_packet_contracts(
        producer_step="spec", artifact_name="spec", output_file=output
    )


def test_producer_completion_ignores_consumer_without_packet_policy(tmp_path: Path) -> None:
    executor = _make_minimal_executor(tmp_path)
    executor.playbook = {
        "playbook": {"id": "full-input"},
        "steps": {
            "spec": {"skill": "cafe-spec", "output_artifact": "spec"},
            "develop": {
                "skill": "cafe-develop",
                "input_artifacts": ["spec"],
            },
        },
    }
    output = tmp_path / "partial-spec.md"
    output.write_text("# Legacy full-input spec\n", encoding="utf-8")

    executor._validate_produced_packet_contracts(
        producer_step="spec",
        artifact_name="spec",
        output_file=output,
    )


def test_get_allowed_directories_returns_defaults_when_no_extras(tmp_path: Path) -> None:
    """未傳 config/flag dirs 時行為與既有相同，包含 .cafe。"""
    executor = _make_minimal_executor(tmp_path)
    dirs = executor._get_allowed_directories()
    assert ".cafe" in dirs


def test_get_allowed_directories_merges_defaults_and_config_and_flag(tmp_path: Path) -> None:
    """三來源（base、config、flag）都出現在結果中，無重複。"""
    executor = _make_minimal_executor(
        tmp_path,
        config_allowed_directories=["src"],
        extra_allowed_directories=["scripts"],
    )
    dirs = executor._get_allowed_directories()
    assert ".cafe" in dirs
    assert "src" in dirs
    assert "scripts" in dirs
    assert len(dirs) == len(set(dirs))


def test_get_allowed_directories_dedupes_overlap(tmp_path: Path) -> None:
    """config 與 flag 同時含相同路徑時，結果只出現一次。"""
    executor = _make_minimal_executor(
        tmp_path,
        config_allowed_directories=["src"],
        extra_allowed_directories=["src"],
    )
    dirs = executor._get_allowed_directories()
    assert dirs.count("src") == 1


def test_get_allowed_directories_preserves_order(tmp_path: Path) -> None:
    """合併後順序：base → config → flag（保序去重）。"""
    executor = _make_minimal_executor(
        tmp_path,
        config_allowed_directories=["alpha"],
        extra_allowed_directories=["beta"],
    )
    dirs = executor._get_allowed_directories()
    base_end = dirs.index(".cafe")
    alpha_idx = dirs.index("alpha")
    beta_idx = dirs.index("beta")
    assert base_end < alpha_idx < beta_idx


def test_workflow_passes_config_allowed_directories_to_agent(tmp_path: Path, monkeypatch) -> None:
    """Executor should pass merged config and CLI dirs into agent_manager.execute."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-agent-dirs"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    agent_manager = FakeAgentManager("confirmed")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-agent-dirs",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
        config_allowed_directories=["src"],
        extra_allowed_directories=["tests"],
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert agent_manager.allowed_directories_calls[-1] == [".cafe", "src", "tests"]


def test_workflow_legacy_behavior_unchanged_when_no_config(tmp_path: Path, monkeypatch) -> None:
    """Without config/CLI dirs, agent execution should receive the base .cafe dir."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-agent-base-dirs"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    agent_manager = FakeAgentManager("confirmed")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-agent-base-dirs",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert agent_manager.allowed_directories_calls[-1] == [".cafe"]


def test_workflow_allows_selected_global_template_directory(tmp_path: Path, monkeypatch) -> None:
    """A selected template outside the worktree remains readable by the agent."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-global-template"
    research_file = tmp_path / "research.md"
    research_file.write_text("evidence", encoding="utf-8")
    global_template = (
        tmp_path.parent / f"{tmp_path.name}-global-cafe" / "templates" / "synthesis" / "evidence.md"
    )
    global_template.parent.mkdir(parents=True)
    global_template.write_text("# Global evidence\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "custom"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "synthesis": {
                "skill": "synthesis",
                "role": "developer",
                "template": "evidence",
                "output_artifact": "report",
                "allowed_tools": ["Read"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("synthesis")
    store.set_artifact(state, "research_notes", str(research_file))
    agent_manager = FakeAgentManager("await_agent")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-global-template",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    with patch(
        "cafe.phases.generic_workflow_step.TemplateManager.get_template_path",
        return_value=global_template,
    ):
        executor.execute_step("synthesis", playbook["steps"]["synthesis"], state)

    assert any(
        Path(directory).resolve() == global_template.parent.resolve()
        for directory in agent_manager.allowed_directories_calls[-1]
    )


def test_workflow_allows_auto_catalog_template_directories(tmp_path: Path, monkeypatch) -> None:
    """Auto selection grants read access to each catalog candidate directory."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-auto-global-template"
    research_file = tmp_path / "research.md"
    research_file.write_text("evidence", encoding="utf-8")
    global_template = (
        tmp_path.parent
        / f"{tmp_path.name}-auto-global-cafe"
        / "templates"
        / "synthesis"
        / "evidence.md"
    )
    global_template.parent.mkdir(parents=True)
    global_template.write_text("# Global evidence\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "custom"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "synthesis": {
                "skill": "synthesis",
                "role": "developer",
                "template": "auto",
                "output_artifact": "report",
                "allowed_tools": ["Read"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("synthesis")
    store.set_artifact(state, "research_notes", str(research_file))
    agent_manager = FakeAgentManager("await_agent")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-auto-global-template",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    with (
        patch(
            "cafe.phases.generic_workflow_step.TemplateManager.list_templates",
            return_value=[("evidence", "global")],
        ),
        patch(
            "cafe.phases.generic_workflow_step.TemplateManager.get_template_path",
            return_value=global_template,
        ),
    ):
        executor.execute_step("synthesis", playbook["steps"]["synthesis"], state)

    assert any(
        Path(directory).resolve() == global_template.parent.resolve()
        for directory in agent_manager.allowed_directories_calls[-1]
    )


def test_workflow_limits_prompt_inputs_to_step_artifacts(tmp_path: Path) -> None:
    """A skill cannot receive a blackboard artifact undeclared by its step."""
    executor = _make_minimal_executor(tmp_path)
    state = BlackboardStore(executor.issue_dir).load_or_create("synthesis")
    BlackboardStore(executor.issue_dir).set_artifact(
        state, "research_notes", str(tmp_path / "research.md")
    )
    step_def = {"skill": "synthesis", "role": "developer", "input_artifacts": []}

    with pytest.raises(ValueError, match="evidence_file"):
        executor._build_context(
            step_name="synthesis",
            step_def=step_def,
            blackboard_state=state,
            agent_name="David",
            output_file=tmp_path / "output.md",
        )


def test_build_context_materializes_agent_for_later_external_read(tmp_path: Path) -> None:
    executor = _make_minimal_executor(tmp_path)
    state = BlackboardStore(executor.issue_dir).load_or_create("plan")
    source = tmp_path / "global" / "agents" / "developer" / "David.md"
    source.parent.mkdir(parents=True)
    source.write_text("---\nname: David\n---\n\nold guidance\n", encoding="utf-8")
    output_file = executor.issue_dir / "plan" / "iteration_001" / "output.md"

    with patch.object(AgentManager, "get_agent_file_path", return_value=str(source)):
        context = executor._build_context(
            step_name="plan",
            step_def={"skill": "cafe-plan", "role": "developer"},
            blackboard_state=state,
            agent_name="David",
            output_file=output_file,
        )

    source.write_text("---\nname: David\n---\n\nnew guidance\n", encoding="utf-8")

    materialized = Path(context["agent_file"])
    assert materialized != source
    assert "old guidance" in materialized.read_text(encoding="utf-8")


def test_build_context_resolves_builtin_agent_from_declared_qa_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    issue_dir = tmp_path / ".cafe" / "issues" / "qa-fallback"
    playbook = {
        "playbook": {"id": "qa-fallback"},
        "roles": {"qa": {"default_agent": "Quinn"}},
        "steps": {
            "qa": {
                "skill": "cafe-plan",
                "role": "qa",
                "output_artifact": "qa_feedback",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="qa-fallback",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={},
    )
    state = BlackboardStore(executor.issue_dir).load_or_create("qa")

    with patch.object(Path, "home", return_value=tmp_path / "empty-home"):
        _source, expected_guidance = AgentManager.read_agent_file("Quinn", "qa")
        result = executor.execute_step("qa", playbook["steps"]["qa"], state)

    assert result.status_code == "confirmed"
    materialized = issue_dir / "qa" / "iteration_001" / "context_agent_file.md"
    assert materialized.read_text(encoding="utf-8") == expected_guidance


def test_build_context_resolves_global_agent_from_custom_declared_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    global_home = tmp_path / "global-home"
    global_agent = global_home / ".cafe" / "agents" / "security" / "Avery.md"
    global_agent.parent.mkdir(parents=True)
    expected_guidance = "---\nname: Avery\n---\n\nReview security boundaries.\n"
    global_agent.write_text(expected_guidance, encoding="utf-8")
    executor = _make_minimal_executor(tmp_path)
    state = BlackboardStore(executor.issue_dir).load_or_create("audit")
    output_file = executor.issue_dir / "audit" / "iteration_001" / "output.md"

    with patch.object(Path, "home", return_value=global_home):
        context = executor._build_context(
            step_name="audit",
            step_def={"skill": "cafe-plan", "role": "security"},
            blackboard_state=state,
            agent_name="Avery",
            output_file=output_file,
        )

    assert Path(context["agent_file"]).read_text(encoding="utf-8") == expected_guidance


def test_workflow_limits_checklist_inputs_to_step_artifacts(tmp_path: Path) -> None:
    """Checklist generation uses the same declared artifact boundary as the prompt."""
    executor = _make_minimal_executor(tmp_path)
    state = BlackboardStore(executor.issue_dir).load_or_create("synthesis")
    BlackboardStore(executor.issue_dir).set_artifact(
        state, "research_notes", str(tmp_path / "research.md")
    )
    step_def = {"skill": "synthesis", "role": "developer", "input_artifacts": []}

    with pytest.raises(ValueError, match="evidence_file"):
        executor._generate_checklist(
            step_name="synthesis",
            skill_name="synthesis",
            agent_name="David",
            step_def=step_def,
            blackboard_state=state,
            checklist_file=tmp_path / "checklist.md",
            output_file=tmp_path / "output.md",
            questions_xml_file=tmp_path / "questions.xml",
        )


def _plan_step_playbook() -> dict:
    return {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "plan": {
                "skill": "plan",
                "role": "developer",
                "output_artifact": "plan",
                "allowed_tools": ["Read"],
                "valid_intents": ["ready_for_review", "need_clarification", "confirmed"],
                "on": {
                    "confirm_output": "plan",
                    "need_clarification": "plan",
                    "await_agent": "develop",
                },
            },
        },
    }


def _write_plan_prereq_artifacts(issue_dir: Path) -> None:
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    (spec_dir / "iteration.json").write_text('{"iteration": 1}', encoding="utf-8")


def test_plan_non_interactive_ready_for_review_hands_off_to_user(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-plan-noninteractive-review"
    _write_plan_prereq_artifacts(issue_dir)
    playbook = _plan_step_playbook()
    state = BlackboardStore(issue_dir).load_or_create("plan")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-plan-noninteractive-review",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("ready_for_review"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=False,
    )

    result = executor.execute_step("plan", playbook["steps"]["plan"], state)

    assert result.status_code == "ready_for_review"
    assert result.auto_continue is False
    reloaded = BlackboardStore(issue_dir).load_or_create("plan")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.to_step == "user"


def test_plan_non_interactive_need_clarification_hands_off_to_user(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-plan-noninteractive-clarify"
    _write_plan_prereq_artifacts(issue_dir)
    playbook = _plan_step_playbook()
    state = BlackboardStore(issue_dir).load_or_create("plan")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-plan-noninteractive-clarify",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("need_clarification"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=False,
    )

    result = executor.execute_step("plan", playbook["steps"]["plan"], state)

    assert result.status_code == "need_clarification"
    assert result.auto_continue is False
    reloaded = BlackboardStore(issue_dir).load_or_create("plan")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.intent == HandoffIntent.NEED_CLARIFICATION


def test_develop_checklist_prefers_review_feedback_over_pr_result(
    tmp_path: Path, monkeypatch
) -> None:
    """Develop correction checklist uses review_feedback when both artifacts exist."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-develop-feedback"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    review_file = issue_dir / "review" / "iteration_001" / "output.md"
    pr_file = issue_dir / "pr" / "iteration_001" / "output.md"
    for path in (spec_file, plan_file, review_file, pr_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.parent.parent.name}\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    store.set_artifact(state, "review_feedback", str(review_file))
    store.set_artifact(state, "pr_result", str(pr_file))

    skill_dir = tmp_path / ".cafe" / "skills" / "cafe-develop"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: cafe-develop
description: test skill
workflow:
  prompt_inputs:
    - artifacts: [review_feedback, pr_result]
      placeholder: feedback_file
      required: false
  checklist:
    variants:
      - when: {feedback: true}
        sections: [{reference: execution.md}]
    include_role_guidance: false
---
""",
        encoding="utf-8",
    )
    (skill_dir / "references" / "execution.md").write_text(
        "[ ] Use {feedback_file}\n", encoding="utf-8"
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-develop-feedback",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )
    executor.execute_step("develop", playbook["steps"]["develop"], state)

    checklist = (issue_dir / "develop" / "iteration_001" / "checklist.md").read_text(
        encoding="utf-8"
    )
    assert "review/iteration_001/output.md" in checklist
    assert "pr/iteration_001/output.md" not in checklist


def _write_skill_with_basic_principles(
    tmp_path: Path,
    *,
    skill_name: str,
    basic_principles: str,
) -> None:
    skill_dir = tmp_path / ".cafe" / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {skill_name}
description: test skill
workflow:
  checklist:
    variants:
      - when: {{}}
        sections: [{{optional_checklist: basic_principles.md}}]
    include_role_guidance: false
---
""",
        encoding="utf-8",
    )
    references = skill_dir / "references"
    references.mkdir(parents=True, exist_ok=True)
    (references / "basic_principles.md").write_text(
        basic_principles,
        encoding="utf-8",
    )


def test_spec_checklist_loads_custom_basic_principles_reference(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-spec-basic-principles"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "spec": {
                "skill": "spec",
                "role": "developer",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    _write_skill_with_basic_principles(
        tmp_path,
        skill_name="cafe-spec",
        basic_principles="- Stay scoped\n- Keep behavior stable",
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-spec-basic-principles",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )
    executor.phase_dir = issue_dir / "spec"
    executor.iteration = 1
    output_file = issue_dir / "output.md"
    checklist_file = issue_dir / "checklist.md"
    questions_xml_file = issue_dir / "questions.xml"

    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor._generate_checklist(
        step_name="spec",
        skill_name="spec",
        agent_name="David",
        step_def={"skill": "spec"},
        blackboard_state=state,
        checklist_file=checklist_file,
        output_file=output_file,
        questions_xml_file=questions_xml_file,
    )

    checklist = checklist_file.read_text(encoding="utf-8")
    assert "Stay scoped" in checklist
    assert "Keep behavior stable" in checklist


def test_spec_checklist_omits_missing_basic_principles_reference(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-spec-no-basic-principles"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "spec": {
                "skill": "spec",
                "role": "developer",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-spec-no-basic-principles",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("confirmed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )
    executor.phase_dir = issue_dir / "spec"
    executor.iteration = 1
    output_file = issue_dir / "output.md"
    checklist_file = issue_dir / "checklist.md"
    questions_xml_file = issue_dir / "questions.xml"
    state = BlackboardStore(issue_dir).load_or_create("spec")

    executor._generate_checklist(
        step_name="spec",
        skill_name="spec",
        agent_name="David",
        step_def={"skill": "spec"},
        blackboard_state=state,
        checklist_file=checklist_file,
        output_file=output_file,
        questions_xml_file=questions_xml_file,
    )

    assert checklist_file.read_text(encoding="utf-8") == ""


def test_update_iteration_history_preserves_model_and_stats_on_second_call(
    tmp_path: Path,
) -> None:
    """Second _update_iteration_history without model/token_usage must not erase prior fields."""
    import json

    from cafe.core.phase import Phase
    from cafe.core.status_codes import PhaseStatusCode
    from cafe.core.types import TokenUsage

    class ConcretePhase(Phase):
        def __init__(self, phase_dir: Path) -> None:
            super().__init__()
            self.phase_dir = phase_dir
            self.iteration = 1

        def execute(self):
            pass

    phase_dir = tmp_path / "pr"
    phase_dir.mkdir()
    phase = ConcretePhase(phase_dir)
    iter_dir = phase._get_iteration_dir(1)
    iter_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(phase, "_append_iteration_index"):
        phase._update_iteration_history(
            phase_specific_data={
                "response": "agent output",
                "permission_denials": [],
                "streaming_log": [],
            },
            prompt="test prompt",
            agent_cli="claude",
            model="claude-haiku-4-5-20251001",
            token_usage=TokenUsage(
                input_tokens=100,
                output_tokens=50,
                duration_ms=5000,
                duration_api_ms=4800,
            ),
        )
        phase._update_iteration_history(
            phase_specific_data={
                "pr_number": "159",
                "pr_url": "https://example.com/pr/159",
                "branch": "issue158",
            },
            status_code=PhaseStatusCode.READY_FOR_REVIEW,
        )

    context = json.loads((iter_dir / "iteration.json").read_text(encoding="utf-8"))
    assert context["model"] == "claude-haiku-4-5-20251001"
    assert context["stats"]["input_tokens"] == 100
    assert context["stats"]["output_tokens"] == 50
    assert context["cli"] == "claude"
    assert context["prompt"] == "test prompt"
    assert context["status_code"] == "ready_for_review"
    assert context["pr_number"] == "159"


# ---------------------------------------------------------------------------
# Tests for no_changes_needed playbook-driven routing (Issue #301)
# ---------------------------------------------------------------------------


def _develop_step_playbook_with_no_changes_target(no_changes_target: str | None) -> dict:
    """Build a minimal develop-step playbook with configurable no_changes_needed routing."""
    on_map: dict = {"await_agent": "review", "manual_handoff": "pr"}
    if no_changes_target is not None:
        on_map["no_changes_needed"] = no_changes_target
    return {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["no_changes_needed", "confirmed"],
                "on": on_map,
            },
            "review": {
                "skill": "review",
                "role": "developer",
                "output_artifact": "review_feedback",
                "allowed_tools": ["Read"],
                "on": {"await_agent": "_done"},
            },
            "pr": {
                "skill": "pr",
                "role": "developer",
                "output_artifact": "pr_result",
                "allowed_tools": ["Read"],
                "on": {"await_agent": "_done"},
            },
        },
    }


def _write_develop_prereq_artifacts(issue_dir: Path) -> None:
    for phase in ("spec", "plan"):
        phase_dir = issue_dir / phase / "iteration_001"
        phase_dir.mkdir(parents=True, exist_ok=True)
        (phase_dir / "output.md").write_text(f"# {phase.title()}\n", encoding="utf-8")
        (phase_dir / "iteration.json").write_text('{"iteration": 1}', encoding="utf-8")


def test_no_changes_needed_non_interactive_auto_routes_to_playbook_target(
    tmp_path: Path, monkeypatch
) -> None:
    """非互動模式且 playbook 映射為 agent step 時，直接寫 AGENT handoff 自動繼續。"""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-ncn-noninteractive-auto"
    _write_develop_prereq_artifacts(issue_dir)
    playbook = _develop_step_playbook_with_no_changes_target("review")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-ncn-noninteractive-auto",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("no_changes_needed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=False,
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "no_changes_needed"
    reloaded = BlackboardStore(issue_dir).load_or_create("develop")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.AGENT
    assert reloaded.handoff_contract.to_step == "review"
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_no_changes_needed_non_interactive_pauses_when_playbook_target_is_user(
    tmp_path: Path, monkeypatch
) -> None:
    """非互動模式且 playbook 映射為 user 時，寫 USER handoff 暫停。"""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-ncn-noninteractive-user"
    _write_develop_prereq_artifacts(issue_dir)
    playbook = _develop_step_playbook_with_no_changes_target("user")
    state = BlackboardStore(issue_dir).load_or_create("develop")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-ncn-noninteractive-user",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("no_changes_needed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=False,
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "no_changes_needed"
    reloaded = BlackboardStore(issue_dir).load_or_create("develop")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.to_step == "user"


def test_no_changes_needed_non_interactive_pauses_when_mapping_absent(
    tmp_path: Path, monkeypatch
) -> None:
    """非互動模式且 playbook 無 no_changes_needed 映射時，保持向後相容並暫停。"""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-ncn-noninteractive-absent"
    _write_develop_prereq_artifacts(issue_dir)
    playbook = _develop_step_playbook_with_no_changes_target(None)
    state = BlackboardStore(issue_dir).load_or_create("develop")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-ncn-noninteractive-absent",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("no_changes_needed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=False,
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "no_changes_needed"
    reloaded = BlackboardStore(issue_dir).load_or_create("develop")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.to_step == "user"


def test_no_changes_needed_interactive_always_pauses_regardless_of_playbook(
    tmp_path: Path, monkeypatch
) -> None:
    """互動模式時，無論 playbook 映射為何，一律暫停等待使用者的 agree/disagree 決策。"""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-ncn-interactive-pause"
    _write_develop_prereq_artifacts(issue_dir)
    playbook = _develop_step_playbook_with_no_changes_target("review")
    state = BlackboardStore(issue_dir).load_or_create("develop")

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-ncn-interactive-pause",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("no_changes_needed"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        interactive=True,
    )

    result = executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert result.status_code == "no_changes_needed"
    reloaded = BlackboardStore(issue_dir).load_or_create("develop")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert reloaded.handoff_contract.to_step == "user"


def _minimal_spec_executor(
    tmp_path: Path,
    *,
    agent_manager: FakeAgentManager,
) -> GenericWorkflowStepExecutor:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "output_artifact": "spec",
                "valid_intents": ["confirmed"],
            }
        },
    }
    return GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-resume-input",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )


def test_resolve_iteration_user_input_first_start_unchanged(tmp_path: Path) -> None:
    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.phase_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input" / "spec"
    executor.iteration = 1
    executor._step_agent_name = "Roger"
    executor.step_user_inputs["spec"] = "Cold-start requirements"

    assert executor._resolve_iteration_user_input("spec") == "Cold-start requirements"


def test_resolve_iteration_user_input_same_cli_session_keeps_real_input(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    spec_dir = issue_dir / "spec"
    prev_iter = spec_dir / "iteration_001"
    prev_iter.mkdir(parents=True)
    (prev_iter / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "session-1",
                "end_time": "2026-05-23T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.phase_dir = spec_dir
    executor.iteration = 2
    executor._step_agent_name = "Roger"
    executor.step_user_inputs["spec"] = "Please apply the feedback"

    assert executor._resolve_iteration_user_input("spec") == "Please apply the feedback"


def test_resolve_iteration_user_input_different_session_returns_full_candidate(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    spec_dir = issue_dir / "spec"
    prev_iter = spec_dir / "iteration_001"
    prev_iter.mkdir(parents=True)
    (prev_iter / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "old-session",
                "end_time": "2026-05-23T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    manager = FakeAgentManager("confirmed")
    manager.agent.config.session_id = "new-session"

    executor = _minimal_spec_executor(tmp_path, agent_manager=manager)
    executor.phase_dir = spec_dir
    executor.iteration = 2
    executor._step_agent_name = "Roger"
    candidate = "Clarification answer"
    executor.step_user_inputs["spec"] = candidate

    assert executor._resolve_iteration_user_input("spec") == candidate


def test_resolve_iteration_user_input_loads_prewritten_user_input_file(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    spec_dir = issue_dir / "spec"
    current_iter = spec_dir / "iteration_002"
    current_iter.mkdir(parents=True)
    (current_iter / "user_input.md").write_text(
        "Answer from workflow --user-input", encoding="utf-8"
    )
    prev_iter = spec_dir / "iteration_001"
    prev_iter.mkdir(parents=True)
    (prev_iter / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "old-session",
                "end_time": "2026-05-23T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    manager = FakeAgentManager("confirmed")
    manager.agent.config.session_id = "new-session"

    executor = _minimal_spec_executor(tmp_path, agent_manager=manager)
    executor.phase_dir = spec_dir
    executor.iteration = 2
    executor._step_agent_name = "Roger"

    assert executor._resolve_iteration_user_input("spec") == "Answer from workflow --user-input"


def test_resolve_iteration_user_input_interrupted_iteration_reuse(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    spec_dir = issue_dir / "spec"
    current_iter = spec_dir / "iteration_001"
    current_iter.mkdir(parents=True)
    (current_iter / "iteration.json").write_text(
        json.dumps({"cli": "codex", "session_id": "session-1"}),
        encoding="utf-8",
    )

    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.phase_dir = spec_dir
    executor.iteration = 1
    executor._step_agent_name = "Roger"

    assert executor._resolve_iteration_user_input("spec") == CONTINUE_USER_INPUT


def test_apply_resume_to_runtime_context_keeps_real_input(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    spec_dir = issue_dir / "spec"
    prev_iter = spec_dir / "iteration_001"
    prev_iter.mkdir(parents=True)
    (prev_iter / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "session-1",
                "end_time": "2026-05-23T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.phase_dir = spec_dir
    executor.iteration = 2
    executor._step_agent_name = "Roger"

    updated = executor._apply_resume_to_runtime_context(
        {"user_input": "Long clarification that should not be replayed"},
        "spec",
    )

    assert updated["user_input"] == "Long clarification that should not be replayed"
    assert (
        executor._get_resolved_iteration_user_input("spec")
        == "Long clarification that should not be replayed"
    )


def test_apply_resume_to_runtime_context_projects_declared_scope_for_interrupted_run(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    spec_dir = issue_dir / "spec"
    current_iter = spec_dir / "iteration_001"
    current_iter.mkdir(parents=True)
    (current_iter / "iteration.json").write_text(
        json.dumps({"cli": "codex", "session_id": "session-1"}), encoding="utf-8"
    )

    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.phase_dir = spec_dir
    executor.iteration = 1
    executor._step_agent_name = "Roger"
    executor.playbook["steps"]["spec"]["input_artifacts"] = ["batch_scope"]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    replacement = issue_dir / "inputs" / "batch-2.md"
    BlackboardStore(issue_dir).set_artifact(state, "batch_scope", str(replacement))

    updated = executor._apply_resume_to_runtime_context(
        {"develop_file": ".cafe/issues/demo/develop/output.md"},
        "spec",
        state,
    )

    assert updated["resume_input_artifacts"] == f"- batch_scope: {replacement}"


def test_apply_resume_to_runtime_context_omits_artifacts_on_fresh_run(tmp_path: Path) -> None:
    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.phase_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input" / "spec"
    executor.phase_dir.mkdir(parents=True)
    executor.iteration = 1
    executor._step_agent_name = "Roger"

    updated = executor._apply_resume_to_runtime_context(
        {"develop_file": ".cafe/issues/demo/develop/output.md"},
        "spec",
    )

    assert "resume_input_artifacts" not in updated


def test_apply_resume_to_runtime_context_excludes_undeclared_and_fallback_scope(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    spec_dir = issue_dir / "spec"
    current_iter = spec_dir / "iteration_001"
    current_iter.mkdir(parents=True)
    (current_iter / "iteration.json").write_text(
        json.dumps({"cli": "codex", "session_id": "session-1"}), encoding="utf-8"
    )

    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.phase_dir = spec_dir
    executor.iteration = 1
    executor._step_agent_name = "Roger"
    executor.playbook["steps"]["spec"]["input_artifacts"] = ["batch_scope"]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    current_scope = issue_dir / "inputs" / "current.md"
    historical = issue_dir / "history" / "old.md"
    store = BlackboardStore(issue_dir)
    store.set_artifact(state, "batch_scope", str(current_scope))
    store.set_artifact(state, "historical_output", str(historical))

    updated = executor._apply_resume_to_runtime_context(
        {
            "feedback_file": "review.md",
            "plan_file": "plan.md",
            "develop_file": "develop.md",
            "spec_file": "spec.md",
        },
        "spec",
        state,
    )

    assert updated["resume_input_artifacts"] == f"- batch_scope: {current_scope}"
    assert "historical_output" not in updated["resume_input_artifacts"]
    assert "develop_file" not in updated["resume_input_artifacts"]


def test_apply_resume_to_runtime_context_uses_latest_declared_artifact_path(tmp_path: Path) -> None:
    """U3 — interrupted runs read the current Blackboard path, not stale prompt data."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-custom-artifacts"
    phase_dir = issue_dir / "synthesize"
    current = phase_dir / "iteration_001"
    current.mkdir(parents=True)
    (current / "iteration.json").write_text(
        json.dumps({"cli": "codex", "session_id": "session-1"}), encoding="utf-8"
    )
    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.playbook = {
        "steps": {
            "synthesize": {
                "skill": "synthesis",
                "role": "developer",
                "input_artifacts": ["batch_scope"],
            }
        }
    }
    executor.phase_dir = phase_dir
    executor.iteration = 1
    executor._step_agent_name = "David"
    state = BlackboardStore(issue_dir).load_or_create("synthesize")
    replacement = issue_dir / "inputs" / "batch-2.md"
    BlackboardStore(issue_dir).set_artifact(state, "batch_scope", str(replacement))

    updated = executor._apply_resume_to_runtime_context(
        {"evidence_file": "batch-1.md"}, "synthesize", state
    )

    assert updated["resume_input_artifacts"] == f"- batch_scope: {replacement}"


def test_apply_resume_to_runtime_context_omits_scope_for_completed_correction_iteration(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    spec_dir = issue_dir / "spec"
    previous = spec_dir / "iteration_001"
    previous.mkdir(parents=True)
    (previous / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "session-1",
                "end_time": "2026-05-23T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    executor = _minimal_spec_executor(tmp_path, agent_manager=FakeAgentManager("confirmed"))
    executor.phase_dir = spec_dir
    executor.iteration = 2
    executor._step_agent_name = "Roger"
    executor.playbook["steps"]["spec"]["input_artifacts"] = []
    state = BlackboardStore(issue_dir).load_or_create("spec")

    updated = executor._apply_resume_to_runtime_context(
        {"user_input": "Apply review feedback."}, "spec", state
    )

    assert "resume_input_artifacts" not in updated
    assert updated["user_input"] == "Apply review feedback."


def test_execute_step_same_session_resume_keeps_real_input_in_prompt_and_user_input_md(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-exec"
    spec_dir = issue_dir / "spec"
    prev_iter = spec_dir / "iteration_001"
    prev_iter.mkdir(parents=True)
    (prev_iter / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "session-1",
                "end_time": "2026-05-23T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    (spec_dir / "iteration_002").mkdir(parents=True)
    (spec_dir / "iteration_002" / "user_input.md").write_text(
        "Long clarification that should appear in prompt",
        encoding="utf-8",
    )

    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": {"1": "spec_first", "default": "spec_first"},
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    manager = FakeAgentManager("confirmed")
    manager.agent.config.session_id = "session-1"

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-resume-exec",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
    )

    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor.execute_step("spec", playbook["steps"]["spec"], state)

    assert manager.prompts
    prompt = manager.prompts[0]
    assert "Long clarification that should appear in prompt" in prompt
    assert "Current user input for this iteration:" in prompt
    user_input_file = spec_dir / "iteration_002" / "user_input.md"
    assert (
        user_input_file.read_text(encoding="utf-8")
        == "Long clarification that should appear in prompt"
    )


def test_execute_step_interrupted_fresh_session_surfaces_declared_current_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-artifacts"
    develop_dir = issue_dir / "develop"
    current_iter = develop_dir / "iteration_001"
    current_iter.mkdir(parents=True)
    (current_iter / "iteration.json").write_text(
        json.dumps({"cli": "codex", "session_id": "interrupted-session"}),
        encoding="utf-8",
    )

    replacement = issue_dir / "inputs" / "batch-2.md"
    replacement.parent.mkdir(parents=True)
    replacement.write_text("current batch", encoding="utf-8")
    historical = issue_dir / "history" / "batch-1.md"
    historical.parent.mkdir(parents=True)
    historical.write_text("already completed", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    state.handoff_summary = "Continue the old batch."
    store.set_artifact(state, "batch_scope", str(replacement))
    store.set_artifact(state, "historical_output", str(historical))

    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "cafe-develop",
                "role": "developer",
                "input_artifacts": ["batch_scope"],
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "alignment": {"enabled": False},
                "on": {"await_agent": "_done"},
            }
        },
    }
    manager = FakeAgentManager("confirmed")
    manager.agent.config.session_id = "session-1"

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-resume-artifacts",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("develop", playbook["steps"]["develop"], state)

    assert manager.prompts
    prompt = manager.prompts[0]
    assert "Current resume scope (declared step inputs):" in prompt
    scope = prompt.split("Current resume scope (declared step inputs):", maxsplit=1)[1]
    scope = scope.split("Current user input for this iteration:", maxsplit=1)[0]
    assert str(replacement) in scope
    assert str(historical) not in scope


def _make_alignment_executor(tmp_path: Path, issue_name: str, step_def: dict, user_input: str):
    issue_dir = tmp_path / ".cafe" / "issues" / issue_name
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {"develop": step_def},
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    agent_manager = FakeAgentManager("confirmed")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name=issue_name,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
        step_user_inputs={"develop": user_input},
    )
    return issue_dir, playbook, state, agent_manager, executor


def test_agent_baton_rejects_intent_not_exposed_by_step(tmp_path: Path) -> None:
    step_def = {
        "skill": "develop",
        "role": "developer",
        "output_artifact": "code",
        "on": {"await_agent": "_done", "need_clarification": "develop"},
    }
    issue_dir, _, _, _, executor = _make_alignment_executor(
        tmp_path,
        "issue-baton-effective-intents",
        step_def,
        "",
    )
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "to_owner": "user",
                "to_step": "user",
                "intent": "alignment_checkpoint",
            }
        ),
        encoding="utf-8",
    )

    assert not executor._agent_wrote_baton(
        "develop",
        step_def,
    )


def test_alignment_gate_skipped_without_alignment_block(tmp_path: Path, monkeypatch) -> None:
    """Opt-in gate: a step with no `alignment:` block does not pause, even when
    policy triggers (e.g. roadmap-scope changes) would otherwise fire — the agent
    runs normally instead."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "strategic_context.yaml").write_text(
        """
version: 1
mandate:
  axes:
    product_scope:
      level: escalate
      grounds: [roadmap]
""",
        encoding="utf-8",
    )
    step_def = {
        "skill": "develop",
        "role": "developer",
        "output_artifact": "code",
        "allowed_tools": ["Read"],
        "on": {"await_agent": "_done", "alignment_checkpoint": "develop"},
    }
    issue_dir, playbook, state, agent_manager, executor = _make_alignment_executor(
        tmp_path, "issue-align-optin-skip", step_def, "This changes roadmap scope."
    )

    result = executor.execute_step("develop", step_def, state)

    assert result.status_code != "alignment_checkpoint"
    assert agent_manager.prompts != []
    assert not (issue_dir / "develop" / "iteration_001" / "alignment_request.json").exists()


def test_alignment_gate_requires_missing_strategic_context_once(
    tmp_path: Path, monkeypatch
) -> None:
    """Missing strategic_context.yaml pauses with a document requirement; a prior
    unblocking decision suppresses the repeat requirement for the issue."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "strategic_context.yaml").unlink()
    step_def = {
        "skill": "develop",
        "role": "developer",
        "output_artifact": "code",
        "allowed_tools": ["Read"],
        "alignment": {},  # opt into the gate (empty block = enabled with defaults)
        "on": {"await_agent": "_done", "alignment_checkpoint": "develop"},
    }
    issue_dir, playbook, state, agent_manager, executor = _make_alignment_executor(
        tmp_path, "issue-align-missing-ctx", step_def, "Fix a small bug."
    )

    result = executor.execute_step("develop", step_def, state)

    assert result.status_code == "alignment_checkpoint"
    payload = json.loads(
        (issue_dir / "develop" / "iteration_001" / "alignment_request.json").read_text(
            encoding="utf-8"
        )
    )
    categories = [
        req["category"] for req in payload.get("strategic_document_update_requirements", [])
    ]
    assert "strategic_context" in categories

    store = BlackboardStore(issue_dir)
    store.record_event(
        state,
        "alignment_decision",
        {"step": "develop", "decision": "proceed", "unblocks_execution": True},
    )
    issue_dir2, playbook2, state2, agent_manager2, executor2 = _make_alignment_executor(
        tmp_path, "issue-align-missing-ctx", step_def, "Fix a small bug."
    )
    result2 = executor2.execute_step("develop", step_def, state2)

    assert result2.status_code != "alignment_checkpoint"
    assert agent_manager2.prompts


def test_alignment_gate_explicit_opt_out_skips(tmp_path: Path, monkeypatch) -> None:
    """`alignment: {enabled: false}` remains an explicit opt-out."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "strategic_context.yaml").write_text(
        """
version: 1
mandate:
  axes:
    product_scope:
      level: escalate
      grounds: [roadmap]
""",
        encoding="utf-8",
    )
    step_def = {
        "skill": "develop",
        "role": "developer",
        "output_artifact": "code",
        "allowed_tools": ["Read"],
        "alignment": {"enabled": False},
        "on": {"await_agent": "_done"},
    }
    issue_dir, playbook, state, agent_manager, executor = _make_alignment_executor(
        tmp_path, "issue-align-opt-out", step_def, "This changes roadmap scope."
    )

    result = executor.execute_step("develop", step_def, state)

    assert result.status_code != "alignment_checkpoint"
    assert agent_manager.prompts


def test_completed_correction_selects_new_session_by_default(tmp_path: Path) -> None:
    executor = _minimal_spec_executor(
        tmp_path,
        agent_manager=FakeAgentManager("confirmed"),
    )
    executor.phase_dir = executor.issue_dir / "spec"
    previous = executor.phase_dir / "iteration_001"
    previous.mkdir(parents=True)
    (previous / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "old-session",
                "end_time": "2026-07-30T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    executor.iteration = 2

    continuation = executor._select_session_continuation(
        agent_name="Roger",
        step_def=executor.playbook["steps"]["spec"],
    )

    assert continuation.policy == SessionContinuationPolicy.NEW


def test_completed_correction_ignores_resume_override(tmp_path: Path) -> None:
    executor = _minimal_spec_executor(
        tmp_path,
        agent_manager=FakeAgentManager("confirmed"),
    )
    executor.phase_dir = executor.issue_dir / "spec"
    previous = executor.phase_dir / "iteration_001"
    previous.mkdir(parents=True)
    (previous / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "old-session",
                "end_time": "2026-07-30T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    executor.iteration = 2
    step_def = {
        **executor.playbook["steps"]["spec"],
        "correction_session": "resume",
    }

    continuation = executor._select_session_continuation(
        agent_name="Roger",
        step_def=step_def,
    )

    assert continuation.policy == SessionContinuationPolicy.NEW


def test_incomplete_iteration_selects_exact_resume(tmp_path: Path) -> None:
    executor = _minimal_spec_executor(
        tmp_path,
        agent_manager=FakeAgentManager("confirmed"),
    )
    executor.phase_dir = executor.issue_dir / "spec"
    current = executor.phase_dir / "iteration_001"
    current.mkdir(parents=True)
    (current / "iteration.json").write_text(
        json.dumps({"cli": "codex", "session_id": "interrupted-session"}),
        encoding="utf-8",
    )
    executor.iteration = 1

    continuation = executor._select_session_continuation(
        agent_name="Roger",
        step_def=executor.playbook["steps"]["spec"],
    )

    assert continuation.policy == SessionContinuationPolicy.RESUME_EXACT
    assert continuation.session_id == "interrupted-session"


def test_interrupted_correction_preserves_partial_output_before_resume(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input"
    phase_dir = issue_dir / "spec"
    previous = phase_dir / "iteration_001"
    current = phase_dir / "iteration_002"
    previous.mkdir(parents=True)
    current.mkdir(parents=True)
    (previous / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "old-session",
                "end_time": "2026-07-30T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    (previous / "output.md").write_text("completed output\n", encoding="utf-8")
    (current / "iteration.json").write_text(
        json.dumps({"cli": "codex", "session_id": "session-1"}),
        encoding="utf-8",
    )
    (current / "output.md").write_text("partial interrupted output\n", encoding="utf-8")
    (current / "checklist.md").write_text(
        "[x] completed\n[ ] partial progress\n",
        encoding="utf-8",
    )

    def assert_partial_output_preserved(**kwargs) -> None:
        assert (current / "output.md").read_text(encoding="utf-8") == (
            "partial interrupted output\n"
        )
        assert (current / "checklist.md").read_text(encoding="utf-8") == (
            "[x] completed\n[ ] partial progress\n"
        )
        (current / "checklist.md").write_text(
            "[x] completed\n[x] partial progress\n",
            encoding="utf-8",
        )

    manager = FakeAgentManager(
        "confirmed",
        on_execute=assert_partial_output_preserved,
    )
    executor = _minimal_spec_executor(tmp_path, agent_manager=manager)
    state = BlackboardStore(issue_dir).load_or_create("spec")

    executor.execute_step("spec", executor.playbook["steps"]["spec"], state)


def test_same_invocation_second_agent_call_resumes_fresh_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    class RecordingManager(FakeAgentManager):
        def __init__(self):
            super().__init__(["confirmed", "confirmed"])
            self.continuations = []

        def execute(self, *args, continuation=None, **kwargs):
            self.continuations.append(continuation)
            return super().execute(*args, **kwargs)

        def get_last_cli(self):
            return AgentCLI.CODEX

        def get_last_session_id(self):
            return "fresh-session"

    manager = RecordingManager()
    executor = _minimal_spec_executor(tmp_path, agent_manager=manager)
    executor.phase_dir = executor.issue_dir / "spec"
    executor.iteration = 1
    executor._session_continuation = SessionContinuation.new()

    for prompt in ("first", "hook retry"):
        executor._execute_agent_iteration(
            agent_name="Roger",
            prompt=prompt,
            user_input="workflow execute",
            valid_intents=[PhaseStatusCode.CONFIRMED],
            require_status_code=False,
            phase_specific_data={"step_name": "spec"},
        )

    assert [item.policy for item in manager.continuations] == [
        SessionContinuationPolicy.NEW,
        SessionContinuationPolicy.RESUME_EXACT,
    ]
    assert manager.continuations[1].session_id == "fresh-session"


def test_after_execute_retry_accumulates_raw_iteration_telemetry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    class RetryOnce:
        name = "RetryOnce"
        calls = 0

        def run(self, **kwargs):
            self.__class__.calls += 1
            return HookResult(retry_requested=self.__class__.calls == 1)

    class UsageManager(FakeAgentManager):
        def __init__(self):
            super().__init__(["confirmed", "confirmed"])
            self.usages = iter(
                [
                    TokenUsage(
                        input_tokens=10,
                        cache_write_input_tokens=4,
                        reasoning_output_tokens=2,
                    ),
                    TokenUsage(
                        input_tokens=20,
                        cache_write_input_tokens=6,
                        reasoning_output_tokens=3,
                    ),
                ]
            )

        def execute(self, *args, continuation=None, phase_name=None, **kwargs):
            self.prompts.append(args[1])
            return (
                next(self._responses),
                next(self.usages),
                [],
                [],
                [],
                "gpt-test",
            )

        def get_last_cli(self):
            return AgentCLI.CODEX

        def get_last_session_id(self):
            return "fresh-session"

    RetryOnce.calls = 0
    manager = UsageManager()
    executor = _minimal_spec_executor(tmp_path, agent_manager=manager)
    executor.generic_phase.hook_registry["RetryOnce"] = RetryOnce
    step_def = {
        **executor.playbook["steps"]["spec"],
        "hooks": {"after_execute": ["RetryOnce"]},
    }
    executor.playbook["steps"]["spec"] = step_def
    state = BlackboardStore(executor.issue_dir).load_or_create("spec")

    executor.execute_step("spec", step_def, state)

    context = json.loads(
        (executor.issue_dir / "spec" / "iteration_001" / "iteration.json").read_text(
            encoding="utf-8"
        )
    )
    assert context["stats"]["input_tokens"] == 30
    assert context["stats"]["cache_write_input_tokens"] == 10
    assert context["stats"]["reasoning_output_tokens"] == 5


def test_same_invocation_baton_retry_resumes_actual_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    class RecordingManager(FakeAgentManager):
        def __init__(self):
            super().__init__(["confirmed", "confirmed"])
            self.continuations = []

        def execute(self, *args, continuation=None, phase_name=None, **kwargs):
            self.continuations.append(continuation)
            return super().execute(*args, **kwargs)

        def get_last_cli(self):
            return AgentCLI.CODEX

        def get_last_session_id(self):
            return "actual-session"

    manager = RecordingManager()
    executor = _minimal_spec_executor(tmp_path, agent_manager=manager)
    state = BlackboardStore(executor.issue_dir).load_or_create("spec")
    step_def = executor.playbook["steps"]["spec"]

    executor.execute_step("spec", step_def, state)
    executor.execute_step(
        "spec",
        step_def,
        state,
        extra_prompt="[BATON ERROR] repair next_step.txt",
        same_invocation_retry=True,
    )

    assert [item.policy for item in manager.continuations] == [
        SessionContinuationPolicy.NEW,
        SessionContinuationPolicy.RESUME_EXACT,
    ]
    assert manager.continuations[1].session_id == "actual-session"
    assert "[BATON ERROR] repair next_step.txt" in manager.prompts[1]
    snapshot = executor.issue_dir / "spec" / "iteration_002" / "delta_input.md"
    assert "[BATON ERROR] repair next_step.txt" in snapshot.read_text(encoding="utf-8")


def test_pre_step_baton_repair_after_prior_run_starts_new_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    class RecordingManager(FakeAgentManager):
        def __init__(self):
            super().__init__("confirmed")
            self.continuations = []

        def execute(self, *args, continuation=None, phase_name=None, **kwargs):
            self.continuations.append(continuation)
            return super().execute(*args, **kwargs)

    manager = RecordingManager()
    executor = _minimal_spec_executor(tmp_path, agent_manager=manager)
    previous = executor.issue_dir / "spec" / "iteration_001"
    previous.mkdir(parents=True)
    (previous / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "prior-run-session",
                "end_time": "2026-07-30T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    (previous / "output.md").write_text("previous output\n", encoding="utf-8")
    state = BlackboardStore(executor.issue_dir).load_or_create("spec")
    step_def = executor.playbook["steps"]["spec"]

    executor.execute_step(
        "spec",
        step_def,
        state,
        extra_prompt="[BATON ERROR] repair stale baton",
        same_invocation_retry=False,
    )

    assert manager.continuations[0].policy == SessionContinuationPolicy.NEW


def test_correction_writes_and_inlines_delta_packet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-delta"
    previous = issue_dir / "spec" / "iteration_001"
    previous.mkdir(parents=True)
    (previous / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "codex",
                "session_id": "old-session",
                "end_time": "2026-07-30T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    (previous / "output.md").write_text("Previous finding F1\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "output_artifact": "spec",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    manager = FakeAgentManager("confirmed")
    state = BlackboardStore(issue_dir).load_or_create("spec")
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-delta",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"pm": "Roger"},
        step_user_inputs={"spec": "Apply correction"},
    )

    executor.execute_step("spec", playbook["steps"]["spec"], state)

    iteration_dir = issue_dir / "spec" / "iteration_002"
    packet_path = iteration_dir / "delta_packet.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    metadata = json.loads((iteration_dir / "iteration.json").read_text(encoding="utf-8"))[
        "delta_packet"
    ]
    assert packet["run_kind"] == "correction"
    assert packet["previous_output"]["path"].endswith("spec/iteration_001/output.md")
    assert packet["previous_output"]["state"] == "file"
    assert packet["user_input"]["sha256"]
    assert packet["user_input"]["path"].endswith("spec/iteration_002/delta_input.md")
    assert (iteration_dir / "delta_input.md").read_text(encoding="utf-8") == "Apply correction"
    assert metadata["path"].endswith("spec/iteration_002/delta_packet.json")
    assert metadata["bytes"] == packet_path.stat().st_size
    assert "Correction delta packet" in manager.prompts[0]
    assert "Read previous_output" in manager.prompts[0]


def test_checklist_retry_receives_exact_session_and_phase_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    class ChecklistManager(FakeAgentManager):
        def __init__(self, checklist_path: Path):
            super().__init__("confirmed")
            self.checklist_path = checklist_path
            self.received = []

        def execute(self, *args, continuation=None, phase_name=None, **kwargs):
            self.received.append((continuation, phase_name))
            self.checklist_path.write_text("[x] fixed\n", encoding="utf-8")
            return super().execute(*args, **kwargs)

        def get_last_cli(self):
            return AgentCLI.CODEX

        def get_last_session_id(self):
            return "fresh-session"

    phase_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input" / "spec"
    checklist = phase_dir / "iteration_001" / "checklist.md"
    checklist.parent.mkdir(parents=True)
    checklist.write_text("[ ] fix\n", encoding="utf-8")
    manager = ChecklistManager(checklist)
    executor = _minimal_spec_executor(tmp_path, agent_manager=manager)
    executor.phase_dir = phase_dir
    executor.phase_name = "spec"
    executor.iteration = 1
    executor._session_continuation = SessionContinuation.resume_exact(
        AgentCLI.CODEX,
        "fresh-session",
    )

    _, status, passed = executor._validate_and_retry_checklist_completion(
        agent_name="Roger",
        prompt="prompt",
        user_input="",
        valid_intents=[PhaseStatusCode.CONFIRMED],
        max_retries=1,
    )

    assert passed is True
    assert status == PhaseStatusCode.CONFIRMED
    assert manager.received[0][0].policy == SessionContinuationPolicy.RESUME_EXACT
    assert manager.received[0][0].session_id == "fresh-session"
    assert manager.received[0][1] == "spec"
    assert manager.execute_call_count == 1


def test_checklist_retry_accumulates_raw_iteration_telemetry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    class ChecklistUsageManager(FakeAgentManager):
        def __init__(self, checklist_path: Path):
            super().__init__("confirmed")
            self.checklist_path = checklist_path

        def execute(self, *args, continuation=None, phase_name=None, **kwargs):
            self.checklist_path.write_text("[x] fixed\n", encoding="utf-8")
            return (
                "confirmed",
                TokenUsage(
                    cache_write_input_tokens=5,
                    reasoning_output_tokens=7,
                ),
                [],
                [],
                [],
                "gpt-test",
            )

    phase_dir = tmp_path / ".cafe" / "issues" / "issue-resume-input" / "spec"
    iteration_dir = phase_dir / "iteration_001"
    iteration_dir.mkdir(parents=True)
    checklist = iteration_dir / "checklist.md"
    checklist.write_text("[ ] fix\n", encoding="utf-8")
    (iteration_dir / "iteration.json").write_text(
        json.dumps(
            {
                "response": "confirmed",
                "streaming_log": [],
                "stats": TokenUsage(
                    cache_write_input_tokens=2,
                    reasoning_output_tokens=3,
                ).model_dump(),
            }
        ),
        encoding="utf-8",
    )
    executor = _minimal_spec_executor(
        tmp_path,
        agent_manager=ChecklistUsageManager(checklist),
    )
    executor.phase_dir = phase_dir
    executor.phase_name = "spec"
    executor.iteration = 1

    _, _, passed = executor._validate_and_retry_checklist_completion(
        agent_name="Roger",
        prompt="prompt",
        user_input="",
        valid_intents=[PhaseStatusCode.CONFIRMED],
        max_retries=1,
    )

    context = json.loads((iteration_dir / "iteration.json").read_text(encoding="utf-8"))
    assert passed is True
    assert context["stats"]["cache_write_input_tokens"] == 7
    assert context["stats"]["reasoning_output_tokens"] == 10


def test_persisted_packet_decision_rejects_malformed_iteration_metadata(tmp_path: Path) -> None:
    """UT-005: takeover must never derive a replacement packet decision."""
    iteration_dir = tmp_path / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(
            iteration_dir,
            require_persisted_packet_decision=True,
        )


def test_persisted_packet_decision_rejects_missing_effective_inputs(tmp_path: Path) -> None:
    """UT-004: an interrupted iteration cannot replace a lost packet decision."""
    iteration_dir = tmp_path / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text(json.dumps({"iteration": 1}), encoding="utf-8")

    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(
            iteration_dir,
            require_persisted_packet_decision=True,
        )


def test_persisted_packet_decision_rejects_empty_singleton_packet_binding(
    tmp_path: Path,
) -> None:
    """UT-004: a declared singleton packet policy cannot reload as an empty map."""
    iteration_dir = tmp_path / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    source = tmp_path / "spec.md"
    _write_valid_spec_contract(source)
    (iteration_dir / "iteration.json").write_text(
        json.dumps({"effective_inputs": {}}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(
            iteration_dir,
            require_persisted_packet_decision=True,
            authoritative_inputs={"packet_spec": source},
            packet_requested_placeholders=frozenset({"packet_spec"}),
        )


def test_persisted_packet_decision_requires_complete_binding_record(tmp_path: Path) -> None:
    """UT-005: partial state is unsafe rather than a signal to recompute."""
    iteration_dir = tmp_path / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text(
        json.dumps({"effective_inputs": {"spec_file": {"mode": "packet"}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(iteration_dir)


def _write_valid_spec_contract(path: Path) -> None:
    path.write_text(
        "# Source\n\nGOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001\n\n"
        "## Downstream Contract\n\n- Contract-Version: `1`\n- Artifact-Kind: `spec`\n\n"
        "### Goals\n| ID | Statement |\n| --- | --- |\n| GOAL-001 | goal |\n\n"
        "### Non-Goals\n| ID | Statement |\n| --- | --- |\n| NONGOAL-001 | no |\n\n"
        "### Acceptance Criteria\n| ID | Priority | Statement |\n| --- | --- | --- |\n| AC-001 | must | yes |\n\n"
        "### Invariants\n| ID | Statement |\n| --- | --- |\n| INV-001 | safe |\n\n"
        "### Trust Boundaries\n| ID | Statement |\n| --- | --- |\n| TRUST-001 | local |\n",
        encoding="utf-8",
    )


def test_generic_workflow_preparation_reloads_relative_packet_decision(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-004: normal relative artifacts survive production packet preparation."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-relative-packet"
    source = issue_dir / "spec" / "iteration_001" / "output.md"
    source.parent.mkdir(parents=True)
    _write_valid_spec_contract(source)
    relative_source = source.relative_to(tmp_path).as_posix()

    skill_dir = tmp_path / ".cafe" / "skills" / "cafe-develop"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: cafe-develop
description: packet preparation test skill
workflow:
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      load_policy: [{mode: packet, contract_kind: spec}]
    - artifacts: [spec]
      placeholder: spec_file_path
      load_policy: [{mode: packet, contract_kind: spec}]
---

Prepare packet inputs.
""",
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "skill": "cafe-develop",
                "role": "developer",
                "input_artifacts": ["spec"],
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["await_agent"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.set_artifact(state, "spec", relative_source)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-relative-packet",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("await_agent"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )

    executor.execute_step("develop", playbook["steps"]["develop"], state)

    iteration_dir = issue_dir / "develop" / "iteration_001"
    persisted = json.loads((iteration_dir / "iteration.json").read_text(encoding="utf-8"))[
        "effective_inputs"
    ]
    assert persisted["spec_file"] == persisted["spec_file_path"]
    assert persisted["spec_file"]["source"]["path"] == relative_source

    reloaded_context = executor._build_context(
        step_name="develop",
        step_def=playbook["steps"]["develop"],
        blackboard_state=state,
        agent_name="David",
        output_file=iteration_dir / "output.md",
    )

    assert reloaded_context["spec_file"] == reloaded_context["spec_file_path"]
    assert reloaded_context["input_loading_modes"] == "spec_file=packet, spec_file_path=packet"
    assert (
        json.loads((iteration_dir / "iteration.json").read_text(encoding="utf-8"))[
            "effective_inputs"
        ]
        == persisted
    )


def test_primary_and_backup_reject_persisted_full_active_packet_binding(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-004: neither execution path may replace an active packet decision with full."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-full-packet-binding"
    source = issue_dir / "spec" / "iteration_001" / "output.md"
    source.parent.mkdir(parents=True)
    _write_valid_spec_contract(source)
    skill_dir = tmp_path / ".cafe" / "skills" / "cafe-develop"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: cafe-develop
description: packet validation test skill
workflow:
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      load_policy: [{mode: packet, contract_kind: spec}]
    - artifacts: [spec]
      placeholder: spec_file_path
      load_policy: [{mode: packet, contract_kind: spec}]
---

Reject tampered persisted packet decisions.
""",
        encoding="utf-8",
    )
    step = {
        "skill": "cafe-develop",
        "role": "developer",
        "input_artifacts": ["spec"],
        "output_artifact": "code",
        "valid_intents": ["await_agent"],
        "on": {"await_agent": "_done"},
    }
    playbook = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {"develop": step},
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.set_artifact(state, "spec", str(source))
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="issue-full-packet-binding",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=FakeAgentManager("await_agent"),
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )
    executor.iteration = 1
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    full_binding = {"mode": "full", "path": str(source)}
    (iteration_dir / "iteration.json").write_text(
        json.dumps(
            {
                "effective_inputs": {
                    "spec_file": full_binding,
                    "spec_file_path": full_binding,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="persisted context packet decision"):
        executor._build_context(
            step_name="develop",
            step_def=step,
            blackboard_state=state,
            agent_name="David",
            output_file=iteration_dir / "output.md",
        )
    with pytest.raises(ValueError, match="persisted context packet decision"):
        executor._build_backup_takeover_context(
            error="primary failed",
            step_name="develop",
            step_def=step,
            blackboard_state=state,
            output_file=iteration_dir / "output.md",
            checklist_file=iteration_dir / "checklist.md",
            iteration_dir=iteration_dir,
        )


def test_persisted_packet_binding_must_match_declared_authority_and_envelope(
    tmp_path: Path,
) -> None:
    """UT-004: takeover cannot redirect a packet binding to another source."""
    from cafe.skills.contracts import SkillWorkflowContract, resolve_effective_prompt_inputs

    source = tmp_path / "spec.md"
    other = tmp_path / "other.md"
    _write_valid_spec_contract(source)
    other.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    contract = SkillWorkflowContract.model_validate(
        {
            "prompt_inputs": [
                {
                    "artifacts": ["spec"],
                    "placeholder": "spec_file",
                    "load_policy": [{"mode": "packet", "contract_kind": "spec"}],
                },
                {
                    "artifacts": ["spec"],
                    "placeholder": "spec_file_path",
                    "load_policy": [{"mode": "packet", "contract_kind": "spec"}],
                },
            ]
        }
    )
    iteration_dir = tmp_path / "develop" / "iteration_001"
    effective = resolve_effective_prompt_inputs(
        contract,
        {"spec": source},
        step="develop",
        iteration=1,
        feedback=False,
        packet_dir=iteration_dir,
    )
    (iteration_dir / "iteration.json").write_text(
        json.dumps({"effective_inputs": effective}), encoding="utf-8"
    )

    loaded = GenericWorkflowStepExecutor._load_persisted_effective_inputs(
        iteration_dir,
        require_persisted_packet_decision=True,
        authoritative_inputs={"spec_file": source, "spec_file_path": source},
        target_step="develop",
        iteration=1,
    )
    assert loaded == effective

    original = json.loads((iteration_dir / "iteration.json").read_text(encoding="utf-8"))
    packet_path = Path(original["effective_inputs"]["spec_file"]["path"])
    original_packet = packet_path.read_text(encoding="utf-8")

    tampered = json.loads(json.dumps(original))
    tampered["effective_inputs"]["spec_file_path"]["path"] = str(other)
    (iteration_dir / "iteration.json").write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(
            iteration_dir,
            require_persisted_packet_decision=True,
            authoritative_inputs={"spec_file": source, "spec_file_path": source},
            target_step="develop",
            iteration=1,
        )

    tampered = json.loads(json.dumps(original))
    tampered["effective_inputs"].pop("spec_file_path")
    (iteration_dir / "iteration.json").write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(
            iteration_dir,
            require_persisted_packet_decision=True,
            authoritative_inputs={"spec_file": source, "spec_file_path": source},
            target_step="develop",
            iteration=1,
        )

    tampered_packet = json.loads(original_packet)
    tampered_packet["contract"]["bytes"] = "agent-substituted contract"
    tampered_packet["contract"]["sha256"] = (
        __import__("hashlib")
        .sha256(tampered_packet["contract"]["bytes"].encode("utf-8"))
        .hexdigest()
    )
    packet_path.write_text(json.dumps(tampered_packet), encoding="utf-8")
    (iteration_dir / "iteration.json").write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(
            iteration_dir,
            require_persisted_packet_decision=True,
            authoritative_inputs={"spec_file": source, "spec_file_path": source},
            target_step="develop",
            iteration=1,
        )

    packet_path.write_text(original_packet, encoding="utf-8")
    replacement = iteration_dir / "agent-selected-packet.json"
    replacement.write_text(original_packet, encoding="utf-8")
    tampered = json.loads(json.dumps(original))
    for binding in tampered["effective_inputs"].values():
        binding["path"] = str(replacement)
    (iteration_dir / "iteration.json").write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(
            iteration_dir,
            require_persisted_packet_decision=True,
            authoritative_inputs={"spec_file": source, "spec_file_path": source},
            target_step="develop",
            iteration=1,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("fallback_reason", "contract_invalid"),
        ("reason", "packet_persist_failed"),
        ("detail", "agent supplied secret"),
        ("source", {"artifact_name": "spec", "artifact_version": "one"}),
    ],
)
def test_persisted_packet_decision_fails_closed_on_tampered_runtime_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    """UT-004: primary/backup/retry/resume share the same strict loader."""
    iteration_dir = tmp_path / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    binding = {
        "requested_mode": "packet",
        "mode": "full_fallback",
        "path": "spec.md",
        "source": {"artifact_name": "spec", "artifact_version": 1},
        "reason": "packet_invalid",
        "fallback_reason": "packet_invalid",
        "detail": "context packet validation failed",
    }
    binding[field] = value
    (iteration_dir / "iteration.json").write_text(
        json.dumps({"effective_inputs": {"spec_file": binding}}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="context packet decision"):
        GenericWorkflowStepExecutor._load_persisted_effective_inputs(
            iteration_dir, require_persisted_packet_decision=True
        )


def test_hybrid_portion_uses_a_private_baton_sink(tmp_path: Path, monkeypatch) -> None:
    """A hybrid portion cannot write the canonical routing baton."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "hybrid-sink"
    playbook = {
        "playbook": {"id": "hybrid-sink"},
        "roles": {"developer": {"default_agent": "David"}},
        "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
        "steps": {
            "mixed": {
                "skill": "develop",
                "role": "developer",
                "output_artifact": "code",
                "allowed_tools": ["Read"],
                "valid_intents": ["confirmed"],
                "on": {},
            }
        },
    }
    canonical_baton = issue_dir / "next_step.txt"
    canonical_payload = {
        "version": 1,
        "from_step": "mixed",
        "to_owner": "agent",
        "to_step": "mixed",
        "intent": "await_agent",
        "status_code": "",
        "created_at": "2026-01-01T00:00:00+08:00",
        "source": "test",
    }
    canonical_baton.parent.mkdir(parents=True)
    canonical_baton.write_text(json.dumps(canonical_payload), encoding="utf-8")
    state = BlackboardStore(issue_dir).load_or_create("mixed", playbook_id="hybrid-sink")
    sink_paths: list[Path] = []

    def write_portion_completion(*, prompt: str, **_kwargs) -> None:
        sink_path = Path(
            next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("next_step_file=")
            )
        )
        sink_paths.append(sink_path)
        sink_path.write_text(
            json.dumps(
                {
                    "from_step": "mixed",
                    "to_owner": "agent",
                    "to_step": "mixed",
                    "intent": "await_agent",
                    "source": "hybrid_portion:mixed:draft",
                }
            ),
            encoding="utf-8",
        )

    agent_manager = FakeAgentManager("confirmed", on_execute=write_portion_completion)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="hybrid-sink",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        agent_manager=agent_manager,
        git_ops=FakeGitOperations(),
        role_agent_map={"developer": "David"},
    )
    hybrid_step = {
        **playbook["steps"]["mixed"],
        "hybrid_portion": {"id": "draft", "instruction": "Draft the proposal."},
    }

    result = executor.execute_step("mixed", hybrid_step, state)

    assert [path.resolve() for path in sink_paths] == [
        issue_dir / "mixed" / "iteration_001" / "hybrid_portion_baton.json"
    ]
    assert json.loads(canonical_baton.read_text(encoding="utf-8")) == canonical_payload
    allowed_tools = agent_manager.allowed_tools_calls[0] or []
    assert (
        "write(./.cafe/issues/hybrid-sink/mixed/iteration_001/hybrid_portion_baton.json)"
        in allowed_tools
    )
    assert "write(./.cafe/issues/hybrid-sink/next_step.txt)" not in allowed_tools
    assert any(event["type"] == "hybrid_portion_baton" for event in result.events)
