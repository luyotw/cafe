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
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus
from cafe.ui.display import Display
from cafe.utils.github import get_pr_comments, filter_unresolved_comments, format_comments_for_prompt
from cafe.utils.prompt_utils import format_checklist_instruction


class DevelopPhase(Phase):
    """Phase 3: Development with developer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        spec_file: str,
        plan_file: str,
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
            issue_id: GitHub issue ID (optional)
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

        # Phase directory for develop phase (required by base class)
        self.phase_dir = self.issue_dir / "develop"

        # History directory for develop phase
        # Path: .cafe/issues/{issue_name}/develop/history
        self.history_dir = self.phase_dir / "history"

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

        Returns the latest iteration_XXX/output.md file.

        Returns:
            Path object of review file
        """
        spec_path = Path(self.spec_file)
        # spec_file is like .cafe/issues/{issue_name}/spec/iteration_XXX/output.md
        # Go up: output.md -> iteration_XXX -> spec -> issue_name
        issue_dir = spec_path.parent.parent.parent
        review_dir = issue_dir / "review"

        # Find all iteration_XXX/output.md files
        if review_dir.exists():
            iteration_files = sorted(review_dir.glob("iteration_*/output.md"))
            if iteration_files:
                # Return the latest iteration file
                return iteration_files[-1]

        # If no iteration files found, construct the expected path
        # This will be used even if the file doesn't exist yet
        return review_dir / "iteration_001" / "output.md"

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
        # spec_file is like .cafe/issues/{issue_name}/spec/iteration_XXX/output.md
        # Go up: output.md -> iteration_XXX -> spec -> issue_name
        issue_dir = spec_path.parent.parent.parent
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
        # FIRST: Always check and set review feedback flag (do this BEFORE checking existing_progress)
        # This ensures the flag is set for _generate_prompt even on first execution
        review_status = self._load_review_status()
        if review_status and review_status.get("status_code") == "CAFE_NEEDS_CHANGES":
            self._has_review_feedback = True
        else:
            self._has_review_feedback = False

        existing_progress = self._load_progress()
        if not existing_progress or existing_progress.status != PhaseStatus.COMPLETED:
            # Not completed yet, but flag is set - continue execution
            if self._has_review_feedback:
                review_file = self._get_review_file_path()
                print(f"ℹ️  Review feedback detected: {review_file}")
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

        # Check if review feedback has already been handled
        if self._has_review_feedback:
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
            return None  # Don't return early - let execution continue to handle review feedback

        # Check PR comments (only if no review feedback)
        if self.pr_number:
            print(f"ℹ️  PR #{self.pr_number} comments will be addressed")
            # Check if there are unpushed commits that address PR comments
            if self.git_ops.has_unpushed_commits():
                latest_unpushed_timestamp_str = self.git_ops.get_latest_unpushed_commit_timestamp()

                if latest_unpushed_timestamp_str:
                    from datetime import datetime, timezone

                    latest_unpushed_timestamp = datetime.fromisoformat(latest_unpushed_timestamp_str)
                    if latest_unpushed_timestamp.tzinfo is None:
                        latest_unpushed_timestamp = latest_unpushed_timestamp.replace(tzinfo=timezone.utc)

                    latest_pr_comment_timestamp = self._get_latest_pr_comment_timestamp()

                    if latest_pr_comment_timestamp:
                        if latest_unpushed_timestamp > latest_pr_comment_timestamp:
                            print(f"✅ Development already completed - unpushed commits address PR comments")
                            print(f"   Latest unpushed commit: {latest_unpushed_timestamp.isoformat()}")
                            print(f"   Latest PR comment: {latest_pr_comment_timestamp.isoformat()}")
                            print(f"   Next step: Run 'cafe pr' to push and create/update PR")

                            return PhaseResult(
                                status=PhaseStatus.COMPLETED,
                                message=f"Development already completed - {len(self.git_ops.get_unpushed_commits())} unpushed commit(s) address PR comments",
                                data={
                                    "branch": self._get_branch_name(),
                                    "iterations": existing_progress.iteration,
                                    "status_code": existing_progress.status_code,
                                    "unpushed_commits": len(self.git_ops.get_unpushed_commits()),
                                },
                            )

            return None

        # No review feedback or PR comments, phase is truly completed
        return PhaseResult(
            status=PhaseStatus.COMPLETED,
            message=f"Development already completed in {existing_progress.iteration} iteration(s)",
            data={
                "branch": self._get_branch_name(),
                "iterations": existing_progress.iteration,
                "status_code": existing_progress.status_code,
            },
        )

    def _handle_no_changes_needed_input(self, prev_data: dict) -> "PhaseResult | str":
        """Handle user input for NO_CHANGES_NEEDED status (developer disputes reviewer).

        Similar to READY_FOR_REVIEW handling:
        - user_input == "confirm": User agrees, save SKIP_REVIEW status and return completion
        - user_input has content: User disagrees, return their feedback as user_input
        - No user_input (interactive): Ask user for decision
        - No user_input (non-interactive): Return failure

        Args:
            prev_data: Previous iteration data

        Returns:
            PhaseResult: If user agrees (skip review) or non-interactive without input
            str: User's feedback if they disagree
        """
        if self.interactive:
            # Display delta from previous iteration
            if self.iteration > 1:
                self._display_iteration_delta()

            # Ask user for decision
            choice = self._ask_user_for_no_changes_decision()
        else:
            choice = self.user_input
            self.user_input = ""  # Clear after use

            if not choice:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="Developer returned NO_CHANGES_NEEDED in non-interactive mode without user input. Use --user-input confirm to agree, or provide feedback.",
                    data={
                        "iterations": prev_data.get("iteration", self.iteration - 1),
                        "last_response": prev_data.get("response", ""),
                        "status_code": "CAFE_NO_CHANGES_NEEDED",
                    },
                )

        # Handle user choice
        if choice.strip().lower() == "confirm":
            # User agrees with developer - save SKIP_REVIEW status
            print("✅ User agreed with developer - skipping review phase")

            # Save user confirmation as a new iteration
            self._save_user_input(
                user_input="confirm",
                phase_specific_data={},
            )
            self._update_iteration_history(
                phase_specific_data={
                    "response": "User agreed with developer - skip review",
                    "user_action": "confirm_skip_review",
                },
                prompt="",
                agent_cli=None,
                agent_session_id=None,
                allowed_tools=None,
                status_code=PhaseStatusCode.SKIP_REVIEW,
            )
            self._save_progress(PhaseStatusCode.SKIP_REVIEW)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="User agreed with developer - skipping review phase",
                data={
                    "branch": self._get_branch_name(),
                    "iterations": self.iteration,
                    "status_code": PhaseStatusCode.SKIP_REVIEW.value,
                    "skip_review": True,
                },
            )
        else:
            # User disagrees - return their feedback as user_input for this iteration
            print(f"ℹ️  User provided feedback, continuing development...")
            return choice

    def _ask_user_for_no_changes_decision(self) -> str:
        """Ask user whether they agree with developer's NO_CHANGES_NEEDED decision.

        Returns:
            str: "confirm" if user agrees, or user's feedback if they disagree
        """
        from cafe.ui.inquirer_prompts import prompt_list, prompt_multiline

        print(f"\n{'='*60}")
        print(f"Developer ({self.dev_agent}) believes no changes are needed.")
        print(f"{'='*60}\n")

        # Display the developer's output file
        develop_dir = self.issue_dir / "develop"
        develop_file = develop_dir / f"iteration_{self.iteration - 1:03d}" / "output.md"
        if develop_file.exists():
            print(f"Developer's response: {develop_file}\n")
            with open(develop_file, "r", encoding="utf-8") as f:
                content = f.read()
                print(content)
                print(f"\n{'='*60}\n")

        choices = [
            {"name": "Agree - Skip review and proceed to PR", "value": "c"},
            {"name": "Disagree - Provide feedback for developer", "value": "m"},
        ]

        choice = prompt_list(
            "Do you agree with the developer?",
            choices,
            default=None,
        )

        if choice == "c":
            return "confirm"
        else:
            feedback = prompt_multiline("Please provide feedback for the developer")

            if not feedback.strip():
                print("\n⚠️  No feedback entered, please try again.")
                return self._ask_user_for_no_changes_decision()

            print()
            print("✅ Received your feedback...")
            print()

            return feedback

    def _display_iteration_delta(self) -> None:
        """Display changes from previous iteration."""
        from cafe.ui.cli import _display_iteration_delta
        from rich.console import Console

        prev_iteration = self.iteration - 1
        develop_dir = self.issue_dir / "develop"
        develop_file = develop_dir / f"iteration_{prev_iteration:03d}" / "output.md"

        if develop_file.exists():
            _display_iteration_delta(
                prev_iteration,
                str(develop_file),
                Console(),
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
        - If current iteration was interrupted (has prompt but no response): Reuse user_input from that iteration

        Returns:
            PhaseResult: If phase needs to be ended/paused
            str: User input content (usually empty, unless handling NEED_PERMISSION or --user-input provided)
        """
        # First, check if current iteration was interrupted (e.g., rate limit error)
        # If so, we should reuse the user_input from that interrupted iteration
        current_iteration_file = Path(self.history_dir) / f"iteration_{self.iteration:03d}.json"
        if current_iteration_file.exists():
            with open(current_iteration_file, "r", encoding="utf-8") as f:
                current_data = json.load(f)

            # Check if this iteration was interrupted (has prompt but no response)
            if current_data.get("prompt") and not current_data.get("response"):
                # Reuse the user_input from interrupted iteration
                interrupted_user_input = current_data.get("user_input", "")
                return interrupted_user_input

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

        # Handle NO_CHANGES_NEEDED - developer disputes reviewer's feedback
        if prev_status == "CAFE_NO_CHANGES_NEEDED":
            return self._handle_no_changes_needed_input(prev_data)

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

        # Find the latest develop clarification file (iteration_XXX/output.md format)
        develop_dir = self.issue_dir / "develop"

        latest_develop_file = None
        if develop_dir.exists():
            iteration_files = sorted(develop_dir.glob("iteration_*/output.md"))
            if iteration_files:
                latest_develop_file = iteration_files[-1]

        if latest_develop_file and latest_develop_file.exists():
            print(f"\n{'='*60}")
            print(f"Dev ({self.dev_agent}):")
            print(f"{'='*60}")
            develop_content = latest_develop_file.read_text(encoding='utf-8')
            print(develop_content)
            print(f"{'='*60}\n")
            print("💡 Developer needs clarification.")
            print()
        else:
            # No clarification file found - this shouldn't happen
            print(f"\n{'='*60}")
            print("⚠️  No clarification file found")
            print(f"{'='*60}")
            print(f"Expected location: {develop_dir}/iteration_XXX/output.md")
            print(f"{'='*60}\n")

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
            develop_file: develop clarification file path (e.g. iteration_001/output.md)

        Returns:
            True if already answered in any subsequent iteration, False otherwise
        """
        # Extract iteration number from develop file path
        # iteration_001/output.md -> 1
        import re
        match = re.search(r'iteration_(\d+)', str(develop_file))
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

    def _get_latest_pr_comment_timestamp(self) -> Optional["datetime"]:
        """Get timestamp of the latest PR comment.

        Returns:
            Timezone-aware datetime object of latest comment, or None if no comments
        """
        if not self.pr_number:
            return None

        try:
            from datetime import datetime, timezone
            from cafe.utils.github import get_pr_comments

            comments = get_pr_comments(self.pr_number)
            if not comments:
                return None

            latest_timestamp = None
            for comment in comments:
                timestamp_str = comment.created_at
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str.replace('Z', '+00:00')
                comment_time = datetime.fromisoformat(timestamp_str)

                if comment_time.tzinfo is None:
                    comment_time = comment_time.replace(tzinfo=timezone.utc)

                if latest_timestamp is None or comment_time > latest_timestamp:
                    latest_timestamp = comment_time

            return latest_timestamp
        except Exception as e:
            print(f"⚠️  Failed to get latest PR comment timestamp: {e}")
            return None

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
                PhaseStatusCode.NO_CHANGES_NEEDED,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "Development work completed",
                PhaseStatusCode.NEED_PERMISSION: "Need to request tool usage permissions",
                PhaseStatusCode.NEED_CLARIFICATION: "Need user to clarify next steps",
                PhaseStatusCode.NO_CHANGES_NEEDED: "You believe reviewer's feedback is incorrect/unnecessary and have valid technical reasons to disagree",
            },
        )

        # Load PR feedback (either from GitHub comments or local pr_XXX.md files)
        config_file = self.issue_dir / "issue.yaml"
        pr_auto_create = self._get_issue_config_value(config_file, "pr.auto_create")

        # Skip PR comments if review feedback exists (review takes priority)
        if hasattr(self, '_has_review_feedback') and self._has_review_feedback:
            pr_comments_section = ""
            has_pr_comments = False
            unresolved_count = 0
        elif pr_auto_create is False:
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
        worktree_path = self._get_issue_config_value(config_file, "worktree_path")

        worktree_note = ""
        if worktree_path:
            worktree_note = f"\n- **When working in worktree mode: Strictly prohibit modifying any files outside the worktree directory ({worktree_path})**"

        important_note = f"""
**Important**
- **Strictly maintain consistency with {base_branch}'s commit message format**, can commit multiple times, consistency includes:
  - Language (English/Chinese/...)
  - Message is one line (subject line only) or multiple lines (subject + body){worktree_note}
"""

        # Get the develop file path for current iteration
        develop_dir = self.issue_dir / "develop"
        develop_file_path = develop_dir / f"iteration_{self.iteration:03d}" / "output.md"

        clarification_note = f"""
Clarification can be requested only in these two cases, **any other situations strictly prohibit clarification requests, just decide the solution by yourself**:
- Requested actions conflict with the agent's behavioral guidelines
- Encountering technical problems beyond current capability

**⚠️ Never request clarification due to time pressure or token concerns - just do the work. CAFE has a resume mechanism to handle long tasks.**

Steps for requesting clarification:
1. Confirm again that your question meets the above conditions
2. Write your question clearly to {develop_file_path}
3. Return CAFE_NEED_CLARIFICATION only, with no other content

⚠️ **Important:** Write the markdown content in your native language (the language you were configured with).
"""

        if has_review_feedback:
            # With review feedback - correction mode
            from cafe.utils.prompt_utils import extract_agent_guidelines_checklist
            agent_guidelines_checklist = extract_agent_guidelines_checklist(agent_file)

            user_input_section = f"\n\n**Additional user notes:**\n{user_input}\n" if user_input else ""

            # Build review sources instruction
            review_sources = []
            if review_file_path:
                review_sources.append(str(review_file_path))
            if has_pr_comments:
                review_sources.append(f"PR comments (see {unresolved_count} unresolved comments above)")

            review_source_text = ""
            if len(review_sources) == 1:
                review_source_text = review_sources[0]
            else:
                review_source_text = " and ".join(review_sources)

            # Get checklist file path
            iteration_dir = self._get_iteration_dir(self.iteration)
            checklist_file = iteration_dir / "checklist.md"
            from cafe.utils.git_utils import to_cwd_relative_path
            try:
                checklist_path = to_cwd_relative_path(checklist_file)
            except ValueError:
                checklist_path = str(checklist_file.resolve())

            checklist_instruction = format_checklist_instruction(checklist_path)
            base_prompt = f"""# Develop Phase (Correction Mode)

**Your Role:** Developer
Read {agent_file} to understand your complete role definition and responsibilities.

{checklist_instruction}

**Task:** Make corrections based on Code Review feedback.

**File paths:**
- Review Feedback: {review_file_path}
- Requirements Specification: {self.spec_file}
- Implementation Plan: {self.plan_file}{develop_file_section}
{pr_comments_section}{user_input_section}

{status_code_prompt}

{clarification_note}
"""

            return base_prompt

        # No review feedback - normal development mode
        user_input_section = f"\n\n**Additional user notes:**\n{user_input}\n" if user_input else ""

        # Get checklist file path
        iteration_dir = self._get_iteration_dir(self.iteration)
        checklist_file = iteration_dir / "checklist.md"
        from cafe.utils.git_utils import to_cwd_relative_path
        try:
            checklist_path = to_cwd_relative_path(checklist_file)
        except ValueError:
            checklist_path = str(checklist_file.resolve())

        checklist_instruction = format_checklist_instruction(checklist_path)
        base_prompt = f"""# Develop Phase (Normal Mode)

**Your Role:** Developer
Read {agent_file} to understand your complete role definition and responsibilities.

**Basic Principles (MUST follow before writing any code):**
- Follow existing commit message style (format, language, structure)
- Use same language as existing code comments when writing new comments
- Maximize code reuse by looking for existing patterns and utilities

{checklist_instruction}

**Task:** Execute development work according to the implementation plan.

**File paths:**
- Requirements Specification: {self.spec_file}
- Implementation Plan: {self.plan_file}{develop_file_section}
{pr_comments_section}{user_input_section}

{status_code_prompt}

{clarification_note}
"""

        return base_prompt

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

            # Load PR comments only if there's no review feedback (review takes priority)
            # Note: We don't skip execution even if there are no new comments,
            # as the developer may still have work to do based on the plan
            if self.pr_number and (not hasattr(self, '_has_review_feedback') or not self._has_review_feedback):
                print(f"\n🔍 Checking PR #{self.pr_number} for unresolved comments...")
                pr_comments, unresolved_count = self._load_pr_comments()
                if unresolved_count > 0:
                    print(f"✅ Found {unresolved_count} new unresolved PR comment(s) to address")
                else:
                    print(f"ℹ️  No new unresolved PR comments since last develop")
                print()
            elif hasattr(self, '_has_review_feedback') and self._has_review_feedback:
                print(f"ℹ️  Skipping PR comments check - prioritizing review feedback")
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

            # Generate checklist for this iteration
            from cafe.utils.checklist_generator import generate_develop_checklist

            checklist_path = self._get_iteration_dir(self.iteration) / "checklist.md"

            # Check for develop clarification file from previous iteration
            # (only exists if previous iteration returned CAFE_NEED_CLARIFICATION)
            develop_file = None
            if self.iteration > 1:
                prev_iteration_dir = self.issue_dir / "develop" / f"iteration_{self.iteration - 1:03d}"
                prev_output = prev_iteration_dir / "output.md"
                if prev_output.exists():
                    develop_file = str(prev_output)

            # Check if in correction mode (has review feedback)
            correction_mode = hasattr(self, '_has_review_feedback') and self._has_review_feedback

            # Get review file path if in correction mode
            review_file = None
            if correction_mode:
                review_dir = self.issue_dir / "review"
                review_file_path = self._get_latest_versioned_file("review", review_dir)
                if review_file_path and review_file_path.exists():
                    review_file = str(review_file_path)

            # Define basic principles
            basic_principles = """- Follow existing commit message style (format, language, structure)
- Use same language as existing code comments when writing new comments
- Maximize code reuse by looking for existing patterns and utilities"""

            # Calculate output file path for this iteration
            iteration_dir = self._get_iteration_dir(self.iteration)
            output_file = str(iteration_dir / "output.md")

            generate_develop_checklist(
                agent_name=self.dev_agent,
                spec_file_path=self.spec_file,
                plan_file_path=self.plan_file,
                develop_file=develop_file,
                checklist_file_path=checklist_path,
                correction_mode=correction_mode,
                review_file_path=review_file,
                basic_principles=basic_principles,
                output_file=output_file,
            )

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

            # Merge base tools + previous iteration's tools + newly approved tools
            # Even if no tools were approved from denials, agent can still use base_allowed_tools
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
                    PhaseStatusCode.NO_CHANGES_NEEDED,
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.CONFIRMED],
                continue_codes=[],  # No automatic continue codes
            )

            # Handle NEED_PERMISSION, NEED_CLARIFICATION, NO_CHANGES_NEEDED specially - return and wait for next invocation
            if response:
                response_status = StatusCodeParser.extract(
                    response,
                    valid_codes=[
                        PhaseStatusCode.CONFIRMED,
                        PhaseStatusCode.NEED_PERMISSION,
                        PhaseStatusCode.NEED_CLARIFICATION,
                        PhaseStatusCode.NO_CHANGES_NEEDED,
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
                elif response_status == PhaseStatusCode.NO_CHANGES_NEEDED:
                    # Check if output.md exists and has content
                    print(f"\n⚠️  Developer returned CAFE_NO_CHANGES_NEEDED, checking for reasoning in output.md...")

                    iteration_dir = self._get_iteration_dir(self.iteration)
                    output_file = iteration_dir / "output.md"

                    has_reasoning = output_file.exists() and output_file.stat().st_size > 0

                    if not has_reasoning:
                        # No reasoning provided, require agent to write it
                        print(f"⚠️  No reasoning found in output.md. Requesting agent to provide explanation...")

                        continue_prompt = f"""Your response returned CAFE_NO_CHANGES_NEEDED.

You MUST provide your reasoning and explain why the reviewer's feedback is incorrect or unnecessary.

Please:
1. Write your detailed reasoning to {output_file}
2. Return CAFE_NO_CHANGES_NEEDED again

Do NOT return any other status code until you have written your reasoning."""

                        # Execute agent again to get reasoning
                        try:
                            continuation_response, continuation_streaming_log, continuation_token_usage, _, continuation_streaming_log_list, _ = self.agent_manager.execute(
                                self.dev_agent,
                                continue_prompt,
                                allowed_tools=allowed_tools,
                                allowed_directories=self._get_allowed_directories(),
                            )

                            # Extract status code from continuation response
                            continuation_status = StatusCodeParser.extract(
                                continuation_response,
                                valid_codes=[PhaseStatusCode.NO_CHANGES_NEEDED],
                            )

                            # Merge responses
                            merged_response = response + "\n\n[Reasoning Request]\n" + continuation_response

                            # Get streaming logs and prompt from context
                            context_file = iteration_dir / "context.json"
                            original_streaming_log = []
                            original_prompt = ""
                            if context_file.exists():
                                with open(context_file, "r", encoding="utf-8") as f:
                                    context_data = json.load(f)
                                    original_streaming_log = context_data.get("streaming_log", [])
                                    original_prompt = context_data.get("prompt", "")

                            merged_streaming_log = original_streaming_log + continuation_streaming_log_list

                            # Update iteration history
                            self._update_iteration_history(
                                phase_specific_data={
                                    "response": merged_response,
                                    "streaming_log": merged_streaming_log,
                                },
                                prompt=original_prompt,
                                agent_cli=None,
                                agent_session_id=None,
                                allowed_tools=allowed_tools,
                            )

                            # Check again if output.md has content now
                            has_reasoning_now = output_file.exists() and output_file.stat().st_size > 0

                            if has_reasoning_now and continuation_status == PhaseStatusCode.NO_CHANGES_NEEDED:
                                print(f"✅ Developer provided reasoning in output.md")
                                # Return IN_PROGRESS and wait for user decision in next iteration
                                return PhaseResult(
                                    status=PhaseStatus.IN_PROGRESS,
                                    message=f"Developer returned NO_CHANGES_NEEDED with reasoning in iteration {self.iteration}. Run command again to respond.",
                                    data={
                                        "iterations": self.iteration,
                                        "last_response": merged_response,
                                        "status_code": PhaseStatusCode.NO_CHANGES_NEEDED.value,
                                    },
                                )
                            else:
                                # Still no valid reasoning
                                print(f"❌ Agent did not provide reasoning in output.md")
                                return PhaseResult(
                                    status=PhaseStatus.FAILED,
                                    message=f"Developer returned NO_CHANGES_NEEDED but did not provide reasoning in output.md",
                                    data={
                                        "iterations": self.iteration,
                                        "last_response": merged_response,
                                    },
                                )
                        except Exception as e:
                            print(f"⚠️  Failed to request reasoning: {e}")
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message=f"Error while requesting reasoning for NO_CHANGES_NEEDED: {e}",
                                data={
                                    "iterations": self.iteration,
                                    "last_response": response,
                                },
                            )
                    else:
                        print(f"✅ Developer provided reasoning in output.md")
                        # Return IN_PROGRESS and wait for user decision in next iteration
                        return PhaseResult(
                            status=PhaseStatus.IN_PROGRESS,
                            message=f"Developer returned NO_CHANGES_NEEDED with reasoning in iteration {self.iteration}. Run command again to respond.",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                                "status_code": PhaseStatusCode.NO_CHANGES_NEEDED.value,
                            },
                        )
                elif response_status == PhaseStatusCode.NEED_CLARIFICATION:
                    # Verify that clarification request meets allowed criteria
                    # Send confirmation prompt to agent
                    print(f"\n⚠️  Developer returned CAFE_NEED_CLARIFICATION, verifying if it meets allowed criteria...")

                    confirmation_prompt = """Your previous response returned CAFE_NEED_CLARIFICATION.

Please confirm: Does your clarification request meet one of these allowed criteria?
1. Requested actions conflict with your behavioral guidelines
2. Encountering technical problems beyond your current capability

If YES (meets criteria): Return CAFE_NEED_CLARIFICATION again
If NO (does not meet criteria): Continue with development work and return appropriate status code when done

**Response format:**
- Return ONLY the status code on the first line
- Do NOT include any summary or explanation"""

                    try:
                        confirmation_response, _, _, _, confirmation_streaming_log, _ = self.agent_manager.execute(
                            self.dev_agent,
                            confirmation_prompt,
                            allowed_tools=allowed_tools,
                            allowed_directories=self._get_allowed_directories(),
                        )

                        confirmation_status = StatusCodeParser.extract(
                            confirmation_response,
                            valid_codes=[
                                PhaseStatusCode.CONFIRMED,
                                PhaseStatusCode.NEED_PERMISSION,
                                PhaseStatusCode.NEED_CLARIFICATION,
                            ],
                        )

                        # Merge original response and confirmation response
                        merged_response = response + "\n\n" + confirmation_response

                        # Get original streaming_log and prompt from context.json
                        # Since we're in the middle of handling agent response, we need to get it from context
                        iteration_dir = self._get_iteration_dir(self.iteration)
                        context_file = iteration_dir / "context.json"
                        original_streaming_log = []
                        original_prompt = ""
                        if context_file.exists():
                            with open(context_file, "r", encoding="utf-8") as f:
                                context_data = json.load(f)
                                original_streaming_log = context_data.get("streaming_log", [])
                                original_prompt = context_data.get("prompt", "")

                        # Merge streaming logs
                        merged_streaming_log = original_streaming_log + confirmation_streaming_log

                        if confirmation_status == PhaseStatusCode.NEED_CLARIFICATION:
                            # Agent confirmed that clarification is needed
                            print(f"✅ Developer confirmed CAFE_NEED_CLARIFICATION is appropriate.")

                            # Update iteration history with merged response and merged streaming_log
                            self._update_iteration_history(
                                phase_specific_data={
                                    "response": merged_response,
                                    "clarification_confirmed": True,
                                    "streaming_log": merged_streaming_log,
                                },
                                prompt=original_prompt,
                                agent_cli=None,
                                agent_session_id=None,
                                allowed_tools=allowed_tools,
                            )

                            # Save clarification to file
                            self._save_develop_clarification(merged_response)

                            # Display clarification request and return IN_PROGRESS
                            if self.interactive:
                                print(f"\n{'='*60}")
                                print(f"Dev ({self.dev_agent}) - Iteration {self.iteration}:")
                                print(f"{'='*60}")
                                print(merged_response)
                                print(f"{'='*60}\n")
                                print("💡 Developer needs clarification. Run 'cafe develop' again to respond.")

                                return PhaseResult(
                                    status=PhaseStatus.IN_PROGRESS,
                                    message=f"Clarification requested in iteration {self.iteration}. Run command again to respond.",
                                    data={
                                        "iterations": self.iteration,
                                        "last_response": merged_response,
                                        "status_code": PhaseStatusCode.NEED_CLARIFICATION.value,
                                    },
                                )
                            else:
                                return PhaseResult(
                                    status=PhaseStatus.COMPLETED,
                                    message="Clarification needed - saved to develop file. Re-run with --user-input to provide response.",
                                    data={
                                        "iterations": self.iteration,
                                        "last_response": merged_response,
                                        "status_code": PhaseStatusCode.NEED_CLARIFICATION.value,
                                    },
                                )
                        else:
                            # Agent decided to continue development (returned CONFIRMED or NEED_PERMISSION or other)
                            print(f"✅ Developer decided to continue development instead of requesting clarification.")

                            # Update iteration history with merged response and merged streaming_log
                            self._update_iteration_history(
                                phase_specific_data={
                                    "response": merged_response,
                                    "clarification_confirmed": False,
                                    "streaming_log": merged_streaming_log,
                                },
                                prompt=original_prompt,
                                agent_cli=None,
                                agent_session_id=None,
                                allowed_tools=allowed_tools,
                            )

                            # Update response and response_status for downstream processing
                            response = merged_response
                            response_status = confirmation_status

                            # Continue to handle the new status code (fall through to code below)
                            # Check if it's NEED_PERMISSION and handle it
                            if confirmation_status == PhaseStatusCode.NEED_PERMISSION:
                                # Display permission request and return IN_PROGRESS
                                if self.interactive:
                                    print(f"\n{'='*60}")
                                    print(f"Dev ({self.dev_agent}) - Iteration {self.iteration}:")
                                    print(f"{'='*60}")
                                    print(merged_response)
                                    print(f"{'='*60}\n")
                                    print("💡 Developer requested permissions. Run 'cafe develop' again to respond.")

                                    return PhaseResult(
                                        status=PhaseStatus.IN_PROGRESS,
                                        message=f"Permission requested in iteration {self.iteration}. Run command again to respond.",
                                        data={
                                            "iterations": self.iteration,
                                            "last_response": merged_response,
                                            "status_code": confirmation_status.value,
                                        },
                                    )
                                else:
                                    return PhaseResult(
                                        status=PhaseStatus.FAILED,
                                        message="Permission required but running in non-interactive mode",
                                        data={
                                            "iterations": self.iteration,
                                            "last_response": merged_response,
                                        },
                                    )
                            # If CONFIRMED, fall through to normal completion handling below

                    except Exception as e:
                        print(f"⚠️  Failed to verify CAFE_NEED_CLARIFICATION: {e}")
                        # On verification failure, allow clarification to proceed
                        # (safer to let human decide than to auto-reject)

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
            return self._handle_keyboard_interrupt("develop")
        except Exception as e:
            return self._handle_exception_in_execute(e, "Development phase failed")

    def _get_branch_name(self) -> str:
        """Get branch name based on workflow mode.

        Returns:
            Branch name
        """
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

        DevelopPhase uses iteration_XXX/output.md to record CAFE_NEED_CLARIFICATION questions.

        Returns:
            List[Path]: Return list containing iteration_XXX/output.md if it exists, otherwise empty list
        """
        develop_dir = self.issue_dir / "develop"
        develop_file = develop_dir / f"iteration_{self.iteration:03d}" / "output.md"
        return [develop_file] if develop_file.exists() else []
