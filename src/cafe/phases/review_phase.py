"""Code review phase."""

from pathlib import Path
from typing import List, Optional
import json
from datetime import datetime

import yaml

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus
from cafe.utils.prompt_utils import format_checklist_instruction


class ReviewPhase(Phase):
    """Legacy compatibility implementation for the review phase.

    Review phase is non-iterative: each execution is a single, independent code review.
    Unlike spec/plan/develop phases, there's no conversational loop.
    """

    phase_name = "review"

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        spec_file: str,
        plan_file: str,
        review_agent: str = "Richard",
        target_commit: Optional[str] = None,
        base_branch: Optional[str] = None,
        interactive: bool = True,
        pr_number: Optional[int] = None,
        force: bool = False,
    ) -> None:
        """Initialize review phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            spec_file: Path to spec file (deprecated - will be computed from latest version)
            plan_file: Path to plan file (deprecated - will be computed from latest version)
            review_agent: Review agent name (default: Richard)
            target_commit: Specific commit to review (None for full branch)
            base_branch: Base branch for diff (default: main)
            interactive: Enable interactive mode (default: True)
            pr_number: PR number to fetch unresolved comments from (optional)
            force: Force re-execution even if already completed (default: False)
        """
        super().__init__(interactive=interactive)

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.review_agent = review_agent
        self.target_commit = target_commit
        self.iteration = 1  # Track iteration number for subsequent reviews
        self.pr_number = pr_number
        self.force = force  # Store force flag for use in execute()

        # Get issue directory from current branch
        self.issue_dir = self._get_issue_dir(git_ops)

        # Get latest versioned files
        spec_dir = self.issue_dir / "spec"
        plan_dir = self.issue_dir / "plan"

        latest_spec = self._get_latest_versioned_file("spec", spec_dir)
        latest_plan = self._get_latest_versioned_file("plan", plan_dir)

        # Use latest versioned files if available, otherwise use provided paths
        self.spec_file = str(latest_spec) if latest_spec else spec_file
        self.plan_file = str(latest_plan) if latest_plan else plan_file

        # Try to read base branch from issue config
        config_file = self.issue_dir / "issue.yaml"
        config_base_branch = self._get_issue_config_value(config_file, "base_branch")
        if config_base_branch:
            self.base_branch = config_base_branch
        elif base_branch:
            self.base_branch = base_branch
        else:
            self.base_branch = self.git_ops.get_default_base_branch()

        # Set up phase and history directories (needed for _check_if_already_completed)
        self.phase_dir = self.issue_dir / "review"
        self.review_dir = self.phase_dir  # Alias for backward compatibility
        self.history_dir = self.phase_dir / "history"

    def execute(self) -> PhaseResult:
        """Execute code review phase (single iteration).

        Review phase is non-iterative: executes once and returns result.

        Returns:
            Phase result
        """
        try:
            # Check if there are unpushed commits newer than last review
            # This must be checked BEFORE checking completion status, so that
            # new commits after a CONFIRMED review will trigger a new review
            has_new_commits_to_review = False  # Flag to skip completion check if there are new commits
            has_unpushed = self.git_ops.has_unpushed_commits()
            if has_unpushed:
                review_context = self._get_latest_iteration_context("review", require_completed=True)
                if review_context:
                    try:
                        from datetime import datetime, timezone

                        # Use end_time for comparison, skip if not available
                        review_end_time_str = review_context.get("end_time", "")
                        if review_end_time_str:
                            review_end_time = datetime.fromisoformat(review_end_time_str)
                            if review_end_time.tzinfo is None:
                                review_end_time = review_end_time.replace(tzinfo=timezone.utc)

                            # Check develop phase end_time instead of commit timestamp
                            # This prevents false "already completed" when develop runs but produces no commits
                            develop_end_time_str = self._get_phase_end_time("develop")
                            if develop_end_time_str:
                                develop_end_time = datetime.fromisoformat(develop_end_time_str)
                                if develop_end_time.tzinfo is None:
                                    develop_end_time = develop_end_time.replace(tzinfo=timezone.utc)

                                if review_end_time > develop_end_time:
                                    print(f"✅ Code review already completed - no new development since last review")
                                    print(f"   Last review: {review_end_time.isoformat()}")
                                    print(f"   Latest develop: {develop_end_time.isoformat()}")
                                    print(f"   Continue the workflow with: 'cafe make'")

                                    return PhaseResult(
                                        status=PhaseStatus.COMPLETED,
                                        message=f"Review already completed - no new development since last review",
                                        data={
                                            "status_code": self._context_status_code(review_context),
                                            "review_timestamp": review_end_time.isoformat(),
                                            "latest_develop_timestamp": develop_end_time.isoformat(),
                                        },
                                    )
                                else:
                                    # Develop is newer than review, must do new review
                                    has_new_commits_to_review = True
                    except Exception as e:
                        print(f"⚠️  Warning: Failed to check review timestamp: {e}")
                        pass

            # Check if phase is already completed (avoid re-running completed phases)
            # unless force flag is set OR there are new commits to review
            from cafe.core.status_codes import PhaseStatusCode
            if not has_new_commits_to_review:
                early_exit_result = self._check_if_already_completed(
                    [PhaseStatusCode.CONFIRMED, PhaseStatusCode.REJECTED],
                    force=self.force
                )
                if early_exit_result:
                    return early_exit_result

            # Note: We don't check if diff is empty here - let the review agent
            # see the empty diff and decide (usually NEEDS_CHANGES)



            # Calculate iteration number based on iteration history
            self.iteration = self._get_next_iteration_number("review", self.review_dir)

            # Prepare allowed tools with edit permission for review file
            # Use iteration_XXX/output.md format (consistent with other phases)
            iteration_dir = self.review_dir / f"iteration_{self.iteration:03d}"
            review_file_path = iteration_dir / "output.md"

            # Check for PR feedback file and todo list (from completed PR iterations)
            pr_feedback_file = None
            pr_todo_list_file = None
            pr_dir = self.issue_dir / "pr"
            if pr_dir.exists():
                iteration_dirs = sorted(pr_dir.glob("iteration_*"))
                # Search backwards for the latest iteration with a completed status_code
                for iteration_dir_pr in reversed(iteration_dirs):
                    context_file = iteration_dir_pr / "context.json"
                    if not context_file.exists():
                        continue
                    with open(context_file, "r", encoding="utf-8") as f:
                        ctx = json.load(f)
                    if not self._context_marks_completed(ctx):
                        continue
                    pr_user_input_file = iteration_dir_pr / "user_input.md"
                    if pr_user_input_file.exists() and pr_user_input_file.read_text(encoding="utf-8").strip():
                        from cafe.utils.git_utils import to_cwd_relative_path
                        try:
                            pr_feedback_file = to_cwd_relative_path(pr_user_input_file)
                        except ValueError:
                            pr_feedback_file = str(pr_user_input_file.resolve())
                        # Also capture the PR todo list (output.md in same iteration)
                        pr_output_file = iteration_dir_pr / "output.md"
                        if pr_output_file.exists() and pr_output_file.read_text(encoding="utf-8").strip():
                            try:
                                pr_todo_list_file = to_cwd_relative_path(pr_output_file)
                            except ValueError:
                                pr_todo_list_file = str(pr_output_file.resolve())
                    break

            # Generate checklist for this iteration
            from cafe.utils.checklist_generator import generate_review_checklist

            checklist_path = iteration_dir / "checklist.md"
            generate_review_checklist(
                agent_name=self.review_agent,
                spec_file_path=self.spec_file,
                plan_file_path=self.plan_file,
                review_file_path=str(review_file_path),
                base_branch=self.base_branch,
                checklist_file_path=checklist_path,
                pr_feedback_file_path=pr_feedback_file,
                pr_todo_list_file_path=pr_todo_list_file,
            )

            # Use path relative to current working directory (supports worktree)
            from cafe.utils.git_utils import to_cwd_relative_path

            try:
                review_file_pattern = to_cwd_relative_path(review_file_path)
            except ValueError:
                # Fallback to absolute path if file is not under cwd
                review_file_pattern = str(review_file_path.resolve())

            # Get checklist path for this iteration
            iteration_dir = self._get_iteration_dir(self.iteration)
            checklist_file = iteration_dir / "checklist.md"
            try:
                checklist_pattern = to_cwd_relative_path(checklist_file)
            except ValueError:
                checklist_pattern = str(checklist_file.resolve())

            base_allowed_tools = [
                "read",                         # Read spec and plan files
                "grep",                         # Search file content
                "glob",                         # Find files by pattern
                "ls",                           # List directory contents
                "web_fetch",                    # Fetch web content
                "web_search",                   # Search the web
                "bash(git log)",                # View commit history and messages
                "bash(git diff)",               # View code changes
                "bash(git show)",               # View specific commit details
                "bash(git status)",             # View specific commit details
                f"edit({review_file_pattern})", # Allow editing to specific review file
                f"edit({checklist_pattern})",   # Allow editing checklist
            ]

            # Merge base tools with previous iteration's tools (if any)
            allowed_tools = self._merge_allowed_tools(base_allowed_tools)

            # Create review file with placeholder content to ensure it exists for edit tool
            review_file_path.parent.mkdir(parents=True, exist_ok=True)
            if not review_file_path.exists():
                review_file_path.write_text("# TODO: Write review content here\n")

            # Execute review using base class method
            result, response = self._execute_and_handle_agent_response(
                agent_name=self.review_agent,
                user_input="",  # Review doesn't need user input
                valid_intents=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEEDS_CHANGES,
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEEDS_CHANGES],
                continue_codes=[],  # No continue codes - single iteration only
            )

            # Save review to iteration_XXX/output.md file
            # Note: Real agent would write via Edit tool, but we save it here to ensure
            # the file has actual content in mock mode or if agent doesn't execute Edit tool
            iteration_dir = self.review_dir / f"iteration_{self.iteration:03d}"
            review_file_path = iteration_dir / "output.md"
            # Check if file is placeholder or doesn't exist
            is_placeholder = (review_file_path.exists() and
                            review_file_path.read_text().strip() == "# TODO: Write review content here")
            if not review_file_path.exists() or is_placeholder:
                # Write response if agent didn't write it via Edit tool
                review_file_path.parent.mkdir(parents=True, exist_ok=True)
                review_file_path.write_text(response, encoding="utf-8")

            # If base class returned a result, use it
            if result:
                return result

            # Fallback: In interactive mode, base class may return None for complete_codes
            # Extract status code from response and return completion result
            status_code = self._extract_status_code_from_response(
                response,
                valid_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEEDS_CHANGES,
                ],
            )

            self._print_token_usage_summary()
            token_usage = self.agent_manager.get_total_token_usage()

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="Code review completed",
                data={
                    "iterations": self.iteration,
                    "final_response": response,
                    "status_code": status_code.value if status_code else None,
                    "target_commit": self.target_commit,
                    "base_branch": self.base_branch,
                    "token_usage": {
                        "input_tokens": token_usage.input_tokens,
                        "output_tokens": token_usage.output_tokens,
                        "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                        "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                        "total_cost_usd": token_usage.total_cost_usd,
                    }
                },
                token_usage=token_usage,
            )

        except Exception as e:
            return self._handle_exception_in_execute(e, "Review phase failed")

    def _generate_prompt(self, user_input: str) -> str:
        """Generate review prompt (implements abstract method from Phase).

        Args:
            user_input: Not used for review phase

        Returns:
            Review prompt string
        """
        return self._generate_review_prompt()

    def _get_completion_data(self) -> dict:
        """Get phase-specific completion data (implements abstract method from Phase).

        Returns:
            Dictionary with review-specific data
        """
        return {
            "target_commit": self.target_commit,
            "base_branch": self.base_branch,
        }

    def _save_progress(self, status_code: PhaseStatusCode) -> None:
        """Save phase progress to status.json (overrides base class).

        For ReviewPhase, both CONFIRMED and NEEDS_CHANGES are completion codes.

        Args:
            status_code: Phase status code
        """
        # Both CONFIRMED and NEEDS_CHANGES are completion statuses for review
        complete_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEEDS_CHANGES]
        super()._save_progress(status_code, complete_codes=complete_codes)

    def _check_if_develop_is_newer(self) -> bool:
        """Check if develop phase timestamp is newer than last review.

        Returns:
            True if develop is newer (need to re-run all checks), False otherwise
        """
        try:
            develop_end_time_str = self._get_phase_end_time("develop")
            if not develop_end_time_str:
                return False

            review_end_time_str = self._get_phase_end_time("review")
            if not review_end_time_str:
                # First review, need to re-run all checks
                return True

            develop_time = datetime.fromisoformat(develop_end_time_str)
            review_time = datetime.fromisoformat(review_end_time_str)

            # If develop time is newer than review, there are new changes
            return develop_time > review_time

        except Exception:
            # If error occurs, return True to be safe (re-run checks)
            return True

    def _generate_review_prompt(self) -> str:
        """Generate review prompt.

        Returns:
            Review prompt string
        """
        # Check if need to re-run checks (develop is newer than review)
        develop_is_newer = self._check_if_develop_is_newer()
        recheck_instruction = ""
        # Only show recheck_instruction before iteration 4
        if develop_is_newer and self.iteration < 4:
            recheck_instruction = """
**[Important Notice] Develop phase has new changes after last review, please re-run all checks:**
- **Must re-run git log command**, do not use cached results
- Check the latest commit messages and code changes
- This is a fresh review, please ignore previous review records

"""

        # Generate status code prompt
        status_code_prompt = ""

        # Add restriction for iteration 4+
        restriction = ""
        if self.iteration >= 4:
            # Previous review file
            previous_iteration_dir = self.review_dir / f"iteration_{self.iteration - 1:03d}"
            previous_review_path = previous_iteration_dir / "output.md"
            restriction = f"""
⚠️ **Important Restriction:**
- You are now in iteration {self.iteration}, only follow up on "issues raised in the previous round"
- Previous review content is at: {previous_review_path}
- **Cannot raise new issues, only review new changes after previous review**
"""

        # Generate review file path (iteration_XXX/output.md format)
        iteration_dir = self.review_dir / f"iteration_{self.iteration:03d}"
        review_file_path = iteration_dir / "output.md"

        # Build prompt
        try:
            from cafe.agents.manager import AgentManager
            from cafe.skills.bridge import try_load_skill_body

            agent_file = AgentManager.get_agent_file_path(self.review_agent, "reviewer")
            skill_body = try_load_skill_body(
                "review",
                context={
                    "agent_file": agent_file,
                    "spec_file": str(self.spec_file),
                    "plan_file": str(self.plan_file),
                    "output_file": str(review_file_path),
                    "status_code_instruction": status_code_prompt,
                },
            )
            skill_section = f"{skill_body}\n\n" if skill_body else ""

            # Get checklist file path
            iteration_dir = self._get_iteration_dir(self.iteration)
            checklist_file = iteration_dir / "checklist.md"
            from cafe.utils.git_utils import to_cwd_relative_path
            try:
                checklist_path = to_cwd_relative_path(checklist_file)
            except ValueError:
                checklist_path = str(checklist_file.resolve())

            recheck_note = ""
            if recheck_instruction:
                recheck_note = recheck_instruction

            restriction_note = ""
            if restriction:
                restriction_note = restriction

            checklist_instruction = format_checklist_instruction(checklist_path)
            base_prompt = f"""# Review Phase

{skill_section}\
**Your Role:** Reviewer
Read {agent_file} to understand your complete role definition and responsibilities.

{checklist_instruction}

⚠️ **CRITICAL WARNING:** Do NOT check off items in the checklist without actually executing them. If you are caught checking items without performing the actual work, you will be fired immediately and the police will be called.

**Task:** Conduct iteration {self.iteration} code review.
Review scope: commits in current branch but not in {self.base_branch}.
{recheck_note}
{restriction_note}

{status_code_prompt}

**Commit Message Update Command Example:**
```bash
# Modify commit abc123 message
echo "Fix login logic" > ./commit_msg.txt && \\
git rebase --onto {self.base_branch} {self.base_branch} HEAD --exec '
  if test $(git rev-parse HEAD) = abc123 || test $(git rev-parse HEAD) = $(git rev-parse abc123); then
    git commit --amend -F ./commit_msg.txt --allow-empty --no-edit;
  fi
' && rm -f ./commit_msg.txt
```
"""
        except Exception as e:
            raise RuntimeError(f"Error building prompt: {e}") from e

        return base_prompt


    def _get_status_analysis_prompt(self) -> str:
        """Get prompt for analyzing status code.

        Returns:
            Analysis prompt string
        """
        iteration_dir = self.review_dir / f"iteration_{self.iteration:03d}"
        review_file = iteration_dir / "output.md"
        return f"""Please read {review_file} and analyze the code review results.

Based on the following conditions, determine which status code to return:

- confirmed: Code review passed, no issues to fix
- needs_changes: Issues need to be fixed

Please only return one status code (e.g., confirmed) without any other content."""

    def _detect_written_output_files(self) -> List[Path]:
        """Check if review file was written before failure.

        Returns:
            List[Path]: Returns list containing iteration_XXX/output.md if it exists, otherwise empty list
        """
        iteration_dir = self.review_dir / f"iteration_{self.iteration:03d}"
        review_file = iteration_dir / "output.md"
        return [review_file] if review_file.exists() else []
