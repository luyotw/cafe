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
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus
from cafe.ui.chat import launch_chat_session
from cafe.ui.display import Display
from cafe.ui.inquirer_prompts import prompt_list, prompt_multiline
from cafe.utils.prompt_utils import format_checklist_instruction


class DevelopPhase(Phase):
    """Legacy compatibility implementation for the develop phase."""

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
        review_feedback_info = self._get_latest_review_feedback_info()
        if self._feedback_is_confirmed(review_feedback_info):
            return False

        return True

    def _get_latest_review_feedback_info(self) -> Optional[Dict[str, Any]]:
        """Return latest completed review iteration info from iteration contexts."""
        review_dir = self.issue_dir / "review"
        if not review_dir.exists():
            return None

        for iteration_dir in reversed(sorted(review_dir.glob("iteration_*"))):
            context_file = iteration_dir / "context.json"
            if not context_file.exists():
                continue
            try:
                with open(context_file, "r", encoding="utf-8") as f:
                    ctx = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            if not self._context_marks_completed(ctx):
                continue

            end_time = None
            end_time_str = ctx.get("end_time")
            if isinstance(end_time_str, str) and end_time_str:
                try:
                    end_time = datetime.fromisoformat(end_time_str)
                except ValueError:
                    end_time = None

            output_file = iteration_dir / "output.md"
            return {
                "iteration_dir": iteration_dir,
                "context": ctx,
                "status_code": self._context_status_code(ctx),
                "end_time": end_time,
                "output_file": output_file if output_file.exists() else None,
            }

        return None

    @staticmethod
    def _feedback_status(info: Optional[Dict[str, Any]]) -> Optional[str]:
        """Return normalized status code from a feedback info dict."""
        if not info:
            return None
        status = info.get("status_code")
        return status if isinstance(status, str) and status else None

    @classmethod
    def _feedback_is_confirmed(cls, info: Optional[Dict[str, Any]]) -> bool:
        """Return True when feedback info represents a confirmed review."""
        return cls._feedback_status(info) == PhaseStatusCode.CONFIRMED.value

    @classmethod
    def _feedback_needs_changes(cls, info: Optional[Dict[str, Any]]) -> bool:
        """Return True when feedback info represents needs-changes feedback."""
        return cls._feedback_status(info) == PhaseStatusCode.NEEDS_CHANGES.value

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

    def _show_pr_todo_list(self) -> None:
        """Display PR todo list and comments from the latest iteration with output.md."""
        pr_dir = self.issue_dir / "pr"
        iteration_dirs = sorted(pr_dir.glob("iteration_*"), reverse=True)

        from cafe.utils.git_utils import to_cwd_relative_path

        for iteration_dir in iteration_dirs:
            output_file = iteration_dir / "output.md"
            if output_file.exists():
                try:
                    output_path = to_cwd_relative_path(output_file)
                except ValueError:
                    output_path = str(output_file.resolve())
                print(f"  → Todo list: {output_path}")

                user_input_file = iteration_dir / "user_input.md"
                if user_input_file.exists():
                    try:
                        user_input_path = to_cwd_relative_path(user_input_file)
                    except ValueError:
                        user_input_path = str(user_input_file.resolve())
                    print(f"  → PR comments: {user_input_path}")
                break

    def _get_latest_pr_feedback_info(self) -> Optional[Dict[str, Any]]:
        """Return latest completed PR iteration info from iteration contexts."""
        pr_dir = self.issue_dir / "pr"
        if not pr_dir.exists():
            return None

        for iteration_dir in reversed(sorted(pr_dir.glob("iteration_*"))):
            context_file = iteration_dir / "context.json"
            if not context_file.exists():
                continue
            try:
                with open(context_file, "r", encoding="utf-8") as f:
                    ctx = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            if not self._context_marks_completed(ctx):
                continue

            end_time = None
            end_time_str = ctx.get("end_time")
            if isinstance(end_time_str, str) and end_time_str:
                try:
                    end_time = datetime.fromisoformat(end_time_str)
                except ValueError:
                    end_time = None

            output_file = iteration_dir / "output.md"
            return {
                "iteration_dir": iteration_dir,
                "context": ctx,
                "status_code": self._context_status_code(ctx),
                "end_time": end_time,
                "output_file": output_file if output_file.exists() else None,
            }

        return None

    def _check_if_already_completed_with_review(self) -> Optional[PhaseResult]:
        """Check if phase is already completed, considering special logic for review feedback and PR comments.

        Returns:
            PhaseResult if completed and no review feedback/PR comments need to be handled, None if should continue execution
        """
        # FIRST: Always check and set review feedback flag (do this BEFORE checking existing_progress)
        # This ensures the flag is set for _generate_prompt even on first execution
        review_feedback_info = self._get_latest_review_feedback_info()
        self._has_review_feedback = self._feedback_needs_changes(review_feedback_info)

        existing_progress = self._load_progress()
        if not existing_progress or existing_progress.status != PhaseStatus.COMPLETED:
            # Not completed yet, but flag is set - continue execution
            if self._has_review_feedback:
                review_file = self._get_review_file_path()
                print(f"ℹ️  Review feedback detected: {review_file}")
            return None

        # Collect NEEDS_CHANGES timestamps from review and PR phases
        # Only phases newer than develop's end_time need to be handled
        develop_end_time = getattr(existing_progress, 'end_time', None)

        review_needs_changes_time = None
        pr_needs_changes_time = None

        # Check review phase end_time
        if self._feedback_needs_changes(review_feedback_info):
            review_needs_changes_time = review_feedback_info.get("end_time")

        pr_feedback_info = self._get_latest_pr_feedback_info()
        if self._feedback_needs_changes(pr_feedback_info):
            pr_needs_changes_time = pr_feedback_info.get("end_time")

        # Filter: only keep phases newer than develop's end_time
        if develop_end_time:
            if review_needs_changes_time and develop_end_time > review_needs_changes_time:
                review_needs_changes_time = None
                self._has_review_feedback = False
            if pr_needs_changes_time and develop_end_time > pr_needs_changes_time:
                pr_needs_changes_time = None

        # Decide which phase to handle based on end_time priority
        if review_needs_changes_time and pr_needs_changes_time:
            # Both have NEEDS_CHANGES newer than develop — pick the newer one
            if review_needs_changes_time >= pr_needs_changes_time:
                # Review is newer or equal, handle review feedback
                return None
            else:
                # PR is newer, handle PR feedback
                self._has_review_feedback = False
                self._show_pr_todo_list()
                return None
        elif review_needs_changes_time:
            # Only review has NEEDS_CHANGES newer than develop
            return None
        elif pr_needs_changes_time:
            # Only PR has NEEDS_CHANGES newer than develop
            self._has_review_feedback = False
            self._show_pr_todo_list()
            return None

        # If review has NEEDS_CHANGES but no end_time available, assume it needs handling
        if self._has_review_feedback and not review_needs_changes_time:
            if self._feedback_needs_changes(review_feedback_info):
                if not review_feedback_info.get("end_time"):
                    return None

        # No review feedback or PR comments newer than develop, phase is truly completed
        self._has_review_feedback = False
        phase_data: dict = {
            "branch": self._get_branch_name(),
            "iterations": existing_progress.iteration,
            "status_code": existing_progress.status_code,
        }
        # If the previous iteration completed with CONFIRMED_SKIP_REVIEW, propagate skip_review flag on resume
        if existing_progress.status_code == PhaseStatusCode.CONFIRMED_SKIP_REVIEW.value:
            phase_data["skip_review"] = True
        return PhaseResult(
            status=PhaseStatus.COMPLETED,
            message=f"Development already completed in {existing_progress.iteration} iteration(s)",
            data=phase_data,
        )

    def _save_progress(self, status_code: "PhaseStatusCode") -> None:  # type: ignore[override]
        """Override _save_progress to treat CONFIRMED_SKIP_REVIEW as a completion code.

        Args:
            status_code: Phase status code
        """
        complete_codes = [
            PhaseStatusCode.CONFIRMED,
            PhaseStatusCode.CONFIRMED_SKIP_REVIEW,
        ]
        super()._save_progress(status_code, complete_codes=complete_codes)

    def _handle_no_changes_needed_input(self, prev_data: dict) -> "PhaseResult | str":
        """Handle user input for NO_CHANGES_NEEDED status (developer disputes reviewer).

        Similar to READY_FOR_REVIEW handling:
        - user_input == "confirm": User agrees, save CONFIRMED_SKIP_REVIEW status and return completion
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
            # User agrees with developer - save CONFIRMED_SKIP_REVIEW status
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
                status_code=PhaseStatusCode.CONFIRMED_SKIP_REVIEW,
            )
            self._save_progress(PhaseStatusCode.CONFIRMED_SKIP_REVIEW)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="User agreed with developer - skipping review phase",
                data={
                    "branch": self._get_branch_name(),
                    "iterations": self.iteration,
                    "status_code": PhaseStatusCode.CONFIRMED_SKIP_REVIEW.value,
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
        from InquirerPy.separator import Separator

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

        while True:
            choices = [
                {"name": "Agree - Skip review and proceed to PR", "value": "c"},
                {"name": "Disagree - Provide feedback for developer", "value": "m"},
                Separator(),
                {"name": f"Chat with {self.dev_agent}", "value": "chat"},
            ]

            choice = prompt_list(
                "Do you agree with the developer?",
                choices,
                default=None,
            )

            if choice == "chat":
                launch_chat_session("developer", self.issue_name)
                continue

            if choice == "c":
                return "confirm"

            # choice == "m"
            feedback = prompt_multiline("Please provide feedback for the developer")

            if not feedback.strip():
                print("\n⚠️  No feedback entered, please try again.")
                continue

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
                # Load from user_input.md or context.json for backward compatibility
                interrupted_user_input = self._load_user_input(self.iteration)
                return interrupted_user_input

        # Check for previous iteration data
        prev_data = self._load_previous_iteration_data()

        # No previous data: first execution
        if not prev_data:
            # Use self.user_input if provided (from --user-input parameter)
            user_input = self.user_input if self.user_input else ""
            self.user_input = ""  # Clear after first use
            return user_input

        prev_status = self._context_status_code(prev_data) or ""

        # Handle pending NEED_PERMISSION from previous run
        if prev_status == "CAFE_NEED_PERMISSION":
            recovered_denials = []
            if self.iteration > 1:
                recovered_denials = self._extract_codex_permission_denials_from_streaming_file(self.iteration - 1)

            # Check if has permission_denials
            if not prev_data.get("permission_denials") and not recovered_denials:
                host_execution_followup = self._build_host_execution_followup(self.iteration - 1)
                if host_execution_followup:
                    return host_execution_followup
                # Fallback for CLIs that do not emit structured permission_denials.
                return self._handle_need_permission_input(prev_data, agent_display_name="Developer")

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

    def _ask_user_for_clarification(self, role: str = "developer") -> str:
        """Ask user for clarification using questions.xml from previous iteration.

        Uses interactive_qa_flow() if questions.xml exists and is valid,
        otherwise falls back to base class prompt with optional chat.

        Returns:
            str: User's answer
        """
        from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
        from cafe.ui.interactive_qa import interactive_qa_flow

        # Look for questions.xml in the previous iteration directory
        if self.iteration > 1:
            prev_iter_dir = self._get_iteration_dir(self.iteration - 1)
            xml_path = prev_iter_dir / "questions.xml"
            if xml_path.exists() and validate_questions_xml(xml_path):
                questions = parse_questions_xml(xml_path)
                return interactive_qa_flow(questions, role=role, issue_name=self.issue_name, agent_name=self.dev_agent)

        # Fallback to base class prompt with chat option
        return super()._ask_user_for_clarification(role=role, agent_name=self.dev_agent)

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
                # Load user_input from user_input.md or context.json for backward compatibility
                user_input = self._load_user_input(iteration_num)
                # If this iteration has user_input, the clarification was answered
                if user_input.strip():
                    return True
            except Exception:
                continue

        return False

    def _generate_prompt(self, user_input: str = "") -> str:
        """Generate prompt for current iteration.

        Args:
            user_input: User input for this iteration (additional instructions from user)

        Returns:
            Prompt string
        """
        status_code_prompt = ""

        # Load PR feedback (either from GitHub comments or local pr_XXX.md files)
        config_file = self.issue_dir / "issue.yaml"
        pr_auto_create = self._get_issue_config_value(config_file, "pr.auto_create")

        # PR feedback is now handled via checklist, not in prompt

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
        from cafe.skills.bridge import try_load_skill_body
        agent_file = AgentManager.get_agent_file_path(self.dev_agent, "developer")
        skill_body = try_load_skill_body(
            "develop",
            context={
                "agent_file": agent_file,
                "spec_file": str(self.spec_file),
                "plan_file": str(self.plan_file),
                "status_code_instruction": status_code_prompt,
            },
        )
        skill_section = f"{skill_body}\n\n" if skill_body else ""

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

        # Compute questions.xml path for clarification instructions
        questions_xml_file_path = self._get_iteration_dir(self.iteration) / "questions.xml"

        clarification_note = f"""
Clarification can be requested only in these two cases, **any other situations strictly prohibit clarification requests, just decide the solution by yourself**:
- Requested actions conflict with the agent's behavioral guidelines
- Encountering technical problems beyond current capability

**⚠️ Never request clarification due to time pressure or token concerns - just do the work. CAFE has a resume mechanism to handle long tasks.**

Steps for requesting clarification:
1. Confirm your question meets the above conditions
2. Write structured questions to {questions_xml_file_path} following this XML schema:
   <questions>
     <question id="q1">
       <title>Your question here</title>
       <options>
         <option>Option A</option>
         <option>Option B</option>
       </options>
     </question>
   </questions>
3. Return CAFE_NEED_CLARIFICATION only, with no other content
"""

        if has_review_feedback:
            # With review feedback - correction mode
            from cafe.utils.prompt_utils import extract_agent_guidelines_checklist
            agent_guidelines_checklist = extract_agent_guidelines_checklist(agent_file)

            user_input_section = f"\n\n**Additional user notes:**\n{user_input}\n" if user_input else ""

            # Review sources are now listed in checklist, not needed in prompt text

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

{skill_section}\
**Your Role:** Developer
Read {agent_file} to understand your complete role definition and responsibilities.

{checklist_instruction}

**Task:** Make corrections based on feedback.

**File paths:**
- Requirements Specification: {self.spec_file}
- Implementation Plan: {self.plan_file}{develop_file_section}
{user_input_section}

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

{skill_section}\
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
{user_input_section}

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


            # Check if already completed (with review feedback awareness)
            already_completed = self._check_if_already_completed_with_review()
            if already_completed:
                return already_completed


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

            # Check if in correction mode (has review feedback or PR feedback)
            correction_mode = hasattr(self, '_has_review_feedback') and self._has_review_feedback

            # Determine feedback file by comparing end_time of PR and review phases
            # The phase with the newer end_time takes priority (represents the latest failed gate)
            feedback_file = None
            pr_feedback_file = None
            pr_end_time = None
            review_feedback_file = None
            review_end_time = None

            from cafe.utils.git_utils import to_cwd_relative_path

            # Collect PR feedback file and its end_time
            pr_feedback_info = self._get_latest_pr_feedback_info()
            if pr_feedback_info:
                pr_output_file = pr_feedback_info.get("output_file")
                if pr_output_file and pr_output_file.read_text(encoding="utf-8").strip():
                    try:
                        pr_feedback_file = to_cwd_relative_path(pr_output_file)
                    except ValueError:
                        pr_feedback_file = str(pr_output_file.resolve())
                pr_end_time = pr_feedback_info.get("end_time")

            # Collect review feedback file and its end_time
            if correction_mode:
                review_feedback_info = self._get_latest_review_feedback_info()
                review_output_file = review_feedback_info.get("output_file") if review_feedback_info else None
                if review_output_file and review_output_file.exists():
                    review_feedback_file = str(review_output_file)
                else:
                    review_dir = self.issue_dir / "review"
                    review_file_path = self._get_latest_versioned_file("review", review_dir)
                    if review_file_path and review_file_path.exists():
                        review_feedback_file = str(review_file_path)

                if review_feedback_info:
                    review_end_time = review_feedback_info.get("end_time")

            # Select feedback file based on end_time priority
            if pr_feedback_file and review_feedback_file:
                # Both have feedback — pick the one with newer end_time
                if pr_end_time and review_end_time:
                    if review_end_time >= pr_end_time:
                        feedback_file = review_feedback_file
                    else:
                        feedback_file = pr_feedback_file
                        if not correction_mode:
                            correction_mode = True
                else:
                    # Timestamps not available — default to review (earlier gate, safer)
                    feedback_file = review_feedback_file
            elif pr_feedback_file:
                feedback_file = pr_feedback_file
                if not correction_mode:
                    correction_mode = True
            elif review_feedback_file:
                feedback_file = review_feedback_file

            # Define basic principles
            basic_principles = """- Follow existing commit message style (format, language, structure)
- Use same language as existing code comments when writing new comments
- Maximize code reuse by looking for existing patterns and utilities"""

            # Add worktree file modification restriction if in worktree mode
            config_file = self.issue_dir / "issue.yaml"
            worktree_path = self._get_issue_config_value(config_file, "worktree_path")
            if worktree_path:
                basic_principles += f"\n- In worktree mode: Only modify files under {worktree_path}, modifying project root files is strictly prohibited"

            # Calculate output file and questions.xml paths for this iteration
            iteration_dir = self._get_iteration_dir(self.iteration)
            output_file = str(iteration_dir / "output.md")
            questions_xml_path = iteration_dir / "questions.xml"

            from cafe.utils.git_utils import to_cwd_relative_path as _to_cwd_rel
            try:
                questions_xml_file = _to_cwd_rel(questions_xml_path)
            except (ValueError, OSError):
                questions_xml_file = str(questions_xml_path.resolve())

            generate_develop_checklist(
                agent_name=self.dev_agent,
                spec_file_path=self.spec_file,
                plan_file_path=self.plan_file,
                develop_file=develop_file,
                checklist_file_path=checklist_path,
                correction_mode=correction_mode,
                feedback_file_path=feedback_file,
                basic_principles=basic_principles,
                output_file=output_file,
                questions_xml_file=questions_xml_file,
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
                response_status = self._extract_status_code_from_response(
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
                            continuation_status = self._extract_status_code_from_response(
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
                    # Validate questions.xml — same mechanism as spec/plan phases
                    # If agent didn't write questions.xml, the clarification request is rejected
                    self._validate_and_retry_questions_xml(
                        xml_path=questions_xml_path,
                        agent_name=self.dev_agent,
                        allowed_tools=allowed_tools,
                    )

                    if not questions_xml_path.exists():
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message=f"Developer returned CAFE_NEED_CLARIFICATION but did not write questions.xml in iteration {self.iteration}",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                                "status_code": PhaseStatusCode.NEED_CLARIFICATION.value,
                            },
                        )

                    # Valid questions.xml exists — pause and wait for user response
                    if self.interactive:
                        print("💡 Developer needs clarification. Run 'cafe develop' again to respond.")

                    return PhaseResult(
                        status=PhaseStatus.IN_PROGRESS,
                        message=f"Clarification requested in iteration {self.iteration}. Run command again to respond.",
                        data={
                            "iterations": self.iteration,
                            "last_response": response,
                            "status_code": PhaseStatusCode.NEED_CLARIFICATION.value,
                        },
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
