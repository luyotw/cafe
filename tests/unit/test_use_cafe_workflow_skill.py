"""Tests for bundled use-cafe-workflow skill guidance."""

import subprocess
import sys
from pathlib import Path

from cafe.core.playbook import confirmation_gate_steps
from cafe.core.status_codes import (
    PhaseStatusCode,
    effective_step_handoff_intents,
    effective_step_status_codes,
)
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_use_cafe_workflow_skill_makes_driver_own_alignment_decisions() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "## Driver-Owned Alignment" in skill
    assert "Bundled playbooks omit `alignment:` configuration" in normalized
    assert "`proposal_delta`" in skill
    assert "`strategic_ground`" in skill
    assert "`mandate_level`" in skill
    assert "`relation`" in skill
    assert "`within` + `escalate`: stop for the user" in normalized
    assert "Except for an explicit `escalate` mandate" in normalized
    assert "If `within` and `mandate_level` is `agent`" in normalized
    assert "If `mandate_level` is `escalate`" in normalized
    assert "Do not re-evaluate an unchanged scope" in normalized
    assert "compatibility evidence, not as proof that the user must decide" in normalized
    assert "plain text must not approve a core checkpoint" in normalized


def test_use_cafe_workflow_skill_requires_playbook_derived_kickoff_contract() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "## Kickoff Contract (first blocking gate)" in skill
    assert "Before `cafe prepare`, any repository mutation, or the first `cafe make`" in skill
    assert "cafe playbook confirmation-gates <playbook-id>" in skill
    assert '`steps.<step>."on".confirm_output`' in skill
    assert "Do not reuse a repo default or another issue's contract silently" in normalized
    assert "their union must equal the derived" in normalized
    assert "driver_confirmable" in skill
    assert "`need_clarification` and `need_permission` are reactive" in normalized
    assert "Alignment is a proactive driver decision" in normalized
    assert "alignment_policy:" not in skill
    assert "alignment_checkpoint: driver_resolvable_when_clear" in skill
    assert ".cafe/issues/<issue-name>/issue.yaml" in skill
    assert "is not parsed or auto-approved by CAFE" in normalized
    assert "scripts/format_kickoff_contract.py" in skill
    assert "every playbook phase" in normalized
    assert "whether execution will stop for the user" in normalized


def test_kickoff_contract_formatter_lists_all_phases_and_confirmation_owners(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        """\
version: 1
mandate:
  preset: technical-led
  playbook_id: default
  axes:
    product_scope: {level: escalate, grounds: [roadmap, positioning]}
    technical: {level: agent, grounds: [engineering_guidelines]}
  out_of_mandate: [pricing, production deploy approval]
""",
        encoding="utf-8",
    )
    script = (
        PROJECT_ROOT
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "use-cafe-workflow"
        / "scripts"
        / "format_kickoff_contract.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "default",
            "--issue-name",
            "issue346",
            "--effective-locale",
            "zh-TW",
            "--locale-source",
            "user thread override",
            "--user-required",
            "--driver-confirmable",
            "spec",
            "plan",
            "--worktree",
            ".cafe/worktrees/issue346",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "## Kickoff Contract — issue346" in result.stdout
    assert "| spec | pm | cafe-spec | 是 | driver（驗證後繼續） | 否 |" in result.stdout
    assert "| plan | developer | cafe-plan | 是 | driver（驗證後繼續） | 否 |" in result.stdout
    assert "| develop | developer | cafe-develop | 否 | — | 否 |" in result.stdout
    assert "| review | reviewer | cafe-review | 否 | — | 否 |" in result.stdout
    assert "| pr | developer | cafe-pr | 否 | — | 否 |" in result.stdout
    assert "| effective_locale | zh-TW (user thread override) |" in result.stdout
    assert "| need_clarification | user_required | 否 |" in result.stdout
    assert "| product_scope | escalate | roadmap, positioning |" in result.stdout


def test_kickoff_contract_formatter_rejects_incomplete_gate_partition(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    script = (
        PROJECT_ROOT
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "use-cafe-workflow"
        / "scripts"
        / "format_kickoff_contract.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "default",
            "--issue-name",
            "issue346",
            "--user-required",
            "spec",
            "--worktree",
            ".cafe/worktrees/issue346",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unassigned gates: plan" in result.stderr


def test_builtin_confirmation_gate_candidates_come_from_playbook_declarations() -> None:
    loader = PlaybookLoader(project_root=PROJECT_ROOT)

    actual = {
        playbook_id: confirmation_gate_steps(loader.load_model(playbook_id).model)
        for playbook_id in (
            "default",
            "simple",
            "tdd",
            "editorial",
            "hotfix",
            "incident",
            "research",
        )
    }

    assert actual == {
        "default": ("spec", "plan"),
        "simple": ("spec",),
        "tdd": ("spec", "plan"),
        "editorial": ("brief",),
        "hotfix": (),
        "incident": (),
        "research": (),
    }


def test_bundled_playbooks_do_not_delegate_alignment_judgment_to_core() -> None:
    loader = PlaybookLoader(project_root=PROJECT_ROOT)

    for playbook_id in (
        "default",
        "simple",
        "tdd",
        "editorial",
        "hotfix",
        "incident",
        "research",
    ):
        playbook = loader.load_model(playbook_id).model
        for step in playbook.steps.values():
            assert step.alignment is None
            assert "alignment_checkpoint" not in step.on
            assert "alignment_checkpoint" not in step.valid_intents
            assert "AlignmentCheckpointGate" not in step.hooks.prepare_input
            step_def = step.model_dump(by_alias=True)
            assert (
                PhaseStatusCode.ALIGNMENT_CHECKPOINT
                not in effective_step_status_codes(step_def)
            )
            assert (
                "alignment_checkpoint"
                not in effective_step_handoff_intents(step_def)
            )
            assert (
                GenericPhase._detect_status_code(
                    response="alignment_checkpoint",
                    step_def=step_def,
                )
                is None
            )


def test_use_cafe_workflow_skill_protects_issue_overrides() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "### Issue overrides are opt-in only" in skill
    assert "The `issues:` section is protected" in skill
    assert "do not write to this section" in skill
    assert "Do not create `issues.<issue-name>` just because" in skill
    assert "Do not store workflow progress, baton state, phase outputs" in skill
    assert "leave `issues:` untouched unless the user explicitly requested" in skill


def test_use_cafe_workflow_bounds_diagnosis_and_repairs_only_declarative_layers() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "## Bounded Self-Diagnosis And Declarative Repair" in skill
    assert "Playbook declarative defect" in skill
    assert "Phase declarative defect" in skill
    assert "Driver or CAFE core defect" in skill
    assert "activate `write-cafe-playbook`" in normalized
    assert "activate `write-cafe-phase`" in normalized
    assert "Do not invent or require a `write-cafe-driver` skill" in normalized
    assert "search open and closed issues read-only" in normalized
    assert "https://github.com/luyotw/cafe/issues" in skill
    assert (
        "never create, comment on, or close an upstream issue without explicit user" in normalized
    )
    assert "stale installed skill copies" in normalized
    assert "unconfirmed or transient failure" in normalized


def test_use_cafe_workflow_uses_playbook_conversation_locale() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "## Conversation Locale" in skill
    assert "playbook.conversation_locale" in skill
    assert "cafe playbook confirmation-gates <playbook-id>" in skill
    assert "`Conversation locale:` line" in normalized
    assert "For `auto`, use the language of the user's current request" in normalized
    assert "conversation_locale: en-US (from playbook: default)" in normalized
    assert "required kickoff field, not a confirmation gate" in normalized
    assert "asking why a language was used is not an override" in normalized
    assert "Never claim that this skill lacks a conversation locale rule" in normalized
    assert "Do not copy the conversation locale into `issue.yaml`" in normalized
    assert "commands, paths, playbook/step names, intents, artifact keys" in normalized
