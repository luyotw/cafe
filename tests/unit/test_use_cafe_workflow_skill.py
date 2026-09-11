"""Tests for bundled use-cafe-workflow skill guidance."""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cafe.core.playbook import confirmation_gate_steps, mandatory_confirmation_gate_steps
from cafe.core.status_codes import (
    PhaseStatusCode,
    effective_step_handoff_intents,
    effective_step_status_codes,
)
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader
from tests.fixtures.delivery_contract import delivery_contract

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

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


def _proactive_review_args(playbook_id: str, *, project_root: Path = PROJECT_ROOT) -> list[str]:
    result: list[str] = []
    model = PlaybookLoader(project_root=project_root).load_model(playbook_id).model
    for step_name, step in model.steps.items():
        if step.assignee_type in {"agent", "hybrid"}:
            result.extend(
                [
                    "--proactive-review-decision",
                    f"{step_name}=not_required:User confirms no proactive review for {step_name}.",
                ]
            )
    return result


def _preflight_args() -> list[str]:
    return [
        "--delivery-contract",
        json.dumps(delivery_contract()),
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


def test_driver_defers_release_check_until_workflow_completion() -> None:
    text = _read_skill_resource("SKILL.md")

    assert "Driver must never execute `release-check` while a workflow is active" in text
    assert "Defer any in-workflow request until the workflow is complete" in text


def _kickoff_formatter_command(
    strategic_context: Path,
    *extra_args: str,
    playbook_id: str = "standard",
    pr_auto_create: bool | str | None = False,
    phase_chains: dict[str, str] | None = None,
    phase_rationales: dict[str, str] | None = None,
    driver_confirmable: tuple[str, ...] = ("spec", "plan"),
    include_proactive_review_args: bool = True,
) -> list[str]:
    pr_args = (
        []
        if pr_auto_create is None
        else ["--capability-choice", "pr.auto_create=" + json.dumps(pr_auto_create)]
    )
    proactive_args = []
    if include_proactive_review_args and "--proactive-review-decision" not in extra_args:
        proactive_args = _proactive_review_args(playbook_id)
    return [
        sys.executable,
        str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
        playbook_id,
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
        "--driver-mode",
        "unattended",
        *extra_args,
        *pr_args,
        *_preflight_args(),
        "--risk-factor",
        "public contract",
        "--assessment-rationale",
        "Changes a public workflow contract across runtime and CLI.",
        *_phase_chain_args(phase_chains),
        *_phase_rationale_args(phase_rationales),
        "--effective-locale",
        "zh-TW",
        "--locale-source",
        "user thread override",
        "--repository-content-locale",
        "zh-TW",
        "--user-required",
        "--driver-confirmable",
        *driver_confirmable,
        "--worktree",
        ".cafe/worktrees/issue346",
        "--strategic-context",
        str(strategic_context),
        *proactive_args,
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
        "completion_and_authority.md",
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
    assert "do not ask a catalog question" in normalized_lower
    assert "`missing_global` is an ordinary project-only entry" in reference
    assert "`content_mismatch_entry_ids`" in reference
    assert "at the very end of the kickoff contract" in normalized
    assert "scripts/catalog_version_check.py" in reference
    assert "effective conversation locale" in normalized
    assert "Only unwrap those two fields when the script exits zero" in reference
    assert "do not read nested keys or reminder IDs" in normalized
    assert "route its raw catalog stdout, stderr, and exit code" in " ".join(
        _read_skill_resource("references/kickoff.md").split()
    )
    assert (SKILL_ROOT / "scripts" / "catalog_version_check.py").is_file()
    assert "not_requested" in reference
    assert "separate approval scopes" in normalized
    assert "must not be described as current" in normalized
    assert "continues with the installed version" in normalized
    assert "over_budget" in reference
    assert "affected_entry_ids" in reference
    assert "discovery_complete" in reference
    assert "one exact combined `--entry` scope" not in normalized
    assert "cafe update apply --token" in reference
    assert "cafe catalog sync-global --token" in reference
    assert "re-run both read-only checks" in normalized_lower
    assert "re-render and reconfirm the kickoff contract" in normalized
    assert "before every start or resume" in normalized_running.lower()
    assert "user explicitly requests it" in normalized_running
    assert "reminder script runs only while rendering" in normalized_running


def test_driver_update_preflight_requires_a_user_decision_before_prepare() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/project_global_skill_sync.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    running = _read_skill_resource("references/running_workflow.md")
    normalized_reference = " ".join(reference.split())
    normalized_kickoff = " ".join(kickoff.split())
    normalized_running = " ".join(running.split())

    assert "user-owned runtime-update decision before invoking non-interactive `cafe prepare`" in skill
    assert "## Driver-managed runtime-update decision" in reference
    assert "show the installed and latest versions" in normalized_reference
    assert "explicitly ask the user whether to update" in normalized_reference
    assert "Only explicit acceptance may apply the exact comparison token" in normalized_reference
    assert "re-run `cafe update check --json` before `cafe prepare`" in normalized_reference
    assert "A decline records `declined`" in normalized_reference
    assert "Detached and event callbacks must not answer" in normalized_reference
    assert "before `cafe prepare --no-interactive`" in normalized_kickoff.lower()
    assert "must never prompt" in normalized_kickoff
    assert "Driver-managed preparation" in normalized_running


def test_skill_local_catalog_sync_path_has_no_write_authority() -> None:
    script = SKILL_ROOT / "scripts" / "catalog_version_check.py"
    source = script.read_text(encoding="utf-8")

    assert "shell=True" not in source
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
    assert "Mandatory HumanTask" in reference
    assert "never enter the kickoff partition" in normalized
    assert "reactive interruptions, not scheduled candidates" in normalized
    assert "Alignment is a proactive driver decision" in normalized
    assert "alignment_policy:" not in reference
    assert "Driver-owned policy" in reference
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
    assert "Driver cannot change a phase" in normalized
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
        "legacy `settings.playbook`, top-level `playbook`, or `playbook_id`" in normalized_selection
    )
    assert (
        "persist the effective playbook in `.cafe/issues/<issue-name>/issue.yaml`"
        in normalized_selection
    )
    assert (
        "separate Driver-owned subset is persisted in `driver/contract.json`"
        in normalized_selection
    )
    assert (
        "separate Driver-owned subset is persisted in `driver/contract.json`"
        in normalized_selection
    )
    assert "Strategic context is not playbook configuration" in normalized_strategic
    assert "Do not add `playbook_id` to `mandate` or `issues.<name>`" in normalized_strategic
    assert "playbook_id: standard" not in strategic
    assert "Do not write the selected playbook to `.cafe/config.yaml`" in normalized_kickoff
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
    assert "### Delivery Contract" in result.stdout
    assert delivery_contract()["outcome"] in result.stdout
    assert delivery_contract()["required_evidence"][0] in result.stdout
    assert (
        "| playbook_selection_rationale | Repository policy requires the standard graph; "
        "QA is not independently required, so standard-qa is unnecessary. |" in result.stdout
    )
    assert "| spec | pm | cafe-spec | 是 | driver（驗證後繼續） | 否 |" in result.stdout
    assert "| plan | developer | cafe-plan | 是 | driver（驗證後繼續） | 否 |" in result.stdout
    assert "| develop | developer | cafe-develop | 否 | — | 否 |" in result.stdout
    assert "| review | reviewer | cafe-review | 否 | — | 否 |" in result.stdout
    assert "| pr | developer | cafe-pr | 是 | user（mandatory） | 是 |" in result.stdout
    assert "| mandatory_human_tasks | pr |" in result.stdout
    assert "| effective_locale | zh-TW (user thread override) |" in result.stdout
    assert "| repository_content_locale | zh-TW |" in result.stdout
    assert "| issue_nature | feature/integration |" in result.stdout
    assert "| issue_scale | medium |" in result.stdout
    assert "model_adjustment" not in result.stdout
    assert "| schema_version | 4 |" in result.stdout
    assert "| driver.mode | unattended |" in result.stdout
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
        "| review | gemini:review-main | copilot:review-fallback | --phase-chain | frontier:"
        in result.stdout
    )
    assert (
        "| pr | cursor-agent:publication-main | gemini:publication-fallback | --phase-chain | efficiency:"
        in result.stdout
    )
    assert (
        "| review | cafe-review | review | high | correctness, security | "
        "equivalent_or_stronger | declared |" in result.stdout
    )
    assert "| need_clarification | driver_confirmable | 否 |" in result.stdout
    assert "| product_scope | escalate | roadmap, positioning |" in result.stdout
    assert result.stdout.count("| playbook_id |") == 1


def _write_fake_cafe(
    path: Path,
    *,
    stdout: str,
    stderr: str = "",
    exit_code: int = 0,
) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({json.dumps(stdout)})\n"
        f"sys.stderr.write({json.dumps(stderr)})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_catalog_version_check_reports_only_content_mismatch_ids(
    tmp_path: Path,
) -> None:
    report = {
        "entries": [
            {
                "entry_id": "agent:developer/project-only",
                "reason": "missing_global",
            },
            {
                "entry_id": "agent:developer/shared",
                "reason": "content_mismatch",
            },
        ]
    }
    executable_dir = tmp_path / "quoted'bin"
    executable_dir.mkdir()
    executable = executable_dir / "cafe"
    _write_fake_cafe(executable, stdout=json.dumps(report))

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "catalog_version_check.py"),
            "--cafe-executable",
            str(executable),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "catalog_check": report,
        "content_mismatch_entry_ids": ["agent:developer/shared"],
    }


def test_catalog_version_check_ignores_missing_global() -> None:
    module = _load_script_module(
        SKILL_ROOT / "scripts" / "catalog_version_check.py", "catalog_version_check"
    )

    assert (
        module.content_mismatch_entry_ids(
            {
                "entries": [
                    {
                        "entry_id": "agent:developer/project-only",
                        "reason": "missing_global",
                    }
                ]
            }
        )
        == []
    )


def test_catalog_version_check_forwards_catalog_command_failure(tmp_path: Path) -> None:
    executable = tmp_path / "cafe"
    _write_fake_cafe(
        executable,
        stdout='{"status":"over_budget"}\n',
        stderr="catalog limit exceeded\n",
        exit_code=7,
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "catalog_version_check.py"),
            "--cafe-executable",
            str(executable),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 7
    assert result.stdout == '{"status":"over_budget"}\n'
    assert result.stderr == "catalog limit exceeded\n"


def test_kickoff_formatter_contains_no_fixed_language_catalog_reminder() -> None:
    source = _read_skill_resource("scripts/format_kickoff_contract.py")

    assert "Catalog 同步提醒" not in source
    assert "Catalog synchronization reminder" not in source


def test_confirmed_kickoff_activates_one_issue_scoped_driver_contract(tmp_path: Path) -> None:
    """Test List integration 1: the confirmed rendered policy becomes durable before use."""
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    issue_dir = tmp_path / "issues" / "issue346"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps({"workflow_id": "prepared-346"}), encoding="utf-8"
    )
    result = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            "--activate-confirmed",
            "--workflow-id",
            "prepared-346",
            "--confirmed-by",
            "user",
            "--confirmed-at",
            "2026-09-06T02:00:00+00:00",
            "--issue-dir",
            str(issue_dir),
            "--proactive-review-decision",
            "spec=not_required:Specification review remains user-confirmed.",
            "--proactive-review-decision",
            "plan=not_required:Planning review remains user-confirmed.",
            "--proactive-review-decision",
            "develop=not_required:No proactive review was confirmed for development.",
            "--proactive-review-decision",
            "review=not_required:The normal review phase remains reactive.",
            "--proactive-review-decision",
            "pr=not_required:Publication uses the generic PR path.",
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    contract = json.loads((issue_dir / "driver" / "contract.json").read_text(encoding="utf-8"))
    assert contract["identity"] == {"issue_name": "issue346", "workflow_id": "prepared-346"}
    assert "pr" not in contract
    assert "playbook" not in contract
    assert contract["locales"] == {
        "conversation": {"value": "zh-TW", "source": "user thread override"}
    }
    assert "proactive_review.yaml" not in {path.name for path in (issue_dir / "driver").iterdir()}
    assert "No proactive review was confirmed for development." in result.stdout
    assert "| schema_version | 4 |" in result.stdout

    entry = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "validate_driver_entry.py"),
            "--issue-dir",
            str(issue_dir),
            "--issue-name",
            "issue346",
            "--workflow-id",
            "prepared-346",
            "--fresh-facts",
            json.dumps(contract["preflight"]),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert entry.returncode == 0, entry.stderr
    entry_projection = json.loads(entry.stdout)
    assert "generic_inputs" not in entry_projection
    assert "pr_auto_create" not in entry.stdout
    assert entry_projection["phase_model_authority"]["develop"][0]["model"] == "implementation-main"


def test_confirmed_event_driven_kickoff_binds_the_visible_codex_thread(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    issue_dir = tmp_path / "issues" / "issue346"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps({"workflow_id": "prepared-346"}), encoding="utf-8"
    )
    command = _kickoff_formatter_command(
        strategic_context,
        "--activate-confirmed",
        "--workflow-id",
        "prepared-346",
        "--confirmed-by",
        "user",
        "--confirmed-at",
        "2026-09-06T02:00:00+00:00",
        "--issue-dir",
        str(issue_dir),
    )
    mode_index = command.index("--driver-mode") + 1
    command[mode_index] = "event-driven"
    command[mode_index + 1 : mode_index + 1] = [
        "--event-driver",
        "codex",
    ]
    environment = dict(os.environ)
    environment["CODEX_THREAD_ID"] = "visible-thread"

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads((issue_dir / "driver" / "dispatch_state.json").read_text(encoding="utf-8"))
    assert state["entries"][0]["session"]["id"] == "visible-thread"
    assert state["entries"][0]["session"]["source"] == "host_session"


def test_kickoff_formatter_renders_the_complete_normalized_policy_before_activation(
    tmp_path: Path,
) -> None:
    """A user sees the exact policy fields before choosing to activate them."""
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
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
    assert "### Confirmed durable policy" in result.stdout
    assert '"proactive_review"' in result.stdout
    assert '"effective_graph"' not in result.stdout
    rendered = result.stdout.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert "pr" not in json.loads(rendered)["policy"]
    assert '"material_assumptions"' in result.stdout
    assert not (tmp_path / ".cafe" / "issues" / "issue346" / "driver").exists()


def test_kickoff_formatter_keeps_the_rendered_policy_stable_until_activation(
    tmp_path: Path,
) -> None:
    """Confirmation evidence belongs to provenance, not the displayed policy."""
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    issue_dir = tmp_path / "issues" / "issue346"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps({"workflow_id": "prepared-346"}), encoding="utf-8"
    )
    normal = subprocess.run(
        _kickoff_formatter_command(strategic_context),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    activated = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            "--activate-confirmed",
            "--workflow-id",
            "prepared-346",
            "--confirmed-by",
            "user",
            "--confirmed-at",
            "2026-09-06T02:00:00+00:00",
            "--issue-dir",
            str(issue_dir),
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert normal.returncode == 0, normal.stderr
    assert activated.returncode == 0, activated.stderr

    def rendered_policy(output: str) -> dict[str, object]:
        serialized = output.split("```json\n", 1)[1].split("\n```", 1)[0]
        return json.loads(serialized)["policy"]

    assert rendered_policy(normal.stdout) == rendered_policy(activated.stdout)


@pytest.mark.parametrize("choice", [True, False])
def test_kickoff_formatter_requires_and_binds_explicit_publication_choice(
    tmp_path: Path,
    choice: bool,
) -> None:
    """Test List 2: PR-capable kickoff binds one strict Boolean choice."""
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        _kickoff_formatter_command(strategic_context, pr_auto_create=choice),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    token = str(choice).lower()
    assert result.stdout.count(f"| pr.auto_create | {token} |") == 1
    assert "confirmation_contract.pr_auto_create" not in result.stdout
    assert "verified PR URL" in result.stdout
    assert "Publication mode: local-only. No PR URL exists." in result.stdout


@pytest.mark.parametrize("choice", [None, "yes"])
def test_kickoff_formatter_rejects_missing_or_malformed_publication_choice(
    tmp_path: Path,
    choice: str | None,
) -> None:
    """Test List 2: omission and truthy strings cannot imply publication intent."""
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        _kickoff_formatter_command(strategic_context, pr_auto_create=choice),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "capability-choice" in result.stderr


def test_non_pr_playbook_omits_choice_and_rejects_supplied_false(tmp_path: Path) -> None:
    """Test List 3: inapplicable publication config fails closed."""
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    chains = {step: f"gemini:{step}-main" for step in ("brief", "draft", "review", "publish")}
    rationales = {
        step: "balanced: bounded editorial work with an equivalent configured model"
        for step in chains
    }

    supplied = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            playbook_id="editorial",
            pr_auto_create=False,
            phase_chains=chains,
            phase_rationales=rationales,
            driver_confirmable=("brief",),
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    omitted = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            playbook_id="editorial",
            pr_auto_create=None,
            phase_chains=chains,
            phase_rationales=rationales,
            driver_confirmable=("brief",),
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert supplied.returncode == 2
    assert "not applicable" in supplied.stderr
    assert omitted.returncode == 0, omitted.stderr
    assert "pr.auto_create" not in omitted.stdout


def test_minimal_non_software_kickoff_renders_only_its_two_declared_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    module = _load_script_module(
        SKILL_ROOT / "scripts/format_kickoff_contract.py", "minimal_kickoff"
    )
    loaded = PlaybookLoader(project_root=tmp_path).load_model("editorial")
    steps = {
        name: loaded.model.steps[name].model_copy(
            update={
                "on": {"await_agent": target},
                "human_tasks": [],
            }
        )
        for name, target in (("brief", "draft"), ("draft", "_done"))
    }
    minimal = loaded.model.model_copy(update={"steps": steps})
    monkeypatch.setattr(
        module.PlaybookLoader,
        "load_model",
        lambda *a, **kw: SimpleNamespace(
            model=minimal,
            source="project",
            path=tmp_path / "minimal.yaml",
        ),
    )
    chains = {step: "codex:exact-model" for step in steps}
    command = _kickoff_formatter_command(
        tmp_path / "strategic_context.yaml",
        "--proactive-review-decision",
        "brief=not_required:No confirmation gate.",
        "--proactive-review-decision",
        "draft=not_required:No confirmation gate.",
        playbook_id="minimal",
        pr_auto_create=None,
        phase_chains=chains,
        phase_rationales={step: "Bounded content work." for step in steps},
        driver_confirmable=(),
    )
    (tmp_path / "strategic_context.yaml").write_text("mandate: {preset: technical-led}\n")
    args = module._parser().parse_args(command[2:])
    proposal = module.build_confirmed_proposal(args)
    rendered = module.render(args, confirmed_proposal=proposal)
    assert [phase["name"] for phase in proposal["phases"]] == ["brief", "draft"]
    assert proposal["confirmation_contract"] == {
        "user_required": [],
        "driver_confirmable": [],
        "mandatory_human_stops": [],
    }
    assert "PR" not in rendered
    assert "pr.auto_create" not in rendered
    assert "Prepare arguments" not in rendered


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


def test_kickoff_defaults_verified_github_issues_to_pr_publication() -> None:
    kickoff = _read_skill_resource("references/kickoff.md")
    normalized = " ".join(kickoff.split())

    assert "verified corresponding GitHub issue" in normalized
    assert "default the publication setup question" in normalized
    assert "enable automatic PR creation" in normalized
    assert "Without a corresponding issue" in normalized
    assert "manifest's local-only choice" in normalized
    assert "A direct user choice or an existing valid confirmed choice" in normalized
    assert "does not authorize publication before" in normalized
    assert "never authorizes merge or issue closure" in normalized


def test_need_clarification_defaults_to_bounded_driver_confirmation() -> None:
    skill = _read_skill_resource("SKILL.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    running = _read_skill_resource("references/running_workflow.md")
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")
    normalized = " ".join((skill + kickoff + running + handoffs).split()).lower()

    assert "default `need_clarification` to bounded `driver_confirmable`" in normalized
    assert "existing authority or `allowed_variations`" in normalized
    assert "triggers no deviation" in normalized
    assert "reserved product or strategy decisions" in normalized
    assert "uncertainty about whether authority already exists remain user-owned" in normalized
    assert "normal engineering uncertainty is not itself a user handoff" in normalized
    assert "new permission or external-effect authority" in normalized


def test_kickoff_contract_formatter_accepts_event_driven_binding(
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
            "--driver-mode",
            "event-driven",
            "--event-driver",
            "codex",
            "--event-driver",
            "claude:claude-opus-exact",
            "--event-driver",
            "gemini:gemini-pro-exact",
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| driver.mode | event-driven |" in result.stdout
    positions = [
        result.stdout.index("| driver.clis[0] | codex |"),
        result.stdout.index("claude:claude-opus-exact"),
        result.stdout.index("gemini:gemini-pro-exact"),
    ]
    assert positions == sorted(positions)
    assert result.stdout.count("event-driven session-and-dispatch: conforming") == 3
    assert "runtime-owned" in result.stdout
    assert "does not grant HumanTask, permission, or capability authority" in result.stdout


@pytest.mark.parametrize(
    "extra_args",
    [
        ("--driver-mode", "event-driven"),
        ("--driver-mode", "event-driven", "--event-driver", "codex:"),
        ("--driver-mode", "event-driven", "--event-driver", "codex:one"),
        (
            "--driver-mode",
            "event-driven",
            "--event-driver",
            "codex",
            "--event-driver",
            "codex:two",
        ),
        (
            "--driver-mode",
            "event-driven",
            "--event-driver",
            "codex",
            "--event-driver",
            "claude",
        ),
        ("--driver-mode", "attached", "--event-driver", "codex"),
        ("--driver-mode", "unattended", "--event-driver", "codex"),
        ("--driver-mode", "event-driven", "--event-driver", "unsupported"),
    ],
)
def test_kickoff_contract_rejects_nonconforming_event_driver_chains(
    tmp_path: Path, extra_args: tuple[str, ...]
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


def test_kickoff_contract_accepts_one_event_driver_entry(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            "--driver-mode",
            "event-driven",
            "--event-driver",
            "copilot",
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| driver.clis[0] | copilot |" in result.stdout


@pytest.mark.parametrize(
    ("extra_args", "expected_error"),
    [
        (("--driver-mode", "invalid"), "invalid choice"),
        (("--poll-interval-seconds", "0"), "must be greater than zero"),
        (("--poll-interval-seconds", "-1"), "must be greater than zero"),
        (
            ("--poll-interval-seconds", "1.5"),
            "must be an integer number of seconds",
        ),
    ],
)
def test_kickoff_contract_formatter_rejects_invalid_operating_mode(
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
    script = (SKILL_ROOT / "scripts" / "format_kickoff_contract.py").read_text(encoding="utf-8")
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
            "--driver-mode",
            "unattended",
            "--capability-choice",
            "pr.auto_create=false",
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
            *_proactive_review_args("standard"),
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
    original = (
        "develop:\n  clis:\n    - {cli: codex, model: old}\n    - {cli: claude, model: old}\n"
    )
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
                    ],
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
    monkeypatch.setattr(
        module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked"))
    )

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
        '\'{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"CAFE_PREFLIGHT_OK"}}\' '
        '\'{"type":"turn.completed","usage":{}}\'\n',
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

    executable.write_text("#!/bin/sh\nprintf '%s\\n' 'codex version 2.0'\n", encoding="utf-8")
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


def test_preflight_cache_can_invalidate_candidate_evidence(tmp_path: Path, monkeypatch) -> None:
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
            "--driver-mode",
            "unattended",
            "--capability-choice",
            "pr.auto_create=false",
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


def test_kickoff_contract_formatter_rejects_mandatory_gate_assignment(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )

    command = _kickoff_formatter_command(strategic_context)
    driver_index = command.index("--driver-confirmable")
    command[driver_index + 1 : driver_index + 3] = ["spec", "plan", "pr"]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unknown gates: pr" in result.stderr


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
            "--driver-mode",
            "unattended",
            "--capability-choice",
            "pr.auto_create=false",
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
            *_proactive_review_args("standard"),
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
            "--driver-mode",
            "unattended",
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
            *_proactive_review_args("custom-audit", project_root=tmp_path),
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
            "--driver-mode",
            "unattended",
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
            "--driver-mode",
            "unattended",
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

    mandatory = {
        playbook_id: mandatory_confirmation_gate_steps(loader.load_model(playbook_id).model)
        for playbook_id in actual
    }
    assert mandatory == {
        "direct": ("pr",),
        "simple": ("pr",),
        "standard": ("pr",),
        "standard-qa": ("pr",),
        "tdd": ("pr",),
        "tdd-qa": ("pr",),
        "editorial": (),
        "hotfix": ("pr",),
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


def test_use_cafe_workflow_defines_phase_scoped_proactive_driver_review() -> None:
    skill = _read_skill_resource("SKILL.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    running = _read_skill_resource("references/running_workflow.md")
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")
    normalized = " ".join((skill + kickoff + running + handoffs).split())

    assert "Default every assignable scheduled confirmation gate" in normalized
    assert "Phases without such a pause are ineligible" in normalized
    assert "`proactive_review.phase_decisions` projection" in running
    assert "existing scheduled confirmation pause" in normalized
    assert "current Driver performs the review directly" in normalized
    assert "missing necessary scope and excessive or unnecessary scope" in normalized
    assert "code and non-code phase output" in normalized
    assert "must not launch a separate reviewer" in normalized


def test_kickoff_defaults_assignable_gates_to_driver_confirmation() -> None:
    module = _load_script_module(
        SKILL_ROOT / "scripts" / "format_kickoff_contract.py",
        "kickoff_default_confirmation_partition",
    )

    user_required, driver_confirmable = module._resolve_partition(
        candidates=("spec", "plan"),
        user_values=None,
        driver_values=None,
    )

    assert user_required == []
    assert driver_confirmable == ["spec", "plan"]


def test_proactive_review_overrides_are_sparse_ordered_and_fail_closed() -> None:
    module = _load_script_module(
        SKILL_ROOT / "scripts" / "format_kickoff_contract.py",
        "proactive_review_override_defaults",
    )
    kwargs = {
        "agent_phases": ["define", "build", "publish"],
        "eligible_phases": {"define", "publish"},
    }

    decisions = module._proactive_review_decisions(
        ["define=not_required:User explicitly disabled this review."],
        **kwargs,
    )
    assert [item["phase"] for item in decisions] == ["define", "build", "publish"]
    assert [item["decision"] for item in decisions] == [
        "not_required",
        "not_required",
        "required",
    ]

    with pytest.raises(ValueError, match="duplicate proactive review decision"):
        module._proactive_review_decisions(
            ["define=required:First.", "define=not_required:Second."],
            **kwargs,
        )
    with pytest.raises(ValueError, match="unknown or non-agent phase: missing"):
        module._proactive_review_decisions(
            ["missing=required:Unknown phase."],
            **kwargs,
        )
    with pytest.raises(ValueError, match="must follow agent phase order"):
        module._proactive_review_decisions(
            [
                "publish=required:Later phase first.",
                "define=required:Earlier phase second.",
            ],
            **kwargs,
        )


def test_kickoff_derives_proactive_defaults_only_at_scheduled_pauses(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            include_proactive_review_args=False,
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = result.stdout.split("```json\n", 1)[1].split("\n```", 1)[0]
    policy = json.loads(rendered)["policy"]
    decisions = {
        item["phase"]: item["decision"]
        for item in policy["proactive_review"]["phase_decisions"]
    }
    assert decisions == {
        "spec": "required",
        "plan": "required",
        "develop": "not_required",
        "review": "not_required",
        "pr": "required",
    }
    section = result.stdout.split("### Proactive review at scheduled pauses", 1)[1]
    section = section.split("### Reactive user handoffs", 1)[0]
    assert "| spec | required |" in section
    assert "| plan | required |" in section
    assert "| pr | required |" in section
    assert "| develop |" not in section
    assert "| review |" not in section
    assert section.count("Driver may confirm and advance after clean review") == 2
    assert section.count("user confirmation remains required") == 1


def test_kickoff_defaults_apply_to_custom_assignable_and_mandatory_gates(
    tmp_path: Path,
) -> None:
    playbooks_root = tmp_path / ".cafe" / "playbooks"
    playbooks_root.mkdir(parents=True)
    (playbooks_root / "custom-gates.yaml").write_text(
        """\
playbook:
  id: custom-gates
  conversation_locale: en-US
roles:
  author: {default_agent: Ada}
steps:
  define:
    type: skill
    skill: cafe-spec
    role: author
    assignee_type: agent
    input_artifacts: []
    output_artifact: requirements
    human_tasks:
      - trigger: confirm_output
        task_id: output-review
        outcomes: {confirm: publish, revise: define}
    'on': {confirm_output: define}
  publish:
    type: skill
    skill: cafe-pr
    role: author
    assignee_type: agent
    input_artifacts: [requirements, workflow_feedback]
    output_artifact: publication
    human_tasks:
      - trigger: confirm_output
        task_id: local-review
        outcomes: {fix_now: publish, create_follow_up: _done, continue_without_issue: _done}
        feedback_delivery:
          artifact: workflow_feedback
          source_kind: local_review
    'on': {confirm_output: publish}
entry_point: define
""",
        encoding="utf-8",
    )
    strategic_context = tmp_path / ".cafe" / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
            "custom-gates",
            "--project-root",
            str(tmp_path),
            "--issue-name",
            "custom-1",
            "--playbook-rationale",
            "Custom graph verifies semantic defaults without conventional phase names.",
            "--issue-nature",
            "workflow",
            "--issue-scale",
            "small",
            "--driver-mode",
            "unattended",
            *_preflight_args(),
            "--risk-factor",
            "custom graph",
            "--assessment-rationale",
            "Two bounded custom confirmation gates.",
            "--phase-chain",
            "define=codex:define-model",
            "--phase-chain",
            "publish=codex:publish-model",
            "--phase-rationale",
            "define=balanced custom requirements work.",
            "--phase-rationale",
            "publish=efficiency custom publication work.",
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
    rendered = result.stdout.split("```json\n", 1)[1].split("\n```", 1)[0]
    policy = json.loads(rendered)["policy"]
    assert policy["confirmation_contract"] == {
        "user_required": [],
        "driver_confirmable": ["define"],
        "mandatory_human_stops": ["publish"],
    }
    decisions = policy["proactive_review"]["phase_decisions"]
    assert [(item["phase"], item["decision"]) for item in decisions] == [
        ("define", "required"),
        ("publish", "required"),
    ]
    section = result.stdout.split("### Proactive review at scheduled pauses", 1)[1]
    section = section.split("### Reactive user handoffs", 1)[0]
    assert "| define | required |" in section
    assert "Driver may confirm and advance after clean review" in section
    assert "| publish | required |" in section
    assert "user confirmation remains required" in section


def test_kickoff_renders_not_required_override_without_claiming_a_review(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            "--proactive-review-decision",
            "spec=not_required:User explicitly disabled proactive specification review.",
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    section = result.stdout.split("### Proactive review at scheduled pauses", 1)[1]
    section = section.split("### Reactive user handoffs", 1)[0]
    spec_row = next(line for line in section.splitlines() if line.startswith("| spec |"))
    assert "| not_required |" in spec_row
    assert "ordinary evidence verification" in spec_row
    assert "after clean review" not in spec_row


def test_proactive_review_consensus_uses_formal_correction_and_user_owned_confirmation() -> None:
    """The Driver contract keeps correction authority narrow and independently reviewed."""
    skill = _read_skill_resource("SKILL.md")
    running = _read_skill_resource("references/running_workflow.md")
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")
    contract = " ".join((skill + running + handoffs).split())

    for required in (
        "complete all applicable review passes before producing one bounded findings batch",
        "exact current artifact identity",
        "every observable blocker",
        "requirement or boundary",
        "concise evidence",
        "accept or rebut each finding",
        "`cafe chat <role> -p`",
        "existing responsible phase-agent session",
        "Chat must not edit the current phase output",
        "chat response is discussion evidence, not workflow authority",
        "findings, chat attempts, disagreements, and rebuttals do not create an iteration",
        "formal correction iteration only through the unique active declared correction outcome",
        "requires feedback",
        "`correction: true`",
        "non-advancing correction continuation",
        "consolidated findings, reached consensus, and acceptance conditions",
        "--no-resume --json",
        "verify the durable task result and correction continuation",
        "Only the resumed runtime materializes and executes the next formal iteration",
        "next observable pause or failure",
        "complete Driver re-review",
        "attached, unattended, and event-driven callback",
        "fail closed",
        "same unchanged artifact",
        "accepted finding without a durable correction",
        "partial, ambiguous, interrupted, failed, stale, or unresolved",
    ):
        assert required.lower() in contract.lower()

    for required in (
        "Driver may submit only that derived outcome",
        "user_required and mandatory confirmation gates keep advancing `confirm` user-owned",
        "driver_confirmable clean confirm remains driver-permitted",
        "No user prompt occurs during an autonomous correction loop",
        "one final user confirmation for each user-owned clean advancement candidate",
        "later clean candidate must be presented again",
        "`driver_confirmable` clarification within the confirmed contract and existing authority",
        "clarification that changes the contract, needs new authority, is reserved to the user, or has uncertain authority",
        "first provide these four items",
        "bare confirmation requests, artifact-link-only handoffs, and raw artifact dumps are invalid",
        "only when they materially affect the active decision",
    ):
        assert required.lower() in contract.lower()


def test_proactive_review_derives_correction_routing_from_the_active_human_task() -> None:
    resources = (
        _read_skill_resource("SKILL.md")
        + _read_skill_resource("references/running_workflow.md")
        + _read_skill_resource("references/handoffs_and_alignment.md")
    )
    normalized = " ".join(resources.split()).lower()

    for required in (
        "unique active declared correction outcome",
        "requires feedback",
        "`correction: true`",
        "non-advancing correction continuation",
        "zero or multiple eligible correction outcomes",
        "fail closed for user/playbook clarification",
        "regardless of outcome, phase, or target names",
    ):
        assert required in normalized


def test_proactive_review_handoff_keeps_the_required_summary_compact() -> None:
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")
    normalized = " ".join(handoffs.split())

    for element in (
        "where the workflow paused and what completed",
        "why it needs the user and the exact decision needed",
        "every declared option and its practical consequence",
        "the required reply format with one valid plain-language example",
        "only when they materially affect the active decision",
    ):
        assert element in normalized


def test_delivery_contract_allows_bounded_technical_flexibility() -> None:
    skill = _read_skill_resource("SKILL.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    running = _read_skill_resource("references/running_workflow.md")
    contract = " ".join((skill + kickoff + running).split())

    for required in (
        "reasonable technical choices in `allowed_variations`",
        "working assumption or bounded variation",
        "does not replace the Driver contract",
        "archiving, deleting, or rebuilding callback dispatch state",
        "no adequate handoff has been given in the current conversation",
        "append the compact summary",
    ):
        assert required in contract


def test_proactive_review_consensus_has_one_authority_path_and_a_bounded_input() -> None:
    running = _read_skill_resource("references/running_workflow.md")
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")
    contract = " ".join((running + handoffs).split()).lower()

    for required in (
        "chat before any correction routing",
        "only user-owned clean advancement candidates receive a user confirmation",
        "the unique active declared correction outcome exception permits",
        "at most 20 findings",
        "at most 12,000 utf-8 bytes",
        "each evidence item is limited to at most 500 utf-8 bytes",
        "over-budget batch remains paused",
        "must not truncate, split, or silently omit findings",
        "4,000-byte output cap",
        "120-second timeout",
    ):
        assert required in contract


def test_proactive_review_execution_limits_are_driver_policy_only() -> None:
    running = _read_skill_resource("references/running_workflow.md")
    normalized = " ".join(running.split()).lower()

    assert "policy-only driver limits" in normalized
    assert "generic `cafe chat` runtime does not enforce them" in normalized
    assert "must not claim runtime enforcement" in normalized
    assert "ordinary user-initiated chat behavior remains unchanged" in normalized


def test_proactive_review_authority_precedence_has_no_blanket_callback_or_route_bypass() -> None:
    running = _read_skill_resource("references/running_workflow.md")
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")

    task_authority = " ".join(
        running.split("The callback receives only an asynchronous durable-event notice.", 1)[1]
        .split("## Commands and handoffs", 1)[0]
        .split()
    )
    correction_flow = " ".join(
        handoffs.split("## Route proactive-review findings through existing handoffs", 1)[1]
        .split("## Present a self-contained user decision", 1)[0]
        .split()
    )

    def is_consistent(task_policy: str, routing: str) -> bool:
        task_policy = task_policy.lower()
        routing = routing.lower()
        callback_blanket = re.search(
            r"callback.{0,100}(?:must never|cannot).{0,100}eligible correction outcome",
            task_policy,
        )
        route_before_chat = re.search(
            r"(?:route|routing).{0,100}before (?:the )?chat",
            routing,
        )
        return (
            "choose a user answer" in task_policy
            and "unique active declared correction outcome is not a user answer" in task_policy
            and "zero or multiple eligible outcomes fail closed for user/playbook clarification"
            in task_policy
            and "a mandatory, `user_required`, permission, or capability task requires a **user-facing driver turn**"
            in task_policy
            and "a `need_clarification` task whose confirmed reactive policy is `driver_confirmable`"
            in task_policy
            and "including an event-driven callback, to submit only that eligible outcome"
            in task_policy
            and not callback_blanket
            and "chat before any correction routing" in routing
            and "unique active declared correction outcome" in routing
            and "advancing `confirm`" in routing
            and not route_before_chat
        )

    assert is_consistent(task_authority, correction_flow)
    assert not is_consistent(
        task_authority.replace(
            "A `need_clarification` task whose confirmed reactive policy is `driver_confirmable`",
            "A `need_clarification` task",
        ),
        correction_flow,
    )
    assert not is_consistent(
        task_authority + " The callback must never submit an eligible correction outcome.",
        correction_flow,
    )
    assert not is_consistent(
        task_authority,
        correction_flow
        + " The Driver may route correction before chat and before applying the authority matrix.",
    )


def test_proactive_review_initial_routing_task_flow_and_matrix_share_correction_precedence() -> (
    None
):
    running = _read_skill_resource("references/running_workflow.md")
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")

    initial_routing = " ".join(
        handoffs.split("Then route by intent:", 1)[1]
        .split("## Route proactive-review findings through existing handoffs", 1)[0]
        .split()
    ).lower()
    task_flow = " ".join(
        running.split("## Completing a HumanTask", 1)[1]
        .split("## Commands and handoffs", 1)[0]
        .split()
    ).lower()
    authority_matrix = " ".join(
        handoffs.split(
            "Use this outcome-sensitive authority matrix after due review/chat consensus:", 1
        )[1]
        .split("## Present a self-contained user decision", 1)[0]
        .split()
    ).lower()

    correction_outcome = "unique active declared correction outcome"
    prior_initial_routing_rules = (
        "`confirm_output` from a mandatory humantask step: always stop for the real user.",
        "`confirm_output` from a `user_required` step: stop for user approval or correction.",
    )
    prior_task_flow = " ".join("""
        2. For user-owned tasks, serialize only the user's supplied answer into that schema.
        The driver may add the task ID required by the schema, but must not infer a decision,
        approval, permission, or missing answer.
        """.split()).lower()

    def is_consistent(initial: str, task: str, matrix: str) -> bool:
        return (
            correction_outcome in initial
            and "after complete driver review and one `cafe chat` consensus exchange" in initial
            and "mandatory or `user_required` advancing `confirm`" in initial
            and not any(rule in initial for rule in prior_initial_routing_rules)
            and correction_outcome in task
            and "driver may serialize a correction result" in task
            and prior_task_flow not in task
            and correction_outcome in matrix
            and "mandatory confirmation gates keep advancing `confirm` user-owned" in matrix
        )

    assert is_consistent(initial_routing, task_flow, authority_matrix)
    for prior_rule in prior_initial_routing_rules:
        assert not is_consistent(
            initial_routing + " " + prior_rule,
            task_flow,
            authority_matrix,
        )
    assert not is_consistent(
        initial_routing,
        task_flow + " " + prior_task_flow,
        authority_matrix,
    )
    assert not is_consistent(
        initial_routing + " " + " ".join(prior_initial_routing_rules),
        task_flow + " " + prior_task_flow,
        authority_matrix,
    )


def test_proactive_review_rechecks_a_composite_snapshot_at_each_use_boundary() -> None:
    running = _read_skill_resource("references/running_workflow.md")
    normalized = " ".join(running.split()).lower()

    for required in (
        "composite review snapshot",
        "artifact identity, accepted-requirements identity, correction-history identity",
        "active task identity, handoff/baton identity, and driver-contract identity",
        "immediately before invoking chat",
        "immediately before task completion, confirmation, or reuse of a clean result",
        "any mismatch invalidates the review/chat result",
        "retain the pause and restart the full review",
    ):
        assert required in normalized


def test_proactive_review_snapshot_includes_the_resolved_chat_identity() -> None:
    running = _read_skill_resource("references/running_workflow.md")
    normalized = " ".join(running.split()).lower()

    for required in (
        "phase configuration identity, resolved cli/model identity, persisted session identity",
        "playbook chat-skills identity, and prepared chat-environment identity",
        "unique active declared correction outcome is not a user answer",
        "zero or multiple eligible outcomes fail closed for user/playbook clarification",
        "it may also complete a confirmed `driver_confirmable` clean advancement",
        "may not choose an advancing mandatory or `user_required` confirmation",
    ):
        assert required in normalized


def test_kickoff_rejects_required_review_without_a_scheduled_pause(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )

    for ineligible_phase in ("develop", "review"):
        decisions: list[str] = []
        for phase in PRIMARY_ONLY_PHASE_CHAINS:
            state = "required" if phase == ineligible_phase else "not_required"
            decisions.extend(
                [
                    "--proactive-review-decision",
                    f"{phase}={state}:Confirmed review decision for {phase}.",
                ]
            )
        result = subprocess.run(
            _kickoff_formatter_command(strategic_context, *decisions),
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 2
        assert (
            f"proactive review phase '{ineligible_phase}' cannot be required because it has no "
            "scheduled confirmation pause"
        ) in result.stderr


def test_kickoff_rejects_proactive_review_without_a_rationale(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    decisions = _proactive_review_args("standard")
    decisions[1] = "spec=not_required:   "

    result = subprocess.run(
        _kickoff_formatter_command(strategic_context, *decisions),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "proactive review decision for 'spec' requires a rationale" in result.stderr


def test_kickoff_accepts_required_review_at_scheduled_pauses(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    decisions: list[str] = []
    for phase in PRIMARY_ONLY_PHASE_CHAINS:
        state = "required" if phase in {"spec", "plan"} else "not_required"
        decisions.extend(
            [
                "--proactive-review-decision",
                f"{phase}={state}:Confirmed review decision for {phase}.",
            ]
        )

    result = subprocess.run(
        _kickoff_formatter_command(strategic_context, *decisions),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = result.stdout.split("```json\n", 1)[1].split("\n```", 1)[0]
    phase_decisions = json.loads(rendered)["policy"]["proactive_review"]["phase_decisions"]
    required = {item["phase"] for item in phase_decisions if item["decision"] == "required"}
    assert required == {"spec", "plan"}


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


def test_use_cafe_workflow_defines_event_driven_mode_and_model_authority() -> None:
    skill = _read_skill_resource("SKILL.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    running = _read_skill_resource("references/running_workflow.md")
    models = _read_skill_resource("references/model_selection.md")
    normalized_kickoff = " ".join(kickoff.split())
    normalized_running = " ".join(running.split())
    normalized_models = " ".join(models.split())
    normalized_skill = " ".join(skill.split())

    assert "references/model_selection.md" in skill
    assert "attached with positive polling" in normalized_skill
    assert "event-driven" in skill
    assert "fallback entry requires one explicit exact model" in normalized_skill
    assert "cafe workflow --execute --mute-agent-output" in skill
    assert "scripts/validate_driver_entry.py" in running
    assert "does not inspect `issue.yaml`, phase chains, or capability choices" in running
    assert "manual diagnostic `--single-step`" in normalized_skill
    assert "callbacks are best effort" in normalized_running
    assert "No ordinary operating mode uses it" in normalized_running
    assert "--on-workflow-event builtin:use-cafe-workflow:workflow_event_callback" in running
    assert "`driver/config.yaml` is a legacy migration input" in running
    assert "`codex queue`" in running
    assert "--advancement" not in normalized_running
    assert "--delegated-availability" not in normalized_running
    assert "persisted baton without forcing `--start-step`" in skill
    assert "Attached polling starts after the full confirmed interval" in normalized_running
    assert "exactly one operating mode" in normalized_kickoff
    assert "Do not put the mode, CLI, model, session" in normalized_kickoff
    assert "model_adjustment" not in kickoff
    assert "No provider or model is built into this skill" in normalized_models
    assert "The driver owns the capability-band classification" in normalized_models
    assert "scripts/preflight_cache.py" in models
    assert (SKILL_ROOT / "scripts" / "preflight_cache.py").is_file()
    assert "active worktree's `.cafe/phases.yaml`" in normalized_models


def test_event_driver_documentation_defines_the_contract_managed_lifecycle() -> None:
    skill = _read_skill_resource("SKILL.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    running = _read_skill_resource("references/running_workflow.md")
    contract = " ".join((skill + kickoff + running).split())

    assert 'exactly equivalent to `say "HI"`' in contract
    assert "Codex, Claude, Gemini, Cursor, and Copilot" in contract
    assert "provider-created session ID" in contract
    assert "persisted in `dispatch_state.json` before the actual callback" in contract
    assert "existing acquired session" in contract
    assert "best-effort first-session hint" in contract
    assert "binding failure does not block workflow execution" in contract
    assert "bootstrap never counts as event delivery or acceptance" in contract
    assert "actual callback durable acceptance" in contract
    assert "Copilot never receives a caller-selected new-session ID" in contract
    assert (
        "`dispatch_state.json` is mutable runtime state bound to that contract's digest" in contract
    )
    assert "callback reads the issue-scoped `driver/contract.json`" in contract
    assert "provider acknowledgement is bound to the exact event identity" in contract
    assert "no session-file discovery, directory diff, sleep, polling, or watcher" in contract
    assert "ambiguous outcome stops forward routing" in contract
    assert "transport-local" in contract
    assert "does not merge conversations" in contract
    assert "--status --issue-dir .cafe/issues/<issue>" in contract


def test_use_cafe_workflow_keeps_human_task_completion_in_the_interactive_driver() -> None:
    skill = _read_skill_resource("SKILL.md")
    running = _read_skill_resource("references/running_workflow.md")
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")
    normalized_running = " ".join(running.split())

    assert "user-facing driver turn" in skill
    assert "cafe task complete <task-id> --result '<json>' --no-resume --json" in running
    assert (
        "Direct `cafe task complete` users retain its normal automatic foreground-resume"
        in normalized_running
    )
    assert (
        "cannot wait for, collect, infer, or choose a user answer for a mandatory"
        in normalized_running
    )
    assert "whose confirmed reactive policy is `driver_confirmable` may be completed by any driver" in normalized_running.lower()
    assert "cafe task complete <active-human-task-id>" in handoffs
    assert '--user-input \'{"task":"output-review"' not in handoffs


def test_use_cafe_workflow_makes_user_handoffs_self_contained() -> None:
    skill = _read_skill_resource("SKILL.md")
    handoffs = _read_skill_resource("references/handoffs_and_alignment.md")

    assert "self-contained in conversation" in skill
    assert "no terminal" in skill
    assert "## Present a self-contained user decision" in handoffs
    assert "Render every current question in the conversation" in handoffs
    assert "Never ask the user to open `questions.xml`" in handoffs
    assert "reply `all`" in handoffs
    assert "For every option, state its downstream effect" in handoffs
    assert "`requires_feedback`, `requires_target`, and" in handoffs
    assert "`correction` requirements" in handoffs
    assert "every `allowed_targets` value" in handoffs
    assert "valid reply example" in handoffs


def test_use_cafe_workflow_never_shows_unmuted_driver_execution() -> None:
    offenders = []
    paths = [SKILL_ROOT / "SKILL.md", *sorted((SKILL_ROOT / "references").glob("*.md"))]

    for path in paths:
        logical_text = path.read_text(encoding="utf-8").replace("\\\n", " ")
        for line_number, line in enumerate(logical_text.splitlines(), start=1):
            if "cafe workflow --execute" in line and "--mute-agent-output" not in line:
                offenders.append(f"{path.relative_to(SKILL_ROOT)}:{line_number}")

    assert not offenders, f"unmuted driver execution examples: {offenders}"


def test_driver_keeps_completion_separate_from_external_authority() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/completion_and_authority.md")
    assert "references/completion_and_authority.md" in skill
    assert "scripts/check_action_authority.py" in reference
    assert not (SKILL_ROOT / "references/convergent_pr_review.md").exists()
    for path in [SKILL_ROOT / "SKILL.md", *(SKILL_ROOT / "references").glob("*.md")]:
        text = path.read_text(encoding="utf-8")
        assert "convergent review" not in " ".join(text.split())
        assert "cafe.pr.publish" not in text
        assert "pr.auto_create" not in text
        assert "gh pr merge" not in text
        assert "gh issue close" not in text


def test_driver_can_propose_a_user_approved_bounded_direct_closeout() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/completion_and_authority.md")
    running = _read_skill_resource("references/running_workflow.md")
    normalized = " ".join(reference.split())

    assert "user-approved bounded closeout route" in skill
    assert "## Offer a bounded direct closeout instead of rerunning" in reference
    assert "the workflow is paused" in normalized
    assert "no phase agent, background worker, or callback is running" in normalized
    assert "uncertain liveness disqualifies this route" in normalized
    assert "no pending HumanTask or unresolved declared gate" in normalized
    assert "explicitly says not to rerun the workflow" in normalized
    assert "lists every remaining edit or task" in normalized
    assert "Ask for explicit approval" in reference
    assert "local, reversible, within the confirmed Delivery Contract" in normalized
    assert "make only the listed local edits" in normalized
    assert "do not perform an external action under this approval" in normalized
    assert "or confidence drops, stop direct work" in normalized
    assert "continue process-only monitoring" in normalized
    assert "a nonterminal workflow will remain nonterminal" in normalized
    assert "never describe a still-nonterminal workflow as completed" in normalized
    assert "Direct-closeout approval is session-local authority" in reference
    assert "reauthorize the same remaining list or return to the workflow" in normalized
    assert "a later Driver must not automatically resume" in normalized
    assert "user-approved bounded" in running


class TestPollingContract:
    def test_first_poll_waits_for_the_full_confirmed_interval(self) -> None:
        skill = " ".join(_read_skill_resource("SKILL.md").split())
        kickoff = " ".join(_read_skill_resource("references/kickoff.md").split())
        running = " ".join(_read_skill_resource("references/running_workflow.md").split())

        assert "In attached mode, honor the full positive poll cadence" in skill
        assert "there is no shorter startup or warm-up cadence" in kickoff
        assert "The first proactive inspection is due only after that full interval" in running
        assert "Continue a single deferred wait for the remaining interval instead" in running
        assert "wait on the same deferred operation" in running

    def test_transport_yields_do_not_trigger_workflow_inspection(self) -> None:
        kickoff = " ".join(_read_skill_resource("references/kickoff.md").split())
        running = " ".join(_read_skill_resource("references/running_workflow.md").split())

        assert "is transport state rather than an event-driven signal" in kickoff
        assert "must not cause a sub-interval status or artifact poll" in kickoff
        assert "is transport state, not substantive process output" in running
        assert "It must not trigger a short `write_stdin` poll" in running
        assert "Substantive lifecycle output" in running
        assert "still wake the driver immediately" in running

    def test_formatter_exposes_first_poll_and_timestamp_contract(self, tmp_path: Path) -> None:
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
            _kickoff_formatter_command(
                strategic_context,
                "--driver-mode",
                "attached",
                "--poll-interval-seconds",
                "180",
            ),
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert (
            "| driver.first_poll | after the full interval; "
            "no startup or transport-level poll |" in result.stdout
        )
        assert (
            "| driver.poll_timestamp | capture and print current system time "
            "with every proactive poll |" in result.stdout
        )


@pytest.mark.parametrize("damage", ["missing", "malformed"])
def test_kickoff_rejects_incomplete_delivery_before_activation(tmp_path, damage):
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text("mandate: {preset: technical-led, axes: {}}\n")
    command = _kickoff_formatter_command(strategic_context)
    index = command.index("--delivery-contract")
    if damage == "missing":
        del command[index : index + 2]
    else:
        command[index + 1] = json.dumps({"schema_version": 1, "outcome": "Incomplete."})
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert ("--delivery-contract" if damage == "missing" else "DeliveryContract") in result.stderr
    assert not (tmp_path / ".cafe").exists()
