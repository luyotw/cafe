"""Tests for GenericPhase."""

import json
import subprocess
from pathlib import Path

import pytest

from cafe.core.hooks import HookResult
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import AgentCLI
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge


def _setup_loader(tmp_path: Path) -> SkillLoader:
    skill_root = tmp_path / "builtin" / "skills"
    for name, body in {
        "cafe-plan": "Hello {who}\n",
        "cafe-workflow-common": "Read blackboard first.\n",
        "cafe-review": "Review the latest changes.\n",
    }.items():
        builtin = skill_root / name
        builtin.mkdir(parents=True, exist_ok=True)
        (builtin / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: desc\n---\n\n{body}",
            encoding="utf-8",
        )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return loader


def test_build_prompt_references_skill_invocation_not_embedded_body(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={"handoff_summary": "Resume plan"},
        output_file=tmp_path / "out.md",
        checklist_file=tmp_path / "checklist.md",
    )
    assert "Phase skill: /plan" in prompt
    assert "Shared skills:" in prompt
    assert "Custom project plan skill" not in prompt


def test_build_prompt_includes_files_and_checklist_guard(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={
            "who": "team",
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "blackboard_digest": '{"current_step":"plan","recent_events":[]}',
            "handoff_summary": "Implement cafe skill rm with interactive multi-select and confirmation.",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
        },
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
        questions_xml_file=Path("questions.xml"),
    )
    assert "Shared skills:" in prompt
    assert "/cafe-workflow-common" in prompt
    assert "Phase skill: /plan" in prompt
    assert "Runtime files:" in prompt
    assert "output_file=out.md" in prompt
    assert "checklist_file=checklist.md" in prompt
    assert "questions_file=questions.xml" in prompt
    assert "blackboard_file=.cafe/issues/demo/blackboard.json" in prompt
    assert "next_step_file=.cafe/issues/demo/next_step.txt" in prompt
    assert "Runtime context:" in prompt
    assert "Baton contract (single source of truth):" in prompt
    assert "valid intent values: [await_agent, confirm_output, alignment_checkpoint, need_clarification, need_permission, no_changes_needed, manual_handoff, workflow_complete]" in prompt
    assert "do not invoke external workflow-driving skills (e.g. use-cafe-workflow)" in prompt
    assert "Do NOT finish this step until ALL checklist items are marked as [x]." in prompt
    assert "Do NOT return a status code" not in prompt
    assert "Latest workflow handoff from blackboard:" in prompt
    assert "Implement cafe skill rm" in prompt
    assert "verify whether the requested state change has actually happened" in prompt
    assert "do not treat an old artifact or a closed external object as completion" in prompt
    assert "Bounded blackboard digest:" in prompt
    assert '{"current_step":"plan","recent_events":[]}' in prompt
    assert "Do not read the full blackboard file" in prompt
    assert "query only the specific field or event" in prompt


def test_build_prompt_uses_baton_wording_when_status_code_not_required(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "handoff_summary": "Reopen PR and complete the local artifact before host-side publish.",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
        },
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
    )

    assert "Before finishing this step" in prompt
    assert "Do NOT finish this step until ALL checklist items are marked as [x]." in prompt
    assert "Before returning a status code" not in prompt
    assert "Do NOT return a status code" not in prompt


def test_build_prompt_omits_questions_line_when_questions_file_not_passed(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
        },
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
        questions_xml_file=None,
    )
    assert "questions_file=" not in prompt


def test_build_prompt_includes_user_input_when_set(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
            "user_input": "Please prioritize the auth module.",
        },
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
    )
    assert "Current user input for this iteration:" in prompt
    assert "Please prioritize the auth module." in prompt


def test_build_prompt_includes_resume_input_artifacts_when_set(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-develop",
        skill_invocation="/develop",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
            "resume_input_artifacts": "- develop_file: .cafe/issues/demo/develop/output.md",
        },
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
    )

    assert "Current step input artifacts:" in prompt
    assert "- develop_file: .cafe/issues/demo/develop/output.md" in prompt


def test_build_prompt_omits_resume_input_artifacts_when_unset(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-develop",
        skill_invocation="/develop",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
            "handoff_summary": "Resume implementation.",
            "user_input": "continue",
        },
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
    )

    assert "Current step input artifacts:" not in prompt
    assert "Latest workflow handoff from blackboard:" in prompt
    assert "Current user input for this iteration:" in prompt


def test_build_prompt_pr_phase_appends_publish_ordering_when_handoff_present(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-pr",
        skill_invocation="/pr",
        shared_skill_invocations=["/cafe-workflow-common", "/cafe-github_sync"],
        context={
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "handoff_summary": "Finish local PR artifact.",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
        },
        output_file=Path("pr.md"),
        checklist_file=Path("checklist.md"),
    )
    assert "For the PR phase, completion is local-only" in prompt
    assert "host-side publish_output hook" in prompt


def assert_runtime_handoff_guardrails_persist(prompt: str) -> None:
    """When ``handoff_summary`` is injected, these lines must stay in the runtime prompt.

    They intentionally overlap the spirit of ``workflow-common`` (read real state first)
    but remain concrete execution checks for the agent. Removing them should fail this
    test and force a coordinated update with the packaged ``workflow-common`` skill.
    """

    assert "Latest workflow handoff from blackboard:" in prompt
    assert "Treat this handoff as the highest-priority current request" in prompt
    assert "verify whether the requested state change has actually happened" in prompt
    assert "do not treat an old artifact or a closed external object as completion" in prompt


def test_build_prompt_contract_covers_shared_skills_files_context_and_gate(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="cafe-develop",
        skill_invocation="/develop",
        shared_skill_invocations=["/cafe-workflow-common", "/cafe-github_sync"],
        context={
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
            "user_input": "iteration notes",
            "handoff_summary": "Continue milestone B cleanup.",
        },
        output_file=Path("develop/out.md"),
        checklist_file=Path("develop/checklist.md"),
        questions_xml_file=Path("develop/questions.xml"),
    )
    assert prompt.startswith("Shared skills:")
    assert "/cafe-workflow-common" in prompt
    assert "/cafe-github_sync" in prompt
    assert "Phase skill: /develop" in prompt
    for line in (
        "output_file=develop/out.md",
        "checklist_file=develop/checklist.md",
        "questions_file=develop/questions.xml",
        "blackboard_file=.cafe/issues/demo/blackboard.json",
        "next_step_file=.cafe/issues/demo/next_step.txt",
    ):
        assert line in prompt
    assert "Baton contract (single source of truth):" in prompt
    assert "Do NOT finish this step until ALL checklist items are marked as [x]." in prompt
    assert "Do NOT return a status code" not in prompt
    assert_runtime_handoff_guardrails_persist(prompt)


def test_parse_response_extracts_status_and_goto(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    status, goto_target = phase.parse_response(
        response="confirmed\nGOTO:review",
        valid_intents=[PhaseStatusCode.CONFIRMED],
    )
    assert status == PhaseStatusCode.CONFIRMED
    assert goto_target == "review"


def test_validate_clarification_output_requires_valid_xml(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    xml_file = tmp_path / "questions.xml"
    xml_file.write_text("<bad></bad>", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        phase.validate_clarification_output(
            status_code=PhaseStatusCode.NEED_CLARIFICATION,
            questions_xml_file=xml_file,
        )


class StopHook:
    name = "StopHook"

    def run(self, **kwargs):
        return HookResult(
            continue_pipeline=False,
            override_status_code=PhaseStatusCode.NEED_CLARIFICATION,
            events=[{"type": "stopped"}],
        )


class PrepareHook:
    name = "PrepareHook"

    def run(self, **kwargs):
        return HookResult(context_updates={"who": "prepared"})


class CapturePreparedContextHook:
    name = "CapturePreparedContextHook"
    seen_context: dict[str, str] = {}

    def run(self, **kwargs):
        self.__class__.seen_context = dict(kwargs.get("context") or {})
        return HookResult()


class RetryHook:
    name = "RetryHook"

    def __init__(self):
        self.called = getattr(self.__class__, "_called", False)

    def run(self, **kwargs):
        if not self.called:
            self.__class__._called = True
            return HookResult(retry_requested=True, context_updates={"who": "retried"})
        return HookResult()


class NoArtifactHook:
    name = "NoArtifactHook"

    def run(self, **kwargs):
        return HookResult(artifact_ready=False)


class PublishHook:
    name = "PublishHook"

    def run(self, **kwargs):
        return HookResult(events=[{"type": "published"}])


class PublishOverrideHook:
    name = "PublishOverrideHook"

    def run(self, **kwargs):
        return HookResult(override_status_code=PhaseStatusCode.READY_FOR_REVIEW)


def test_execute_short_circuits_when_before_execute_stops(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path), hook_registry={"StopHook": StopHook})
    calls: list[str] = []

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {"before_execute": ["StopHook"]},
            "valid_intents": ["need_clarification"],
        },
        agent_executor=lambda prompt: calls.append(prompt) or "confirmed",
    )

    assert calls == []
    assert result.status_code == PhaseStatusCode.NEED_CLARIFICATION
    assert result.events == [{"type": "stopped"}]


def test_execute_runs_prepare_input_and_after_execute_retry(tmp_path: Path) -> None:
    RetryHook._called = False
    phase = GenericPhase(
        _setup_loader(tmp_path),
        hook_registry={"PrepareHook": PrepareHook, "RetryHook": RetryHook},
    )
    prompts: list[str] = []
    responses = iter(["confirmed", "confirmed"])

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "prepare_input": ["PrepareHook"],
                "after_execute": ["RetryHook"],
            },
            "valid_intents": ["confirmed"],
        },
        agent_executor=lambda prompt: prompts.append(prompt) or next(responses),
    )

    assert len(prompts) == 2
    assert "Shared skills:" in prompts[0]
    assert "Phase skill: /plan" in prompts[0]
    assert "Shared skills:" in prompts[1]
    assert "Phase skill: /plan" in prompts[1]
    assert result.status_code is None


def test_prepare_hooks_receive_prior_context_updates(tmp_path: Path) -> None:
    CapturePreparedContextHook.seen_context = {}
    phase = GenericPhase(
        _setup_loader(tmp_path),
        hook_registry={
            "PrepareHook": PrepareHook,
            "CapturePreparedContextHook": CapturePreparedContextHook,
        },
    )

    phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        step_def={
            "hooks": {"prepare_input": ["PrepareHook", "CapturePreparedContextHook"]},
            "valid_intents": ["confirmed"],
        },
        context={"existing": "context"},
        agent_executor=lambda prompt: "confirmed",
    )

    assert CapturePreparedContextHook.seen_context["existing"] == "context"
    assert CapturePreparedContextHook.seen_context["who"] == "prepared"


def test_execute_skips_publish_when_artifact_not_ready(tmp_path: Path) -> None:
    phase = GenericPhase(
        _setup_loader(tmp_path),
        hook_registry={"NoArtifactHook": NoArtifactHook, "PublishHook": PublishHook},
    )

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "after_execute": ["NoArtifactHook"],
                "publish_output": ["PublishHook"],
            },
            "valid_intents": ["confirmed"],
        },
        agent_executor=lambda prompt: "confirmed",
    )

    assert result.artifact_ready is False
    assert result.published is False
    assert {"type": "published"} not in result.events


def test_execute_applies_publish_output_status_override(tmp_path: Path) -> None:
    phase = GenericPhase(
        _setup_loader(tmp_path),
        hook_registry={"PublishOverrideHook": PublishOverrideHook},
    )

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "publish_output": ["PublishOverrideHook"],
            },
            "valid_intents": ["confirmed", "ready_for_review"],
        },
        output_file=tmp_path / "out.md",
        agent_executor=lambda prompt: "confirmed",
    )

    assert result.status_code == PhaseStatusCode.READY_FOR_REVIEW


def test_prepare_skill_installs_skill_and_returns_cli_invocation(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    bridge = NativeSkillBridge(
        loader,
        project_root=project_root,
        home_dir=tmp_path / "home",
    )
    phase = GenericPhase(loader, skill_bridge=bridge)

    invocation = phase.prepare_skill(skill_name="cafe-plan", agent_cli=AgentCLI.CODEX)

    assert invocation == "$cafe-plan"
    assert (project_root / ".codex" / "skills" / "cafe-plan" / "SKILL.md").exists()


def test_prepare_skill_renders_iteration_context_without_mutating_source(
    tmp_path: Path,
) -> None:
    loader = _setup_loader(tmp_path)
    source_file = loader.get_skill_dir("cafe-plan") / "SKILL.md"
    source_before = source_file.read_text(encoding="utf-8")
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(
            loader,
            project_root=project_root,
            home_dir=tmp_path / "home",
        ),
    )

    phase.prepare_skill(
        skill_name="cafe-plan",
        agent_cli=AgentCLI.CODEX,
        context={"who": "David"},
    )

    installed_file = project_root / ".codex" / "skills" / "cafe-plan" / "SKILL.md"
    installed = installed_file.read_text(encoding="utf-8")
    assert "Hello David" in installed
    assert "{who}" not in installed
    assert source_file.read_text(encoding="utf-8") == source_before


def test_prepare_skills_installs_shared_and_phase_skills(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    bridge = NativeSkillBridge(
        loader,
        project_root=project_root,
        home_dir=tmp_path / "home",
    )
    phase = GenericPhase(loader, skill_bridge=bridge)

    invocations = phase.prepare_skills(
        skill_names=["workflow-common", "review"],
        agent_cli=AgentCLI.CODEX,
    )

    assert invocations == ["$cafe-workflow-common", "$cafe-review"]
    assert (project_root / ".codex" / "skills" / "cafe-workflow-common" / "SKILL.md").exists()
    assert (project_root / ".codex" / "skills" / "cafe-review" / "SKILL.md").exists()


def test_native_skill_bridge_keeps_global_dir_separate(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    bridge = NativeSkillBridge(
        loader,
        project_root=project_root,
        home_dir=tmp_path / "home",
    )

    assert bridge.get_native_skills_dir(AgentCLI.CODEX) == project_root / ".codex" / "skills"
    assert bridge.get_global_native_skills_dir(AgentCLI.CODEX) == tmp_path / "home" / ".codex" / "skills"
    assert bridge.get_installed_skill_name("plan") == "cafe-plan"


# --- Task 4: validate_skills on NativeSkillBridge ---


def _make_bridge(tmp_path: Path) -> NativeSkillBridge:
    loader = _setup_loader(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    return NativeSkillBridge(loader, project_root=project_root, home_dir=tmp_path / "home")


def test_validate_skills_all_available(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    result = bridge.validate_skills(["plan", "workflow-common"], AgentCLI.CLAUDE)
    assert result.valid
    assert set(result.available) == {"plan", "workflow-common"}
    assert result.missing == []


def test_validate_skills_reports_missing_skill(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    result = bridge.validate_skills(["plan", "ghost"], AgentCLI.CLAUDE)
    assert not result.valid
    assert "ghost" in result.missing
    assert "plan" in result.available


def test_validate_skills_empty_list_is_valid(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    result = bridge.validate_skills([], AgentCLI.CLAUDE)
    assert result.valid
    assert result.available == []
    assert result.missing == []


@pytest.mark.parametrize("cli", list(AgentCLI))
def test_validate_skills_all_clis(tmp_path: Path, cli: AgentCLI) -> None:
    bridge = _make_bridge(tmp_path)
    result = bridge.validate_skills(["plan"], cli)
    assert result.valid
    assert result.cli == cli


def test_validate_skills_does_not_install_any_files(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    bridge.validate_skills(["plan", "workflow-common"], AgentCLI.CLAUDE)
    assert not (tmp_path / "project" / ".claude" / "skills").exists()


def _write_skill_script(loader: SkillLoader, *, skill_name: str, script_name: str, body: str) -> Path:
    skill_dir = loader.get_skill_dir(skill_name)
    script_dir = skill_dir / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / script_name
    script_path.write_text(body, encoding="utf-8")
    return script_path


def test_execute_runs_script_hook_with_schema_and_interpolation(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="echo_args.sh",
        body=(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "echo \"$1|$2|$3|$4\"\n"
        ),
    )
    phase = GenericPhase(loader)
    output_file = tmp_path / "out.md"
    output_file.write_text("x", encoding="utf-8")

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={"output_file": str(output_file)},
        step_def={
            "hooks": {
                "before_execute": [
                    {
                        "script": "echo_args.sh",
                        "args": {
                            "phase": "plan",
                            "output": "{output_file}",
                        },
                        "schema": {
                            "type": "object",
                            "required": ["phase", "output"],
                            "additionalProperties": False,
                            "properties": {
                                "phase": {"type": "string", "enum": ["spec", "plan"]},
                                "output": {"type": "string"},
                            },
                        },
                    }
                ]
            },
            "valid_intents": ["confirmed"],
        },
        output_file=output_file,
        agent_executor=lambda prompt: "confirmed",
    )

    event = next(item for item in result.events if item.get("type") == "script_hook")
    assert event["status"] == "success"
    assert event["stage"] == "before_execute"
    assert "--phase|plan|--output" in event["stdout"]
    assert str(output_file) in event["stdout"]


def test_execute_rejects_script_hook_path_traversal(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    phase = GenericPhase(loader)

    with pytest.raises(ValueError, match="parent traversal"):
        phase.execute(
            skill_name="cafe-plan",
            skill_invocation="/plan",
            shared_skill_invocations=["/cafe-workflow-common"],
            step_def={
                "hooks": {
                    "before_execute": [
                        {
                            "script": "../evil.sh",
                            "args": {},
                        }
                    ]
                },
                "valid_intents": ["confirmed"],
            },
            agent_executor=lambda prompt: "confirmed",
        )


def test_execute_rejects_script_hook_symlink_outside_scripts_dir(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    phase = GenericPhase(loader)
    skill_dir = loader.get_skill_dir("cafe-plan")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    escaped_dir = skill_dir / "scripts_evil"
    escaped_dir.mkdir(parents=True, exist_ok=True)
    escaped_script = escaped_dir / "escaped.sh"
    escaped_script.write_text("#!/usr/bin/env bash\necho escaped\n", encoding="utf-8")

    link_script = scripts_dir / "escape.sh"
    link_script.symlink_to(escaped_script)

    with pytest.raises(ValueError, match="must stay inside"):
        phase.execute(
            skill_name="cafe-plan",
            skill_invocation="/plan",
            shared_skill_invocations=["/cafe-workflow-common"],
            step_def={
                "hooks": {
                    "before_execute": [
                        {
                            "script": "escape.sh",
                            "args": {},
                        }
                    ]
                },
                "valid_intents": ["confirmed"],
            },
            agent_executor=lambda prompt: "confirmed",
        )


def test_execute_script_hook_validation_failure_stops_pipeline(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    marker = tmp_path / "marker.txt"
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="touch_marker.sh",
        body=(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "echo touched > \"" + str(marker) + "\"\n"
        ),
    )
    phase = GenericPhase(loader)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "before_execute": [
                    {
                        "script": "touch_marker.sh",
                        "args": {"phase": 7},
                        "schema": {
                            "type": "object",
                            "required": ["phase"],
                            "additionalProperties": False,
                            "properties": {"phase": {"type": "string"}},
                        },
                    }
                ]
            },
            "valid_intents": ["need_permission", "confirmed"],
        },
        agent_executor=lambda prompt: "confirmed",
    )

    assert not marker.exists()
    assert result.status_code == PhaseStatusCode.NEED_PERMISSION
    event = next(item for item in result.events if item.get("type") == "script_hook")
    assert event["status"] == "validation_failed"
    assert "expected type 'string'" in " ".join(event["validation_errors"])


def test_execute_script_hook_failure_stops_pipeline(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="fail.sh",
        body=(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "echo boom >&2\n"
            "exit 9\n"
        ),
    )
    phase = GenericPhase(loader)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "after_execute": [
                    {
                        "script": "fail.sh",
                        "args": {},
                        "when_intents": ["confirmed"],
                    }
                ]
            },
            "valid_intents": ["confirmed", "need_permission"],
        },
        agent_executor=lambda prompt: "confirmed",
    )

    assert result.status_code == PhaseStatusCode.NEED_PERMISSION
    event = next(item for item in result.events if item.get("type") == "script_hook")
    assert event["status"] == "failed"
    assert event["exit_code"] == 9
    assert "boom" in event["stderr"]


def test_execute_script_hook_can_skip_when_status_mismatch(tmp_path: Path) -> None:
    loader = _setup_loader(tmp_path)
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="noop.sh",
        body=(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "echo noop\n"
        ),
    )
    phase = GenericPhase(loader)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "after_execute": [
                    {
                        "script": "noop.sh",
                        "args": {},
                        "when_intents": ["confirmed"],
                    }
                ]
            },
            "valid_intents": ["ready_for_review", "confirmed"],
        },
        agent_executor=lambda prompt: "ready_for_review",
    )

    event = next(item for item in result.events if item.get("type") == "script_hook")
    assert event["status"] == "skipped"
    assert event["reason"] == "intent_mismatch"


@pytest.mark.parametrize(
    "agent_response,expected_group,skipped_group",
    [
        ("need_permission", "user", "milestone"),
        ("await_agent", "milestone", "user"),
        ("workflow_complete", "milestone", "user"),
    ],
)
def test_execute_script_hook_filters_notification_intent_groups(
    tmp_path: Path,
    agent_response: str,
    expected_group: str,
    skipped_group: str,
) -> None:
    loader = _setup_loader(tmp_path)
    calls_file = tmp_path / "calls.txt"
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="notify-slack.sh",
        body=(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "GROUP=\"\"\n"
            "while [[ $# -gt 0 ]]; do\n"
            "  case \"$1\" in\n"
            "    --group) GROUP=\"$2\"; shift 2 ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            f"echo \"$GROUP\" >> {str(calls_file)!r}\n"
            "echo '{\"action\":\"posted\"}'\n"
        ),
    )
    phase = GenericPhase(loader)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "after_execute": [
                    {
                        "script": "notify-slack.sh",
                        "args": {"group": "user"},
                        "when_intents": ["need_clarification", "need_permission"],
                    },
                    {
                        "script": "notify-slack.sh",
                        "args": {"group": "milestone"},
                        "when_intents": ["await_agent", "workflow_complete"],
                    },
                ]
            },
            "valid_intents": [
                "await_agent",
                "workflow_complete",
                "need_clarification",
                "need_permission",
            ],
        },
        agent_executor=lambda prompt: agent_response,
    )

    assert calls_file.read_text(encoding="utf-8").strip() == expected_group
    events = [item for item in result.events if item.get("type") == "script_hook"]
    skipped_intents = (
        ["await_agent", "workflow_complete"]
        if skipped_group == "milestone"
        else ["need_clarification", "need_permission"]
    )
    assert any(item["status"] == "success" and item["stdout"] for item in events)
    assert any(
        item["status"] == "skipped"
        and item["reason"] == "intent_mismatch"
        and item["when_intents"] == skipped_intents
        for item in events
    )


def test_execute_script_hook_filters_notification_intent_from_baton_file(
    tmp_path: Path,
) -> None:
    loader = _setup_loader(tmp_path)
    calls_file = tmp_path / "calls.txt"
    next_step = tmp_path / "next_step.txt"
    next_step.write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "plan",
                "to_owner": "agent",
                "to_step": "review",
                "intent": "workflow_complete",
                "status_code": "",
                "created_at": "2026-07-06T00:00:00Z",
                "source": "test",
            }
        ),
        encoding="utf-8",
    )
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="notify-slack.sh",
        body=(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "GROUP=\"\"\n"
            "while [[ $# -gt 0 ]]; do\n"
            "  case \"$1\" in\n"
            "    --group) GROUP=\"$2\"; shift 2 ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            f"echo \"$GROUP\" >> {str(calls_file)!r}\n"
            "echo '{\"action\":\"posted\"}'\n"
        ),
    )
    phase = GenericPhase(loader)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={"next_step_path": str(next_step)},
        step_def={
            "hooks": {
                "after_execute": [
                    {
                        "script": "notify-slack.sh",
                        "args": {"group": "user"},
                        "when_intents": ["need_clarification", "need_permission"],
                    },
                    {
                        "script": "notify-slack.sh",
                        "args": {"group": "milestone"},
                        "when_intents": ["await_agent", "workflow_complete"],
                    },
                ]
            },
            "valid_intents": [
                "await_agent",
                "workflow_complete",
                "need_clarification",
                "need_permission",
            ],
        },
        hook_context={"step_name": "plan"},
        agent_executor=lambda prompt: "Done. Wrote baton.",
    )

    assert result.status_code is None
    assert calls_file.read_text(encoding="utf-8").strip() == "milestone"
    events = [item for item in result.events if item.get("type") == "script_hook"]
    assert any(item["status"] == "success" and item["stdout"] for item in events)
    assert any(
        item["status"] == "skipped"
        and item["reason"] == "intent_mismatch"
        and item["when_intents"] == ["need_clarification", "need_permission"]
        for item in events
    )


def test_execute_script_hook_ignores_start_step_override_baton_intent(
    tmp_path: Path,
) -> None:
    loader = _setup_loader(tmp_path)
    calls_file = tmp_path / "calls.txt"
    next_step = tmp_path / "next_step.txt"
    next_step.write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "plan",
                "to_owner": "agent",
                "to_step": "plan",
                "intent": "await_agent",
                "status_code": "",
                "created_at": "2026-07-06T00:00:01Z",
                "source": "workflow.start_step_override",
            }
        ),
        encoding="utf-8",
    )
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="notify-slack.sh",
        body=(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "GROUP=\"\"\n"
            "while [[ $# -gt 0 ]]; do\n"
            "  case \"$1\" in\n"
            "    --group) GROUP=\"$2\"; shift 2 ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            f"echo \"$GROUP\" >> {str(calls_file)!r}\n"
            "echo '{\"action\":\"posted\"}'\n"
        ),
    )
    phase = GenericPhase(loader)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        context={"next_step_path": str(next_step)},
        step_def={
            "hooks": {
                "after_execute": [
                    {
                        "script": "notify-slack.sh",
                        "args": {"group": "user"},
                        "when_intents": ["need_clarification", "need_permission"],
                    },
                    {
                        "script": "notify-slack.sh",
                        "args": {"group": "milestone"},
                        "when_intents": ["await_agent", "workflow_complete"],
                    },
                ]
            },
            "valid_intents": [
                "await_agent",
                "workflow_complete",
                "need_clarification",
                "need_permission",
            ],
        },
        hook_context={"step_name": "plan"},
        agent_executor=lambda prompt: "need_permission\nNeed permission to proceed.",
    )

    assert result.status_code is None
    assert calls_file.read_text(encoding="utf-8").strip() == "user"
    events = [item for item in result.events if item.get("type") == "script_hook"]
    assert any(item["status"] == "success" and item["stdout"] for item in events)
    assert any(
        item["status"] == "skipped"
        and item["reason"] == "intent_mismatch"
        and item["when_intents"] == ["await_agent", "workflow_complete"]
        for item in events
    )


def test_execute_script_hook_passes_timeout_to_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    loader = _setup_loader(tmp_path)
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="noop.sh",
        body="#!/usr/bin/env bash\necho noop\n",
    )
    phase = GenericPhase(loader)
    captured: dict[str, object] = {}

    def _run(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("cafe.phases.generic_phase.subprocess.run", _run)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "before_execute": [
                    {
                        "script": "noop.sh",
                        "args": {},
                        "timeout_seconds": 2.5,
                    }
                ]
            },
            "valid_intents": ["confirmed"],
        },
        agent_executor=lambda prompt: "confirmed",
    )

    assert captured["timeout"] == 2.5
    event = next(item for item in result.events if item.get("type") == "script_hook")
    assert event["status"] == "success"


def test_execute_script_hook_timeout_stops_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    loader = _setup_loader(tmp_path)
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="slow.sh",
        body="#!/usr/bin/env bash\nsleep 30\n",
    )
    phase = GenericPhase(loader)

    def _run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1.0, output="partial", stderr="timed out")

    monkeypatch.setattr("cafe.phases.generic_phase.subprocess.run", _run)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "before_execute": [
                    {
                        "script": "slow.sh",
                        "args": {},
                        "timeout_seconds": 1.0,
                    }
                ]
            },
            "valid_intents": ["confirmed", "need_permission"],
        },
        agent_executor=lambda prompt: "confirmed",
    )

    assert result.status_code == PhaseStatusCode.NEED_PERMISSION
    event = next(item for item in result.events if item.get("type") == "script_hook")
    assert event["status"] == "timeout"
    assert event["exit_code"] is None
    assert "partial" in event["stdout"]
    assert "timed out" in event["stderr"]


def test_execute_script_hook_timeout_decodes_bytes_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = _setup_loader(tmp_path)
    _write_skill_script(
        loader,
        skill_name="cafe-plan",
        script_name="slow.sh",
        body="#!/usr/bin/env bash\nsleep 30\n",
    )
    phase = GenericPhase(loader)

    def _run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1.0, output=b"partial-bytes", stderr=b"timed-bytes")

    monkeypatch.setattr("cafe.phases.generic_phase.subprocess.run", _run)

    result = phase.execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/cafe-workflow-common"],
        step_def={
            "hooks": {
                "before_execute": [
                    {
                        "script": "slow.sh",
                        "args": {},
                        "timeout_seconds": 1.0,
                    }
                ]
            },
            "valid_intents": ["confirmed", "need_permission"],
        },
        agent_executor=lambda prompt: "confirmed",
    )

    assert result.status_code == PhaseStatusCode.NEED_PERMISSION
    event = next(item for item in result.events if item.get("type") == "script_hook")
    assert event["status"] == "timeout"
    assert event["exit_code"] is None
    assert "partial-bytes" in event["stdout"]
    assert "timed-bytes" in event["stderr"]
