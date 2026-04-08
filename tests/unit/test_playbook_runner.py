"""Tests for playbook runner."""

from pathlib import Path

import pytest

from cafe.core.playbook_runner import PlaybookRunner
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
                "on": {"CAFE_NEEDS_CHANGES": "develop", "CAFE_CONFIRMED": "pr"},
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


def test_runner_rejects_invalid_goto_target(tmp_path: Path) -> None:
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "review"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return ("CAFE_CONFIRMED\nCAFE_GOTO:not_exist", {})

    runner = PlaybookRunner(
        issue_dir=tmp_path / ".cafe" / "issues" / "demo",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    with pytest.raises(ValueError, match="Invalid CAFE_GOTO target"):
        runner.run()


def test_runner_rejects_goto_to_existing_but_disallowed_step(tmp_path: Path) -> None:
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
    with pytest.raises(ValueError, match="not in allowed transitions"):
        runner.run()
