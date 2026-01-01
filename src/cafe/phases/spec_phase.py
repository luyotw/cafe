"""Specification phase (requirements clarification)."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.ui.display import Display
from cafe.ui.phase_prompts import prompt_for_input_method, prompt_for_rigor, fetch_github_issue
from cafe.utils.git_utils import get_github_repo_name, get_repo_root, to_cwd_relative_path
from cafe.utils.github import GitHubOps, GitHubError

# Maximum number of clarification iterations to prevent infinite loops
MAX_CLARIFICATION_ITERATIONS = 10


def create_github_issue(content: str) -> str:
    """Create a new GitHub issue with content.

    Args:
        content: Issue content

    Returns:
        Issue ID

    Note:
        This is a placeholder. Actual implementation should use gh CLI.
    """
    # TODO: Implement using gh CLI
    # gh issue create --title "..." --body "..."
    raise NotImplementedError("GitHub issue creation not yet implemented")


def update_github_issue(issue_id: str, content: str) -> None:
    """Update existing GitHub issue.

    Args:
        issue_id: Issue ID
        content: Updated content

    Note:
        This is a placeholder. Actual implementation should use gh CLI.
    """
    # TODO: Implement using gh CLI
    # gh issue edit <issue_id> --body "..."
    raise NotImplementedError("GitHub issue update not yet implemented")


class SpecPhase(Phase):
    """Specification phase: Requirements clarification with PM agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        pm_agent: str = "Roger",
        interactive: bool = True,
        issue_name: Optional[str] = None,
        rigor: Optional["SpecRigor"] = None,
        user_input: str = "",
        fetch_issue_id: Optional[int] = None,
        spec_file: Optional[str] = None,  # Deprecated: kept for backward compatibility
    ) -> None:
        """Initialize requirements phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            pm_agent: PM agent name (default: Roger)
            interactive: Enable interactive mode for user input (default: True)
            issue_name: Issue name for history tracking (default: derived from current branch)
            rigor: Specification rigor level (default: medium)
            user_input: User input for non-interactive mode (default: "")
            fetch_issue_id: GitHub issue number to fetch content from (optional)
            spec_file: (Deprecated) Spec file path - ignored, kept for backward compatibility
        """
        super().__init__(interactive=interactive, git_ops=git_ops)

        from cafe.core.types import SpecRigor

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.pm_agent = pm_agent
        self.user_input = user_input
        self.fetch_issue_id = fetch_issue_id
        self.phase_name = "spec"  # For base class progress tracking

        # Track if rigor was explicitly set (for interactive prompting)
        if rigor is not None:
            self.rigor = rigor
            self._rigor_explicitly_set = True
        else:
            self.rigor = SpecRigor.MEDIUM  # Default, will prompt if interactive
            self._rigor_explicitly_set = False

        self.iteration = 0

        # Determine issue name for history tracking
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from current branch (issue_dir is set by base class)
            self.issue_name = self.issue_dir.name

        # Phase directory for spec phase
        # Path: .cafe/issues/{issue_name}/spec
        self.phase_dir = self.issue_dir / "spec"

        # spec_file will be set in execute() based on iteration number
        self.spec_file: str = ""

        # Config-based settings (loaded from config.yaml by _load_issue_config())
        self._config_input_method: Optional[str] = None
        self._config_issue_id: Optional[int] = None

        # Load issue_id from config.json if exists (for comment posting after resume)
        self._load_issue_config()

        # Store original requirement (from first iteration)
        self.original_requirement: Optional[str] = None

        # Track requirements and questions
        self.confirmed_requirements = []
        self.pending_questions = []

        # Initialize display for better input handling
        self.display = Display()

        # Restore state from last iteration file (if resuming)
        self.iteration = self._load_iteration_counter()

        # Load phase-specific data from last iteration
        if self.iteration > 0:
            existing_iterations = sorted(self.phase_dir.glob("iteration_*/context.json"))
            if existing_iterations:
                last_context_file = existing_iterations[-1]
                with open(last_context_file, "r", encoding="utf-8") as f:
                    last_data = json.load(f)
                if "confirmed_requirements" in last_data:
                    self.confirmed_requirements = last_data["confirmed_requirements"]
                if "pending_questions" in last_data:
                    self.pending_questions = last_data["pending_questions"]

    def execute(self) -> PhaseResult:
        """Execute requirements clarification phase.

        Returns:
            Phase result
        """
        try:
            # Check if phase is already completed (avoid re-running completed phases)
            from cafe.core.status_codes import PhaseStatusCode
            early_exit_result = self._check_if_already_completed([
                PhaseStatusCode.CONFIRMED,
            ])
            if early_exit_result:
                return early_exit_result

            # Ensure phase directory exists
            self.phase_dir.mkdir(parents=True, exist_ok=True)

            # Get next iteration number and setup versioned file path
            try:
                iteration_number = self._get_next_iteration_number("spec", self.phase_dir)
            except ValueError as e:
                # Exceeded 999 iterations
                # spec_file may not be set yet, use hasattr to check
                spec_file = self.spec_file if hasattr(self, 'spec_file') else None
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"Spec phase failed: {str(e)}",
                    data={"spec_file": spec_file},
                )

            # Set self.iteration based on versioned files and history
            # iteration_number is the next file number (count + 1)
            # self.iteration is the current execution iteration
            if self.iteration == 0:  # No history
                # If files exist, this is a resume of the last iteration
                # If no files, this is the first iteration
                self.iteration = max(1, iteration_number - 1)
            else:  # Has history
                # Continue from last completed iteration
                self.iteration += 1

            # Note: Copy of previous version is deferred until just before agent execution
            # to avoid creating new iterations when interrupted before agent is called

            # Set versioned spec_file path
            spec_file_path = self._get_versioned_file_path("spec", iteration_number, self.phase_dir)
            self.spec_file = str(spec_file_path)

            # Fetch issue content from GitHub if --issue-id is provided
            if self.fetch_issue_id:
                error_result = self._fetch_github_issue(self.fetch_issue_id)
                if error_result:
                    return error_result

            # Check if already confirmed - skip execution
            already_completed = self._check_if_already_completed([PhaseStatusCode.CONFIRMED])
            if already_completed:
                return already_completed

            # Validate inputs
            # Note: GitHub mode can now work without issue_id (will create new issue)

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check if spec file exists
                spec_path = Path(self.spec_file)
                if spec_path.exists():
                    # File already exists (resume case), skip to execution
                    pass
                elif iteration_number == 1:
                    # File doesn't exist AND this is first iteration - get initial user story
                    if self.interactive:
                        # If --issue-id not provided, check config or ask user to choose input method
                        if not self.fetch_issue_id:
                            # Check if config has input_method specified
                            if self._config_input_method:
                                # Use config values
                                if self._config_input_method == "github" and self._config_issue_id:
                                    # Fetch from GitHub Issue
                                    error_result = self._fetch_github_issue(self._config_issue_id)
                                    if error_result:
                                        return error_result
                                else:
                                    # Manual input
                                    self._prompt_for_user_story()
                            else:
                                # No config, prompt user
                                method, issue_id = self._prompt_for_input_method()

                                if method == "github" and issue_id:
                                    # Fetch from GitHub Issue
                                    error_result = self._fetch_github_issue(issue_id)
                                    if error_result:
                                        return error_result
                                else:
                                    # Manual input
                                    self._prompt_for_user_story()
                        else:
                            # --issue-id already provided, skip to user story prompt
                            self._prompt_for_user_story()

                    else:
                        # Non-interactive mode: use user_input if provided, otherwise try stdin
                        if self.user_input is None:
                            # Explicitly set to None - not allowed to read from stdin
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message="No user story provided in non-interactive mode",
                                data={"iterations": 0},
                            )
                        elif self.user_input:
                            # User input provided
                            user_story = self.user_input.strip()
                        else:
                            # Try reading from stdin
                            import sys
                            user_story = sys.stdin.read().strip()

                        # Remove END marker if present
                        if user_story.upper().endswith("END"):
                            lines = user_story.split('\n')
                            if lines[-1].strip().upper() == "END":
                                user_story = '\n'.join(lines[:-1]).strip()

                        if not user_story:
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message="No user story provided in non-interactive mode",
                                data={"iterations": 0},
                            )

                        # Create spec file with user story
                        spec_path.write_text(user_story, encoding="utf-8")

            # Ask for rigor level if interactive and not set (after input method selection)
            if self.interactive and iteration_number == 1:
                self._prompt_for_rigor()

            # Capture original requirement before entering clarification loop
            # Note: Iteration is determined by history files, not versioned spec files
            # For iteration 1: read from spec_001.md (initial requirement)
            # For iteration 2+: read from previous iteration's file (spec_{iteration-1}.md)
            if self.iteration > 1:
                # Read from previous iteration's completed spec file
                prev_spec_path = self._get_versioned_file_path("spec", self.iteration - 1, self.phase_dir)
                if prev_spec_path.exists():
                    self.original_requirement = prev_spec_path.read_text(encoding="utf-8").strip()
            else:
                # First iteration: read from spec_001.md if it exists
                first_spec_path = self._get_versioned_file_path("spec", 1, self.phase_dir)
                if first_spec_path.exists():
                    self.original_requirement = first_spec_path.read_text(encoding="utf-8").strip()

            # NOTE: self.iteration is already set from versioned files at line 183
            # No need to increment here as it's handled by _get_next_iteration_number()

            # Safety check: prevent infinite loops
            max_iterations_result = self._check_max_iterations(
                MAX_CLARIFICATION_ITERATIONS,
                "Requirements clarification"
            )
            if max_iterations_result:
                return max_iterations_result

            # Prepare user_input for this iteration
            result_or_input = self._prepare_user_input_for_iteration()
            if isinstance(result_or_input, PhaseResult):
                # Method returned a PhaseResult (completion/failure/pause)
                return result_or_input
            # Otherwise, it's the user input string
            current_user_input = result_or_input

            # Prepare allowed tools with write/edit permission for spec file
            # Convert to relative path (without / prefix) - plain relative path
            # Use path relative to current working directory (supports worktree)
            from cafe.utils.git_utils import to_cwd_relative_path

            try:
                spec_file_pattern = to_cwd_relative_path(self.spec_file)
            except ValueError:
                # Fallback to absolute path if file is not under cwd
                spec_file_pattern = str(Path(self.spec_file).resolve())

            # Merge base tools with previous iteration's tools (if any)
            base_allowed_tools = [
                "read",
                "grep",
                "glob",
                "ls",
                "web_fetch",
                "web_search",
                f"edit({spec_file_pattern})",
            ]
            allowed_tools = self._merge_allowed_tools(base_allowed_tools)

            # Copy previous version just before calling agent (if iteration > 1)
            # This ensures we only create a new iteration when we actually execute the agent
            self._copy_previous_version("spec", iteration_number, self.phase_dir)

            # Execute full agent interaction cycle (generate prompt, execute, handle status)
            result, response = self._execute_and_handle_agent_response(
                agent_name=self.pm_agent,
                user_input=current_user_input,
                valid_status_codes=[
                    PhaseStatusCode.READY_FOR_REVIEW,
                    PhaseStatusCode.NEED_CLARIFICATION,
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
                continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
            )

            # In mock mode or if agent doesn't use write tool, write spec from response
            self._ensure_spec_file_written(response)

            # Verify agent followed instructions: check if content is in file (not response)
            verification_result, final_response = self._verify_output_format(
                agent_name=self.pm_agent,
                response=response,
                spec_file_pattern=spec_file_pattern,
                allowed_tools=allowed_tools,
                valid_status_codes=[
                    PhaseStatusCode.READY_FOR_REVIEW,
                    PhaseStatusCode.NEED_CLARIFICATION,
                ],
            )

            # Use the final response (either original or corrected)
            if final_response:
                response = final_response

            # Phase-specific post-processing: Sync spec to GitHub (no-op in local mode)
            self._sync_spec_to_github("")

            # Save rigor setting to config after each iteration
            self._save_issue_config()

            # Since we removed the while loop, if result is None (meaning need to continue),
            # we should return IN_PROGRESS with the status code from response
            if result is None:
                # Extract status code from response to include in result
                from cafe.core.status_codes import StatusCodeParser
                status_code = StatusCodeParser.extract(response)

                return PhaseResult(
                    status=PhaseStatus.IN_PROGRESS,
                    message=f"Spec phase needs more iterations (iteration {self.iteration})",
                    data={
                        "iterations": self.iteration,
                        "status_code": status_code.value if status_code else None,
                        "spec_file": self.spec_file,  # Issue 1: Add full file path
                    },
                )

            return result

        except KeyboardInterrupt:
            # User paused with Ctrl+C - save progress and allow resume
            print("\n\n⏸️  Paused by user (Ctrl+C).")
            print(f"💾 Progress saved. Current iteration: {self.iteration}")
            print(f"📝 To resume, run: cafe spec {self.issue_name}")
            return PhaseResult(
                status=PhaseStatus.IN_PROGRESS,
                message="Paused by user - can resume later",
                data={
                    "iterations": self.iteration,
                    "spec_file": self.spec_file,  # Issue 1: Add full file path
                },
            )
        except Exception as e:
            # Use base class helper to handle critical errors
            result = self._handle_exception_in_execute(e, "Spec phase failed")
            # Add phase-specific data
            spec_file = self.spec_file if hasattr(self, 'spec_file') else None
            if spec_file:
                result.data["spec_file"] = spec_file
            return result

    def _get_completion_data(self) -> dict:
        """Get additional data when phase completes (provided to base class's _handle_standard_status_codes).

        Returns:
            Dict containing phase-specific data, will be merged into PhaseResult.data
        """
        data = {}
        # Add issue_id if GitHub mode and created new issue
        if self.workflow_mode == WorkflowMode.GITHUB and hasattr(self, '_created_issue_id'):
            data["issue_id"] = self._created_issue_id

        # Post spec.md back to GitHub issue if fetched from GitHub
        if hasattr(self, '_fetched_issue_id'):
            try:
                spec_path = Path(self.spec_file)
                if spec_path.exists():
                    spec_content = spec_path.read_text(encoding="utf-8")

                    # Post comment to GitHub issue
                    gh_ops = GitHubOps()
                    gh_ops.add_issue_comment(self._fetched_issue_id, spec_content)

            except GitHubError as e:
                # Log error but don't fail the phase
                print(f"Warning: Failed to post spec to GitHub issue: {e}")

        # Always include spec_file in completion data (Issue 1: Add full file path)
        # Find the latest existing output.md file (in case current iteration doesn't have one)
        latest_output = self._get_latest_versioned_file("spec", self.phase_dir)
        if latest_output:
            data["spec_file"] = str(latest_output)
        elif hasattr(self, 'spec_file'):
            data["spec_file"] = self.spec_file

        return data

    def _prepare_user_input_for_iteration(self) -> "PhaseResult | str":
        """Prepare user input for current iteration.

        Get all required user input at the beginning:
        - Interactive: Ask user from stdin
        - Non-interactive: Get from self.user_input

        Subsequent processing logic is identical, no distinction between interactive/non-interactive.

        Returns:
            PhaseResult: If phase needs to end/pause
            str: User input content (for agent use)
        """
        # Iteration 1: user_input is the initial user story from spec file
        if self.iteration == 1:
            spec_file_path = Path(self.spec_file)
            return spec_file_path.read_text() if spec_file_path.exists() else ""

        # Iteration 2+: Check if current iteration was interrupted (has user_input but no response)
        current_data = self._load_current_iteration_data()
        if current_data and current_data.get("user_input") and not current_data.get("response"):
            # Resume interrupted iteration, directly use saved user_input
            return current_data["user_input"]

        # Iteration 2+: Display current spec content (interactive only)
        if self.interactive:
            self._display_current_spec()

        # Iteration 2+: Load previous iteration data
        prev_data = self._load_previous_iteration_data()
        if not prev_data:
            return ""

        prev_status = prev_data.get("status_code", "")

        # Get user input based on previous round status
        if prev_status == "CAFE_READY_FOR_REVIEW":
            # Need user choice: confirm/modify
            if self.interactive:
                choice = self._ask_user_for_review_decision("Requirements specification", agent_name="PM")
            else:
                choice = self.user_input
                # Non-interactive mode: clear after use to ensure no reuse
                self.user_input = ""

                if not choice:
                    # Non-interactive but no input provided → fail immediately
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Spec phase failed after iteration {self.iteration - 1}: received READY_FOR_REVIEW in non-interactive mode without user input",
                        data={
                            "iterations": self.iteration - 1,
                            "last_response": prev_data.get("response", ""),
                            "status_code": "CAFE_READY_FOR_REVIEW",
                        },
                    )

            # Handle user choice (no longer distinguish interactive/non-interactive)
            return self._process_review_decision(
                choice,
                prev_data,
                "Specification",
                {"pm_agent": self.pm_agent},
            )

        elif prev_status == "CAFE_NEED_CLARIFICATION":
            return self._handle_need_clarification_input(prev_data, agent_display_name="PM")
        else:
            return ""

    def _display_current_spec(self) -> None:
        """Display current spec.md content (interactive mode only)."""
        prev_iteration = self.iteration - 1
        if prev_iteration > 0:
            prev_spec_file = self._get_versioned_file_path("spec", prev_iteration, self.phase_dir)
            print(f"\n💾 Loading latest requirements specification file: {prev_spec_file}\n")
            spec_content = prev_spec_file.read_text() if prev_spec_file.exists() else "(File not generated)"
        else:
            spec_content = "(File not generated)"

        # Get PM CLI info for display
        pm_cli = self.agent_manager.get_agent_config(self.pm_agent).cli.value

        print(f"\n{'='*60}")
        print(f"PM ({self.pm_agent} by {pm_cli}) - Current specification content (Iteration {self.iteration - 1}):")
        print(f"{'='*60}")
        print(spec_content)
        print(f"{'='*60}\n")

    def _prompt_for_rigor(self) -> None:
        """Prompt user to select rigor level if not already set."""
        from cafe.core.types import SpecRigor

        # Check if rigor is already explicitly set (not default)
        # We only prompt if it's still at default value
        if self._rigor_explicitly_set:
            return

        # Use shared prompt function
        rigor_str = prompt_for_rigor(self.display)
        self.rigor = SpecRigor(rigor_str)

    def _prompt_for_input_method(self) -> tuple[str, Optional[int]]:
        """Ask user to select requirements input method (manual vs GitHub Issue)

        Returns:
            Tuple of (method, issue_id):
            - method: "manual" or "github"
            - issue_id: Issue ID (int) if GitHub selected, None otherwise
        """
        # Use shared prompt function
        gh_ops = GitHubOps()
        return prompt_for_input_method(self.display, gh_ops)

    def _fetch_github_issue(self, issue_id: int) -> Optional[PhaseResult]:
        """Fetch issue content from GitHub

        Args:
            issue_id: GitHub issue ID

        Returns:
            PhaseResult if error occurred, None if success
        """
        try:
            # Get repository name from .git/config
            repo_name = get_github_repo_name()

            # Fetch issue content using shared function
            gh_ops = GitHubOps()
            fetched_content = fetch_github_issue(gh_ops, issue_id)

            # Override user_input with fetched content
            self.user_input = fetched_content

            # Store issue_id for later comment posting
            self._fetched_issue_id = str(issue_id)

            # Save issue config
            self._save_issue_config()

            # Write fetched content to spec file (same as _prompt_for_user_story)
            # If spec_file is not set (when called directly without execute()),
            # compute it using the next iteration number
            if not self.spec_file:
                iteration_number = self._get_next_iteration_number("spec", self.phase_dir)
                spec_path = self._get_versioned_file_path("spec", iteration_number, self.phase_dir)
            else:
                spec_path = Path(self.spec_file)

            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(f"# Initial Requirements\n\n{fetched_content}\n")

            print()
            print("✅ Requirements loaded from GitHub Issue, starting clarification...")
            print()

            return None  # Success

        except FileNotFoundError as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Failed to get repository info: {e}",
            )
        except RuntimeError as e:
            # From fetch_github_issue when gh CLI not authenticated
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=str(e),
            )
        except GitHubError as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Failed to fetch GitHub issue: {e}",
            )

    def _prompt_for_user_story(self) -> None:
        """Prompt user to write initial requirement when no requirements file exists."""
        print("\n" + "="*70)
        print("Please describe your requirements:")
        print("="*70)
        print()
        print("Recommended to write as user stories:")
        print("   Format: As a [role], I want [feature], so that [purpose/value]")
        print()
        print("   Examples:")
        print("   - As a product manager, I want to quickly understand project progress to report development status to the team")
        print("   - As a user, I want to see clear error messages to know what went wrong and how to fix it")
        print()
        print("Or describe requirements in general terms:")
        print("   - Add a CSV export feature")
        print("   - Fix bug where login page cannot submit")
        print("   - Optimize homepage loading speed")
        print()

        # Get user's requirement using prompt_multiline for better UX
        from cafe.ui.inquirer_prompts import prompt_multiline
        user_requirement = prompt_multiline("Please enter your requirements").strip()

        if not user_requirement:
            raise ValueError("No requirements provided, cannot continue")

        # Save requirement as initial spec
        spec_path = Path(self.spec_file)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(f"# Initial Requirements\n\n{user_requirement}\n")

        print()
        print("✅ Requirements recorded, starting clarification...")
        print()

    def _backup_spec(self, spec_path: Path) -> None:
        """Backup original spec file.

        Args:
            spec_path: Path to spec file
        """
        backup_path = Path(f"{spec_path}.backup")
        if not backup_path.exists():
            backup_path.write_text(spec_path.read_text())

    def _ensure_spec_file_written(self, response: str) -> None:
        """Ensure spec file is written (for mock mode or when agent does not use write tool).
        
        In mock mode, agent will not actually call write tool, so content needs to be extracted from response and written.
        
        Args:
            response: Agent response content
        """
        import os
        
        # Only process in mock mode
        if not os.getenv("CAFE_MOCK_AGENTS"):
            return
            
        # Extract content after status code
        lines = response.strip().split("\n")
        if not lines:
            return
            
        # Skip first line (status code) and empty lines
        content_lines = []
        skip_first = True
        for line in lines:
            if skip_first and line.startswith("CAFE_"):
                continue
            skip_first = False
            content_lines.append(line)
        
        content = "\n".join(content_lines).strip()
        if not content:
            return
            
        # Write to file
        spec_path = Path(self.spec_file)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(content, encoding="utf-8")

    def _sync_spec_to_github(self, response: str) -> None:
        """Sync spec file to GitHub issue (GitHub workflow mode only).

        Note: This method **is not responsible for writing spec file**. PM agent has directly written to spec_file via Write tool.

        Purpose of this method:
        - Local mode: No action (agent has written file, no additional processing needed)
        - GitHub mode: Sync local spec_file content to GitHub issue

        Args:
            response: Agent response content (unused, because agent has written file via Write tool)
        """
        spec_path = Path(self.spec_file)
        if not spec_path.exists():
            return  # Agent has not written file yet

        if self.workflow_mode == WorkflowMode.GITHUB:
            # Sync local file content to GitHub issue
            content = spec_path.read_text()
            if not self.issue_id and not hasattr(self, '_created_issue_id'):
                # First time creating GitHub issue
                self._created_issue_id = create_github_issue(content)
            elif hasattr(self, '_created_issue_id'):
                # Update previously created issue
                update_github_issue(self._created_issue_id, content)
            else:
                # Update existing issue
                update_github_issue(self.issue_id, content)

    def _create_github_issue(self, content: str) -> str:
        """Create a new GitHub issue with requirements.

        Args:
            content: Requirements content

        Returns:
            Issue ID
        """
        return create_github_issue(content)

    def _update_github_issue(self, content: str) -> None:
        """Update existing GitHub issue with requirements.

        Args:
            content: Requirements content
        """
        update_github_issue(self.issue_id, content)

    def _generate_prompt(self, user_input: str = "") -> str:
        """Generate prompt for current iteration.

        Args:
            user_input: User's response/clarification for this iteration

        Returns:
            Prompt string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return self._generate_github_prompt(user_input)
        else:
            return self._generate_local_prompt(user_input)

    def _get_non_technical_guidelines(self) -> str:
        """Get non-technical guidelines for PM.

        Returns:
            Guidelines string
        """
        return """**Important: Absolutely no technical details!**
- ❌ Do not mention implementation methods, technical architecture, programming languages, frameworks, databases, etc.
- ❌ Do not suggest any technical solutions
- ❌ Do not modify code yourself
- ✅ Only focus on "what users want" "why they want it" "what the expected outcome is"
- ✅ Think from product and business perspectives"""

    def _get_status_code_prompt(self) -> str:
        """Get status code prompt.

        Returns:
            Status code prompt string
        """
        return generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
            descriptions={
                PhaseStatusCode.READY_FOR_REVIEW: "Requirements specification completed, ready for user confirmation",
                PhaseStatusCode.NEED_CLARIFICATION: "Requirements have unclear parts that need clarification",
            },
        )

    def _get_rigor_guidelines(self) -> str:
        """Get rigor level guidelines for PM.

        Returns:
            Rigor guidelines string
        """
        from cafe.core.types import SpecRigor

        if self.rigor == SpecRigor.LOW:
            return """**Rigor level: Low (fast development)**
- Only ask for the most critical information (what is the core function)
- Do not ask for details that developers can decide themselves
- Allow some ambiguity, trust developers to make reasonable choices
- If requirements are basically clear, can confirm
- Goal: Quickly start development, adjust as you go"""
        elif self.rigor == SpecRigor.HIGH:
            return """**Rigor level: High (precise specification)**
- Ask all details in depth (including edge cases, error handling, special scenarios)
- Ensure every function's input, output, and behavior is clearly defined
- Ask for acceptance criteria to ensure requirements are testable
- Do not allow any vague or "it depends" descriptions
- Must reach the level where test cases can be directly written
- Goal: The clearer the specification, the better, reduce subsequent communication costs"""
        else:  # MEDIUM (default)
            return """**Rigor level: Medium (balanced mode)**
- Ask important details and key scenarios
- Ask about obviously unclear areas, but don't over-pursue minor details
- Ensure main functions and expected behaviors are clear
- Accept reasonable flexibility for secondary details
- Ask for acceptance criteria, but don't require excessive detail
- Goal: Balance between speed and precision"""

    def _generate_local_prompt(self, user_input: str = "") -> str:
        """Generate prompt for local workflow.

        Args:
            user_input: User's response/clarification for this iteration

        Returns:
            Prompt string
        """
        non_technical = self._get_non_technical_guidelines()
        status_code_prompt = self._get_status_code_prompt()
        rigor_guidelines = self._get_rigor_guidelines()

        # Calculate current and previous file names (using relative paths)
        # self.spec_file is the current file to write (already set in execute())
        from pathlib import Path
        current_spec_path = Path(self.spec_file)
        if not current_spec_path.is_absolute():
            current_spec_path = current_spec_path.resolve()

        # Convert to path relative to current working directory (for prompt)
        # Use to_cwd_relative_path to support worktree environment
        try:
            current_spec_file = to_cwd_relative_path(current_spec_path)
        except (ValueError, OSError):
            # If cannot convert (file not under cwd), use absolute path
            current_spec_file = str(current_spec_path)

        # Previous round file: only exists when iteration > 1
        prev_spec_file = None
        if self.iteration > 1:
            # List existing files and get the newest one (as previous round file)
            # Current file (self.spec_file) may not exist yet (about to be created), so the newest file is from previous round
            existing_specs = sorted(self.phase_dir.glob("spec_*.md"))
            if existing_specs:
                # Get the newest file (highest number) as previous round file
                prev_spec_path = existing_specs[-1]
                if not prev_spec_path.is_absolute():
                    prev_spec_path = prev_spec_path.resolve()
                # Convert to path relative to current working directory
                try:
                    prev_spec_file = to_cwd_relative_path(prev_spec_path)
                except (ValueError, OSError):
                    prev_spec_file = str(prev_spec_path)

        # --- 1. Determine context-specific sections ---
        initial_instruction = ""
        context_section = ""
        restriction = ""

        if self.iteration == 1:
            # Round 1: Read user_input (initial requirements), write to spec_001.md
            initial_instruction = f"""**Round 1 Requirements Clarification**

Read {current_spec_file}  for initial requirements content."""
            context_section = """
**Your Responsibilities:**
1. Carefully read requirements document, identify all unclear, vague, or areas that might require developers to make assumptions.
2. **Before asking questions**: Try to find answers from README.md or codebase first using Read/Grep tools.
3. **Only ask when necessary**: If you cannot find the answer from existing documentation/code, then ask users.
4. **As PM** ask users conversationally to confirm all necessary information.
5. If requirements are already clear, say so, do not force questions.
"""
        else:  # Iteration 2+
            # Round 2 onwards: Read previous round spec file and user_input, write new spec file
            initial_instruction = f"""**Round {self.iteration} Requirements Clarification**

1. Use Read tool to read {prev_spec_file}(previous round analysis results)
2. View user's latest answer (see below)
3. Modify {current_spec_file}, update content (new version)"""
            
            if user_input:
                context_section = f"""
**User's Answer:**
{user_input}
"""
            if self.iteration >= 4:
                restriction = f"""
⚠️ **Important Constraints:**
- You are now in round {self.iteration} , can only continue asking about "pending questions".
- **Cannot propose new questions**.
- Can only deeply clarify questions already raised.
"""

        # --- 2. Define common instructions ---
        # Add agent role definition reading instruction
        from cafe.agents.manager import AgentManager
        agent_file = AgentManager.get_agent_file_path(self.pm_agent, "pm")
        
        role_reading_instruction = f"""
**Execution Steps:**
1. Use Read tool to read {agent_file} to find you native language
2. Understand your role definition and work guidelines from {agent_file}
"""
        
        if self.iteration == 1:
            role_reading_instruction += f"""3. Use Read tool to read {current_spec_file} to understand initial requirements content, then read README.md for more context
4. Modify {current_spec_file}，Add analysis results (including original requirements, user stories, current specification, questions to clarify)
"""
        else:
            role_reading_instruction += f"""3. Use Read tool to read {prev_spec_file}(previous round analysis results)
4. Integrate user's latest answers
5. Modify {current_spec_file}，Update analysis results (new version)
"""

        base_prompt = f"""
**Your Role:** PM (Product Manager)
Read {agent_file} to understand your role and responsibilities."""

        output_format = f"""
**⚠️ CRITICAL: Output Format**
1. Write ALL content (requirements, questions, specifications) to {current_spec_file}
2. Return ONLY the status code in your response
3. ❌ NEVER put questions or content in your response
4. ✅ Questions go in the markdown file, response contains ONLY status code

**Why this matters:**
If you put questions in your response instead of the file, the workflow CANNOT continue.
The user will NOT see your questions, and the process will be stuck.

**Example (CORRECT):**
- File {current_spec_file}: Contains all questions and specifications
- Your response: "CAFE_NEED_CLARIFICATION"

**Example (WRONG - workflow will fail):**
- Your response: "I have some questions: 1. What is...? CAFE_NEED_CLARIFICATION" ← ❌ DO NOT DO THIS
"""

        need_clarification_instruction = f"""**Status: CAFE_NEED_CLARIFICATION**
Write to {current_spec_file}:
   - ## Original Requirements Description (preserve exactly as provided)
   - ## User Stories (from user or auto-generated)
   - ## Current Requirements Specification (integrate all known info)
   - ## Questions to Clarify (PM asks conversational questions)

Response: "CAFE_NEED_CLARIFICATION" (nothing else)"""

        confirmed_instruction = f"""**Status: CAFE_READY_FOR_REVIEW**
Write to {current_spec_file}:
   - ## Original Requirements Description (preserve exactly as provided)
   - ## User Stories (from user or auto-generated)
   - ## Requirements Specification (complete spec with functions, scenarios, behaviors, acceptance criteria)

Response: "CAFE_READY_FOR_REVIEW" (nothing else)"""

        # --- 3. Assemble the final prompt ---
        return f"""{initial_instruction}
{base_prompt.strip()}
{role_reading_instruction}
{context_section}
{rigor_guidelines}

{non_technical}

{output_format}

{status_code_prompt}
{restriction}

{need_clarification_instruction}
{confirmed_instruction}
"""

    def _generate_github_prompt(self, user_input: str = "") -> str:
        """Generate prompt for GitHub workflow.

        Args:
            user_input: User's response/clarification for this iteration

        Returns:
            Prompt string
        """
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "Requirements are clear, can proceed with development",
                PhaseStatusCode.NEED_CLARIFICATION: "Requirements have unclear parts that need clarification",
            },
        )

        if self.iteration == 1:
            return f"""This is round {self.iteration}  requirements analysis.

Please use `gh issue view {self.issue_id}` to read Issue content, carefully analyze requirements, identify all unclear, vague, or areas that might require developers to make assumptions.

{status_code_prompt}

**If there are requirements issues that need clarification:**
List questions in the most concise way, do not give any suggestions.

**If requirements are already clear, confirm completion:**
Respond with confirmation message.
"""
        else:
            return f"""This is round {self.iteration}  requirements analysis.

Please use `gh issue view {self.issue_id}`  to view Issue's latest content.

{status_code_prompt}

**If there are requirements issues that need clarification:**
List questions in the most concise way, do not give any suggestions.

**If requirements are already clear, confirm completion:**
Respond with confirmation message.
"""

    def _load_issue_config(self) -> None:
        """Load issue configuration (issue_id, rigor, input_method) from config.yaml if exists."""
        from cafe.core.types import SpecRigor

        # Path: .cafe/issues/{issue_name}/config.yaml
        config_file = self.issue_dir / "issue.yaml"

        config_data = self._read_issue_config(config_file)
        if config_data:
            # Load from new spec section if exists
            spec_config = config_data.get("spec", {})

            # Load input_method and issue_id from spec section
            if "input_method" in spec_config:
                self._config_input_method = spec_config["input_method"]
                # If method is github, also load issue_id
                if spec_config.get("issue_id"):
                    self._config_issue_id = int(spec_config["issue_id"])

            # Load rigor from spec section
            if "rigor" in spec_config and not self._rigor_explicitly_set:
                try:
                    self.rigor = SpecRigor(spec_config["rigor"])
                except (ValueError, KeyError):
                    # Invalid rigor value in config, use default
                    pass

            # Backwards compatibility: load from root level if spec section doesn't exist
            if not spec_config:
                if "issue_id" in config_data:
                    self._fetched_issue_id = config_data["issue_id"]
                # Load rigor from config if not explicitly set by user
                if "rigor" in config_data and not self._rigor_explicitly_set:
                    try:
                        self.rigor = SpecRigor(config_data["rigor"])
                    except (ValueError, KeyError):
                        # Invalid rigor value in config, use default
                        pass

    def _save_issue_config(self) -> None:
        """Save issue configuration (issue_id, rigor) to config.yaml."""
        # Path: .cafe/issues/{issue_name}/config.yaml
        config_file = self.issue_dir / "issue.yaml"

        # Read existing config to preserve base_branch, feature_branch, and worktree_path
        existing_config = self._read_issue_config(config_file) or {}

        # Prepare new config data
        config_data = {**existing_config}

        # Add issue_id if available
        if hasattr(self, '_fetched_issue_id'):
            config_data["issue_id"] = self._fetched_issue_id

        # Always save rigor (even if it's the default) so subsequent iterations use the same value
        config_data["rigor"] = self.rigor.value

        # Write config
        self._write_issue_config(config_file, config_data)

    def get_status_file(self) -> Path:
        """Public method to get status file path for workflow integration.

        Returns:
            Path to status.json
        """
        return self._get_status_file()

    def _get_status_analysis_prompt(self) -> str:
        """Get prompt for analyzing status code.

        Returns:
            Prompt for analyzing spec file status
        """
        # Use absolute path
        from pathlib import Path
        spec_path = Path(self.spec_file)
        if not spec_path.is_absolute():
            spec_path = spec_path.resolve()
        
        return f"""Please use Read tool to read {spec_path}  and analyze current status.

Based on the following conditions, determine which status code to return:

- CAFE_READY_FOR_REVIEW: Requirements specification completed, all necessary information clarified, no pending questions
- CAFE_NEED_CLARIFICATION: Specification still has issues that need user confirmation, or unclear details

Please return only one status code (e.g., CAFE_READY_FOR_REVIEW), no other content."""

    def _detect_written_output_files(self) -> List[Path]:
        """Check if spec file was written before failure.

        Returns:
            List[Path]: If spec_{iteration}.md exists, return list containing it, otherwise return empty list
        """
        spec_file = self._get_versioned_file_path("spec", self.iteration, self.phase_dir)
        return [Path(spec_file)] if Path(spec_file).exists() else []

    def _verify_output_format(
        self,
        agent_name: str,
        response: str,
        spec_file_pattern: str,
        allowed_tools: List[str],
        valid_status_codes: List[PhaseStatusCode],
    ) -> tuple[Optional[PhaseResult], Optional[str]]:
        """Verify that agent followed output format instructions.

        Checks:
        1. Is content written to the markdown file (not in response)?
        2. Is the markdown file written in the agent's native language?

        Args:
            agent_name: Name of the agent
            response: Agent's response
            spec_file_pattern: File pattern for spec file
            allowed_tools: Tools allowed for agent
            valid_status_codes: Valid status codes

        Returns:
            Tuple of (verification_result, final_response)
            - verification_result: PhaseResult if verification failed, None if ok
            - final_response: Updated response if agent corrected the output, None to use original
        """
        from cafe.core.status_codes import StatusCodeParser
        from cafe.agents.manager import AgentManager

        # Extract status code from response
        status_code = StatusCodeParser.extract(response)
        if not status_code:
            return None, None

        # Read spec file to check content
        spec_path = Path(self.spec_file)
        if not spec_path.exists():
            # File doesn't exist - agent didn't write anything
            return None, None

        spec_content = spec_path.read_text(encoding="utf-8")

        # Build verification prompt
        verification_prompt = f"""Please verify your previous output:

1. Did you write ALL questions and specifications to {spec_file_pattern}?
   - Check: Is your response ONLY a status code (e.g., "CAFE_NEED_CLARIFICATION")?
   - Check: Are ALL questions in the markdown file, NOT in your response?

2. Did you write the markdown file in your native language (the language you were configured with)?
   - Check the content in {spec_file_pattern}
   - Your native language: Use the language specified in your agent configuration

**If both checks pass:**
Return ONLY your previous status code: {status_code.value}

**If any check fails:**
1. Fix the markdown file in {spec_file_pattern}
2. Return ONLY the status code (no explanation)

Remember: Your response must contain ONLY the status code, nothing else."""

        # Execute verification using the phase's agent_manager
        try:
            verification_response, _ = self.agent_manager.execute(
                agent_name=agent_name,
                prompt=verification_prompt,
                allowed_tools=allowed_tools,
            )

            # Extract status code from verification response
            verified_status = StatusCodeParser.extract(verification_response)

            # If status code is valid, return the final response
            if verified_status and verified_status in valid_status_codes:
                return None, verification_response.strip()

            # If no valid status code, something went wrong
            return None, None

        except Exception:
            # If verification fails, just use original response
            return None, None
