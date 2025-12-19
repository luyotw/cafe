"""Development phase."""

import json
import readline  # Enable line editing with arrow keys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import yaml

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.ui.display import Display
from cafe.utils.github import get_pr_comments, filter_unresolved_comments, format_comments_for_prompt


class DevelopPhase(Phase):
    """Phase 3: Development with developer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        spec_file: str,
        plan_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        issue_name: Optional[str] = None,
        dev_agent: str = "David",
        interactive: bool = True,
        user_input: str = "",
        approved_denial_indices: Optional[List[int]] = None,
        pr_number: Optional[int] = None,
    ) -> None:
        """Initialize develop phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            spec_file: Path to spec file
            plan_file: Path to plan file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            issue_name: Issue name for history tracking (default: derived from current branch)
            dev_agent: Developer agent name (default: David)
            interactive: Enable interactive mode (default: True)
            user_input: User input for non-interactive mode (default: "")
            approved_denial_indices: Indices of approved permission denials (for non-interactive mode)
            pr_number: PR number to fetch unresolved comments from (optional)
        """
        super().__init__(interactive=interactive, git_ops=git_ops)

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.spec_file = spec_file
        self.plan_file = plan_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent
        self.user_input = user_input
        self.approved_denial_indices = approved_denial_indices if approved_denial_indices is not None else []
        self.pr_number = pr_number
        self._pr_comments_cache = None  # Cache for PR comments to avoid duplicate loading
        self.phase_name = "develop"  # For base class progress tracking

        # Iteration tracking
        self.iteration = 0

        # Determine issue name for history tracking (issue_dir is set by base class)
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from current branch name (via issue_dir)
            self.issue_name = self.issue_dir.name

        # History directory for develop phase
        # Path: .cafe/issues/{issue_name}/develop/history
        self.history_dir = self.issue_dir / "develop" / "history"

        # Track user responses for permission requests
        self.user_responses: List[str] = []

        # Initialize display
        self.display = Display()

        # Restore state from last iteration file (if resuming)
        self.iteration = self._load_iteration_counter()

    def _check_plan_exists(self) -> bool:
        """Check if plan file exists (versioned or legacy plan.md).

        Returns:
            True if plan file exists, False otherwise
        """
        plan_path = Path(self.plan_file)
        if plan_path.exists():
            return True

        # Also check for versioned plan files as fallback
        plan_dir = self.issue_dir / "plan"
        latest_plan = self._get_latest_versioned_file("plan", plan_dir)
        if latest_plan and latest_plan.exists():
            # Update self.plan_file to use the latest versioned file
            self.plan_file = str(latest_plan)
            return True

        return False

    def _save_develop_clarification(self, agent_response: str) -> None:
        """Save developer clarification questions to develop_{it_num}.md file.

        Args:
            agent_response: Agent response content (including status code)
        """
        # Remove status code line from response
        lines = agent_response.strip().split('\n')
        # Skip first line (status code) and any empty lines after it
        content_lines = []
        skip_status = True
        for line in lines:
            if skip_status and line.strip().startswith('CAFE_'):
                continue
            skip_status = False
            content_lines.append(line)

        # Remove leading empty lines
        while content_lines and not content_lines[0].strip():
            content_lines.pop(0)

        content = '\n'.join(content_lines)

        # Get file path
        develop_dir = self.issue_dir / "develop"
        develop_file = self._get_versioned_file_path("develop", self.iteration, develop_dir)

        # Ensure directory exists
        develop_file.parent.mkdir(parents=True, exist_ok=True)

        # Write content
        develop_file.write_text(content, encoding='utf-8')

    def _get_review_file_path(self) -> Path:
        """Get full path to review file.

        Prioritizes returning the latest review_XXX.md file. Falls back to review.md if no numbered file exists (backward compatible).

        Returns:
            Path object of review file
        """
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent  # .cafe/issues/{issue_name}
        review_dir = issue_dir / "review"

        # Find all review_XXX.md files
        if review_dir.exists():
            numbered_reviews = sorted(review_dir.glob("review_*.md"))
            if numbered_reviews:
                # Return the latest numbered review file
                return numbered_reviews[-1]

        # Fallback to review.md for backward compatibility
        return review_dir / "review.md"

    def _check_review_feedback_exists(self) -> bool:
        """Check if review feedback exists that needs to be addressed.

        Returns True only if review file exists and status is not CONFIRMED.
        If review is already CONFIRMED, it means it has passed and no corrections are needed.

        Returns:
            True if review.md exists and status is not CONFIRMED, False otherwise
        """
        review_file = self._get_review_file_path()
        if not review_file.exists():
            return False

        # Check review status
        review_status = self._load_review_status()
        if review_status:
            status_code = review_status.get("status_code")
            # If review is CONFIRMED, no need to address it
            if status_code == "CAFE_CONFIRMED":
                return False

        return True

    def _load_review_status(self) -> Optional[Dict[str, Any]]:
        """Load review phase status from status.json.

        Returns:
            Review status dict if exists, None otherwise
        """
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent
        review_status_file = issue_dir / "review" / "status.json"

        if not review_status_file.exists():
            return None

        try:
            with open(review_status_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            return None


    def _save_progress_with_review_timestamp(
        self,
        status_code: PhaseStatusCode,
        handled_review_timestamp: Optional[str] = None,
    ) -> None:
        """Save phase progress to status.json with review timestamp tracking.

        Args:
            status_code: Phase status code
            handled_review_timestamp: Timestamp of review feedback that was handled (if any)
        """
        # Call base class method first
        self._save_progress(status_code)

        # Add handled_review_timestamp if provided
        if handled_review_timestamp:
            status_file = self._get_status_file()
            with open(status_file, 'r', encoding='utf-8') as f:
                progress_dict = json.load(f)
            progress_dict["handled_review_timestamp"] = handled_review_timestamp
            with open(status_file, 'w', encoding='utf-8') as f:
                json.dump(progress_dict, f, ensure_ascii=False, indent=2)


    def _save_issue_config(self, base_branch: str, feature_branch: str) -> None:
        """Save issue configuration including base branch.

        Args:
            base_branch: Base branch name (e.g., 'main')
            feature_branch: Feature branch name (e.g., 'my-feature')
        """
        config_file = self.history_dir.parent.parent / "issue.yaml"

        # Read existing config to preserve worktree_path, issue_id, and rigor
        existing_config = self._read_issue_config(config_file) or {}

        # Update with base_branch and feature_branch
        config_data = {**existing_config}
        config_data["base_branch"] = base_branch
        config_data["feature_branch"] = feature_branch

        self._write_issue_config(config_file, config_data)

    def _check_if_already_completed_with_review(self) -> Optional[PhaseResult]:
        """Check if phase is already completed, considering special logic for review feedback and PR comments.

        Returns:
            PhaseResult if completed and no review feedback/PR comments need to be handled, None if should continue execution
        """
        existing_progress = self._load_progress()
        if not existing_progress or existing_progress.status != PhaseStatus.COMPLETED:
            return None

        # Check if PR phase has requested changes after this develop (both GitHub and local mode)
        # If PR is newer than last develop with NEEDS_CHANGES, we need to execute develop
        # If develop is newer than PR, changes have been addressed
        pr_status_file = self.issue_dir / "pr" / "status.json"
        if pr_status_file.exists():
            try:
                from datetime import datetime
                from cafe.core.status_codes import PhaseStatusCode

                with open(pr_status_file, 'r', encoding='utf-8') as f:
                    pr_status = json.load(f)

                pr_status_code = pr_status.get("status_code")
                pr_timestamp = datetime.fromisoformat(pr_status["timestamp"])
                develop_timestamp = existing_progress.timestamp

                # If PR has NEEDS_CHANGES and is newer than develop, need to execute
                if pr_status_code == PhaseStatusCode.NEEDS_CHANGES.value and pr_timestamp > develop_timestamp:
                    # Find the latest pr_XXX.md file to show user
                    pr_dir = self.issue_dir / "pr"
                    pr_files = sorted(pr_dir.glob("pr_*.md"))
                    if pr_files:
                        latest_pr_file = pr_files[-1]
                        print(f"ℹ️  PR feedback detected - changes requested: {latest_pr_file.name}")
                    else:
                        print(f"ℹ️  PR feedback detected - changes requested")
                    return None  # Continue execution

                # If develop is newer than PR, changes have been addressed
                if develop_timestamp > pr_timestamp:
                    # Check if there's a newer PR status we haven't seen yet
                    # (This shouldn't happen in normal flow, but just in case)
                    pass

            except Exception:
                # If error, continue with normal flow
                pass

        # If pr_number is provided, always continue execution (user wants to address PR comments)
        if self.pr_number:
            print(f"ℹ️  PR #{self.pr_number} comments will be addressed")
            return None

        # Check if there's review feedback that requires handling
        review_status = self._load_review_status()
        if review_status and review_status.get("status_code") == "CAFE_NEEDS_CHANGES":
            # Check if this review has already been handled
            review_timestamp = review_status.get("timestamp", "")

            # Load develop status.json to check handled_review_timestamp
            status_file = self.history_dir.parent / "status.json"
            if status_file.exists():
                with open(status_file, 'r', encoding='utf-8') as f:
                    develop_status = json.load(f)
                    handled_review_timestamp = develop_status.get("handled_review_timestamp")

                    if handled_review_timestamp == review_timestamp:
                        # This review has already been handled
                        return PhaseResult(
                            status=PhaseStatus.COMPLETED,
                            message=f"Development already completed in {existing_progress.iteration} iteration(s)",
                            data={
                                "branch": self._get_branch_name(),
                                "iterations": existing_progress.iteration,
                                "status_code": existing_progress.status_code,
                            },
                        )

            # Review exists and hasn't been handled yet, continue execution
            review_file = self._get_review_file_path()
            print(f"ℹ️  Review feedback detected: {review_file}")
            return None  # Don't return early - let execution continue to handle review feedback

        # No review feedback or review passed, phase is truly completed
        return PhaseResult(
            status=PhaseStatus.COMPLETED,
            message=f"Development already completed in {existing_progress.iteration} iteration(s)",
            data={
                "branch": self._get_branch_name(),
                "iterations": existing_progress.iteration,
                "status_code": existing_progress.status_code,
            },
        )

    def _get_completion_data(self) -> dict:
        """Get additional data when phase completes (provided to base class _handle_standard_status_codes).

        Returns:
            dict containing phase-specific data, will be merged into PhaseResult.data
        """
        data = {
            "branch": self._get_branch_name(),
        }
        return data

    def _prepare_user_input_for_iteration(self) -> "PhaseResult | str":
        """Prepare user input for current iteration.

        Special logic for develop phase:
        - Iteration 1 (no previous data): Use self.user_input (if --user-input parameter provided)
        - Iteration 2+ (with previous data): Check if there's pending NEED_PERMISSION to handle

        Returns:
            PhaseResult: If phase needs to be ended/paused
            str: User input content (usually empty, unless handling NEED_PERMISSION or --user-input provided)
        """
        # Check for previous iteration data
        prev_data = self._load_previous_iteration_data()

        # No previous data: first execution
        if not prev_data:
            # Use self.user_input if provided (from --user-input parameter)
            user_input = self.user_input if self.user_input else ""
            self.user_input = ""  # Clear after first use
            return user_input

        prev_status = prev_data.get("status_code", "")

        # Handle pending NEED_PERMISSION from previous run
        if prev_status == "CAFE_NEED_PERMISSION":
            # Check if has permission_denials
            if not prev_data.get("permission_denials"):
                # Old format without permission_denials, return empty
                return ""

            # In non-interactive mode, check if user provided approved_denial_indices
            if not self.interactive:
                if not hasattr(self, "approved_denial_indices") or not self.approved_denial_indices:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message="Permission required but running in non-interactive mode without --approve-denied-tools",
                        data={
                            "iterations": prev_data['iteration'],
                            "last_response": prev_data.get('response', ''),
                            "permission_denials": prev_data.get("permission_denials", []),
                        },
                    )

            # Note: The actual permission handling (calling _handle_previous_permission_denials)
            # will be done in execute() method when constructing allowed_tools

        # Handle NEED_CLARIFICATION - use base class method
        if prev_status == "CAFE_NEED_CLARIFICATION":
            return self._handle_need_clarification_input(prev_data, agent_display_name="Developer")

        # No special handling needed - clear user_input to avoid misuse
        self.user_input = ""
        return ""

    def _ask_user_for_clarification(self) -> str:
        """Ask user for response to NEED_CLARIFICATION and display develop file content.

        Override base class method to display develop clarification file content.

        Returns:
            str: User's answer
        """
        from cafe.ui.inquirer_prompts import prompt_multiline

        # Find the latest develop clarification file
        develop_dir = self.issue_dir / "develop"
        develop_files = sorted(develop_dir.glob("develop_*.md"))

        if develop_files:
            latest_develop_file = develop_files[-1]
            print(f"\n{'='*60}")
            print(f"Dev ({self.dev_agent}):")
            print(f"{'='*60}")
            develop_content = latest_develop_file.read_text(encoding='utf-8')
            print(develop_content)
            print(f"{'='*60}\n")
            print("💡 Developer needs clarification.")
            print()

        return prompt_multiline("Please answer the question")

    def _get_last_develop_timestamp(self):
        """Get timestamp from last develop/status.json.

        Returns:
            datetime object (timezone-aware) or None if not found
        """
        try:
            status_file = self.history_dir.parent / "status.json"
            if not status_file.exists():
                return None

            with open(status_file, 'r', encoding='utf-8') as f:
                status_data = json.load(f)
                timestamp_str = status_data.get("timestamp")
                if timestamp_str:
                    from datetime import datetime, timezone
                    # Ensure we always get a timezone-aware datetime
                    if timestamp_str.endswith('Z'):
                        timestamp_str = timestamp_str.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(timestamp_str)
                    # If datetime is naive, assume UTC
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
        except Exception:
            pass
        return None

    def _is_clarification_answered(self, develop_file: Path) -> bool:
        """Check if develop clarification has already been answered.

        Logic: If there is user_input in any iteration after the clarification,
        it means the user has already answered the clarification question.

        Args:
            develop_file: develop clarification file path (e.g. develop_001.md)

        Returns:
            True if already answered in any subsequent iteration, False otherwise
        """
        # Extract iteration number from develop file name
        # develop_001.md -> 1
        import re
        match = re.search(r'develop_(\d+)\.md', develop_file.name)
        if not match:
            return False

        clarification_iteration = int(match.group(1))

        # Check all iterations (not just current iteration) for user_input
        history_dir = self.issue_dir / "develop" / "history"
        if not history_dir.exists():
            return False

        # Look for ANY iteration after the clarification that has user_input
        import json
        for iteration_file in sorted(history_dir.glob("iteration_*.json")):
            match = re.search(r'iteration_(\d+)\.json', iteration_file.name)
            if not match:
                continue

            iteration_num = int(match.group(1))
            # Only check iterations AFTER the clarification was created
            if iteration_num <= clarification_iteration:
                continue

            try:
                with open(iteration_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # If this iteration has user_input, the clarification was answered
                    if data.get('user_input', '').strip():
                        return True
            except (json.JSONDecodeError, IOError):
                continue

        return False

    def _load_local_pr_feedback(self) -> Optional[str]:
        """Load local PR feedback from pr_XXX.md files.

        Returns:
            Feedback content if found, None otherwise
        """
        pr_dir = self.issue_dir / "pr"
        if not pr_dir.exists():
            return None

        # Find all pr_XXX.md files
        pr_files = sorted(pr_dir.glob("pr_*.md"))
        if not pr_files:
            return None

        # Return the latest one
        latest_pr_file = pr_files[-1]
        return latest_pr_file.read_text()

    def _load_pr_comments(self) -> tuple[str, int]:
        """Load PR comments if pr_number is provided.

        Only loads comments that are newer than the last develop timestamp.

        Returns:
            Tuple of (formatted comments string, new unresolved count)
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

    def _generate_prompt(self, user_input: str = "") -> str:
        """Generate prompt for current iteration.

        Args:
            user_input: User input for this iteration (additional instructions from user)

        Returns:
            Prompt string
        """
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_PERMISSION,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "Development work completed",
                PhaseStatusCode.NEED_PERMISSION: "Need to request tool usage permissions",
                PhaseStatusCode.NEED_CLARIFICATION: "Need user to clarify next steps",
            },
        )

        # Load PR feedback (either from GitHub comments or local pr_XXX.md files)
        config_file = self.issue_dir / "issue.yaml"
        pr_auto_create = self._get_issue_config_value(config_file, "pr.auto_create")

        if pr_auto_create is False:
            # Use local PR feedback
            local_feedback = self._load_local_pr_feedback()
            pr_comments_section = f"\n\n## PR Feedback (Local)\n\n{local_feedback}\n" if local_feedback else ""
            has_pr_comments = bool(local_feedback)
            unresolved_count = 0  # Not applicable for local feedback
        else:
            # Use GitHub PR comments
            pr_comments, unresolved_count = self._load_pr_comments()
            pr_comments_section = f"\n\n{pr_comments}\n" if pr_comments else ""
            has_pr_comments = bool(pr_comments)

        # Check for existing develop clarification file
        develop_dir = self.issue_dir / "develop"
        develop_file = self._get_latest_versioned_file("develop", develop_dir)
        develop_file_section = ""
        develop_instruction = ""
        if develop_file and develop_file.exists():
            # Check if this clarification has already been answered
            # by looking for a subsequent iteration with user_input
            if not self._is_clarification_answered(develop_file):
                develop_file_section = f"\n- Developer questions record: {develop_file}"
                develop_instruction = f"1. **First read** questions in {develop_file} (if any)\n"

        # Check if review feedback exists (every iteration, not just first)
        has_review_feedback = self._check_review_feedback_exists()
        review_file_path = self._get_review_file_path()

        # Get agent file path
        from cafe.agents.manager import AgentManager
        agent_file = AgentManager.get_agent_file_path(self.dev_agent, "developer")

        config_file = self.issue_dir / "issue.yaml"
        base_branch = self._get_issue_config_value(config_file, "base_branch") or "main"
        important_note = f"""
**Important**
- **Strictly maintain consistency with {base_branch}'s commit message format**, can commit multiple times, consistency includes:
  - Language (English/Chinese)
  - Message is one line (subject line only) or multiple lines (subject + body)
"""

        clarification_note = """
Clarification can be requested only in these two cases, **any other situation strictly prohibits clarification requests**:
- Requested actions conflict with the agent's behavioral guidelines
- Encountering technical problems beyond current capability

Steps for requesting clarification:
1. Return `CAFE_NEED_CLARIFICATION` status code
2. Clearly describe the clarification questions in the response (can use bullet points)
3. The system will save your questions to develop_XXX.md file (in your native language)
4. After user replies, the system will provide the file for you to read on next execution

⚠️ **Important:** Write the markdown content in your native language (the language you were configured with).
"""

        if has_review_feedback:
            # With review feedback - correction mode
            user_input_section = f"\n\n**Additional user notes:**\n{user_input}\n" if user_input else ""

            # Build review sources instruction
            review_sources = []
            if review_file_path:
                review_sources.append(str(review_file_path))
            if has_pr_comments:
                review_sources.append(f"PR comments (see {unresolved_count} unresolved comments above)")

            review_instruction = ""
            if len(review_sources) == 1:
                review_instruction = f"2. **First read** {review_sources[0]}, understand all issues needing correction"
            else:
                review_instruction = f"2. **First read** {' and '.join(review_sources)}, understand all issues needing correction"

            return f"""Please make corrections based on Code Review feedback.

**Your role:**
Please use Read tool to read {agent_file} to understand your role definition and work guidelines, then strictly follow the requirements in the role definition to perform code corrections.

{important_note}

**File paths:**
- Review Feedback: {review_file_path}
- Requirements Specification: {self.spec_file}
- Implementation Plan: {self.plan_file}{develop_file_section}
{pr_comments_section}{user_input_section}
**Execution steps:**
1. Use Read tool to read {agent_file} to understand role definition
{develop_instruction}{review_instruction}
3. Address issues one by one based on review feedback
4. **Strictly follow existing commit message style**, can commit multiple times
5. **Do not modify commits from other branches**
6. If needed, refer to {self.spec_file} and {self.plan_file}
7. Return status code after completing all corrections

{status_code_prompt}

{clarification_note}

**Return status code only, do not provide any summary**
"""

        # No review feedback - normal development mode
        user_input_section = f"\n\n**Additional user notes:**\n{user_input}\n" if user_input else ""
        return f"""Please execute development work according to the implementation plan.

**Your role:**
Please use Read tool to read {agent_file} to understand your role definition and work guidelines, then strictly follow the requirements in the role definition to perform development work.

{important_note}

**File paths:**
- Requirements Specification: {self.spec_file}
- Implementation Plan: {self.plan_file}{develop_file_section}
{pr_comments_section}{user_input_section}
**Execution steps:**
1. Use Read tool to read {agent_file} to understand role definition
{develop_instruction}2. Carefully read {self.spec_file} and {self.plan_file}
3. Execute development tasks in strict order according to the plan
4. **Strictly follow existing commit message style**, can commit multiple times
5. After completing each task, mark it checked in {self.plan_file} (change - [ ] to - [x])
6. **Do not modify commits from other branches**
7. Return status code after completing all tasks

{status_code_prompt}

{clarification_note}

**Return status code only, do not provide any summary**
"""

    def execute(self) -> PhaseResult:
        """Execute development phase with iterative loop.

        Returns:
            Phase result
        """
        try:
            # Check plan.md exists
            if not self._check_plan_exists():
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"Plan file not found: {self.plan_file}. Please run 'cafe plan' first.",
                )

            # Validate inputs
            if self.workflow_mode == WorkflowMode.GITHUB and not self.issue_id:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="GitHub mode requires issue_id",
                )

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check requirements file exists
                req_path = Path(self.spec_file)
                if not req_path.exists():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Spec file not found: {self.spec_file}",
                    )

            # Auto-detect PR number if not provided (must happen before already_completed check)
            if not self.pr_number:
                try:
                    from cafe.utils.github import GitHubOps
                    github_ops = GitHubOps()
                    branch_name = self._get_branch_name()
                    pr_data = github_ops.get_pr_for_branch(branch_name)
                    if pr_data:
                        self.pr_number = pr_data["number"]
                        print(f"ℹ️  Auto-detected PR #{self.pr_number} for branch '{branch_name}'")
                except Exception as e:
                    # Silently ignore errors - PR detection is optional
                    print(f"ℹ️  No PR detected for current branch (this is normal if PR hasn't been created yet)")

            # Check if already completed (with review feedback awareness)
            already_completed = self._check_if_already_completed_with_review()
            if already_completed:
                return already_completed

            # Load PR comments if pr_number is available (will be included in prompt)
            # Note: We don't skip execution even if there are no new comments,
            # as the developer may still have work to do based on the plan
            if self.pr_number:
                print(f"\n🔍 Checking PR #{self.pr_number} for unresolved comments...")
                pr_comments, unresolved_count = self._load_pr_comments()
                if unresolved_count > 0:
                    print(f"✅ Found {unresolved_count} new unresolved PR comment(s) to address")
                else:
                    print(f"ℹ️  No new unresolved PR comments since last develop")
                print()

            # Create or checkout branch
            branch_name = self._get_branch_name()
            if self.git_ops.branch_exists(branch_name):
                self.git_ops.checkout_branch(branch_name)
            else:
                # Get current branch before creating new one (this is the base branch)
                base_branch = self.git_ops.get_current_branch()
                self.git_ops.create_branch(branch_name)
                # Save issue config with base branch info
                self._save_issue_config(base_branch, branch_name)

            # Load current iteration counter (will be 0 if starting fresh, or last iteration if resuming)
            self.iteration = self._load_iteration_counter()

            # Increment for next execution
            self.iteration += 1

            # Prepare user_input for this iteration
            result_or_input = self._prepare_user_input_for_iteration()
            if isinstance(result_or_input, PhaseResult):
                # Method returned a PhaseResult (completion/failure/pause)
                return result_or_input
            # Otherwise, it's the user input string
            current_user_input = result_or_input

            # Handle previous permission denials and construct allowed_tools
            base_allowed_tools = ["write", "read", "edit", "bash", "grep", "glob", "ls", "web_fetch", "web_search"]
            approved_tools_from_denials, permission_user_input = self._handle_previous_permission_denials()

            # Check if user rejected all tools (no approvals)
            prev_data = self._load_previous_iteration_data()
            if prev_data and prev_data.get("permission_denials") and not approved_tools_from_denials:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="No tools approved - all permission requests were rejected.",
                    data={
                        "iterations": self.iteration,
                        "last_response": prev_data.get('response', ''),
                        "permission_denials": prev_data.get("permission_denials", []),
                    },
                )

            # Merge base tools + previous iteration's tools + newly approved tools
            allowed_tools = self._merge_allowed_tools(base_allowed_tools, approved_tools_from_denials)

            # If user provided additional input about permissions, append to current_user_input
            if permission_user_input:
                if current_user_input:
                    current_user_input = f"{current_user_input}\n\n{permission_user_input}"
                else:
                    current_user_input = permission_user_input

            # Execute full agent interaction cycle (generate prompt, execute, handle status)
            result, response = self._execute_and_handle_agent_response(
                agent_name=self.dev_agent,
                user_input=current_user_input,
                valid_status_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEED_PERMISSION,
                    PhaseStatusCode.NEED_CLARIFICATION,
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.CONFIRMED],
                continue_codes=[],  # No automatic continue codes
            )

            # Handle NEED_PERMISSION and NEED_CLARIFICATION specially - return and wait for next invocation
            if response:
                response_status = StatusCodeParser.extract(
                    response,
                    valid_codes=[
                        PhaseStatusCode.CONFIRMED,
                        PhaseStatusCode.NEED_PERMISSION,
                        PhaseStatusCode.NEED_CLARIFICATION,
                    ],
                )
                if response_status == PhaseStatusCode.NEED_PERMISSION:
                    # Display permission request and return IN_PROGRESS
                    if self.interactive:
                        print(f"\n{'='*60}")
                        print(f"Dev ({self.dev_agent}) - Iteration {self.iteration}:")
                        print(f"{'='*60}")
                        print(response)
                        print(f"{'='*60}\n")
                        print("💡 Developer requested permissions. Run 'cafe develop' again to respond.")

                        return PhaseResult(
                            status=PhaseStatus.IN_PROGRESS,
                            message=f"Permission requested in iteration {self.iteration}. Run command again to respond.",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                                "status_code": response_status.value,
                            },
                        )
                    else:
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message="Permission required but running in non-interactive mode",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                            },
                        )
                elif response_status == PhaseStatusCode.NEED_CLARIFICATION:
                    # Save clarification to file
                    self._save_develop_clarification(response)

                    # Display clarification request and return IN_PROGRESS
                    if self.interactive:
                        print(f"\n{'='*60}")
                        print(f"Dev ({self.dev_agent}) - Iteration {self.iteration}:")
                        print(f"{'='*60}")
                        print(response)
                        print(f"{'='*60}\n")
                        print("💡 Developer needs clarification. Run 'cafe develop' again to respond.")

                        return PhaseResult(
                            status=PhaseStatus.IN_PROGRESS,
                            message=f"Clarification requested in iteration {self.iteration}. Run command again to respond.",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                                "status_code": response_status.value,
                            },
                        )
                    else:
                        return PhaseResult(
                            status=PhaseStatus.COMPLETED,
                            message="Clarification needed - saved to develop file. Re-run with --user-input to provide response.",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                                "status_code": response_status.value,
                            },
                        )

            # Phase-specific post-processing: Handle review feedback timestamp
            if result and result.status == PhaseStatus.COMPLETED:
                review_status = self._load_review_status()
                if review_status and review_status.get("status_code") == "CAFE_NEEDS_CHANGES":
                    handled_review_timestamp = review_status.get("timestamp")
                    # Update status.json with handled_review_timestamp
                    self._save_progress_with_review_timestamp(
                        PhaseStatusCode.CONFIRMED,
                        handled_review_timestamp
                    )

            if result:
                return result

            # No result means agent didn't return a status code - return IN_PROGRESS
            return PhaseResult(
                status=PhaseStatus.IN_PROGRESS,
                message=f"Iteration {self.iteration}: No status code found, need more work",
                data={"iterations": self.iteration},
            )

        except KeyboardInterrupt:
            print("\n\n⏸️  Paused by user (Ctrl+C).")
            print(f"💾 Progress saved. Current iteration: {self.iteration}")
            print(f"📝 To resume, run: cafe develop {self.issue_name}")
            return PhaseResult(
                status=PhaseStatus.IN_PROGRESS,
                message="Paused by user - can resume later",
                data={"iterations": self.iteration},
            )
        except Exception as e:
            return self._handle_exception_in_execute(e, "Development phase failed")

    def _get_branch_name(self) -> str:
        """Get branch name based on workflow mode.

        Returns:
            Branch name
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return f"issue-{self.issue_id}"
        else:
            # Use issue name as branch name
            return self.issue_name

    def _get_status_analysis_prompt(self) -> str:
        """Get prompt for analyzing status code.

        Returns:
            Analysis prompt string
        """
        return f"""Please read {self.plan_file} and check development progress.

Based on the following conditions, determine which status code to return:

- CAFE_CONFIRMED: All tasks completed (all items checked in plan.md)
- CAFE_NEED_PERMISSION: Need to request additional tool usage permissions

Please return only one status code (example: CAFE_CONFIRMED), with no other content."""

    def _detect_written_output_files(self) -> List[Path]:
        """Check if develop file was written before failure.

        DevelopPhase uses develop_{iteration}.md to record CAFE_NEED_CLARIFICATION questions.

        Returns:
            List[Path]: Return list containing develop_{iteration}.md if it exists, otherwise empty list
        """
        develop_dir = self.issue_dir / "develop"
        develop_file = develop_dir / f"develop_{self.iteration:03d}.md"
        return [develop_file] if develop_file.exists() else []
