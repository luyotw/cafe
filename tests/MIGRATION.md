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
| `test_context_session_id_update.py` | `iteration.json` session_id | `tests/unit/test_context_session_id_update.py` (PlanPhase) |
| `test_checklist_validation_status_code.py` | Checklist validation preserves status | `tests/unit/test_checklist_validation_status_code.py` (PlanPhase) |

### Removed-by-design

| Behavior | Rationale |
| --- | --- |
| `SpecPhase._generate_local_prompt` `**Images:**` block | Runtime spec uses skill checklist (`execution_steps_iteration_1.md`) + `checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_1`; `GenericPhase` does not inject a separate images prompt block. |
| `SpecPhase._fetch_github_issue` image download to `spec/images/` | `UserInputCollector._fetch_github_issue` discards `image_urls`; download was not on the production path. Image utilities remain covered in `test_image_download.py`. |
| `SpecPhase._prepare_user_input_for_iteration` review/display/spec-file guards | Replaced by `UserInputCollector` + blackboard; covered in `test_native_user_input_hook.py` and workflow e2e tests. |
| `SpecPhase._ensure_spec_file_written` mock-mode stripping | Spec-phase-only helper; not used by playbook runtime. |
| `test_cli_auto_mode.test_spec_phase_preserves_issue_config` | Issue config save lived on `SpecPhase`; workflow does not call that path. YAML merge preserve behavior covered in `test_issue_yaml_config.py`. |
