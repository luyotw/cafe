"Implementation plan phase."

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus
from cafe.ui.display import Display
from cafe.ui.interactive_qa import interactive_qa_flow
from cafe.utils.git_utils import get_repo_root
from cafe.utils.prompt_utils import format_checklist_instruction
from cafe.utils.github import GitHubOps, GitHubError


class PlanPhase(Phase):
    """Legacy compatibility implementation for the plan phase."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: "GitOperations",
        spec_file: str,
        issue_name: Optional[str] = None,
        dev_agent: str = "David",
        interactive: bool = True,
        template_path: Optional[str] = None,
        template_mode: str = "auto",
        user_input: str = "",
        sync_github: Optional[bool] = None,
    ) -> None:
        """Initialize plan phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations (for deriving issue directory from current branch)
            spec_file: Path to spec file
            issue_name: Issue name for history tracking (default: derived from current branch)
            dev_agent: Developer agent name (default: David)
            template_path: Path to plan template file (optional, used when template_mode='manual')
            template_mode: Template selection mode ('auto' or 'manual', default: 'auto')
            interactive: Whether to allow interactive prompts (default: True)
            user_input: User input for non-interactive mode (default: "")
            sync_github: Whether to sync to GitHub (None=use config default based on issue_id presence)
        """
        super().__init__(interactive=interactive, git_ops=git_ops)

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.spec_file = spec_file
        self.dev_agent = dev_agent
        self.template_path = template_path
        self.template_mode = template_mode
        self.user_input = user_input
        self.display = Display()
        self.iteration = 0
        self.phase_name = "plan"  # For base class progress tracking

        # Track sync_github setting (resolved at load time if None)
        self._sync_github_explicit = sync_github  # Store CLI-provided value
        self._sync_github: bool = False  # Default, will be set by _load_plan_config()

        # Determine issue name for history tracking (issue_dir is set by base class)
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from current branch name (via issue_dir)
            self.issue_name = self.issue_dir.name

        # Load template and sync_github from config if not explicitly provided
        self._load_plan_config()

        # Phase directory for plan phase (for versioned files)
        # Path: .cafe/issues/{issue_name}/plan
        self.phase_dir = self.issue_dir / "plan"

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

            # Increment iteration and execute agent
            self.iteration += 1

            # Get iteration number for versioned file
            iteration_number = self._get_next_iteration_number("plan", self.phase_dir)

            # Note: Copy of previous version is deferred until just before agent execution
            # to avoid creating new iterations when interrupted before agent is called

            # Calculate versioned plan file path
            self.plan_file = self._get_versioned_file_path("plan", iteration_number, self.phase_dir)

            # Check requirements file exists
            req_path = Path(self.spec_file)
            if not req_path.exists():
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"Spec file not found: {self.spec_file}",
                )

            # Check if this is first iteration (no history files or plan files)
            has_history = bool(list(self.phase_dir.glob("iteration_*/context.json")))
            is_first_iteration = not has_history

            # First iteration requires template (unless template_mode is 'auto')
            if is_first_iteration and not self.template_path and self.template_mode != "auto":
                if self.interactive:
                    # Interactive mode: prompt for template selection
                    from cafe.templates.manager import TemplateManager
                    from cafe.ui.template_selector import select_template
                    from rich.console import Console
                    console = Console()

                    template_manager = TemplateManager()
                    templates_with_source = template_manager.list_templates()

                    if not templates_with_source:
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message="No templates found. Use 'cafe template add <source> <name>' to add templates.",
                        )

                    templates = [name for name, _ in templates_with_source]
                    self.display.console.print()
                    self.display.console.print("[yellow]First iteration requires a template.[/yellow]")
                    template_paths = {name: template_manager.get_template_path(name) for name in templates}
                    selected_template = select_template(templates, template_paths)

                    if selected_template:
                        self.template_path = str(template_manager.get_template_path(selected_template))
                        self.display.console.print(f"[dim]Using template: {selected_template}[/dim]")
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

            # Generate checklist for this iteration
            from cafe.utils.checklist_generator import generate_plan_checklist
            from cafe.utils.git_utils import to_cwd_relative_path

            # Define basic principles (language selection is dynamic based on sync setting)
            language_instruction = 'the same language as the "Initial Requirements" section in the spec document, not in your native language' if self._sync_github else "your native language"
            basic_principles = f"""- Write plan content in {language_instruction}
- Only write the finalized content to output.md - do not mention iteration-related information (e.g., "confirmed in iteration 2", "verified in previous round")"""

            # Get template file path (convert to relative path for display)
            template_file = None
            if self.template_path:
                try:
                    template_file = to_cwd_relative_path(Path(self.template_path))
                except (ValueError, OSError):
                    template_file = str(self.template_path)

            checklist_path = self._get_iteration_dir(self.iteration) / "checklist.md"

            # Get previous plan file for iteration > 1
            prev_plan_file = None
            if self.iteration > 1:
                prev_iteration_num = self.iteration - 1
                prev_plan_file = str(self._get_versioned_file_path("plan", prev_iteration_num, self.phase_dir))

            # Compute questions.xml path for this iteration
            questions_xml_path = self._get_iteration_dir(self.iteration) / "questions.xml"
            try:
                questions_xml_file = to_cwd_relative_path(questions_xml_path)
            except (ValueError, OSError):
                questions_xml_file = str(questions_xml_path.resolve())

            generate_plan_checklist(
                agent_name=self.dev_agent,
                plan_file_path=str(self.plan_file),
                spec_file_path=self.spec_file,
                checklist_file_path=checklist_path,
                basic_principles=basic_principles,
                template_file=template_file,
                template_mode=self.template_mode,
                iteration=self.iteration,
                prev_plan_file=prev_plan_file,
                questions_xml_file=questions_xml_file,
            )

            # Prepare user_input for this iteration
            result_or_input = self._prepare_user_input_for_iteration()
            if isinstance(result_or_input, PhaseResult):
                # Method returned a PhaseResult (completion/failure/pause)
                return result_or_input
            # Otherwise, it's the user input string
            current_user_input = result_or_input

            # Prepare allowed tools with write/edit permission for versioned plan file and checklist
            # Convert to relative path (without / prefix) - normal relative path
            # Use path relative to current working directory (supports worktree)
            from cafe.utils.git_utils import to_cwd_relative_path

            try:
                plan_file_pattern = to_cwd_relative_path(self.plan_file)
            except ValueError:
                # Fallback to absolute path if file is not under cwd
                plan_file_pattern = str(Path(self.plan_file).resolve())

            # Get checklist path for this iteration
            iteration_dir = self._get_iteration_dir(self.iteration)
            checklist_file = iteration_dir / "checklist.md"
            try:
                checklist_pattern = to_cwd_relative_path(checklist_file)
            except ValueError:
                checklist_pattern = str(checklist_file.resolve())

            # Get questions.xml path for allowed_tools
            questions_xml_file_path = iteration_dir / "questions.xml"
            try:
                questions_xml_pattern = to_cwd_relative_path(questions_xml_file_path)
            except (ValueError, OSError):
                questions_xml_pattern = str(questions_xml_file_path.resolve())

            # Merge base tools with previous iteration's tools (if any)
            base_allowed_tools = [
                "read",
                "grep",
                "glob",
                "ls",
                "web_fetch",
                "web_search",
                f"edit({plan_file_pattern})",
                f"edit({checklist_pattern})",
                f"edit({questions_xml_pattern})",
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

            # Validate questions.xml when agent returns NEED_CLARIFICATION
            status_code = StatusCodeParser.extract(response)
            if status_code == PhaseStatusCode.NEED_CLARIFICATION:
                self._validate_and_retry_questions_xml(
                    xml_path=questions_xml_file_path,
                    agent_name=self.dev_agent,
                    allowed_tools=allowed_tools,
                )

            if result:
                return result

            # Since we removed the while loop, if result is None (meaning need to continue),
            # we should return IN_PROGRESS with the status code from response
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

    def _get_allowed_directories(self) -> List[str]:
        """Get list of allowed directories for plan phase.

        Extends base class to include plan templates directory.

        Returns:
            List of allowed directories
        """
        from cafe.utils.prompt_utils import get_template_allowed_directories

        dirs = super()._get_allowed_directories()
        template_path = Path(self.template_path) if self.template_path else None
        dirs.extend(get_template_allowed_directories(self.template_mode, template_path, "plan"))
        return dirs

    def _get_completion_data(self) -> dict:
        """Get additional data when phase completes (provided to base class's _handle_standard_status_codes).

        Returns:
            Dict containing phase-specific data, will be merged into PhaseResult.data
        """
        data = {}

        # Find the latest existing output.md file (in case current iteration doesn't have one)
        latest_output = self._get_latest_versioned_file("plan", self.phase_dir)
        if latest_output:
            data["plan_file"] = str(latest_output)
        elif hasattr(self, 'plan_file') and self.plan_file:
            data["plan_file"] = str(self.plan_file)

        return data

    def _generate_prompt(self, user_input: str) -> str:
        """Generate prompt for current iteration.

        Args:
            user_input: User's input/feedback for this iteration

        Returns:
            Prompt string
        """
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
        from cafe.agents.manager import AgentManager
        from cafe.skills.bridge import try_load_skill_body

        agent_file = AgentManager.get_agent_file_path(self.dev_agent, "developer")
        skill_body = try_load_skill_body(
            "plan",
            context={
                "agent_file": agent_file,
                "spec_file": str(spec_file_path),
                "output_file": str(plan_file_path),
                "status_code_instruction": status_code_prompt,
            },
        )
        skill_section = f"{skill_body}\n\n" if skill_body else ""

        rewrite_guardrails = """**Output Guardrails (must follow):**
- In-place rewrite only: edit the existing output file, do not append a second full draft.
- Each H1 section must appear at most once in the final file.
- If old and new content conflict, keep the latest approved intent and remove superseded text.
- Do not include "old version/new version", history logs, or duplicated plan blocks.
- Before finalizing, self-check and merge any duplicated headings or semantically duplicated paragraphs."""

        if self.iteration == 1:
            # Get checklist file path
            iteration_dir = self._get_iteration_dir(self.iteration)
            checklist_file = iteration_dir / "checklist.md"
            from cafe.utils.git_utils import to_cwd_relative_path
            try:
                checklist_path = to_cwd_relative_path(checklist_file)
            except ValueError:
                checklist_path = str(checklist_file.resolve())

            checklist_instruction = format_checklist_instruction(checklist_path)
            base_prompt = f"""# Plan Phase

{skill_section}\
**Your Role:** Developer (Planning)
Read {agent_file} to understand your complete role definition and responsibilities.

{checklist_instruction}

This is iteration {self.iteration} of implementation analysis.
Analyze {spec_file_path} and plan implementation steps.

{rewrite_guardrails}

**Output Format:**
- **CAFE_NEED_CLARIFICATION**: Append \"## Implementation Plan\" and \"## Questions to Confirm\" sections
- **CAFE_READY_FOR_REVIEW**: Append complete implementation plan following template structure

{status_code_prompt}
"""

            return base_prompt
        else:
            # Iteration 2+
            # Get checklist file path
            iteration_dir = self._get_iteration_dir(self.iteration)
            checklist_file = iteration_dir / "checklist.md"
            from cafe.utils.git_utils import to_cwd_relative_path
            try:
                checklist_path = to_cwd_relative_path(checklist_file)
            except ValueError:
                checklist_path = str(checklist_file.resolve())

            user_request_section = ""
            if user_input:
                user_request_section = f"""
**User's Modification Request:**
{user_input}
"""

            checklist_instruction = format_checklist_instruction(checklist_path)
            base_prompt = f"""# Plan Phase

{skill_section}\
**Your Role:** Developer (Planning)
Read {agent_file} to understand your complete role definition and responsibilities.

{checklist_instruction}

This is iteration {self.iteration} of implementation analysis.
Continue analyzing the latest version of {spec_file_path}.
{user_request_section}

{rewrite_guardrails}

**Output Format:**
- **CAFE_NEED_CLARIFICATION**: Update relevant sections and list questions in \"## Questions to Confirm\"
- **CAFE_READY_FOR_REVIEW**: Update sections to meet user's requirements

{status_code_prompt}
"""

            return base_prompt

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
        # Check for development guide heading (case-insensitive)
        return bool(re.search(r"##\s*Development\s+Guide", content, re.IGNORECASE))

    def _get_dev_guide_from_user(self) -> str:
        """Prompt user to provide development guide.

        Returns:
            Development guide content from user input
        """
        self.display.console.print("\n" + "="*70)
        self.display.console.print("📝 Development Guide")
        self.display.console.print("="*70)
        self.display.console.print()
        self.display.console.print("Provide implementation direction and technical background:")
        self.display.console.print("  • Technical solution/direction")
        self.display.console.print("  • Related code locations")
        self.display.console.print("  • Technical constraints or dependencies")
        self.display.console.print("  • Key background information")
        self.display.console.print()

        # Get development guide using prompt_multiline for better UX
        from cafe.ui.inquirer_prompts import prompt_multiline
        dev_guide = prompt_multiline(
            "Please enter development guide (can be left empty)\n"
            "Suggested content:\n"
            "- Technical solution/direction\n"
            "- Related code locations\n"
            "- Technical constraints or dependencies\n"
            "- Key background information\n"
            "(Press Esc + Enter to finish)"
        ).strip()

        if dev_guide:
            self.display.console.print()
            self.display.console.print("✅ Development guide recorded, starting implementation planning...")
        else:
            self.display.console.print()
            self.display.console.print("ℹ️  No development guide provided, will start implementation planning directly...")
        self.display.console.print()

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
        if current_data and not current_data.get("response"):
            # Restore interrupted iteration, load user_input from user_input.md or context.json
            user_input = self._load_user_input(self.iteration)
            if user_input:
                return user_input

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
                # Display delta from previous iteration before asking for decision
                if self.iteration > 1:
                    self._display_iteration_delta()

                prev_plan_file = self._get_versioned_file_path("plan", self.iteration - 1, self.phase_dir)
                choice = self._ask_user_for_review_decision(
                    "Implementation Plan",
                    agent_name=self.dev_agent,
                    role="developer",
                    output_file=prev_plan_file if self.iteration > 1 else None,
                    display_callback=self._display_iteration_delta if self.iteration > 1 else None,
                    edit_option_label="Edit manually - Open in editor",
                )
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
            result_or_input = self._process_review_decision(
                choice,
                prev_data,
                "Implementation plan",
                {"dev_agent": self.dev_agent},
            )
            
            # If user confirmed (returned PhaseResult with CONFIRMED status),
            # sync plan to GitHub before returning (if sync is enabled)
            if isinstance(result_or_input, PhaseResult):
                if result_or_input.data.get("status_code") == PhaseStatusCode.CONFIRMED.value:
                    if self._sync_github:
                        self._sync_plan_to_github()

            return result_or_input

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
            self.display.console.print(f"\n💾 Loading latest plan file: {prev_plan_file}\n")
            plan_content = prev_plan_file.read_text() if prev_plan_file.exists() else "(File not generated)"
        else:
            plan_content = "(File not generated)"

        # Get agent CLI info for display
        agent_cli = self.agent_manager.get_agent_config(self.dev_agent).cli.value

        self.display.console.print(f"\n{'='*60}")
        self.display.console.print(f"Dev ({self.dev_agent} by {agent_cli}) - Current Plan Content (Iteration {self.iteration - 1}):")
        self.display.console.print(f"{'='*60}")
        self.display.console.print(plan_content)
        self.display.console.print(f"{'='*60}\n")

    def _display_iteration_delta(self) -> None:
        """Display changes from previous iteration."""
        from cafe.ui.cli import _display_iteration_delta

        # Get previous iteration's file path (which is the current READY_FOR_REVIEW version)
        prev_iteration = self.iteration - 1
        prev_plan_file = self._get_versioned_file_path("plan", prev_iteration, self.phase_dir)

        _display_iteration_delta(
            prev_iteration,
            str(prev_plan_file),
            self.display.console,
        )

    def _ask_user_for_clarification(self, role: str = "developer") -> str:
        """Ask user for answer to NEED_CLARIFICATION using interactive Q&A when available.

        Overrides base class to check for questions.xml from previous iteration.
        If found and valid, uses interactive_qa_flow(); otherwise falls back to base class
        fallback which shows a prompt with optional chat.

        Returns:
            str: User's answer
        """
        from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml

        # Look for questions.xml in the previous iteration directory
        if self.iteration > 1:
            prev_iter_dir = self._get_iteration_dir(self.iteration - 1)
            xml_path = prev_iter_dir / "questions.xml"

            if xml_path.exists() and validate_questions_xml(xml_path):
                questions = parse_questions_xml(xml_path)
                return interactive_qa_flow(questions, role=role, issue_name=self.issue_name, agent_name=self.dev_agent)

        # Fallback to base class prompt with chat option
        return super()._ask_user_for_clarification(role=role, agent_name=self.dev_agent)

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
        """Load plan configuration (template, sync_github) from issue.yaml if exists."""
        # Path: .cafe/issues/{issue_name}/issue.yaml
        config_file = self.issue_dir / "issue.yaml"

        config_data = self._read_issue_config(config_file)
        if config_data:
            # Load from plan section if exists
            plan_config = config_data.get("plan", {})
            spec_config = config_data.get("spec", {})

            # Load template if not explicitly provided
            if not self.template_path and "template" in plan_config:
                template_name = plan_config["template"]
                # If template is 'auto', set template_mode and skip path resolution
                if template_name == "auto":
                    self.template_mode = "auto"
                    self.template_path = None
                else:
                    # Resolve template name to path
                    from cafe.templates.manager import TemplateManager
                    template_manager = TemplateManager()
                    template_path = template_manager.get_template_path(template_name)
                    if template_path and template_path.exists():
                        self.template_path = str(template_path)
                        self.template_mode = "manual"

            # Load sync_github from plan section
            from cafe.utils.config import resolve_sync_github_config

            self._sync_github = resolve_sync_github_config(
                cli_value=self._sync_github_explicit,
                config_value=bool(plan_config["sync_github"]) if "sync_github" in plan_config else None,
                has_issue_id=bool(spec_config.get("issue_id"))
            )
        else:
            # No config file: use CLI value if provided, otherwise default to False
            from cafe.utils.config import resolve_sync_github_config

            self._sync_github = resolve_sync_github_config(
                cli_value=self._sync_github_explicit,
                config_value=None,
                has_issue_id=False
            )

    def _sync_plan_to_github(self) -> None:
        """Sync confirmed plan to GitHub issue as a comment.

        Only syncs when:
        1. An associated GitHub issue ID exists in issue.yaml
        2. User has confirmed the plan (CAFE_CONFIRMED status)
        """
        # Path: .cafe/issues/{issue_name}/issue.yaml
        config_file = self.issue_dir / "issue.yaml"
        
        # Try to get issue_id from top level first, then from spec section
        issue_id = self._get_issue_config_value(config_file, "issue_id")
        if not issue_id:
            issue_id = self._get_issue_config_value(config_file, "spec.issue_id")
            
        if not issue_id:
            return

        try:
            # Get latest plan file content
            # If self.plan_file is set, use it; otherwise find the latest versioned file
            plan_path = None
            if hasattr(self, 'plan_file') and self.plan_file and self.plan_file.exists():
                plan_path = self.plan_file
            else:
                plan_path = self._get_latest_versioned_file("plan", self.phase_dir)
                
            if not plan_path or not plan_path.exists():
                return

            plan_content = plan_path.read_text(encoding="utf-8")
            
            # Format comment body
            comment_body = f"### 📝 Implementation Plan (Confirmed)\n\n{plan_content}"

            # Post comment to GitHub issue
            gh_ops = GitHubOps()
            if not gh_ops.check_gh_installed():
                return
                
            if not gh_ops.check_gh_auth():
                self.display.console.print(f"[yellow]Warning: gh CLI not authenticated, skipping plan sync to GitHub issue #{issue_id}[/yellow]")
                return

            self.display.console.print(f"Syncing plan to GitHub issue #{issue_id}...")
            gh_ops.add_issue_comment(str(issue_id), comment_body)
            self.display.console.print(f"[green]✅ Plan synced to GitHub issue #{issue_id} as a comment.[/green]")

        except GitHubError as e:
            # Log error but don't fail the phase
            self.display.console.print(f"[yellow]Warning: Failed to sync plan to GitHub: {e}[/yellow]")
        except Exception as e:
            # Log unexpected errors but don't fail
            self.display.console.print(f"[yellow]Warning: Unexpected error during GitHub sync: {e}[/yellow]")
