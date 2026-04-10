"""Tests for playbook runner."""
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.playbook_runner import PlaybookRunner, StepExecutionResult
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.loader import SkillLoader


def _build_loader(tmp_path: Path) -> GenericPhase:
    skill_dir = tmp_path / "builtin" / "skills" / "spec_first"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: spec_first\ndescription: d\n---\n\ntext\n",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return GenericPhase(loader)


def test_runner_can_advance_and_loop_back(tmp_path: Path) -> None:
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "review"},
            },
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_status_codes": ["CAFE_NEEDS_CHANGES", "CAFE_CONFIRMED"],
                "on": {"CAFE_NEEDS_CHANGES": "develop", "CAFE_CONFIRMED": "_done"},
            },
        },
    }
    responses = iter(
        [
            ("CAFE_CONFIRMED", {"code": "d1"}),
            ("CAFE_NEEDS_CHANGES", {"review": "r1"}),
            ("CAFE_CONFIRMED", {"code": "d2"}),
            ("CAFE_CONFIRMED", {"review": "r2"}),
        ]
    )

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return next(responses)

    runner = PlaybookRunner(
        issue_dir=tmp_path / ".cafe" / "issues" / "demo",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=10)
    assert result.completed is True
    assert result.final_step == "review"
    assert result.final_status_code == "CAFE_CONFIRMED"


def test_runner_ignores_invalid_goto_target_and_falls_back(tmp_path: Path) -> None:
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "allowed_goto": ["review"],
                "on": {"CAFE_CONFIRMED": "review"},
            },
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        if step_name == "develop":
            return ("CAFE_CONFIRMED\nCAFE_GOTO:not_exist", {})
        return ("CAFE_CONFIRMED", {})

    runner = PlaybookRunner(
        issue_dir=tmp_path / ".cafe" / "issues" / "demo",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run()
    assert result.final_step == "review"
    assert result.final_status_code == "CAFE_CONFIRMED"


def test_runner_uses_allowed_goto_target(tmp_path: Path) -> None:
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "allowed_goto": ["spec"],
                "on": {"CAFE_CONFIRMED": "review"},
            },
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "pr"},
            },
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "plan"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return ("CAFE_CONFIRMED\nCAFE_GOTO:spec", {})

    runner = PlaybookRunner(
        issue_dir=tmp_path / ".cafe" / "issues" / "demo",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run()
    assert result.final_step == "spec"
    assert result.final_status_code == "CAFE_CONFIRMED"


def test_runner_records_transition_source_events(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "allowed_goto": ["review"],
                "on": {"CAFE_CONFIRMED": "plan"},
            },
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        if step_name == "develop":
            return ("CAFE_CONFIRMED\nCAFE_GOTO:review", {})
        return ("CAFE_CONFIRMED", {})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run()

    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("develop")
    transition_events = [event for event in blackboard.events if event.event_type == "transition"]
    assert result.final_step == "review"
    assert transition_events
    assert transition_events[0].data["source"] == "goto"
    assert transition_events[0].data["to"] == "review"


def test_runner_pauses_when_step_needs_clarification(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_status_codes": ["CAFE_NEED_CLARIFICATION"],
                "on": {"CAFE_NEED_CLARIFICATION": "spec"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return ("CAFE_NEED_CLARIFICATION", {})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=5)

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "CAFE_NEED_CLARIFICATION"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    pause_events = [event for event in blackboard.events if event.event_type == "workflow_paused"]
    assert pause_events
    assert pause_events[-1].data["status_code"] == "CAFE_NEED_CLARIFICATION"


def test_runner_pauses_when_develop_returns_no_changes_needed(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_NO_CHANGES_NEEDED"],
                "on": {"CAFE_NO_CHANGES_NEEDED": "develop"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return ("CAFE_NO_CHANGES_NEEDED", {})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=5)

    assert result.completed is False
    assert result.final_step == "develop"
    assert result.final_status_code == "CAFE_NO_CHANGES_NEEDED"
    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    pause_events = [event for event in blackboard.events if event.event_type == "workflow_paused"]
    assert pause_events
    assert pause_events[-1].data["status_code"] == "CAFE_NO_CHANGES_NEEDED"


def test_runner_pauses_instead_of_failing_when_status_code_is_missing(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(response="finished but no code", artifacts={}, status_code=None)

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=5)

    assert result.completed is False
    assert result.final_step == "develop"
    assert result.final_status_code == "NO_STATUS_CODE"


def test_runner_auto_continues_after_consuming_user_input(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_status_codes": ["CAFE_READY_FOR_REVIEW", "CAFE_CONFIRMED"],
                "on": {"CAFE_READY_FOR_REVIEW": "spec", "CAFE_CONFIRMED": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            },
        },
    }
    executed_steps: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        executed_steps.append(step_name)
        if executed_steps == ["spec"]:
            return StepExecutionResult(
                response="CAFE_READY_FOR_REVIEW",
                artifacts={},
                auto_continue=True,
            )
        return StepExecutionResult(response="CAFE_CONFIRMED", artifacts={})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=5)

    assert result.completed is True
    assert result.final_step == "plan"
    assert executed_steps == ["spec", "spec", "plan"]


def test_runner_accepts_explicit_status_code_without_response_body(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            },
        },
    }
    executed_steps: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        executed_steps.append(step_name)
        if step_name == "spec":
            return StepExecutionResult(
                response="",
                artifacts={},
                status_code="CAFE_CONFIRMED",
            )
        return StepExecutionResult(
            response="CAFE_CONFIRMED",
            artifacts={},
            status_code="CAFE_CONFIRMED",
        )

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=5)

    assert result.completed is True
    assert result.final_step == "plan"
    assert executed_steps == ["spec", "plan"]


def test_runner_resumes_from_next_step_when_current_step_already_confirmed(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    spec_dir = issue_dir / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "status.json").write_text(
        '{"status_code":"CAFE_CONFIRMED","iteration":3}',
        encoding="utf-8",
    )
    plan_dir = issue_dir / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)

    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            },
        },
    }
    executed_steps: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        executed_steps.append(step_name)
        return StepExecutionResult(
            response="CAFE_CONFIRMED",
            artifacts={},
            status_code="CAFE_CONFIRMED",
        )

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=5)

    assert result.completed is True
    assert result.final_step == "plan"
    assert executed_steps == ["plan"]


def test_runner_does_not_pause_when_interactive_step_requests_review_iteration(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_READY_FOR_REVIEW", "CAFE_CONFIRMED"],
                "on": {"CAFE_READY_FOR_REVIEW": "plan", "CAFE_CONFIRMED": "_done"},
            },
        },
    }
    executed_steps: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        executed_steps.append(step_name)
        if len(executed_steps) == 1:
            return StepExecutionResult(
                response="CAFE_READY_FOR_REVIEW",
                artifacts={},
                status_code="CAFE_READY_FOR_REVIEW",
                auto_continue=True,
            )
        return StepExecutionResult(
            response="CAFE_CONFIRMED",
            artifacts={},
            status_code="CAFE_CONFIRMED",
        )

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=5)

    assert result.completed is True
    assert result.final_step == "plan"
    assert executed_steps == ["plan", "plan"]




def test_runner_rejects_reserved_assignee_type_at_runtime(tmp_path: Path) -> None:
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "assignee_type": "human",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return ("CAFE_CONFIRMED", {})

    runner = PlaybookRunner(
        issue_dir=tmp_path / ".cafe" / "issues" / "demo",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    with pytest.raises(RuntimeError, match="assignee_type=human"):
        runner.run()


def test_runner_stops_when_step_exceeds_max_iterations(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "max_iterations": 2,
                "valid_status_codes": ["CAFE_NEEDS_CHANGES"],
                "on": {"CAFE_NEEDS_CHANGES": "review"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return ("CAFE_NEEDS_CHANGES", {})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )

    with pytest.raises(RuntimeError, match="exceeded max_iterations=2"):
        runner.run(max_transitions=5)

    blackboard = BlackboardStore(issue_dir).load_or_create("review")
    loop_events = [event for event in blackboard.events if event.event_type == "loop_detected"]
    assert loop_events
    assert loop_events[-1].data["max_iterations"] == 2


def test_runner_records_hop_limit_event(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "review"},
            },
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_status_codes": ["CAFE_NEEDS_CHANGES"],
                "on": {"CAFE_NEEDS_CHANGES": "develop"},
            },
        },
    }
    responses = iter(
        [
            ("CAFE_CONFIRMED", {}),
            ("CAFE_NEEDS_CHANGES", {}),
            ("CAFE_CONFIRMED", {}),
        ]
    )

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return next(responses)

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )

    with pytest.raises(RuntimeError, match=r"max transition limit \(2\)"):
        runner.run(max_transitions=2)

    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    hop_events = [event for event in blackboard.events if event.event_type == "hop_limit_reached"]
    assert hop_events
    assert hop_events[-1].data["max_transitions"] == 2
