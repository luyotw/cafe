"""Tests for bundled use-cafe-workflow skill guidance."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cafe.core.playbook import confirmation_gate_steps
from cafe.core.status_codes import (
    PhaseStatusCode,
    effective_step_handoff_intents,
    effective_step_status_codes,
)
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow"

DEFAULT_PHASE_CHAINS = {
    "spec": "gemini:requirements-main,copilot:requirements-fallback",
    "plan": "cursor-agent:planning-main,gemini:planning-fallback",
    "develop": "copilot:implementation-main,cursor-agent:implementation-fallback",
    "review": "gemini:review-main,copilot:review-fallback",
    "pr": "cursor-agent:publication-main,gemini:publication-fallback",
}

DEFAULT_PHASE_RATIONALES = {
    "spec": "frontier: high requirements reasoning and public-contract risk; equivalent fallback",
    "plan": "frontier: high architecture reasoning and integration risk; equivalent fallback",
    "develop": "balanced: bounded implementation with integration tests; equivalent fallback",
    "review": "frontier: high correctness and security reasoning; stronger fallback",
    "pr": "efficiency: routine publication artifact with independent host validation; equivalent fallback",
}

PRIMARY_ONLY_PHASE_CHAINS = {
    "spec": "claude:requirements-main",
    "plan": "claude:planning-main",
    "develop": "claude:implementation-main",
    "review": "claude:review-main",
    "pr": "claude:publication-main",
}


def _phase_chain_args(chains: dict[str, str] | None = None) -> list[str]:
    result: list[str] = []
    for step, chain in (chains or DEFAULT_PHASE_CHAINS).items():
        result.extend(["--phase-chain", f"{step}={chain}"])
    return result


def _phase_rationale_args(rationales: dict[str, str] | None = None) -> list[str]:
    result: list[str] = []
    for step, rationale in (rationales or DEFAULT_PHASE_RATIONALES).items():
        result.extend(["--phase-rationale", f"{step}={rationale}"])
    return result


def _preflight_args() -> list[str]:
    return [
        "--update-preflight",
        json.dumps(
            {
                "checked_at": "2026-08-27T12:00:00Z",
                "status": "current",
                "installed_version": "0.3.2",
                "latest_version": "0.3.2",
                "decision": "not_needed",
                "comparison_token": "update-token",
                "post_change_evidence": "not_applicable",
            }
        ),
        "--catalog-preflight",
        json.dumps(
            {
                "checked_at": "2026-08-27T12:00:01Z",
                "status": "identical",
                "comparison_token": "catalog-token",
                "effective_digests": {
                    "playbook": "playbook-digest",
                    "phase": "phase-digest",
                    "agent": "agent-digest",
                },
                "decision": "not_needed",
                "post_change_evidence": "not_applicable",
            }
        ),
    ]


def _read_skill_resource(path: str) -> str:
    return (SKILL_ROOT / path).read_text(encoding="utf-8")


def _kickoff_formatter_command(strategic_context: Path, *extra_args: str) -> list[str]:
    return [
        sys.executable,
        str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
        "standard",
        "--issue-name",
        "issue346",
        "--playbook-rationale",
        (
            "Repository policy requires the standard graph; QA is not independently "
            "required, so standard-qa is unnecessary."
        ),
        "--issue-nature",
        "feature/integration",
        "--issue-scale",
        "medium",
        "--model-adjustment-authority",
        "driver_autonomous",
        *extra_args,
        *_preflight_args(),
        "--risk-factor",
        "public contract",
        "--assessment-rationale",
        "Changes a public workflow contract across runtime and CLI.",
        *_phase_chain_args(),
        *_phase_rationale_args(),
        "--effective-locale",
        "zh-TW",
        "--locale-source",
        "user thread override",
        "--repository-content-locale",
        "zh-TW",
        "--user-required",
        "--driver-confirmable",
        "spec",
        "plan",
        "--worktree",
        ".cafe/worktrees/issue346",
        "--strategic-context",
        str(strategic_context),
    ]


def _load_script_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_preflight_cache(
    cache_file: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "preflight_cache.py"),
            "--cache-file",
            str(cache_file),
            *args,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_use_cafe_workflow_uses_progressive_disclosure() -> None:
    skill = _read_skill_resource("SKILL.md")
    references = (
        "playbook_selection.md",
        "kickoff.md",
        "strategic_context.md",
        "model_selection.md",
        "running_workflow.md",
        "handoffs_and_alignment.md",
        "diagnosis_and_repair.md",
        "convergent_pr_review.md",
        "correction_ab_experiment.md",
        "issue_decomposition.md",
        "project_global_skill_sync.md",
    )

    assert "## Progressive disclosure" in skill
    assert len(skill.splitlines()) <= 150
    for name in references:
        assert f"references/{name}" in skill
        assert (SKILL_ROOT / "references" / name).is_file()

    assert "## Conversation Locale" not in skill
    assert "## Driver-Owned Alignment" not in skill
    assert "## Bounded Self-Diagnosis And Declarative Repair" not in skill


def test_use_cafe_workflow_preflights_runtime_and_all_catalogs_before_execution() -> None:
    skill = _read_skill_resource("SKILL.md")
    running = _read_skill_resource("references/running_workflow.md")
    reference = _read_skill_resource("references/project_global_skill_sync.md")
    normalized = " ".join(reference.split())
    normalized_lower = normalized.lower()
    normalized_running = " ".join(running.split())

    assert "references/project_global_skill_sync.md" in skill
    assert "project_global_skill_sync.md" in running
    assert "cafe update check --json" in reference
    assert "cafe catalog check --json" in reference
    assert "playbooks, phase skills, and agents" in normalized
    assert "new kickoff" in normalized
    assert "stale kickoff contract" in normalized
    assert "stay silent" in normalized_lower
    assert "one combined catalog decision" in normalized
    assert "separate approval scopes" in normalized
    assert "must not be described as current" in normalized
    assert "continues with the installed version" in normalized
    assert "over_budget" in reference
    assert "affected_entry_ids" in reference
    assert "discovery_complete" in reference
    assert "must not paginate" in normalized
    assert "one exact combined `--entry` scope" in normalized
    assert "cafe update apply --token" in reference
    assert "cafe catalog sync-global --token" in reference
    assert "re-run both read-only checks" in normalized_lower
    assert "re-render and reconfirm the kickoff contract" in normalized
    assert "before every start or resume" in normalized_running.lower()


def test_skill_local_catalog_sync_path_has_no_write_authority() -> None:
    script = SKILL_ROOT / "scripts" / "project_global_skill_sync.py"

    if script.exists():
        source = script.read_text(encoding="utf-8")
        assert "update_skills" not in source
        assert "os.replace" not in source
        assert "shutil.copytree" not in source


def test_use_cafe_workflow_skill_makes_driver_own_alignment_decisions() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/handoffs_and_alignment.md")
    normalized = " ".join(reference.split())

    assert "references/handoffs_and_alignment.md" in skill
    assert "## Driver-owned alignment" in reference
    assert "Bundled playbooks omit `alignment:` configuration" in normalized
    assert "`proposal_delta`" in reference
    assert "`strategic_ground`" in reference
    assert "`mandate_level`" in reference
    assert "`relation`" in reference
    assert "`within` + `escalate`: stop" in normalized
    assert "Except for an explicit `escalate` mandate" in normalized
    assert "`within` + `agent`: continue without asking" in normalized
    assert "Do not re-evaluate unchanged scope" in normalized
    assert "checkpoint is evidence, not proof the user must decide" in normalized
    assert "plain text must not approve the checkpoint" in normalized


def test_use_cafe_workflow_uses_structured_human_task_resume_payloads() -> None:
    reference = _read_skill_resource("references/handoffs_and_alignment.md")
    running = _read_skill_resource("references/running_workflow.md")
    normalized = " ".join(reference.split())
    normalized_running = " ".join(running.split())

    assert '"task":"output-review","decision":"confirm"' in reference
    assert '"task":"clarification-answers","answers"' in reference
    assert '"task":"clarification-feedback","feedback"' in reference
    assert '"human_task_id":"<active-human-task-id>"' in reference
    assert "Do not guess or reuse an old task ID" in normalized
    assert "runtime accepts plain text only for a declared `feedback` schema" in normalized
    assert '--user-input "confirmed"' not in reference
    assert "resolve the active HumanTask and its input schema" in normalized_running
    assert "current `human_task_id`" in normalized_running
    assert "Plain text is valid only for a task that explicitly declares" in normalized_running
    assert '--user-input "<confirmed answer or correction>"' not in running


def test_use_cafe_workflow_skill_requires_playbook_derived_kickoff_contract() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/kickoff.md")
    selection = _read_skill_resource("references/playbook_selection.md")
    normalized = " ".join(reference.split())

    assert "references/kickoff.md" in skill
    assert "## Kickoff contract: first blocking gate" in reference
    assert (
        "Before `cafe prepare`, any repository mutation, or the first workflow execution"
        in normalized
    )
    assert "cafe playbook confirmation-gates <playbook-id>" in reference
    assert '`steps.<step>."on".confirm_output`' in reference
    assert "Do not reuse another issue's contract" in normalized
    assert "union to equal the candidates" in normalized
    assert "driver_confirmable" in reference
    assert "reactive interruptions, not scheduled candidates" in normalized
    assert "Alignment is a proactive driver decision" in normalized
    assert "alignment_policy:" not in reference
    assert "alignment_checkpoint: driver_resolvable_when_clear" in reference
    assert "`repository_content_locale`" in reference
    assert "explicitly ask the user to confirm `repository_content_locale`" in normalized
    assert "do not treat inference or a playbook locale as confirmation" in normalized
    assert "repository_language:" in reference
    assert ".cafe/issues/<issue-name>/issue.yaml" in reference
    assert "scripts/format_kickoff_contract.py" in reference
    assert "playbook_selection_rationale" in reference
    assert "independent-QA decision" in reference
    assert "cafe playbook list" in selection
    assert "cafe playbook show <id>" in selection
    assert "repository instructions require an independent QA" in selection
    assert "closest rejected candidates" in selection
    assert "do not infer behavior from a playbook name" in " ".join(selection.split())
    assert "every phase, role, skill, scheduled gate" in normalized
    assert "one primary and zero or more explicitly confirmed fallbacks" in normalized
    assert "model-adjustment authority" in normalized
    assert '--risk-factor "<risk factor; repeat as needed>"' in reference
    assert '--assessment-rationale "<repository evidence for nature and scale>"' in reference


def test_use_cafe_workflow_keeps_playbook_selection_issue_owned() -> None:
    skill = _read_skill_resource("SKILL.md")
    selection = _read_skill_resource("references/playbook_selection.md")
    strategic = _read_skill_resource("references/strategic_context.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    normalized_skill = " ".join(skill.split())
    normalized_selection = " ".join(selection.split())
    normalized_strategic = " ".join(strategic.split())
    normalized_kickoff = " ".join(kickoff.split())

    assert "Keep playbook selection issue-owned" in normalized_skill
    assert "Never write or update a playbook default" in normalized_skill
    assert "are not playbook-selection sources" in normalized_selection
    assert (
        "legacy `settings.playbook`, top-level `playbook`, or `playbook_id`"
        in normalized_selection
    )
    assert "persist the selected `playbook_id` only in" in normalized_selection
    assert "Strategic context is not playbook configuration" in normalized_strategic
    assert (
        "Do not add `playbook_id` to `mandate` or `issues.<name>`"
        in normalized_strategic
    )
    assert "playbook_id: standard" not in strategic
    assert (
        "Do not write the selected playbook to `.cafe/config.yaml`" in normalized_kickoff
    )
    assert "`cafe init --no-interactive`" in kickoff
    assert "`.cafe/config.yaml` has no playbook key" in normalized_kickoff
    assert kickoff.count("--playbook <playbook-id>") == 3
    assert "Verify that `cafe prepare` persisted the active `playbook_id`" in normalized_kickoff


def test_driver_selection_is_evidence_based_across_every_effective_candidate() -> None:
    """U9 — recommendation uses confirmed scope, graph sufficiency, and applicability."""
    skill = _read_skill_resource("SKILL.md")
    selection = _read_skill_resource("references/playbook_selection.md")
    normalized_skill = " ".join(skill.split())
    normalized = " ".join(selection.split())

    assert "direct playbook choice" in normalized
    assert "durable" in normalized
    assert "every valid effective playbook" in normalized
    assert "project, Global, and builtin" in normalized
    assert "resolved graph" in normalized
    assert "before comparing applicability" in normalized
    assert "missing applicability" in normalized
    assert "ineligible for automatic recommendation" in normalized
    assert "cafe playbook validate <id> --strict" in normalized
    assert "names and catalog sources are not ranking signals" in normalized
    assert "smallest sufficient graph" in normalized
    assert "closest rejected candidates" in normalized
    assert "speculative future work" in normalized
    assert "ask the user for an explicit decision" in normalized
    assert "every effective candidate" in normalized_skill
    assert "applicability" in normalized_skill


def test_kickoff_contract_formatter_lists_all_phases_and_confirmation_owners(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        """\
version: 1
mandate:
  preset: technical-led
  playbook_id: standard
  axes:
    product_scope: {level: escalate, grounds: [roadmap, positioning]}
    technical: {level: agent, grounds: [engineering_guidelines]}
  out_of_mandate: [pricing, production deploy approval]
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        _kickoff_formatter_command(strategic_context),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "## Kickoff Contract — issue346" in result.stdout
    assert (
        "| playbook_selection_rationale | Repository policy requires the standard graph; "
        "QA is not independently required, so standard-qa is unnecessary. |" in result.stdout
    )
    assert "| spec | pm | cafe-spec | 是 | driver（驗證後繼續） | 否 |" in result.stdout
    assert "| plan | developer | cafe-plan | 是 | driver（驗證後繼續） | 否 |" in result.stdout
    assert "| develop | developer | cafe-develop | 否 | — | 否 |" in result.stdout
    assert "| review | reviewer | cafe-review | 否 | — | 否 |" in result.stdout
    assert "| pr | developer | cafe-pr | 否 | — | 否 |" in result.stdout
    assert "| effective_locale | zh-TW (user thread override) |" in result.stdout
    assert "| repository_content_locale | zh-TW |" in result.stdout
    assert "| issue_nature | feature/integration |" in result.stdout
    assert "| issue_scale | medium |" in result.stdout
    assert "| model_adjustment_authority | driver_autonomous |" in result.stdout
    assert "| driver_execution.mode | continuous |" in result.stdout
    assert "| driver_execution.poll_interval_seconds | 180 |" in result.stdout
    assert "### Preflight evidence" in result.stdout
    assert "| runtime_update.status | current |" in result.stdout
    assert "| runtime_update.versions | 0.3.2 → 0.3.2 |" in result.stdout
    assert "| catalog.status | identical |" in result.stdout
    assert "| catalog.effective_digests |" in result.stdout
    assert "playbook=playbook-digest" in result.stdout
    assert "### Phase model chains — driver-assessed" in result.stdout
    assert (
        "| develop | copilot:implementation-main | "
        "cursor-agent:implementation-fallback | --phase-chain | balanced:" in result.stdout
    )
    assert (
        "| review | gemini:review-main | copilot:review-fallback | --phase-chain | frontier:" in result.stdout
    )
    assert (
        "| pr | cursor-agent:publication-main | gemini:publication-fallback | --phase-chain | efficiency:"
        in result.stdout
    )
    assert (
        "| review | cafe-review | review | high | correctness, security | "
        "equivalent_or_stronger | declared |" in result.stdout
    )
    assert "| need_clarification | user_required | 否 |" in result.stdout
    assert "| product_scope | escalate | roadmap, positioning |" in result.stdout
    assert result.stdout.count("| playbook_id |") == 1


def test_kickoff_contract_documents_persisted_preflight_and_reconfirmation() -> None:
    kickoff = _read_skill_resource("references/kickoff.md")
    normalized = " ".join(kickoff.split())

    assert "preflight:" in kickoff
    assert "runtime_update:" in kickoff
    assert "catalogs:" in kickoff
    assert "checked_at:" in kickoff
    assert "comparison_token:" in kickoff
    assert "effective_digests:" in kickoff
    assert "post_change_evidence:" in kickoff
    assert "behavior_changed:" in kickoff
    assert "freshly rendered kickoff contract" in normalized


def test_kickoff_contract_formatter_accepts_driver_execution_overrides(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        """\
version: 1
mandate:
  preset: technical-led
  playbook_id: standard
  axes: {}
  out_of_mandate: []
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            "--execution-mode",
            "single_step",
            "--poll-interval-seconds",
            "60",
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| driver_execution.mode | single_step |" in result.stdout
    assert "| driver_execution.poll_interval_seconds | 60 |" in result.stdout


@pytest.mark.parametrize(
    ("extra_args", "expected_error"),
    [
        (("--execution-mode", "invalid"), "invalid choice"),
        (("--poll-interval-seconds", "0"), "must be greater than zero"),
        (("--poll-interval-seconds", "-1"), "must be greater than zero"),
        (
            ("--poll-interval-seconds", "1.5"),
            "must be an integer number of seconds",
        ),
    ],
)
def test_kickoff_contract_formatter_rejects_invalid_driver_execution(
    tmp_path: Path, extra_args: tuple[str, ...], expected_error: str
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text("version: 1\n", encoding="utf-8")

    result = subprocess.run(
        _kickoff_formatter_command(strategic_context, *extra_args),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr


def test_kickoff_formatter_documents_structural_validation_boundary() -> None:
    script = (SKILL_ROOT / "scripts" / "format_kickoff_contract.py").read_text(
        encoding="utf-8"
    )
    kickoff = (SKILL_ROOT / "references" / "kickoff.md").read_text(encoding="utf-8")

    assert "structurally validated" in script
    assert "validates chain structure only; it does not validate model suitability" in kickoff
    assert "driver-assessed" in script


def test_kickoff_contract_formatter_accepts_primary_only_chains(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    rationales = {
        step: "User explicitly selected a primary-only chain; failures stop for adjustment."
        for step in PRIMARY_ONLY_PHASE_CHAINS
    }

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
            "standard",
            "--issue-name",
            "issue-primary-only",
            "--playbook-rationale",
            "The confirmed repository contract selects standard without independent QA.",
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            *_preflight_args(),
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "Focused behavior with bounded verification.",
            *_phase_chain_args(PRIMARY_ONLY_PHASE_CHAINS),
            *_phase_rationale_args(rationales),
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "plan",
            "--current-checkout",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| develop | claude:implementation-main | — | --phase-chain |" in result.stdout


def test_phase_writer_installs_exact_confirmed_chains_atomically(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "role": "developer",
                    "clis": [
                        {"cli": "codex", "model": "gpt-5.6-sol"},
                        {"cli": "claude", "model": "claude-opus-5"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    script = SKILL_ROOT / "scripts" / "write_phase_config.py"

    result = subprocess.run(
        [sys.executable, str(script), "--chains-json", str(chains), "--target", str(target)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    from cafe.utils.phase_config import load_phase_step_model

    resolution = load_phase_step_model(step_name="develop", local_path=target)
    assert resolution.clis == (
        ("codex", "gpt-5.6-sol"),
        ("claude", "claude-opus-5"),
    )


def test_phase_writer_accepts_primary_only_chain(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "role": "developer",
                    "clis": [{"cli": "claude", "model": "claude-opus-5"}],
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "write_phase_config.py"),
            "--chains-json",
            str(chains),
            "--target",
            str(target),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    from cafe.utils.phase_config import load_phase_step_model

    resolution = load_phase_step_model(step_name="develop", local_path=target)
    assert resolution.clis == (("claude", "claude-opus-5"),)


def test_phase_writer_preserves_existing_file_when_candidate_is_invalid(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    target.parent.mkdir()
    original = "develop:\n  clis:\n    - {cli: codex, model: old}\n    - {cli: claude, model: old}\n"
    target.write_text(original, encoding="utf-8")
    chains.write_text(
        json.dumps({"develop": {"clis": [{"cli": "codex", "model": "only-primary"}]}}),
        encoding="utf-8",
    )
    script = SKILL_ROOT / "scripts" / "write_phase_config.py"

    result = subprocess.run(
        [sys.executable, str(script), "--chains-json", str(chains), "--target", str(target)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert target.read_text(encoding="utf-8") == original


def test_phase_writer_rejects_missing_agent_name(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "role": "developer",
                    "clis": [
                        {"cli": "codex", "model": "gpt-5.6-sol"},
                        {"cli": "claude", "model": "claude-opus-5"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "write_phase_config.py"),
            "--chains-json",
            str(chains),
            "--target",
            str(target),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "non-empty agent name" in result.stderr
    assert not target.exists()


def test_phase_writer_preserves_existing_file_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    target.parent.mkdir()
    original = "develop:\n  clis:\n    - {cli: codex, model: old}\n"
    target.write_text(original, encoding="utf-8")
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "clis": [
                        {"cli": "codex", "model": "gpt-5.6-sol"},
                        {"cli": "claude", "model": "claude-opus-5"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    script = SKILL_ROOT / "scripts" / "write_phase_config.py"
    spec = importlib.util.spec_from_file_location("test_write_phase_config", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))

    try:
        module.write_phase_config(chains_file=chains, target=target)
    except OSError:
        pass
    else:
        raise AssertionError("atomic replacement failure must be surfaced")

    assert target.read_text(encoding="utf-8") == original
    assert not list(target.parent.glob(".phases.yaml.*.tmp"))


def test_preflight_cache_reuses_only_success_for_same_cli_fingerprint(
    tmp_path: Path, monkeypatch
) -> None:
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "codex"
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then printf '%s\\n' 'codex 1.0'; exit 0; fi\n"
        "if [ ! -d .git ]; then printf '%s\\n' 'missing disposable git repository' >&2; exit 1; fi\n"
        "printf '%s\\n' "
        "'{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\","
        "\"text\":\"CAFE_PREFLIGHT_OK\"}}' "
        "'{\"type\":\"turn.completed\",\"usage\":{}}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ['PATH']}")
    cache_file = tmp_path / "cache" / "preflight.json"

    miss = _run_preflight_cache(
        cache_file, "candidate-check", "--cli", "codex", "--model", "exact-model-v1"
    )
    assert miss.returncode == 3
    assert json.loads(miss.stdout)["status"] == "miss"

    recorded = _run_preflight_cache(
        cache_file,
        "candidate-probe",
        "--cli",
        "codex",
        "--model",
        "exact-model-v1",
    )
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads(recorded.stdout)["status"] == "fresh"
    assert cache_file.stat().st_mode & 0o777 == 0o600

    hit = _run_preflight_cache(
        cache_file,
        "candidate-probe",
        "--cli",
        "codex",
        "--model",
        "exact-model-v1",
    )
    assert hit.returncode == 0, hit.stderr
    assert json.loads(hit.stdout)["status"] == "hit"

    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'codex version 2.0'\n", encoding="utf-8"
    )
    executable.chmod(0o755)
    changed = _run_preflight_cache(
        cache_file,
        "candidate-check",
        "--cli",
        "codex",
        "--model",
        "exact-model-v1",
    )
    assert changed.returncode == 3
    assert json.loads(changed.stdout)["reason"] == "not_cached"


def test_preflight_cache_can_invalidate_candidate_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "probe-cli"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' 'probe-cli 1.0'\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(executable_dir))
    cache_file = tmp_path / "preflight.json"

    module = _load_script_module(
        SKILL_ROOT / "scripts" / "preflight_cache.py", "preflight_cache_invalidation"
    )
    recorded = module.candidate_record(
        cache_file=cache_file,
        cli="probe-cli",
        model="exact-model",
        resolved_model=None,
        now=1.0,
    )
    assert recorded["status"] == "recorded"
    invalidated = _run_preflight_cache(
        cache_file,
        "candidate-invalidate",
        "--cli",
        "probe-cli",
        "--model",
        "exact-model",
    )
    assert invalidated.returncode == 0, invalidated.stderr
    assert json.loads(invalidated.stdout)["removed"] == 1
    miss = _run_preflight_cache(
        cache_file,
        "candidate-check",
        "--cli",
        "probe-cli",
        "--model",
        "exact-model",
    )
    assert miss.returncode == 3


def test_preflight_cache_runs_and_reuses_cafe_fallback_smoke(tmp_path: Path) -> None:
    cache_file = tmp_path / "preflight.json"
    args = (
        "fallback-smoke",
        "--entry",
        "codex:primary-model",
        "--entry",
        "claude:fallback-model",
    )

    fresh = _run_preflight_cache(cache_file, *args)
    assert fresh.returncode == 0, fresh.stderr
    assert json.loads(fresh.stdout)["status"] == "fresh"
    hit = _run_preflight_cache(cache_file, *args)
    assert hit.returncode == 0, hit.stderr
    assert json.loads(hit.stdout)["status"] == "hit"
    forced = _run_preflight_cache(cache_file, *args, "--force")
    assert forced.returncode == 0, forced.stderr
    assert json.loads(forced.stdout)["status"] == "fresh"


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
            "standard",
            "--issue-name",
            "issue346",
            "--playbook-rationale",
            "The confirmed issue contract selects standard.",
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            *_preflight_args(),
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "One localized behavior and focused tests.",
            "--repository-content-locale",
            "en-US",
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


def test_kickoff_contract_formatter_uses_cafe_python_when_site_packages_are_missing(
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
            "-S",
            str(script),
            "standard",
            "--issue-name",
            "issue346",
            "--playbook-rationale",
            "The confirmed issue contract selects standard.",
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            *_preflight_args(),
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "One localized behavior and focused tests.",
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "plan",
            "--worktree",
            ".cafe/worktrees/issue346",
            "--strategic-context",
            str(strategic_context),
            *_phase_chain_args(),
            *_phase_rationale_args(),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "## Kickoff Contract — issue346" in result.stdout
    assert "| spec | pm | cafe-spec | yes | user | yes |" in result.stdout


def test_kickoff_formatter_resolves_custom_playbook_iteration_skills(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / ".cafe" / "skills"
    playbooks_root = tmp_path / ".cafe" / "playbooks"
    playbooks_root.mkdir(parents=True)
    for name, profile in {
        "cafe-audit_first": """workload: research
    reasoning: standard
    risk_domains: [evidence]
    fallback_strength: equivalent""",
        "cafe-audit_revise": """workload: review
    reasoning: high
    risk_domains: [security]
    fallback_strength: equivalent_or_stronger""",
    }.items():
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"""---
name: {name}
description: Custom audit phase.
workflow:
  execution_profile:
    {profile}
---

# Audit
""",
            encoding="utf-8",
        )
    (playbooks_root / "custom-audit.yaml").write_text(
        """playbook:
  id: custom-audit
  conversation_locale: en-US
roles:
  auditor: {default_agent: Ada}
steps:
  audit:
    skill: {'1': cafe-audit_first, default: cafe-audit_revise}
    role: auditor
    assignee_type: agent
    input_artifacts: []
    output_artifact: report
    'on': {await_agent: _done}
entry_point: audit
""",
        encoding="utf-8",
    )
    strategic_context = tmp_path / ".cafe" / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    script = SKILL_ROOT / "scripts" / "format_kickoff_contract.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "custom-audit",
            "--project-root",
            str(tmp_path),
            "--issue-name",
            "audit-1",
            "--playbook-rationale",
            "The user selected the custom audit graph; no builtin candidate owns this audit responsibility.",
            "--issue-nature",
            "security review",
            "--issue-scale",
            "medium",
            "--model-adjustment-authority",
            "driver_autonomous",
            *_preflight_args(),
            "--risk-factor",
            "security boundary",
            "--assessment-rationale",
            "The custom phase evaluates a security-sensitive contract.",
            "--phase-chain",
            "audit=gemini:audit-main,copilot:audit-fallback",
            "--phase-rationale",
            "audit=frontier: high security review with an equivalent independent fallback",
            "--repository-content-locale",
            "en-US",
            "--current-checkout",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| audit | auditor | cafe-audit_first, cafe-audit_revise |" in result.stdout
    assert (
        "| audit | cafe-audit_first, cafe-audit_revise | research, review | high | "
        "evidence, security | equivalent_or_stronger | declared |" in result.stdout
    )


def test_kickoff_formatter_rejects_unresolved_phase_models(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
            "simple",
            "--project-root",
            str(tmp_path),
            "--issue-name",
            "issue-no-models",
            "--playbook-rationale",
            (
                "The simple graph covers the localized change with independent QA "
                "and no separate plan or code review."
            ),
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            *_preflight_args(),
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "Focused behavior.",
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "--current-checkout",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "step='spec'" in result.stderr
    assert "field='spec'" in result.stderr


def test_kickoff_formatter_rejects_missing_phase_rationale(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
            "simple",
            "--project-root",
            str(PROJECT_ROOT),
            "--issue-name",
            "issue-no-rationale",
            "--playbook-rationale",
            (
                "The simple graph covers the localized change with independent QA "
                "and no separate plan or code review."
            ),
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            *_preflight_args(),
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "Focused behavior.",
            "--phase-chain",
            "spec=gemini:requirements-main,copilot:requirements-fallback",
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "--current-checkout",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "missing phase rationale for agent-executed step: spec" in result.stderr


def test_builtin_confirmation_gate_candidates_come_from_playbook_declarations() -> None:
    loader = PlaybookLoader(project_root=PROJECT_ROOT)

    actual = {
        playbook_id: confirmation_gate_steps(loader.load_model(playbook_id).model)
        for playbook_id in (
            "direct",
            "simple",
            "standard",
            "standard-qa",
            "tdd",
            "tdd-qa",
            "editorial",
            "hotfix",
            "incident",
            "research",
        )
    }

    assert actual == {
        "direct": (),
        "simple": ("spec",),
        "standard": ("spec", "plan"),
        "standard-qa": ("spec", "plan"),
        "tdd": ("spec", "plan"),
        "tdd-qa": ("spec", "plan"),
        "editorial": ("brief",),
        "hotfix": (),
        "incident": (),
        "research": (),
    }


def test_bundled_playbooks_do_not_delegate_alignment_judgment_to_core() -> None:
    loader = PlaybookLoader(project_root=PROJECT_ROOT)

    for playbook_id in (
        "direct",
        "simple",
        "standard",
        "standard-qa",
        "tdd",
        "tdd-qa",
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
            assert PhaseStatusCode.ALIGNMENT_CHECKPOINT not in effective_step_status_codes(step_def)
            assert "alignment_checkpoint" not in effective_step_handoff_intents(step_def)
            assert (
                GenericPhase._detect_status_code(
                    response="alignment_checkpoint",
                    step_def=step_def,
                )
                is None
            )


def test_use_cafe_workflow_skill_protects_issue_overrides() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/strategic_context.md")
    normalized = " ".join(reference.split())

    assert "references/strategic_context.md" in skill
    assert "## Protected issue overrides" in reference
    assert "optional, protected overrides" in normalized
    assert "Do not create `issues.<issue-name>` because" in reference
    assert "Do not store workflow progress, baton state, phase outputs" in reference
    assert "Do not add, edit, or remove an issue override" in normalized
    assert "Leave `issues:` untouched unless the user explicitly requested" in reference


def test_use_cafe_workflow_bounds_diagnosis_and_repairs_only_declarative_layers() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/diagnosis_and_repair.md")
    normalized = " ".join(reference.split())

    assert "references/diagnosis_and_repair.md" in skill
    assert "# Bounded Diagnosis And Repair" in reference
    assert "Playbook declarative defect" in reference
    assert "Phase declarative defect" in reference
    assert "Driver or CAFE core defect" in reference
    assert "activate `write-cafe-playbook`" in normalized
    assert "activate `write-cafe-phase`" in normalized
    assert "Do not invent a `write-cafe-driver` skill" in normalized
    assert "Search open and closed issues read-only" in reference
    assert "https://github.com/luyotw/cafe/issues" in reference
    assert "Do not create, comment on, or close an upstream issue" in normalized
    assert "stale installed skills" in normalized
    assert "unconfirmed or transient failures" in normalized


def test_use_cafe_workflow_prefers_user_conversation_locale() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/kickoff.md")
    normalized = " ".join(reference.split())

    assert "references/kickoff.md" in skill
    assert "## Conversation locale checklist" in reference
    assert "playbook.conversation_locale" in reference
    assert "cafe playbook confirmation-gates <playbook-id>" in reference
    assert "`Conversation locale:` line" in normalized
    assert "a locale the user directly requested for this thread" in normalized
    assert "a locale reliably inferred from the user's own natural-language messages" in normalized
    assert "Do not infer from quoted text, pasted artifacts, code, commands" in normalized
    assert "If the evidence is mixed or ambiguous, use the playbook locale" in normalized
    assert "For `auto`, infer from the user's messages using the same rules above" in normalized
    assert "explicit BCP 47 value as the fallback" in normalized
    assert "conversation_locale: zh-TW (inferred user preference from current thread)" in normalized
    assert "conversation_locale: en-US (from playbook: standard)" in normalized
    assert "required kickoff field, not a confirmation gate" in normalized
    assert "asking why a language was used is not an override" in normalized
    assert "Never claim this skill lacks a locale rule" in normalized
    assert "Do not copy the locale into `issue.yaml`" in normalized
    assert "commands, paths, playbook and step names, intents, artifact keys" in normalized


def test_use_cafe_workflow_requires_confirmed_repository_content_locale() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/kickoff.md")
    normalized_skill = " ".join(skill.split())
    normalized = " ".join(reference.split())

    assert "repository content locale used by documentation and code comments" in normalized_skill
    assert "## Repository content locale checklist" in reference
    assert "Before `cafe init` or any other repository mutation" in normalized
    assert "explicitly ask the user to confirm `repository_content_locale`" in normalized
    assert (
        "Use one repository content locale for both documentation and code comments" in normalized
    )
    assert (
        "scoped exception instead of making two languages a routine kickoff decision" in normalized
    )
    assert "Acceptance of the complete kickoff contract explicitly confirms it" in normalized
    assert "Persist the confirmed value in `.cafe/strategic_context.yaml`" in normalized
    assert "not in issue-owned workflow state" in normalized


def test_use_cafe_workflow_requires_driver_execution_contract_and_model_authority() -> None:
    skill = _read_skill_resource("SKILL.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    running = _read_skill_resource("references/running_workflow.md")
    models = _read_skill_resource("references/model_selection.md")
    normalized_kickoff = " ".join(kickoff.split())
    normalized_running = " ".join(running.split())
    normalized_models = " ".join(models.split())

    assert "references/model_selection.md" in skill
    assert "`driver_execution` contract" in skill
    assert "`continuous` by default" in skill
    assert "may be `single_step`" in skill
    assert "cafe workflow --execute --mute-agent-output" in skill
    assert "provider narration is parsed and persisted" in normalized_running
    assert "does not suppress workflow lifecycle events" in normalized_running
    assert "Keep `--mute-agent-output` on every driver execution" in normalized_running
    assert "Never remove the flag" in normalized_running
    assert "cafe show <step> streaming --iteration <n>" in normalized_running
    assert ".cafe/issues/<issue>/<step>/iteration_NNN/streaming.jsonl" in normalized_running
    assert "reads the entire saved file" in normalized_running
    assert (
        "Do not use `cafe show <step> streaming` for driver diagnosis"
        in normalized_running
    )
    assert "rg -n -C <context-lines> '<pattern>' <streaming-file>" in normalized_running
    assert "tail -n <line-count> <streaming-file>" in normalized_running
    assert "Remove it only when" not in normalized_running
    assert "persisted baton without forcing `--start-step`" in skill
    assert "Do not use `cafe make` for driver execution" in normalized_running
    assert "required cadence" in normalized_running
    assert "Start the timer when the process starts or resumes" in normalized_running
    assert (
        "perform one proactive inspection when the interval elapses" in normalized_running
    )
    assert "then restart the timer" in normalized_running
    assert (
        "if execution remains active after handling the signal, restart the timer"
        in normalized_running
    )
    assert "Stop polling when the command exits" in normalized_running
    assert "must not trigger an extra CAFE status or artifact poll" in normalized_running
    assert "If the mapping is missing, return to `kickoff.md`" in normalized_running
    assert (
        "Start the timer when a workflow process starts or resumes" in normalized_kickoff
    )
    assert (
        "perform one proactive inspection when the interval elapses, then restart the timer"
        in normalized_kickoff
    )
    assert (
        "if the process remains active afterward, restart the timer"
        in normalized_kickoff
    )
    assert "Stop the timer when the command exits" in normalized_kickoff
    assert "must not trigger extra workflow polling" in normalized_kickoff
    assert (
        "For an older prepared issue without `driver_execution`, propose `continuous` and "
        "`180` as defaults and confirm them before the next execution"
        in normalized_kickoff
    )
    assert "do not silently infer that the older driver used either mode" in normalized_kickoff
    assert "`model_adjustment_authority`" in kickoff
    assert "`driver_autonomous`" in kickoff
    assert "`user_approval_required`" in kickoff
    assert "No provider or model is built into this skill" in normalized_models
    assert "The phase skill owns only its provider-neutral minimum execution profile" in normalized_models
    assert "The driver owns the capability-band classification" in normalized_models
    assert "`efficiency`" in models
    assert "`balanced`" in models
    assert "`frontier`" in models
    assert "Treat an existing repository chain as a candidate, not as selection evidence" in normalized_models
    assert "A publication phase may remain `efficiency`" in normalized_models
    assert "Model release order is not capability-band order" in normalized_models
    assert "A user may choose a primary-only chain" in normalized_models
    assert "A primary-only chain skips fallback smoke" in normalized_models
    assert "a primary failure is a hard stop" in normalized_models
    assert "Never reject a fallback solely because its version number is lower" in normalized_models
    assert "never assume two versions are equivalent solely because they share a model family" in normalized_models
    assert "Reduced uncertainty after spec or plan may move" in normalized_models
    assert "Resolve the skill bound by the active playbook" in normalized_models
    assert "actual remaining iteration" in normalized_models
    assert "making the primary fail with the classified `model_not_found`" in normalized_models
    assert "Do not create a fake failure inside a live issue" in normalized_models
    assert "scripts/preflight_cache.py" in models
    assert "last 24 hours" in normalized_models
    assert "successful result for 30 days" in normalized_models
    assert "never records a failed" in normalized_models
    assert "A live workflow failure always overrides cached evidence" in normalized_models
    assert (SKILL_ROOT / "scripts" / "preflight_cache.py").is_file()
    assert "Reassess at contract-defined boundaries" in normalized_models
    assert "In `continuous` mode, do not stop execution" in normalized_models
    assert "In `single_step` mode, reassess after every completed step" in normalized_models
    assert "active worktree's `.cafe/phases.yaml`" in normalized_models


def test_use_cafe_workflow_never_shows_unmuted_driver_execution() -> None:
    offenders = []
    paths = [SKILL_ROOT / "SKILL.md", *sorted((SKILL_ROOT / "references").glob("*.md"))]

    for path in paths:
        logical_text = path.read_text(encoding="utf-8").replace("\\\n", " ")
        for line_number, line in enumerate(logical_text.splitlines(), start=1):
            if "cafe workflow --execute" in line and "--mute-agent-output" not in line:
                offenders.append(f"{path.relative_to(SKILL_ROOT)}:{line_number}")

    assert not offenders, f"unmuted driver execution examples: {offenders}"


def test_use_cafe_workflow_batches_driver_pr_review_findings() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/convergent_pr_review.md")
    normalized_reference = " ".join(reference.split())

    assert "references/convergent_pr_review.md" in skill
    assert "finish its full review matrix" in skill
    assert "consolidate every currently observable blocker" in normalized_reference
    assert "## 1. Establish one review baseline" in reference
    assert (
        "every acceptance criterion and the original reported production journey"
        in normalized_reference
    )
    assert "real production entry points and callers" in normalized_reference
    assert "Continue across all applicable rows after finding a blocker" in reference
    assert "A green unit/full suite is supporting evidence" in normalized_reference
    assert "tests do not forge workflow output, trusted state, or receipts" in normalized_reference
    assert "previously observable but missed" in reference
    assert "Do not restart repository-wide discovery for unchanged areas" in reference


class TestPollingContract:
    def test_first_poll_waits_for_the_full_confirmed_interval(self) -> None:
        skill = " ".join(_read_skill_resource("SKILL.md").split())
        kickoff = " ".join(_read_skill_resource("references/kickoff.md").split())
        running = " ".join(
            _read_skill_resource("references/running_workflow.md").split()
        )

        assert "From the first wait, honor the full poll cadence" in skill
        assert "there is no shorter startup or warm-up cadence" in kickoff
        assert "The first proactive inspection is due only after that full interval" in running
        assert "Continue a single deferred wait for the remaining interval instead" in running
        assert "wait on the same deferred operation" in running

    def test_transport_yields_do_not_trigger_workflow_inspection(self) -> None:
        kickoff = " ".join(_read_skill_resource("references/kickoff.md").split())
        running = " ".join(
            _read_skill_resource("references/running_workflow.md").split()
        )

        assert "is transport state rather than an event-driven signal" in kickoff
        assert "must not cause a sub-interval status or artifact poll" in kickoff
        assert "is transport state, not substantive process output" in running
        assert "It must not trigger a short `write_stdin` poll" in running
        assert "Substantive lifecycle output" in running
        assert "still wake the driver immediately" in running

    def test_formatter_exposes_first_poll_and_timestamp_contract(
        self, tmp_path: Path
    ) -> None:
        strategic_context = tmp_path / "strategic_context.yaml"
        strategic_context.write_text(
            """\
version: 1
mandate:
  preset: technical-led
  playbook_id: standard
  axes:
    product_scope: {level: escalate, grounds: [roadmap, positioning]}
    technical: {level: agent, grounds: [engineering_guidelines]}
  out_of_mandate: [pricing, production deploy approval]
""",
            encoding="utf-8",
        )
        result = subprocess.run(
            _kickoff_formatter_command(strategic_context),
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert (
            "| driver_execution.first_poll | after the full interval; "
            "no startup or transport-level poll |" in result.stdout
        )
        assert (
            "| driver_execution.poll_timestamp | capture and print current system time "
            "with every proactive poll |" in result.stdout
        )
