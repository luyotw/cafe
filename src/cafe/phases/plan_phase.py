"""Implementation plan phase."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.ui.display import Display
from cafe.utils.git_utils import get_repo_root

# Maximum number of planning iterations to prevent infinite loops
MAX_PLANNING_ITERATIONS = 10


class PlanPhase(Phase):
    """Phase 2: Implementation plan with developer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: "GitOperations",
        spec_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        issue_name: Optional[str] = None,
        dev_agent: str = "David",
        interactive: bool = True,
        template_path: Optional[str] = None,
        user_input: str = "",
    ) -> None:
        """Initialize plan phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations (for deriving issue directory from current branch)
            spec_file: Path to spec file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            issue_name: Issue name for history tracking (default: derived from current branch)
            dev_agent: Developer agent name (default: David)
            template_path: Path to plan template file (optional)
            interactive: Whether to allow interactive prompts (default: True)
            user_input: User input for non-interactive mode (default: "")
        """
        super().__init__(interactive=interactive, git_ops=git_ops)

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent
        self.template_path = template_path
        self.user_input = user_input
        self.display = Display()
        self.iteration = 0
        self.phase_name = "plan"  # For base class progress tracking

        # Determine issue name for history tracking (issue_dir is set by base class)
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from current branch name (via issue_dir)
            self.issue_name = self.issue_dir.name

        # Load template from config if not explicitly provided
        self._load_plan_config()

        # Phase directory for plan phase (for versioned files)
        # Path: .cafe/issues/{issue_name}/plan
        self.phase_dir = self.issue_dir / "plan"

        # History directory for plan phase
        # Path: .cafe/issues/{issue_name}/plan/history
        self.history_dir = self.phase_dir / "history"

        # Load existing history if available (will create dir if needed)
        self.iteration = self._load_iteration_counter()

        # Initialize plan_file to None (will be set during execute)
        self.plan_file: Optional[Path] = None

    def execute(self) -> PhaseResult:
        """Execute implementation plan phase.

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

            # Validate inputs
            if self.workflow_mode == WorkflowMode.GITHUB and not self.issue_id:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="GitHub mode requires issue_id",
                )

            # Increment iteration and execute agent
            self.iteration += 1

            # Safety check: prevent infinite loops
            max_iterations_result = self._check_max_iterations(
                MAX_PLANNING_ITERATIONS,
                "Implementation plan"
            )
            if max_iterations_result:
                return max_iterations_result

            # Get iteration number for versioned file
            iteration_number = self._get_next_iteration_number("plan", self.phase_dir)

            # Note: Copy of previous version is deferred until just before agent execution
            # to avoid creating new iterations when interrupted before agent is called

            # Calculate versioned plan file path
            self.plan_file = self._get_versioned_file_path("plan", iteration_number, self.phase_dir)

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check requirements file exists
                req_path = Path(self.spec_file)
                if not req_path.exists():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Spec file not found: {self.spec_file}",
                    )

                # Check if this is first iteration (no history files or plan files)
                has_history = self.history_dir.exists() and list(self.history_dir.glob("iteration_*.json"))
                is_first_iteration = not has_history

                # First iteration requires template
                if is_first_iteration and not self.template_path:
                    if self.interactive:
                        # Interactive mode: prompt for template selection
                        from cafe.templates.manager import TemplateManager
                        from cafe.ui.template_selector import select_template
                        from rich.console import Console
                        console = Console()

                        template_manager = TemplateManager(".cafe")
                        templates = template_manager.list_templates()

                        if not templates:
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message="No templates found. Use 'cafe template add <source> <name>' to add templates.",
                            )

                        console.print()
                        console.print("[yellow]First iteration requires a template.[/yellow]")
                        template_paths = {name: template_manager.get_template_path(name) for name in templates}
                        selected_template = select_template(templates, template_paths)

                        if selected_template:
                            self.template_path = str(template_manager.get_template_path(selected_template))
                            console.print(f"[dim]Using template: {selected_template}[/dim]")
                        else:
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message="Template selection cancelled",
                            )
                    else:
                        # Non-interactive mode: require --template option
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message="Template is required for first iteration. Use --template option.",
                        )

                # Only prompt for dev guide in first iteration
                # Note: self.iteration is determined by history files, not versioned plan files
                if is_first_iteration:
                    # Check for any existing plan file with development guide section
                    # Always check plan_001.md (first versioned file), not self.plan_file
                    first_plan_file = self._get_versioned_file_path("plan", 1, self.phase_dir)
                    plan_exists = first_plan_file.exists() and self._has_dev_guide_section(first_plan_file)
                else:
                    # Not first iteration: plan should already exist
                    plan_exists = True

                # First round: no plan file exists or no dev guide
                if is_first_iteration and not plan_exists:
                    # Need to get dev guide (optional)
                    dev_guide = ""
                    if self.interactive:
                        # Interactive: prompt user for development guide
                        dev_guide = self._get_dev_guide_from_user()
                    else:
                        # Non-interactive: use user_input as dev guide (can be empty)
                        dev_guide = self.user_input

                    # Save development guide as initial versioned plan file
                    self.plan_file.parent.mkdir(parents=True, exist_ok=True)
                    self.plan_file.write_text(f"## Development Guide\n\n{dev_guide}\n")

            # Prepare user_input for this iteration
            result_or_input = self._prepare_user_input_for_iteration()
            if isinstance(result_or_input, PhaseResult):
                # Method returned a PhaseResult (completion/failure/pause)
                return result_or_input
            # Otherwise, it's the user input string
            current_user_input = result_or_input

            # Prepare allowed tools with write/edit permission for versioned plan file
            # Convert to relative path (without / prefix) - normal relative path
            # Use path relative to current working directory (supports worktree)
            from cafe.utils.git_utils import to_cwd_relative_path

            try:
                plan_file_pattern = to_cwd_relative_path(self.plan_file)
            except ValueError:
                # Fallback to absolute path if file is not under cwd
                plan_file_pattern = str(Path(self.plan_file).resolve())

            # Merge base tools with previous iteration's tools (if any)
            base_allowed_tools = [
                "read",
                "grep",
                "glob",
                "ls",
                "web_fetch",
                "web_search",
                f"edit({plan_file_pattern})",
            ]
            allowed_tools = self._merge_allowed_tools(base_allowed_tools)

            # Copy previous version just before calling agent (if iteration > 1)
            # This ensures we only create a new iteration when we actually execute the agent
            self._copy_previous_version("plan", iteration_number, self.phase_dir)

            # Execute full agent interaction cycle (generate prompt, execute, handle status)
            result, response = self._execute_and_handle_agent_response(
                agent_name=self.dev_agent,
                user_input=current_user_input,
                valid_status_codes=[
                    PhaseStatusCode.READY_FOR_REVIEW,
                    PhaseStatusCode.NEED_CLARIFICATION,
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
                continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
                phase_specific_data={"dev_agent": self.dev_agent},
            )

            if result:
                return result

            # Since we removed the while loop, if result is None (meaning need to continue),
            # we should return IN_PROGRESS with the status code from response
            from cafe.core.status_codes import StatusCodeParser
            status_code = StatusCodeParser.extract(response)

            return PhaseResult(
                status=PhaseStatus.IN_PROGRESS,
                message=f"Plan phase needs more iterations (iteration {self.iteration})",
                data={
                    "iterations": self.iteration,
                    "status_code": status_code.value if status_code else None,
                },
            )

        except Exception as e:
            return self._handle_exception_in_execute(e, "Plan phase failed")

    def _generate_prompt(self, user_input: str) -> str:
        """Generate prompt for current iteration.

        Args:
            user_input: User's input/feedback for this iteration

        Returns:
            Prompt string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return self._generate_github_prompt(user_input)
        else:
            return self._generate_local_prompt(user_input)

    def _generate_local_prompt(self, user_input: str) -> str:
        """Generate prompt for local workflow.

        Args:
            user_input: User's input/feedback for this iteration

        Returns:
            Prompt string
        """
        # Use versioned plan file path (fallback to calculating it if not set)
        if self.plan_file is None:
            # For tests or edge cases where plan_file wasn't set in execute()
            iteration_number = self._get_next_iteration_number("plan", self.phase_dir)
            self.plan_file = self._get_versioned_file_path("plan", iteration_number, self.phase_dir)

        # Convert all file paths to paths relative to current working directory (for prompt)
        # Use to_cwd_relative_path to support worktree environment
        from cafe.utils.git_utils import to_cwd_relative_path
        
        try:
            plan_file_path = to_cwd_relative_path(Path(self.plan_file).resolve())
        except (ValueError, OSError):
            plan_file_path = self.plan_file

        try:
            spec_file_path = to_cwd_relative_path(Path(self.spec_file).resolve())
        except (ValueError, OSError):
            spec_file_path = self.spec_file

        if self.template_path:
            try:
                template_path = to_cwd_relative_path(Path(self.template_path).resolve())
            except (ValueError, OSError):
                template_path = self.template_path
        else:
            template_path = None

        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
            descriptions={
                PhaseStatusCode.READY_FOR_REVIEW: "Implementation analysis completed, ready for user review",
                PhaseStatusCode.NEED_CLARIFICATION: "Need more information or confirmation",
            },
        )

        # Add template reference if template is provided
        template_instruction = ""
        if template_path:
            template_instruction = f"""
**Important: Must strictly follow template format**
Please first read {template_path}, then strictly follow the template's format, section structure, and writing style to write plan.md.
- Strictly use the same section titles and structure as the template
- Reference the level of detail and writing style in the template
- For parts with words like "strictly" or "must", please maintain consistency
- Keep it concise, avoid overly verbose explanations
- Do not write actual implementation content (code, config files, etc.) in the document, only write plans and steps
"""

        if self.iteration == 1:
            from cafe.agents.manager import AgentManager
            agent_file = AgentManager.get_agent_file_path(self.dev_agent, "developer")

            return f"""Analyze {spec_file_path} and plan implementation steps.

**Your Role:**
Please first use Read tool to read {agent_file} to understand your role definition and work guidelines, then strictly follow the requirements in the role definition for planning.

This is iteration {self.iteration} of implementation analysis.

**Execution Steps:**
1. Use Read tool to read {agent_file} to understand the role definition
2. Use Read tool to read the development guide in {plan_file_path}
3. Use Read tool to read the requirements document {spec_file_path}
4. Plan implementation steps according to the role definition requirements (Note: your job is "planning" not "implementation", just need to write plans and steps)
{template_instruction}
{status_code_prompt}

**Important: Edit existing file, append implementation plan to the file**
- Append implementation plan **after** the "## Development Guide" section
- Keep the "## Development Guide" section unchanged

**If need more information (status: CAFE_NEED_CLARIFICATION):**
Append after development guide:
   - "## Implementation Plan" - current implementation analysis content
   - "## Questions to Confirm" - list technical questions that need confirmation

**If analysis complete (status: CAFE_READY_FOR_REVIEW):**
Append complete implementation plan after development guide, strictly follow template's section structure and format.
"""
        else:
            # Add user's modification request section for iteration 2+
            user_request_section = ""
            if user_input:
                user_request_section = f"""
**User's Modification Request:**
{user_input}

"""

            from cafe.agents.manager import AgentManager
            agent_file = AgentManager.get_agent_file_path(self.dev_agent, "developer")
            
            return f"""Continue analyzing the latest version of {spec_file_path}.

**Your Role:**
Please use Read tool to read {agent_file} to understand your role definition and work guidelines, then strictly follow the requirements in the role definition for planning.

This is iteration {self.iteration} of implementation analysis.

{user_request_section}
**Execution Steps:**
1. Use Read tool to read {agent_file} to understand the role definition (if necessary)
2. Use Read tool to read the latest version of {plan_file_path}
3. According to user's modification request and role definition, **update** existing implementation plan (do not rewrite entirely)
{template_instruction}
{status_code_prompt}

**Important: Edit existing file, update specific sections**
- Modify specific section content (using old_string/new_string method)
- Keep the "## Development Guide" section unchanged
- Only modify parts that need changes

**If still need confirmation (status: CAFE_NEED_CLARIFICATION):**
Update relevant sections in {plan_file_path}, and list technical questions that need confirmation in the "## Questions to Confirm" section.

**If analysis complete (status: CAFE_READY_FOR_REVIEW):**
Update sections that need modification in {plan_file_path}, ensure implementation plan meets user's requirements.
"""

    def _generate_github_prompt(self, user_input: str) -> str:
        """Generate prompt for GitHub workflow.

        Args:
            user_input: User's input/feedback for this iteration

        Returns:
            Prompt string
        """
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
            descriptions={
                PhaseStatusCode.READY_FOR_REVIEW: "Implementation analysis completed, ready for user review",
                PhaseStatusCode.NEED_CLARIFICATION: "Need more information or confirmation",
            },
        )

        if self.iteration == 1:
            return f"""Analyze GitHub Issue #{self.issue_id} and plan implementation steps.

**Your Role:**
You are an experienced Developer, responsible for planning detailed implementation steps based on requirements specifications and development guidelines.

This is iteration {self.iteration} of implementation analysis.

Please use `gh issue view {self.issue_id}` to read Issue content, plan detailed implementation steps based on requirements and development guidelines.

{status_code_prompt}

**If need more information:**
Use `gh issue comment {self.issue_id}` to post a comment and ask questions.

**If analysis complete:**
Reply with confirmation message.
"""
        else:
            # Add user's modification request section for iteration 2+
            user_request_section = ""
            if user_input:
                user_request_section = f"""
**User's Modification Request:**
{user_input}

"""

            return f"""Continue analyzing GitHub Issue #{self.issue_id}.

**Your Role:**
You are an experienced Developer, responsible for planning detailed implementation steps based on requirements specifications and development guidelines.

This is iteration {self.iteration} of implementation analysis.

{user_request_section}Please use `gh issue view {self.issue_id}` to view the latest Issue content.

{status_code_prompt}

**If need more information:**
Use `gh issue comment {self.issue_id}` to post a comment and ask questions.

**If analysis complete:**
Reply with confirmation message.
"""


    def _has_dev_guide_section(self, plan_file: Path) -> bool:
        """Check if plan.md has development guide section.

        Args:
            plan_file: Path to plan.md

        Returns:
            True if development guide section exists
        """
        if not plan_file.exists():
            return False

        content = plan_file.read_text()
        # Check for development guide heading
        patterns = [
            r"##\s*Development\s+Guide",
            r"##\s*[Dd]evelopment\s+[Gg]uide",
        ]

        for pattern in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True

        return False

    def _get_dev_guide_from_user(self) -> str:
        """Prompt user to provide development guide.

        Returns:
            Development guide content from user input
        """
        print("\n" + "="*70)
        print("Please provide development guide, explain implementation direction and technical background:")
        print("="*70)
        print()
        print("Development guide should include:")
        print("  1. Recommended technical solution or implementation direction")
        print("  2. Related code locations or module descriptions")
        print("  3. Technical constraints or dependencies to note")
        print("  4. Background information that other developers may not know")
        print()
        print("Example:")
        print("  This feature should be implemented in src/core/processor.py,")
        print("  you can reference the existing DataProcessor class.")
        print("  Note to maintain backward compatibility with existing API.")
        print()

        # Get development guide using prompt_multiline for better UX
        from cafe.ui.inquirer_prompts import prompt_multiline
        dev_guide = prompt_multiline("Please enter development guide (can be left empty)").strip()

        if dev_guide:
            print()
            print("✅ Development guide recorded, starting implementation planning...")
        else:
            print()
            print("ℹ️  No development guide provided, will start implementation planning directly...")
        print()

        return dev_guide

    def _prepare_user_input_for_iteration(self) -> "PhaseResult | str":
        """Prepare user input for current iteration.

        Get all needed user input at the beginning:
        - Interactive: Ask user from stdin
        - Non-interactive: Get from self.user_input

        Afterwards, processing logic is the same, no longer distinguishing interactive/non-interactive.

        Returns:
            PhaseResult: If need to end/pause phase (completed, failed, or waiting for user input)
            str: User input content (for agent use)
        """
        # Iteration 1: user_input is the dev guide content from versioned plan file
        if self.iteration == 1:
            # Always read from plan_001.md (the first versioned file)
            # Note: Iteration number is determined by history files, not by versioned plan files
            first_plan_file = self._get_versioned_file_path("plan", 1, self.phase_dir)
            return first_plan_file.read_text() if first_plan_file.exists() else ""

        # Iteration 2+: Check if current iteration was interrupted (has user_input but no response)
        current_data = self._load_current_iteration_data()
        if current_data and current_data.get("user_input") and not current_data.get("response"):
            # Restore interrupted iteration, directly use saved user_input
            return current_data["user_input"]

        # Iteration 2+: Display current plan.md content (interactive only)
        if self.interactive:
            self._display_current_plan()

        # Iteration 2+: Load previous iteration data
        prev_data = self._load_previous_iteration_data()
        if not prev_data:
            return ""

        prev_status = prev_data.get("status_code", "")

        # Get user input based on previous round status
        if prev_status == "CAFE_READY_FOR_REVIEW":
            # Need user choice: confirm/modify
            if self.interactive:
                choice = self._ask_user_for_review_decision("Implementation Plan", agent_name=self.dev_agent)
            else:
                choice = self.user_input
                # Non-interactive mode: clear after use to ensure not reused
                self.user_input = ""

                if not choice:
                    # Non-interactive but no input provided → immediate failure
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Plan phase failed after iteration {self.iteration - 1}: received READY_FOR_REVIEW in non-interactive mode without user input",
                        data={
                            "iterations": self.iteration - 1,
                            "last_response": prev_data.get("response", ""),
                            "status_code": "CAFE_READY_FOR_REVIEW",
                        },
                    )

            # Process user choice (no longer distinguish interactive/non-interactive)
            return self._process_review_decision(
                choice,
                prev_data,
                "Implementation plan",
                {"dev_agent": self.dev_agent},
            )

        elif prev_status == "CAFE_NEED_CLARIFICATION":
            return self._handle_need_clarification_input(prev_data, agent_display_name="Developer")
        else:
            return ""

    def _display_current_plan(self) -> None:
        """Display current plan file content (interactive mode only)."""
        # Display the previous version (current iteration - 1)
        prev_iteration = self.iteration - 1
        if prev_iteration > 0:
            prev_plan_file = self._get_versioned_file_path("plan", prev_iteration, self.phase_dir)
            print(f"\n💾 Loading latest plan file: {prev_plan_file}\n")
            plan_content = prev_plan_file.read_text() if prev_plan_file.exists() else "(File not generated)"
        else:
            plan_content = "(File not generated)"

        # Get agent CLI info for display
        agent_cli = self.agent_manager.get_agent_config(self.dev_agent).cli.value

        print(f"\n{'='*60}")
        print(f"Dev ({self.dev_agent} by {agent_cli}) - Current Plan Content (Iteration {self.iteration - 1}):")
        print(f"{'='*60}")
        print(plan_content)
        print(f"{'='*60}\n")

    def _get_status_analysis_prompt(self) -> str:
        """Get prompt for analyzing status code.

        Returns:
            Analysis prompt string
        """
        return f"""Please read {self.plan_file} and analyze the current state.

Based on the following conditions, determine which status code to return:

- CAFE_READY_FOR_REVIEW: Implementation plan is complete, ready for user review
- CAFE_NEED_CLARIFICATION: There are still questions that need confirmation with user

Please only return one status code (e.g., CAFE_READY_FOR_REVIEW) without any other content."""

    def _detect_written_output_files(self) -> List[Path]:
        """Check if plan file was written before failure.

        Returns:
            List[Path]: Returns list containing plan_{iteration}.md if it exists, otherwise empty list
        """
        plan_file = self._get_versioned_file_path("plan", self.iteration, self.phase_dir)
        return [Path(plan_file)] if Path(plan_file).exists() else []

    def _load_plan_config(self) -> None:
        """Load plan configuration (template) from config.yaml if exists."""
        # If template_path is already explicitly provided, don't override it
        if self.template_path:
            return

        # Path: .cafe/issues/{issue_name}/config.yaml
        config_file = self.issue_dir / "issue.yaml"

        config_data = self._read_issue_config(config_file)
        if config_data:
            # Load from plan section if exists
            plan_config = config_data.get("plan", {})

            if "template" in plan_config:
                # Resolve template name to path
                from cafe.templates.manager import TemplateManager
                template_manager = TemplateManager(".cafe")
                template_path = template_manager.get_template_path(plan_config["template"])
                if template_path and template_path.exists():
                    self.template_path = str(template_path)


