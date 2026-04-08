"""Tests for GenericPhase."""

from pathlib import Path

import pytest

from cafe.core.hooks import HookResult
from cafe.core.status_codes import PhaseStatusCode
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.loader import SkillLoader


def _setup_loader(tmp_path: Path) -> SkillLoader:
    builtin = tmp_path / "builtin" / "skills" / "plan"
    builtin.mkdir(parents=True, exist_ok=True)
    (builtin / "SKILL.md").write_text(
        "---\nname: plan\ndescription: desc\n---\n\nHello {who}\n",
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
        context={"who": "team"},
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
        questions_xml_file=Path("questions.xml"),
    )
    assert "Hello team" in prompt
    assert "Do NOT return a status code until ALL checklist items are marked as [x]." in prompt
    assert "questions.xml" in prompt


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


def test_execute_short_circuits_when_before_execute_stops(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path), hook_registry={"StopHook": StopHook})
    calls: list[str] = []

    result = phase.execute(
        skill_name="plan",
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
    assert "Hello prepared" in prompts[0]
    assert "Hello retried" in prompts[1]
    assert result.status_code == PhaseStatusCode.CONFIRMED


def test_execute_skips_publish_when_artifact_not_ready(tmp_path: Path) -> None:
    phase = GenericPhase(
        _setup_loader(tmp_path),
        hook_registry={"NoArtifactHook": NoArtifactHook, "PublishHook": PublishHook},
    )

    result = phase.execute(
        skill_name="plan",
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
