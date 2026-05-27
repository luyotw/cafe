# Test migration log (phase retirement)

This file records behaviors removed or re-homed when retiring legacy per-phase Python classes.
Related issues: #290 (SpecPhase), #291–#294 (sibling retirements).

## Issue 290 — Retire SpecPhase

GitHub: issue #290

Production spec already runs via `BlackboardWorkflowRuntime` + `GenericPhase` + skills. Legacy `SpecPhase` and its test files were deleted after contracts moved to the layers below.

### Re-homed contracts

| Source | Behavior | New location |
| --- | --- | --- |
| `test_spec_phase_qa.py` | `_validate_and_retry_questions_xml` | `tests/unit/test_phase_review_mixin.py` |
| `test_spec_phase_qa.py` | Interactive QA / clarification input | `tests/unit/test_native_user_input_hook.py` |
| `test_spec_interactive_qa_e2e.py` | XML parse, checkbox, `interactive_qa_flow` | `tests/unit/test_questions_schema.py`, `tests/unit/test_native_user_input_hook.py` |
| `test_spec_phase_sync_github_config.py` | `issue.yaml` `spec.sync_github` | `tests/unit/test_issue_yaml_config.py`, `tests/unit/test_resolve_sync_github.py` |
| `test_spec_github_sync.py` | No phase-internal confirmed sync | `tests/unit/test_skill_sync_github_script.py`, runtime baton hooks |
| `test_spec_prompt_with_images.py` | Images checklist line | `tests/unit/test_skill_checklist_composer.py` (`spec/execution_steps_iteration_1.md`) |
| `test_spec_image_download.py` | Image URL download | `tests/unit/test_image_download.py` |
| `test_spec_interactive_qa_e2e.py` | Spec clarification pause → resume → plan | `tests/integration/test_spec_clarification_runtime.py`, `tests/integration/test_workflow_e2e.py` |
| `test_context_session_id_update.py` | `iteration.json` session_id | `tests/unit/test_context_session_id_update.py` (`GenericWorkflowStepExecutor`) |
| `test_checklist_validation_status_code.py` | Checklist validation preserves status | `tests/unit/test_checklist_validation_status_code.py` (`GenericWorkflowStepExecutor`) |

### Removed-by-design

| Behavior | Rationale |
| --- | --- |
| `SpecPhase._generate_local_prompt` `**Images:**` block | Runtime spec uses skill checklist (`spec/references/execution_steps_iteration_1.md`); `GenericPhase` does not inject a separate images prompt block. |
| `SpecPhase._fetch_github_issue` image download to `spec/images/` | `UserInputCollector._fetch_github_issue` discards `image_urls`; download was not on the production path. Image utilities remain covered in `test_image_download.py`. |
| `SpecPhase._prepare_user_input_for_iteration` review/display/spec-file guards | Replaced by `UserInputCollector` + blackboard; covered in `test_native_user_input_hook.py` and workflow e2e tests. |
| `SpecPhase._ensure_spec_file_written` mock-mode stripping | Spec-phase-only helper; not used by playbook runtime. |
| `test_cli_auto_mode.test_spec_phase_preserves_issue_config` | Issue config save lived on `SpecPhase`; workflow does not call that path. YAML merge preserve behavior covered in `test_issue_yaml_config.py`. |

## Issue 291 — Retire PlanPhase

GitHub: issue #291

Production plan already runs via `BlackboardWorkflowRuntime` + `GenericPhase` + skills. Legacy `PlanPhase` and its test files were deleted after contracts moved to the layers below.

### Re-homed contracts

| Source | Behavior | New location |
| --- | --- | --- |
| `test_plan_phase_qa.py` | `_validate_and_retry_questions_xml` | `tests/unit/test_phase_review_mixin.py` (from #290) |
| `test_plan_phase_qa.py` | Interactive QA / clarification input | `tests/unit/test_native_user_input_hook.py`, `tests/integration/test_plan_clarification_runtime.py` |
| `test_plan_phase_sync_github_config.py` | `issue.yaml` `plan.sync_github` | `tests/unit/test_issue_yaml_config.py`, `tests/unit/test_resolve_sync_github.py` |
| `test_plan_phase_github_sync.py` | No phase-internal confirmed sync | `tests/unit/test_skill_sync_github_script.py`, default playbook script hooks |
| `test_plan_phase_execute_xml.py` | Plan checklist `questions_xml_file` | `tests/unit/test_plan_checklist_xml.py`, `tests/unit/test_generic_workflow_step.py` |
| `test_plan_phase_template_mode.py` | Auto/manual checklist template instructions | `tests/unit/test_plan_checklist_xml.py`, `tests/unit/test_cli_prepare.py`, `tests/unit/test_template_selector_auto.py` |
| `test_plan_phase_status_codes.py` | Status/baton transitions (ready_for_review, need_clarification) | `tests/unit/test_workflow_runtime.py`, `tests/integration/test_workflow_e2e.py` (`test_plan_self_loop_then_confirms`), `tests/integration/test_plan_clarification_runtime.py` |
| `test_plan_phase_status_codes.py` | Status token parsing (middle of response, case insensitive, need_permission token) | `tests/unit/test_status_code_parser.py`, `tests/unit/test_workflow_runtime.py` (`test_runtime_plan_need_permission_pauses_at_user`) |
| `test_context_session_id_update.py` | `iteration.json` session_id | `tests/unit/test_context_session_id_update.py` (`GenericWorkflowStepExecutor`) |
| `test_checklist_validation_status_code.py` | Checklist validation preserves status | `tests/unit/test_checklist_validation_status_code.py` (`PhaseChecklistMixin` stub) |
| `test_phase_non_interactive_behavior.py` | Plan non-interactive ready_for_review / need_clarification handoff | `tests/unit/test_generic_workflow_step.py` (`test_plan_non_interactive_*`) |
| `test_phase_skill_bridge.py` | Skill loader / checklist reference helpers | `tests/unit/test_phase_skill_bridge.py` (minus plan prompt body case) |

### Removed-by-design

| Behavior | Rationale |
| --- | --- |
| `PlanPhase._prepare_user_input_for_iteration` plan review menu / delta display | Runtime uses baton `confirm_output` + skill checklist; legacy interactive review loop not on production path |
| `PlanPhase._load_plan_config` internal `_sync_github` flag driving phase-internal sync | Sync runs via skill `sync_github.sh` after user confirmation per workflow-common |
| `PlanPhase` legacy `plan/plan.md` monolithic layout | Production uses `.cafe/issues/{issue}/plan/iteration_N/{output,checklist,questions}.md` via GenericPhase |
| `PlanPhase.template_mode` / `template_path` instance attributes | Template mode resolved via `issue.yaml` + `generate_plan_checklist` / CLI prepare, not a phase class |
| Duplicate mixin XML retry tests | Covered by `test_phase_review_mixin.py` from #290 |
| `PlanPhase._generate_prompt` inline skill body via `try_load_skill_body` | `GenericPhase.build_prompt` references installed skill invocations only; skill content lives in native skill install path (`test_build_prompt_references_skill_invocation_not_embedded_body`) |
| `test_plan_phase_status_codes.py` permission_denials → need_permission without agent retry | Generic workflow plan step maps status via `StatusCodeParser` on agent text at step boundary; structured `permission_denials` from agent execute are not re-mapped when `require_status_code=False` during the agent callback. Agents should return `need_permission` or write a baton; runtime pause covered in `test_runtime_plan_need_permission_pauses_at_user` |
| `test_plan_phase_status_codes.py` plaintext permission markers without token | `PhaseStateMixin._infer_human_input_status_from_response` retained for legacy phase classes (`test_phase_state_mixin.py`); not invoked on generic workflow plan executor path |
| `test_plan_phase_status_codes.py` `test_no_status_code_continues_iteration` (interactive retry loop) | PlanPhase multi-iteration status recovery removed with class; generic workflow is single-pass per step with baton-first handoff |

## Issue 292 — Retire DevelopPhase

GitHub: issue #292

Production develop already runs via `BlackboardWorkflowRuntime` + `GenericPhase` + skills. Legacy `DevelopPhase` and its test files are deleted after contracts move to the layers below.

### Re-homed contracts

| Source | Behavior | New location |
| --- | --- | --- |
| `test_develop_phase_prompt.py` | Correction checklist `feedback_file_path` | `tests/unit/test_skill_checklist_composer.py`, `tests/unit/test_generic_workflow_step.py` |
| `test_develop_clarification.py` | `need_clarification` + questions.xml | `tests/unit/test_phase_review_mixin.py`, `tests/integration/test_develop_clarification_runtime.py` |
| `test_develop_clarification.py` | `questions_xml_file` in checklist | `tests/unit/test_generic_workflow_step.py` |
| `test_develop_no_changes_needed.py` | Reasoning gate + user decision | `tests/unit/test_no_changes_needed_handler.py`, `tests/unit/test_native_user_input_hook.py` |
| `test_develop_phase_no_changes_chat.py` | Agree/disagree/chat UI | `tests/unit/test_no_changes_needed_handler.py` |
| `test_develop_no_changes_needed.py` | Checklist `output_file` / `no_changes_needed` | `tests/unit/test_skill_checklist_composer.py` |
| `test_context_session_id_update.py` | develop `iteration.json` session_id | `tests/unit/test_context_session_id_update.py` (`GenericWorkflowStepExecutor`) |
| `test_checklist_validation_status_code.py` | develop checklist gate | `tests/unit/test_checklist_validation_status_code.py` |
| `test_workflow_e2e.py` | develop self-loop / handoff | `tests/integration/test_workflow_e2e.py`, `tests/integration/test_develop_clarification_runtime.py` |

### Removed-by-design

| Behavior | Rationale |
| --- | --- |
| `DevelopPhase._generate_prompt` inline PR comment blocks | PR/review via checklist `feedback_file_path`; `GenericPhase.build_prompt` uses skill invocations |
| Review vs develop `end_time` via `develop/status.json` | Blackboard artifacts + `correction_mode`; no legacy status.json timing |
| `CONFIRMED_SKIP_REVIEW` + `skip_review` resume | Legacy status.json; user agree routes via `manual_handoff: pr` on default playbook |
| `DevelopPhase._prepare_user_input` permission/Codex recovery | `PermissionRetryHandler` is NoOp on default playbook |
| `DevelopPhase._check_if_already_completed_with_review` early return | Per-iteration blackboard + review→develop `manual_handoff` |

## Issue 293 — Retire PRPhase

GitHub: issue #293

Production PR already runs via `BlackboardWorkflowRuntime` + `GenericPhase` + skills. Legacy `PRPhase` and PR-bound test files are deleted after contracts move to the layers below.

### Re-homed contracts

| Source | Behavior | New location |
| --- | --- | --- |
| `test_pr_phase_parsing.py` | `parse_pr_title`, `parse_pr_body` | `tests/unit/test_pr_parsing.py`, `src/cafe/utils/pr.py` |
| `test_pr_phase_post_todo_comment.py` | `post_pr_todo_list`, todo-list gates | `tests/unit/test_github_pr_comments.py` (`TestPostPrTodoList`) |
| `test_pr_phase_issue_comment.py` | `issue_id` top-level vs `spec.issue_id`, coercion | `tests/unit/test_issue_config.py`, `src/cafe/utils/issue_config.py` |
| `test_pr_phase_context.py` | Iteration field preservation on second update | `tests/unit/test_generic_workflow_step.py` (`test_update_iteration_history_preserves_model_and_stats_on_second_call`) |
| `test_pr_comment_images.py` | `GitHubOps.extract_image_urls` | Unchanged (scrubbed unused `PRPhase` import) |
| `test_pr_phase_github_mode.py` | `last_seen_comment_ids` artifact I/O | `tests/unit/test_github_pr_comments.py` (`persist` / `load` round-trip); runtime PR flow in `test_workflow_runtime.py`, `test_generic_workflow_step.py` |
| `test_pr_command_non_interactive.py` | Title/body parse + gh create | `tests/integration/test_pr_command_non_interactive.py` (utils + `sync_pr.sh` + runtime alias) |
| `test_pr_e2e_with_mock.py` | PR step completion + capability receipt | `tests/integration/test_pr_e2e_with_mock.py` (`BlackboardWorkflowRuntime`) |

### Removed-by-design

| Behavior | Rationale |
| --- | --- |
| `PRPhase._get_status_analysis_prompt()` | Prompt lives in skill/playbook YAML; no Python unit target |
| `PRPhase._organize_comments_to_todo_list()` output.md init | Runtime output file setup + skill checklist |
| `PRPhase._prepare_pr_content()` / `_generate_pr_content()` orchestration | Replaced by `GenericPhase` + `sync_pr.sh` / publish hook |
| `test_pr_phase_iteration_logic.py` iteration orchestration helpers | Covered by runtime/blackboard tests; comment IDs in `test_github_pr_comments.py` |
| `test_pr_phase_output_todo_only.py` output.md empty guard | Todo contract in `post_pr_todo_list` tests; init is runtime-owned |
| `test_pr_phase_prepare_content.py` / `test_pr_phase_generate_content.py` | Legacy agent orchestration; production uses workflow executor |
| `PRPhase.execute()` draft/custom title integration | Legacy `cafe pr` routes to runtime alias; publish uses `sync_pr.sh` + `parse_pr_*` |

## Issue 294 — Phase retirement cleanup

GitHub: issue #294 (closes umbrella #288)

Baseline (develop iteration 001): `pytest tests/unit tests/integration -q` → 1980 passed, 5 skipped, 1 xfailed.

**Product decision (issue #294):** Option A — retain hidden `cafe spec|plan|develop|review|pr` as thin documented aliases. **Retired in issue #315** — `phases_legacy.py` and the hidden commands were deleted; use `cafe make` or `cafe workflow --start-step <step> --execute`.

Pre-flight `src/` grep (before cleanup): `src/cafe/utils/pr.py`, `src/cafe/ui/cli_shared.py`, `src/cafe/core/phase.py`.

Final verification: `rg 'SpecPhase|PlanPhase|DevelopPhase|ReviewPhase|PRPhase' src/` → **zero** lines; guardrail `tests/unit/test_no_deleted_phase_names_in_src.py`.

### Re-homed / updated contracts

| Area | Behavior | Notes |
| --- | --- | --- |
| Legacy CLI notice | Retired in issue #315 | `tests/unit/test_phases_legacy_retired.py` guardrail; `cafe workflow --start-step <step> --execute` is the replacement |
| Module docs | Retired (issue #315) | `phases_legacy.py` removed; migration note in CHANGELOG |
| Contributor docs | Source-of-truth boundary | `CONTRIBUTING.md` **Workflow behavior**, `docs/roadmap.md` v0.2 completion criteria |
| PR parsing utils | `parse_pr_title` / `parse_pr_body` | `src/cafe/utils/pr.py` (generic module docstring) |

### Removed-by-design

| Behavior | Rationale |
| --- | --- |
| Option B (delete `phases_legacy.py`) | Adopted in issue #315 after Option A served its transition period |
| Per-phase Python classes | Already removed in #289–#293; this issue scrubs cosmetic names only |
| “being retired” legacy CLI copy | Replaced with explicit alias-to-workflow messaging |

## Issue 317 — Workflow/worktree/skill test audit

GitHub: issue #317

Baseline (issue317 develop): `uv run --with pytest pytest tests/unit tests/integration -q` → 2017 passed, 1 skipped, 1 xfailed (before changes); **2022 passed**, 1 skipped, 1 xfailed (after issue317 additions).

### Inventory (keep / update / delete / add)

| File / area | Disposition | Notes |
| --- | --- | --- |
| `tests/unit/test_skill_loader.py` | **keep** | project > global > builtin precedence; project `install_skill` body |
| `tests/unit/test_generic_phase.py` | **keep** | project-local native skill install paths |
| `tests/unit/test_generic_workflow_step.py` | **keep** | workflow-common + phase skill install per step |
| `tests/unit/test_workflow_runtime.py` | **keep** | structured baton default; `test_runtime_rejects_legacy_text_baton_in_core_path`; agent-step legacy normalize (`test_runtime_normalizes_legacy_baton_written_by_pr_agent`) as explicit boundary |
| `tests/unit/test_workflow_models.py` | **keep** | `allow_legacy_text` legacy step-name fallback; invalid JSON still rejects |
| `tests/unit/test_phases_legacy_retired.py` | **update** | guardrail for retired `cafe spec|plan|…`; add `cafe workflow --start-step spec` dry-run |
| `tests/unit/test_issue_yaml_config.py` | **update** | docstrings only (remove SpecPhase/PlanPhase mirror wording) |
| `tests/integration/test_workflow_e2e.py` | **keep** | happy path, pause/resume, baton handoff |
| `tests/integration/test_*_clarification_runtime.py` | **keep** | per-step user pause contracts |
| `tests/unit/test_git_worktree.py` | **keep** | git worktree plumbing only |
| `tests/unit/test_cli_prepare.py`, `test_prepare_non_github.py` | **keep** | prepare/close worktree options |
| `tests/unit/test_parallel_skill_install.py` | **add** | parallel workflow installs isolated per project root; no home-dir writes |
| `tests/integration/test_worktree_workflow_parallel.py` | **add** | worktree cwd workflow pause→resume; two worktrees / two issues isolated |

### Added coverage

| Behavior | New location |
| --- | --- |
| Parallel CAFE workflows install skills without cross-writing | `tests/unit/test_parallel_skill_install.py` |
| `install_skill` does not populate user home native skills dir | `tests/unit/test_parallel_skill_install.py` |
| Workflow in git worktree: spec user pause then advance toward plan | `tests/integration/test_worktree_workflow_parallel.py` |
| Parallel worktrees: separate blackboard/baton/artifacts per issue | `tests/integration/test_worktree_workflow_parallel.py` |
| `cafe workflow --start-step spec` supported; `cafe spec` unknown | `tests/unit/test_phases_legacy_retired.py` |

### Removed-by-design (issue317)

| Behavior | Rationale |
| --- | --- |
| Duplicate legacy-text baton tests outside runtime/models boundaries | No additional deletes this pass; runtime agent-step normalize kept as single integration boundary alongside `test_workflow_models.allow_legacy_text` |
| Tests asserting global home as default install target | None found; new contract tests lock project-local install |
