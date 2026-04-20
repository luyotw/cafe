"""Tests for GenericPhase."""

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
        "plan": "Hello {who}\n",
        "workflow-common": "Read blackboard first.\n",
        "review": "Review the latest changes.\n",
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


def test_build_prompt_includes_files_and_checklist_guard(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/workflow-common"],
        context={
            "who": "team",
            "blackboard_path": ".cafe/issues/demo/blackboard.json",
            "handoff_summary": "Implement cafe skill rm with interactive multi-select and confirmation.",
            "next_step_path": ".cafe/issues/demo/next_step.txt",
        },
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
        questions_xml_file=Path("questions.xml"),
    )
    assert "Shared skills:" in prompt
    assert "/workflow-common" in prompt
    assert "Phase skill: /plan" in prompt
    assert "Runtime files:" in prompt
    assert "output_file=out.md" in prompt
    assert "checklist_file=checklist.md" in prompt
    assert "questions_file=questions.xml" in prompt
    assert "blackboard_file=.cafe/issues/demo/blackboard.json" in prompt
    assert "next_step_file=.cafe/issues/demo/next_step.txt" in prompt
    assert "Runtime context:" in prompt
    assert "Do NOT return a status code until ALL checklist items are marked as [x]." in prompt
    assert "Latest workflow handoff from blackboard:" in prompt
    assert "Implement cafe skill rm" in prompt
    assert "verify whether the requested state change has actually happened" in prompt
    assert "do not treat an old artifact or a closed external object as completion" in prompt
    assert "Blackboard digest:" not in prompt


def test_parse_response_extracts_status_and_goto(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    status, goto_target = phase.parse_response(
        response="CAFE_CONFIRMED\nCAFE_GOTO:review",
        valid_status_codes=[PhaseStatusCode.CONFIRMED],
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
        skill_name="plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/workflow-common"],
        step_def={
            "hooks": {"before_execute": ["StopHook"]},
            "valid_status_codes": ["CAFE_NEED_CLARIFICATION"],
        },
        agent_executor=lambda prompt: calls.append(prompt) or "CAFE_CONFIRMED",
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
    responses = iter(["CAFE_CONFIRMED", "CAFE_CONFIRMED"])

    result = phase.execute(
        skill_name="plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/workflow-common"],
        step_def={
            "hooks": {
                "prepare_input": ["PrepareHook"],
                "after_execute": ["RetryHook"],
            },
            "valid_status_codes": ["CAFE_CONFIRMED"],
        },
        agent_executor=lambda prompt: prompts.append(prompt) or next(responses),
    )

    assert len(prompts) == 2
    assert "Shared skills:" in prompts[0]
    assert "Phase skill: /plan" in prompts[0]
    assert "Shared skills:" in prompts[1]
    assert "Phase skill: /plan" in prompts[1]
    assert result.status_code == PhaseStatusCode.CONFIRMED


def test_execute_skips_publish_when_artifact_not_ready(tmp_path: Path) -> None:
    phase = GenericPhase(
        _setup_loader(tmp_path),
        hook_registry={"NoArtifactHook": NoArtifactHook, "PublishHook": PublishHook},
    )

    result = phase.execute(
        skill_name="plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/workflow-common"],
        step_def={
            "hooks": {
                "after_execute": ["NoArtifactHook"],
                "publish_output": ["PublishHook"],
            },
            "valid_status_codes": ["CAFE_CONFIRMED"],
        },
        agent_executor=lambda prompt: "CAFE_CONFIRMED",
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
        skill_name="plan",
        skill_invocation="/plan",
        shared_skill_invocations=["/workflow-common"],
        step_def={
            "hooks": {
                "publish_output": ["PublishOverrideHook"],
            },
            "valid_status_codes": ["CAFE_CONFIRMED", "CAFE_READY_FOR_REVIEW"],
        },
        output_file=tmp_path / "out.md",
        agent_executor=lambda prompt: "CAFE_CONFIRMED",
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

    invocation = phase.prepare_skill(skill_name="plan", agent_cli=AgentCLI.CODEX)

    assert invocation == "$cafe-plan"
    assert (tmp_path / "home" / ".codex" / "skills" / "cafe-plan" / "SKILL.md").exists()


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
    assert (tmp_path / "home" / ".codex" / "skills" / "cafe-workflow-common" / "SKILL.md").exists()
    assert (tmp_path / "home" / ".codex" / "skills" / "cafe-review" / "SKILL.md").exists()


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
