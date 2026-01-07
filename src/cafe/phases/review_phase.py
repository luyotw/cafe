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
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.utils.github import get_pr_comments, filter_unresolved_comments, format_comments_for_prompt


class ReviewPhase(Phase):
    """Phase 4: Code review with reviewer agent.

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
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        review_agent: str = "Richard",
        target_commit: Optional[str] = None,
        base_branch: str = "main",
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
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
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
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.review_agent = review_agent
        self.target_commit = target_commit
        self.iteration = 1  # Track iteration number for subsequent reviews
        self.pr_number = pr_number
        self._pr_comments_cache = None  # Cache for PR comments to avoid duplicate loading
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
        self.base_branch = config_base_branch if config_base_branch else base_branch

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
                review_status_file = self.issue_dir / "review" / "status.json"

                if review_status_file.exists():
                    try:
                        from datetime import datetime, timezone

                        with open(review_status_file, 'r', encoding='utf-8') as f:
                            review_status = json.load(f)

                        review_timestamp_str = review_status.get("timestamp", "")
                        if review_timestamp_str:
                            review_timestamp = datetime.fromisoformat(review_timestamp_str)
                            if review_timestamp.tzinfo is None:
                                review_timestamp = review_timestamp.replace(tzinfo=timezone.utc)

                            # Check develop phase timestamp instead of commit timestamp
                            # This prevents false "already completed" when develop runs but produces no commits
                            develop_timestamp_str = self._get_phase_timestamp("develop")
                            if develop_timestamp_str:
                                develop_timestamp = datetime.fromisoformat(develop_timestamp_str)
                                if develop_timestamp.tzinfo is None:
                                    develop_timestamp = develop_timestamp.replace(tzinfo=timezone.utc)

                                if review_timestamp > develop_timestamp:
                                    print(f"✅ Code review already completed - no new development since last review")
                                    print(f"   Last review: {review_timestamp.isoformat()}")
                                    print(f"   Latest develop: {develop_timestamp.isoformat()}")
                                    print(f"   Next step: Run 'cafe pr' to push changes")

                                    return PhaseResult(
                                        status=PhaseStatus.COMPLETED,
                                        message=f"Review already completed - no new development since last review",
                                        data={
                                            "status_code": review_status.get("status_code"),
                                            "review_timestamp": review_timestamp.isoformat(),
                                            "latest_develop_timestamp": develop_timestamp.isoformat(),
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

            # Check PR comments if pr_number is provided
            if self.pr_number:
                print(f"ℹ️  PR #{self.pr_number} comments will be reviewed")
                _, unresolved_count = self._load_pr_comments()
                if unresolved_count == 0:
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"PR #{self.pr_number} has no unresolved comments. Nothing to review.",
                        data={
                            "pr_number": self.pr_number,
                        },
                    )

                # Check if there are commits since the latest PR comment
                if not self._has_commits_since_pr_comments():
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"PR #{self.pr_number} has no new commits since latest PR comment. Nothing to review.",
                        data={
                            "pr_number": self.pr_number,
                        },
                    )

            # Calculate iteration number based on iteration history
            self.iteration = self._get_next_iteration_number("review", self.review_dir)

            # Prepare allowed tools with edit permission for review file
            # Use iteration_XXX/output.md format (consistent with other phases)
            iteration_dir = self.review_dir / f"iteration_{self.iteration:03d}"
            review_file_path = iteration_dir / "output.md"

            # Use path relative to current working directory (supports worktree)
            from cafe.utils.git_utils import to_cwd_relative_path

            try:
                review_file_pattern = to_cwd_relative_path(review_file_path)
            except ValueError:
                # Fallback to absolute path if file is not under cwd
                review_file_pattern = str(review_file_path.resolve())

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
                "bash(git status)",               # View specific commit details
                f"edit({review_file_pattern})",  # Allow editing to specific review file
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
                valid_status_codes=[
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
            from cafe.core.status_codes import StatusCodeParser
            status_code = StatusCodeParser.extract(
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

    def _load_pr_comments(self) -> tuple[str, int]:
        """Load PR comments if pr_number is provided.

        Returns:
            Tuple of (formatted comments string, unresolved count)
        """
        if not self.pr_number:
            return "", 0

        # Return cached result if already loaded
        if self._pr_comments_cache is not None:
            return self._pr_comments_cache

        try:
            print(f"  → Calling get_pr_comments({self.pr_number})")
            comments = get_pr_comments(self.pr_number)
            print(f"  → Got {len(comments)} total comments")

            unresolved = filter_unresolved_comments(comments)
            print(f"  → {len(unresolved)} unresolved comments")

            result = format_comments_for_prompt(unresolved)
            if result:
                print(f"  → Formatted result length: {len(result)} chars")

            # Cache the result
            self._pr_comments_cache = (result, len(unresolved))
            return self._pr_comments_cache
        except (ValueError, Exception) as e:
            # Log error but don't fail - PR comments are optional context
            print(f"⚠️  Failed to load PR comments: {e}")
            import traceback
            traceback.print_exc()
            self._pr_comments_cache = ("", 0)
            return self._pr_comments_cache

    def _get_latest_pr_comment_timestamp(self):
        """Get timestamp of the latest PR comment.

        Returns:
            datetime object of the latest comment, or None if no comments
        """
        if not self.pr_number:
            return None

        try:
            comments = get_pr_comments(self.pr_number)
            if not comments:
                return None

            # Find the latest comment by created_at timestamp
            from datetime import datetime
            latest_timestamp = None
            for comment in comments:
                timestamp_str = comment.created_at
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str.replace('Z', '+00:00')
                comment_time = datetime.fromisoformat(timestamp_str)

                if latest_timestamp is None or comment_time > latest_timestamp:
                    latest_timestamp = comment_time

            return latest_timestamp
        except Exception as e:
            print(f"⚠️  Failed to get latest PR comment timestamp: {e}")
            return None

    def _has_commits_since_pr_comments(self) -> bool:
        """Check if there are commits since the latest PR comment.

        Returns:
            True if there are new commits, False otherwise
        """
        latest_comment_time = self._get_latest_pr_comment_timestamp()
        if not latest_comment_time:
            # No PR comments, so proceed with review
            return True

        try:
            # Get commits since the latest PR comment timestamp
            timestamp_str = latest_comment_time.isoformat()
            commits = self.git_ops.get_commits_since(timestamp_str)
            return len(commits) > 0
        except Exception as e:
            print(f"⚠️  Failed to check commits since PR comments: {e}")
            # On error, assume there are new commits to be safe
            return True

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
        import json
        from datetime import datetime
        from cafe.core.types import PhaseStatus, PhaseProgress

        status_file = self._get_status_file()
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Both CONFIRMED and NEEDS_CHANGES are completion statuses for review
        complete_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEEDS_CHANGES]
        phase_status = PhaseStatus.COMPLETED if status_code in complete_codes else PhaseStatus.IN_PROGRESS

        # Set end_time when phase completes
        end_time = datetime.now().astimezone() if phase_status == PhaseStatus.COMPLETED else None

        progress = PhaseProgress(
            phase=self.phase_name,
            status=phase_status,
            status_code=status_code.value,
            timestamp=datetime.now().astimezone(),
            iteration=self.iteration,
            message=f"Code review completed with {status_code.value}" if phase_status == PhaseStatus.COMPLETED else f"Iteration {self.iteration}",
            end_time=end_time,
        )

        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

    def _check_if_develop_is_newer(self) -> bool:
        """Check if develop phase timestamp is newer than last review.

        Returns:
            True if develop is newer (need to re-run all checks), False otherwise
        """
        try:
            # Get issue name
            if not self.spec_file:
                return False  # No spec file, cannot compare timestamps
            spec_path = Path(self.spec_file).resolve()
            issue_name = spec_path.parent.parent.name

            # Read develop/status.json (using path relative to spec_file)
            issue_dir = spec_path.parent.parent
            develop_status_file = issue_dir / "develop" / "status.json"
            if not develop_status_file.exists():
                return False

            # Read review/status.json
            review_status_file = issue_dir / "review" / "status.json"
            if not review_status_file.exists():
                # First review, need to re-run all checks
                return True

            # Compare timestamps
            with open(develop_status_file) as f:
                develop_data = json.load(f)
            with open(review_status_file) as f:
                review_data = json.load(f)

            develop_time = datetime.fromisoformat(develop_data["timestamp"])
            review_time = datetime.fromisoformat(review_data["timestamp"])

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
        # Load PR comments if available
        pr_comments, _ = self._load_pr_comments()
        pr_comments_section = f"\n\n{pr_comments}\n" if pr_comments else ""

        # Get requirements section
        try:
            requirements_section = self._get_requirements_section()
        except Exception as e:
            raise RuntimeError(f"Error in _get_requirements_section: {e}") from e

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
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEEDS_CHANGES,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "Code review passed, no issues",
                PhaseStatusCode.NEEDS_CHANGES: "Changes needed",
            },
        )

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
            from cafe.utils.prompt_utils import extract_agent_guidelines_checklist

            agent_file = AgentManager.get_agent_file_path(self.review_agent, "reviewer")
            agent_guidelines_checklist = extract_agent_guidelines_checklist(agent_file)

            execution_steps = f"""
## Execution Steps Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the requirements specification and implementation plan
[ ] Review commits in current branch (not in {self.base_branch})
[ ] Complete all review tasks listed below
[ ] Save complete review result to {review_file_path} in your native language
[ ] Return appropriate status code
"""

            review_tasks = f"""
## Review Tasks Checklist (Priority Order)

**1. Git Status Check**
[ ] Check for uncommitted changes (if any, development is incomplete)
[ ] Check for sensitive info (passwords, API keys, credentials - CRITICAL if found)

**2. [Important] Check Commit Message Style Consistency**
[ ] Get commits: `git log {self.base_branch}..HEAD` (current branch)
[ ] Get commits: `git log {self.base_branch} --max-count=5` (base branch)
[ ] Calculate if base branch uses single-line or multi-line messages
[ ] Calculate if current branch uses single-line or multi-line messages
[ ] Compare consistency: body presence and language must match base branch
[ ] If inconsistent: list non-conforming commit SHAs and provide update commands

**3. Confirm Implementation Completion**
[ ] Check for unfinished items in implementation plan

**4. Find Potential Issues**
[ ] Check conformance to existing project coding style
[ ] Check for excessive duplicate code
[ ] Check code correctness, readability, performance, security
[ ] Check for missing updates (error messages, docs, examples)
[ ] Check for files that should not be committed (config, logs)
[ ] Check for files or code that should not be deleted

**5. Brief Explanation of Issues**
[ ] List file paths and line numbers with explanations
[ ] Do NOT provide code solutions
"""

            important_notes = f"""
## Important Notes Checklist

[ ] ✅ Write review result in your native language
[ ] ✅ Save to {review_file_path} in Markdown format
[ ] ✅ Include all issues and suggestions
[ ] ⚠️ Commit message style issues are CRITICAL - must fix before passing
[ ] ✅ Return status code only, no summary or explanation
{f"[ ] ⚠️ Iteration {self.iteration}: Only follow up on previous round issues" if self.iteration >= 4 else ""}
"""

            recheck_note = ""
            if recheck_instruction:
                recheck_note = recheck_instruction

            restriction_note = ""
            if restriction:
                restriction_note = restriction

            base_prompt = f"""# Review Phase

**Your Role:** Reviewer
Read {agent_file} to understand your complete role definition and responsibilities.

**Task:** Conduct iteration {self.iteration} code review.
Review scope: commits in current branch but not in {self.base_branch}.

**Requirements Specification and Implementation Plan:**
{requirements_section}
{pr_comments_section}
{recheck_note}
{restriction_note}
"""

            prompt = f"""{base_prompt}

{execution_steps}

{review_tasks}

{important_notes}

{agent_guidelines_checklist}

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

        return prompt



    def _get_requirements_section(self) -> str:
        """Get requirements and plan section for review prompt.

        Returns:
            Requirements and plan section string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return f"Please use `gh issue view {self.issue_id}` to view Issue content (including requirements and implementation analysis)."
        else:
            # Only provide file paths, let agent read them
            # Avoid overly long prompts
            return f"""Please read the following files to understand requirements and implementation plan:
- Requirements Spec: {self.spec_file}
- Implementation Plan: {self.plan_file}"""

    def _get_status_analysis_prompt(self) -> str:
        """Get prompt for analyzing status code.

        Returns:
            Analysis prompt string
        """
        iteration_dir = self.review_dir / f"iteration_{self.iteration:03d}"
        review_file = iteration_dir / "output.md"
        return f"""Please read {review_file} and analyze the code review results.

Based on the following conditions, determine which status code to return:

- CAFE_CONFIRMED: Code review passed, no issues to fix
- CAFE_NEEDS_CHANGES: Issues need to be fixed

Please only return one status code (e.g., CAFE_CONFIRMED) without any other content."""

    def _detect_written_output_files(self) -> List[Path]:
        """Check if review file was written before failure.

        Returns:
            List[Path]: Returns list containing iteration_XXX/output.md if it exists, otherwise empty list
        """
        iteration_dir = self.review_dir / f"iteration_{self.iteration:03d}"
        review_file = iteration_dir / "output.md"
        return [review_file] if review_file.exists() else []

