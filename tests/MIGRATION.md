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
| `test_spec_prompt_with_images.py` | Images checklist line | `tests/unit/test_checklist_generator.py` (`SPEC_EXECUTION_STEPS_ITERATION_1`) |
| `test_spec_image_download.py` | Image URL download | `tests/unit/test_image_download.py` |
| `test_spec_interactive_qa_e2e.py` | Spec clarification pause → resume → plan | `tests/integration/test_spec_clarification_runtime.py`, `tests/integration/test_workflow_e2e.py` |
| `test_context_session_id_update.py` | `iteration.json` session_id | `tests/unit/test_context_session_id_update.py` (`GenericWorkflowStepExecutor`) |
| `test_checklist_validation_status_code.py` | Checklist validation preserves status | `tests/unit/test_checklist_validation_status_code.py` (`GenericWorkflowStepExecutor`) |

### Removed-by-design

| Behavior | Rationale |
| --- | --- |
| `SpecPhase._generate_local_prompt` `**Images:**` block | Runtime spec uses skill checklist (`execution_steps_iteration_1.md`) + `checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_1`; `GenericPhase` does not inject a separate images prompt block. |
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
