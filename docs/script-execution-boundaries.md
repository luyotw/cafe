# Script execution boundaries

Every workflow-managed script launch is classified before path resolution. Unknown launchers fail closed. A path, skill override, symlink, or approval never changes the assigned class.

| Launch path | Class | Trust source | Environment / credentials | Network | CWD and writes | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Skill and phase script hooks | sandbox | workflow declaration | constructed allowlist; no credentials | denied unless sandbox policy declares it | workflow cwd; declared roots | execution receipt |
| Permission retry | sandbox | agent permission boundary | constructed allowlist; no credentials | sandbox policy only | original sandbox roots | execution receipt |
| Long-running operation child | sandbox | workflow operation request | constructed allowlist; no credentials | sandbox policy only | issue workspace; declared roots | operation and execution receipts |
| Prepare/close lifecycle script | lifecycle | user-owned canonical declaration | constructed allowlist; no credentials | denied | declared cwd and local write roots | declaration identity and execution receipt |
| Registered adapter | capability | immutable package registry | manifest grants only | manifest destinations only | manifest cwd/write effects | policy decision and capability receipt |
| Bundled GitHub helpers | capability | immutable package registry | manifest grants only | GitHub only | declared issue artifacts | policy decision and capability receipt |

Fixed internal executables used to implement CAFE itself (for example Git queries and the Python worker bootstrap) are not workflow script launchers. They must accept typed internal inputs and cannot be used as an arbitrary-script bridge.

## Process launcher inventory

This inventory is executable documentation: the unit contract discovers every direct `subprocess.run`, `subprocess.Popen`, and `os.exec*` call under `src/cafe`, including multiplicity, and fails when it differs from this table. “Internal” entries have fixed program families and typed inputs; boundary adapters are the only entries permitted to launch workflow-selected behavior.

| Launcher identity | Classification |
| --- | --- |
| `src/cafe/agents/executor.py::_execute_with_streaming` | Internal agent CLI transport |
| `src/cafe/agents/manager.py::_create_claude_session` | Internal agent CLI transport |
| `src/cafe/core/capabilities.py::_current_repo_slug` | Internal fixed Git query |
| `src/cafe/core/capabilities.py::_git_ref_exists` | Internal fixed Git query |
| `src/cafe/core/capabilities.py::run_pr_publish_capability` | Registered host capability adapter |
| `src/cafe/core/git.py::initialize_repository` | Internal fixed Git command |
| `src/cafe/core/git.py::is_repository` | Internal fixed Git query |
| `src/cafe/core/git.py::run_git` | Internal fixed Git command family |
| `src/cafe/core/long_running_operation_helper.py::_monitor` | Sandbox operation adapter |
| `src/cafe/core/long_running_operation_helper.py::run_operation_command` | Internal fixed monitor bootstrap |
| `src/cafe/core/phase_review_mixin.py::_open_file_with_editor` | Explicit interactive editor |
| `src/cafe/data/skills/use-cafe-workflow/scripts/format_kickoff_contract.py::_reexec_with_cafe_python` | Internal fixed Python re-exec |
| `src/cafe/data/skills/use-cafe-workflow/scripts/preflight_cache.py::_cli_fingerprint` | Internal version probe |
| `src/cafe/install/bootstrap.py::_run` | Internal installer command family |
| `src/cafe/skills/native_bridge.py::_ensure_cli_dir_git_excluded` | Internal fixed Git command |
| `src/cafe/ui/chat.py::launch_chat_session` | Internal agent CLI transport |
| `src/cafe/ui/cli.py::_check_for_updates` | Internal package update probe |
| `src/cafe/ui/cli.py::_reexec_repo_entrypoint` | Internal fixed Python re-exec |
| `src/cafe/ui/cli.py::agent_cat` | Explicit interactive pager |
| `src/cafe/ui/cli.py::agent_create` | Explicit interactive editor |
| `src/cafe/ui/cli.py::agent_edit` | Explicit interactive editor |
| `src/cafe/ui/cli_shared.py::_edit_file_with_editor` | Explicit interactive editor |
| `src/cafe/ui/cli_shared.py::_execute_next_phase_auto` | Internal fixed CAFE phase command |
| `src/cafe/ui/commands/agents.py::agent_cat` | Explicit interactive pager |
| `src/cafe/ui/commands/agents.py::agent_create` | Explicit interactive editor |
| `src/cafe/ui/commands/agents.py::agent_edit` | Explicit interactive editor |
| `src/cafe/ui/commands/issues.py::config` | Explicit interactive editor |
| `src/cafe/ui/commands/lifecycle.py::_ensure_worktree_cafe_excluded` | Internal fixed Git command |
| `src/cafe/ui/commands/templates.py::template_cat` | Explicit interactive pager |
| `src/cafe/ui/commands/templates.py::template_create` | Explicit interactive editor |
| `src/cafe/ui/commands/templates.py::template_edit` | Explicit interactive editor |
| `src/cafe/ui/commands/workflow.py::make` | Internal fixed CAFE workflow command |
| `src/cafe/ui/menu.py::_run_command` | Internal menu command dispatcher |
| `src/cafe/utils/config.py::_get_issue_config` | Internal fixed Git query |
| `src/cafe/utils/git_utils.py::get_git_toplevel` | Internal fixed Git query |
| `src/cafe/utils/git_utils.py::get_github_repo_name` | Internal fixed Git query |
| `src/cafe/utils/git_utils.py::rewrite_commit_message` ×2 | Internal fixed Git command family |
| `src/cafe/utils/github.py::add_issue_comment` | Registered GitHub effect boundary |
| `src/cafe/utils/github.py::add_pr_comment` | Registered GitHub effect boundary |
| `src/cafe/utils/github.py::check_gh_auth` | Internal fixed GitHub auth probe |
| `src/cafe/utils/github.py::check_gh_installed` | Internal fixed GitHub CLI probe |
| `src/cafe/utils/github.py::create_pr` | Registered GitHub effect boundary |
| `src/cafe/utils/github.py::download_issue_images` | Registered GitHub read boundary |
| `src/cafe/utils/github.py::get_current_pr_url` | Registered GitHub read boundary |
| `src/cafe/utils/github.py::get_issue` | Registered GitHub read boundary |
| `src/cafe/utils/github.py::get_pr_comments` ×2 | Registered GitHub read boundary |
| `src/cafe/utils/github.py::get_pr_for_branch` | Registered GitHub read boundary |
| `src/cafe/utils/github.py::get_pr_review_body_comments` ×2 | Registered GitHub read boundary |
| `src/cafe/utils/github.py::get_pr_status` | Registered GitHub read boundary |
| `src/cafe/utils/github.py::get_pr_timeline_comments` | Registered GitHub read boundary |
| `src/cafe/utils/github.py::update_issue` | Registered GitHub effect boundary |
| `src/cafe/utils/github.py::update_pr` | Registered GitHub effect boundary |
| `src/cafe/verification/receipt.py::_run_git` | Internal fixed Git query |
| `src/cafe/verification/receipt.py::run_focused_verification` | Explicit verification runner |
| `src/cafe/verification/receipt.py::run_verification` | Explicit verification runner |

## Migrating custom hooks

Existing custom hooks are attempted as sandbox execution and are never grandfathered into host authority. Inspect what the hook actually needs:

1. Keep local, workspace-scoped behavior as a sandbox hook. Verify its receipt reports `sandbox` and `workflow` trust.
2. For a user-owned `prepare` or `close` script with narrow local writes, run `cafe trust lifecycle` and verify the canonical digest, stage, cwd, and write roots. Revoke and recreate the declaration whenever identity or scope changes.
3. For credentials, external network access, or privileged mutation, define a package-owned registered capability with exact effects and policy. Do not point a capability at a project or global skill script.

An unavailable sandbox backend, changed lifecycle identity, unknown launcher, ambient credential dependency, or undeclared network/write request is a safe denial. Correct the boundary and retry; CAFE does not automatically promote or fall back to host execution.
