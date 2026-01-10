"""Base class for workflow phases."""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, TokenUsage


class Phase(ABC):
    """Abstract base class for all workflow phases.

    Each phase represents a step in the CAFE workflow (e.g., requirements clarification,
    implementation analysis, development, code review, etc.).

    Subclasses must implement the execute() method to define the phase's behavior.
    
    Attributes:
        interactive: Whether to allow interactive user prompts (default: True)
    """

    def __init__(self, interactive: bool = True, git_ops: Optional["GitOperations"] = None):
        """Initialize phase with common attributes.

        Args:
            interactive: Whether to allow interactive user prompts
            git_ops: Git operations (optional, for automatic issue_dir setup)
        """
        self.interactive = interactive

        # Automatically set issue_dir from current branch if git_ops is provided
        if git_ops is not None:
            self.issue_dir = self._get_issue_dir(git_ops)
    
    def _handle_exception_in_execute(self, e: Exception, default_message: str = "Phase failed") -> PhaseResult:
        """Unified exception handling for phase execute().
        
        This method should be called in the except Exception block of each phase's execute().
        It checks if it is CriticalPhaseError, if so re-raises, otherwise returns FAILED result.
        
        Args:
            e: Caught exception
            default_message: Default error message prefix
            
        Returns:
            PhaseResult with FAILED status (only for non-critical errors)
            
        Raises:
            CriticalPhaseError: Re-raise if critical error
        """
        from cafe.core.types import CriticalPhaseError
        
        # Re-raise critical errors to stop the workflow
        if isinstance(e, CriticalPhaseError):
            raise e
        
        # Non-critical error - return FAILED result
        return PhaseResult(
            status=PhaseStatus.FAILED,
            message=f"{default_message}: {e}",
            data={},
        )

    @abstractmethod
    def execute(self) -> PhaseResult:
        """Execute the phase and return the result.

        Returns:
            PhaseResult containing the status and any relevant data

        Raises:
            Any exceptions from phase execution will propagate to the caller
        """
        pass

    def _save_user_input(
        self,
        user_input: str,
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save user input at the start of an iteration.

        Save user_input at the beginning of each round. This ensures:
        1. Even if agent execution fails, user_input is recorded
        2. Next round can read previous round's user_input from history
        3. History file completely records start (user input) and end (agent response) of each round

        Args:
            user_input: User input at the start of this round
            phase_specific_data: Phase-specific initial data (optional)
        """
        # Ensure phase_dir exists
        if not hasattr(self, "phase_dir"):
            raise AttributeError(
                "Phase must have 'phase_dir' attribute to use _save_user_input"
            )

        # Ensure iteration exists
        if not hasattr(self, "iteration"):
            raise AttributeError(
                "Phase must have 'iteration' attribute to use _save_user_input"
            )

        # Create iteration directory
        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create initial context data
        context_data: Dict[str, Any] = {
            "iteration": self.iteration,
            "timestamp": datetime.now().astimezone().isoformat(),
            "user_input": user_input,
        }

        # Add phase-specific initial data
        if phase_specific_data:
            context_data.update(phase_specific_data)

        # Save as context.json file
        context_file = iteration_dir / "context.json"
        with open(context_file, "w", encoding="utf-8") as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)

    def _update_iteration_history(
        self,
        phase_specific_data: Dict[str, Any],
        prompt: Optional[str] = None,
        agent_cli: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        denied_tools: Optional[List[str]] = None,
        cli_command_args: Optional[List[str]] = None,
        status_code: Optional[PhaseStatusCode] = None,
    ) -> None:
        """Update iteration history with agent response and metadata.

        After agent responds, update existing history file.

        Args:
            phase_specific_data: Phase-specific data (such as response etc.)
            prompt: Actual prompt received by agent
            agent_cli: CLI tool used by agent (e.g. "copilot", "claude")
            agent_session_id: Agent's session ID
            allowed_tools: List of tools available to agent
            denied_tools: List of tools unavailable to agent
            cli_command_args: CLI command argument list (excluding prompt)
            status_code: Phase status code (e.g. CONFIRMED, NEED_CLARIFICATION)
        """
        # Ensure phase_dir exists
        if not hasattr(self, "phase_dir"):
            raise AttributeError(
                "Phase must have 'phase_dir' attribute to use _update_iteration_history"
            )

        # Get iteration directory and context file
        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        context_file = iteration_dir / "context.json"

        # Read existing context data
        if context_file.exists():
            with open(context_file, "r", encoding="utf-8") as f:
                context_data = json.load(f)
        else:
            # If file does not exist, create basic structure
            context_data = {
                "iteration": self.iteration,
                "timestamp": datetime.now().astimezone().isoformat(),
            }

        # Update phase-specific data
        context_data.update(phase_specific_data)

        # Ensure required fields exist (if phase_specific_data does not provide, set to default values)
        # These fields are very important for debugging and tracking, should always exist
        if "response" not in context_data:
            context_data["response"] = None
        if "permission_denials" not in context_data:
            context_data["permission_denials"] = []
        if "streaming_log" not in context_data:
            context_data["streaming_log"] = []

        # Update shared agent metadata
        context_data["prompt"] = prompt
        context_data["cli"] = agent_cli
        context_data["session_id"] = agent_session_id
        context_data["allowed_tools"] = allowed_tools
        context_data["denied_tools"] = denied_tools
        context_data["cli_command_args"] = cli_command_args
        context_data["status_code"] = status_code.value if status_code is not None else None

        # Save updated context.json file
        with open(context_file, "w", encoding="utf-8") as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)

        # Save streaming JSONL file (if streaming data exists)
        streaming_log = context_data.get("streaming_log", [])
        if streaming_log:
            try:
                self._save_streaming_jsonl(streaming_log)
            except Exception as e:
                # Log error but don't interrupt flow (JSONL save failure shouldn't affect main functionality)
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to save streaming JSONL: {e}")

        # Append one record to iterations.jsonl
        iteration_index_data = {
            "iteration": self.iteration,
            "timestamp": context_data.get("timestamp", datetime.now().astimezone().isoformat()),
            "status": status_code.value if status_code is not None else None,
            "has_error": "error" in context_data,
        }
        try:
            self._append_iteration_index(iteration_index_data)
        except Exception as e:
            # Log error but don't interrupt flow
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to append iteration index: {e}")

    def _save_streaming_jsonl(self, streaming_log: List[str]) -> None:
        """Save streaming fragments as JSONL format file.

        Save each fragment in streaming_log as JSONL format (one JSON object per line),
        saved to iteration_XXX/streaming.jsonl.

        Args:
            streaming_log: List of streaming fragments
        """
        # If streaming_log is empty, don't create file
        if not streaming_log:
            return

        # Ensure phase_dir exists
        if not hasattr(self, "phase_dir"):
            raise AttributeError(
                "Phase must have 'phase_dir' attribute to use _save_streaming_jsonl"
            )

        # Get iteration directory
        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create JSONL file path
        jsonl_file = iteration_dir / "streaming.jsonl"

        # Write JSONL file (one JSON object per line)
        with open(jsonl_file, "w", encoding="utf-8") as f:
            for index, content in enumerate(streaming_log):
                # Create JSON object
                json_obj = {
                    "index": index,
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "content": content
                }
                # Write one line of JSON (without indentation)
                f.write(json.dumps(json_obj, ensure_ascii=False) + "\n")

    def _get_iteration_dir(self, iteration: int) -> Path:
        """Get iteration directory path.

        Args:
            iteration: iteration number

        Returns:
            Path object of iteration directory, format is iteration_XXX/
        """
        if not hasattr(self, "phase_dir"):
            raise AttributeError(
                "Phase must have 'phase_dir' attribute to use _get_iteration_dir"
            )

        return Path(self.phase_dir) / f"iteration_{iteration:03d}"

    def _append_iteration_index(self, iteration_data: dict) -> None:
        """Append one iteration record to iterations.jsonl.

        Args:
            iteration_data: iteration data dictionary, should contain iteration, timestamp, status, has_error fields
        """
        if not hasattr(self, "phase_dir"):
            raise AttributeError(
                "Phase must have 'phase_dir' attribute to use _append_iteration_index"
            )

        iterations_file = Path(self.phase_dir) / "iterations.jsonl"

        # Append one line of JSON to end of file
        with open(iterations_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(iteration_data, ensure_ascii=False) + "\n")

    def _read_iterations_index(self) -> List[dict]:
        """Read all records from iterations.jsonl.

        Returns:
            List of iteration records, each element is a dictionary
        """
        if not hasattr(self, "phase_dir"):
            raise AttributeError(
                "Phase must have 'phase_dir' attribute to use _read_iterations_index"
            )

        iterations_file = Path(self.phase_dir) / "iterations.jsonl"

        # If file doesn't exist, return empty list
        if not iterations_file.exists():
            return []

        # Read all lines and parse as JSON
        result = []
        content = iterations_file.read_text(encoding="utf-8").strip()

        # Handle empty file
        if not content:
            return []

        for line in content.split("\n"):
            if line.strip():  # Ignore empty lines
                result.append(json.loads(line))

        return result


    def _check_empty_response(self, response: str) -> Optional[PhaseStatusCode]:
        """Check if agent response is empty, if empty return NO_RESPONSE status code.

        This is a common helper method that all phases can use.

        Args:
            response: Agent's response content

        Returns:
            If response is empty (empty string or only whitespace), return PhaseStatusCode.NO_RESPONSE
            Otherwise return None
        """
        if not response or not response.strip():
            return PhaseStatusCode.NO_RESPONSE
        return None

    def _execute_agent_iteration(
        self,
        agent_name: str,
        prompt: str,
        user_input: str,
        valid_status_codes: List[PhaseStatusCode],
        allowed_tools: Optional[List[str]] = None,
        denied_tools: Optional[List[str]] = None,
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, Optional[PhaseStatusCode]]:
        """Common agent execution flow that all phases can use.

        This method encapsulates the standard process of executing agent:
        1. Save user_input to history
        2. Get agent metadata
        3. Save prompt to history
        4. Execute agent
        5. Check empty response
        6. Extract status code
        7. Update history
        8. Save progress

        Args:
            agent_name: Agent name (e.g. pm_agent, dev_agent)
            prompt: Prompt to send to agent
            user_input: User input for this round
            valid_status_codes: Valid status codes accepted by this phase
            allowed_tools: Tools available to agent (default None)
            denied_tools: Tools unavailable to agent (default None)
            phase_specific_data: Phase-specific initial data (default None)

        Returns:
            tuple[response, status_code]:
                - response: Agent's response content
                - status_code: Extracted status code, None if not found

        Raises:
            AttributeError: If phase lacks required attributes（phase_dir, iteration, agent_manager）
        """
        # Check required attributes
        if not hasattr(self, "phase_dir"):
            raise AttributeError("Phase must have 'phase_dir' attribute")
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "agent_manager"):
            raise AttributeError("Phase must have 'agent_manager' attribute")

        # 1. Save user_input to history
        self._save_user_input(
            user_input=user_input,
            phase_specific_data=phase_specific_data or {},
        )

        # 2. Get agent metadata
        agent_executor = self.agent_manager.get_agent(agent_name)
        agent_cli = agent_executor.config.cli.value
        agent_session_id = agent_executor.config.session_id

        # 3. Save prompt to context.json (before executing agent)
        iteration_dir = self._get_iteration_dir(self.iteration)
        context_file = iteration_dir / "context.json"
        if context_file.exists():
            with open(context_file, "r", encoding="utf-8") as f:
                context_data = json.load(f)
            context_data["prompt"] = prompt
            context_data["cli"] = agent_cli
            context_data["session_id"] = agent_session_id
            context_data["allowed_tools"] = allowed_tools
            context_data["denied_tools"] = denied_tools
            with open(context_file, "w", encoding="utf-8") as f:
                json.dump(context_data, f, ensure_ascii=False, indent=2)

        # 4. Execute agent (with error recovery)
        try:
            response, token_usage, permission_denials, cli_command_args, streaming_log = self.agent_manager.execute(
                agent_name,
                prompt,
                allowed_tools=allowed_tools,
                allowed_directories=self._get_allowed_directories(),
            )
        except Exception as e:
            # Agent execution failed - attempt recovery
            from cafe.agents.executor import AgentExecutionError
            from cafe.core.types import CriticalPhaseError

            print(f"⚠️  Agent execution failed: {e}")

            # 4a. Check if it's a critical error - fail immediately without recovery
            is_critical_error = (
                isinstance(e, AgentExecutionError) and 
                hasattr(e, "error_type") and 
                e.error_type in ("rate_limit", "cli_not_found")
            )
            
            if is_critical_error:
                print(f"❌ Critical error detected ({e.error_type}) - stopping execution\n")

                # Update iteration context with error info
                if context_file.exists():
                    with open(context_file, "r", encoding="utf-8") as f:
                        context_data = json.load(f)

                    context_data["response"] = None
                    context_data["status_code"] = None
                    context_data["error"] = str(e)
                    context_data["error_type"] = e.error_type
                    context_data["is_critical"] = True

                    with open(context_file, "w", encoding="utf-8") as f:
                        json.dump(context_data, f, ensure_ascii=False, indent=2)

                # Create a CriticalError wrapper to signal this should stop the workflow
                raise CriticalPhaseError(
                    message=str(e),
                    error_type=e.error_type,
                    phase_name=getattr(self, '__class__', type(self)).__name__
                ) from e

            # 4b. Check if agent wrote output files
            written_files = self._detect_written_output_files()

            # 4c. Attempt to recover response from written files
            recovered_response, recovered_status_code = self._recover_from_written_files(
                written_files,
                valid_status_codes,
            )

            # 4d. Create error.json file for debugging
            iteration_dir = self._get_iteration_dir(self.iteration)
            iteration_dir.mkdir(parents=True, exist_ok=True)
            error_file = iteration_dir / "error.json"
            error_data = {
                "error": str(e),
                "error_type": type(e).__name__,
                "is_critical": isinstance(e, CriticalPhaseError),
                "timestamp": datetime.now().astimezone().isoformat(),
                "written_files": [str(f) for f in written_files],
                "recovered_response": bool(recovered_response),
                "recovered_status": recovered_status_code.value if recovered_status_code else None,
            }
            with open(error_file, "w", encoding="utf-8") as f:
                json.dump(error_data, f, ensure_ascii=False, indent=2)

            if recovered_response and recovered_status_code:
                # 4e. Recovery successful - treat as partial success
                print(f"✅ Recovered response from {written_files[0].name}")
                print(f"   Status code: {recovered_status_code.value}")

                response = recovered_response
                status_code = recovered_status_code
                permission_denials = []  # No permission information
                token_usage = TokenUsage()  # Empty token usage

                # Add recovery metadata to iteration history
                phase_specific_data = phase_specific_data or {}
                phase_specific_data["response"] = response
                phase_specific_data["permission_denials"] = []
                phase_specific_data["recovered_from_error"] = True
                phase_specific_data["original_error"] = str(e)

                # Update history（Including recovered response and status）
                # Record actual CLI arguments if possible (if error object has them)
                cli_args: List[str] = []
                if isinstance(e, AgentExecutionError) and hasattr(e, "cli_command_args"):
                    cli_args = getattr(e, "cli_command_args") or []

                self._update_iteration_history(
                    phase_specific_data=phase_specific_data,
                    prompt=prompt,
                    agent_cli=agent_cli,
                    agent_session_id=agent_session_id,
                    allowed_tools=allowed_tools,
                    denied_tools=denied_tools,
                    cli_command_args=cli_args,
                    status_code=status_code,
                )

                # Save progress
                if hasattr(self, "_save_progress"):
                    self._save_progress(status_code)

                # Return recovered result
                return response, status_code
            else:
                # 4f. Recovery failed - Update history and re-raise
                print(f"❌ Could not recover from error")

                # Update iteration history including error information and CLI arguments
                if context_file.exists():
                    with open(context_file, "r", encoding="utf-8") as f:
                        history_data = json.load(f)

                    history_data["response"] = None
                    history_data["status_code"] = None
                    history_data["error"] = str(e)

                    # Record error type (if any)
                    if isinstance(e, AgentExecutionError) and hasattr(e, "error_type"):
                        history_data["error_type"] = e.error_type

                    # Record actually used CLI command arguments if possible
                    if isinstance(e, AgentExecutionError) and hasattr(e, "cli_command_args"):
                        history_data["cli_command_args"] = getattr(e, "cli_command_args")
                    else:
                        # Explicitly mark as None, convenient for subsequent debugging to see "no available CLI arguments"
                        history_data.setdefault("cli_command_args", None)

                    with open(context_file, "w", encoding="utf-8") as f:
                        json.dump(history_data, f, ensure_ascii=False, indent=2)

                # Re-raise to let phase handle failure
                raise

        # 5. Check empty response
        no_response_status = self._check_empty_response(response)
        if no_response_status:
            # Agent returned empty response - save and return NO_RESPONSE
            self._update_iteration_history(
                phase_specific_data={
                    "response": response,
                    "permission_denials": [denial.model_dump() for denial in permission_denials],
                    "streaming_log": streaming_log
                },
                prompt=prompt,
                agent_cli=agent_cli,
                agent_session_id=agent_session_id,
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                cli_command_args=cli_command_args,
                status_code=no_response_status,
            )
            return response, no_response_status

        # 6. Extract status code
        from cafe.core.status_codes import StatusCodeParser
        status_code = StatusCodeParser.extract(
            response,
            valid_codes=valid_status_codes,
        )

        # 6.1. Check if there are multiple different status codes
        all_status_codes = StatusCodeParser.extract_all(response, valid_codes=valid_status_codes)
        has_multiple_codes = len(all_status_codes) > 1

        # 6.2. If no status code (including case of multiple status codes), send continue message
        # Because multiple status codes also means status is unclear, needs agent confirmation
        # Maximum 5 retries
        analysis_attempted = False
        analysis_response = None
        original_status_code_missing = (status_code is None)  # Record original status
        max_retries = 5
        retry_count = 0

        if original_status_code_missing:  # Including: no status code or multiple status codes
            analysis_attempted = True

            while retry_count < max_retries and status_code is None:
                retry_count += 1
                print(f"\n⚠️  Agent response has no status code, sending continue message... (attempt {retry_count}/{max_retries})")

                try:
                    # Send continue prompt and attach original prompt as reference
                    # This way even if session is recreated, agent can understand complete context
                    continue_prompt = f"If completed respond with status code, if not continue\n\nBelow is the original task description for reference:\n\n{prompt}"
                    continue_response, _, _, _, _ = self.agent_manager.execute(
                        agent_name,
                        continue_prompt,
                        allowed_tools=allowed_tools,
                        allowed_directories=self._get_allowed_directories(),
                    )

                    # Try to extract status code from continue response
                    continue_status_code = StatusCodeParser.extract(
                        continue_response,
                        valid_codes=valid_status_codes,
                    )

                    if continue_status_code:
                        print(f"✅ Obtained status code after continue execution: {continue_status_code.value}")
                        # Merge original response and continue response
                        response = response + "\n\n" + continue_response
                        status_code = continue_status_code
                        analysis_response = continue_response
                        break
                    else:
                        print(f"⚠️  Still no status code after continue execution")
                        analysis_response = continue_response

                except Exception as e:
                    print(f"⚠️  Failed to send continue message: {e}")
                    analysis_response = None

        # 6.3. Write error log (if original response has issues: no status code, multiple codes, or still no status code after analysis)
        # Note: Even if analysis successfully finds status code, we still record issues with original response
        if analysis_attempted or original_status_code_missing:
            self._write_status_code_error_log(
                original_response=response,
                valid_status_codes=valid_status_codes,
                status_code=status_code,
                analysis_attempted=analysis_attempted,
                analysis_response=analysis_response,
                cli_command_args=cli_command_args,
                multiple_codes_found=list(all_status_codes) if has_multiple_codes else None,
            )

        # 6.4. If still no status code after 5 retries, throw error
        if original_status_code_missing and status_code is None and retry_count >= max_retries:
            error_msg = f"Agent Still did not return valid status code after {max_retries} attempts"
            print(f"\n❌ {error_msg}")
            raise ValueError(error_msg)

        # 7. Update history (save metadata only, NOT response/status_code yet)
        # Response and status_code will be saved after checklist validation passes
        phase_data = {
            "response": response,
            "permission_denials": [denial.model_dump() for denial in permission_denials],
            "streaming_log": streaming_log
        }

        # If status code analysis was performed, record to history
        if analysis_attempted:
            phase_data["status_code_analyzed"] = True
            if analysis_response:
                phase_data["status_code_analysis_response"] = analysis_response

        self._update_iteration_history(
            phase_specific_data=phase_data,
            prompt=prompt,
            agent_cli=agent_cli,
            agent_session_id=agent_session_id,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            cli_command_args=cli_command_args,
            status_code=None,  # Don't save status_code yet - will be saved after checklist validation
        )

        # 8. Don't save progress yet - will be saved after checklist validation
        # (Removed: _save_progress will be called after checklist validation)

        return response, status_code

    def _ask_user_for_review_decision(self, item_name: str = "content", agent_name: str = "Developer") -> str:
        """Ask user for decision on READY_FOR_REVIEW (interactive mode).

        Args:
            item_name: Item name to confirm (e.g. "plan", "code", "requirements")
            agent_name: Agent name (e.g. "Developer", "PM", "Reviewer")

        Returns:
            str: "confirm" or modification opinion content
        """
        from cafe.ui.inquirer_prompts import prompt_list, prompt_multiline

        print(f"{agent_name} thinks {item_name} is complete. Please confirm:")

        # Use prompt_list for better UX with arrow keys
        choices = [
            {"name": "Confirm - Confirm, continue", "value": "c"},
            {"name": "Modify - Request modification", "value": "m"},
        ]

        choice = prompt_list(
            "Please select an option",
            choices,
            default=None,
        )

        if choice == "c":
            return "confirm"
        else:  # choice == "m"
            modification_request = prompt_multiline("Please enter modification opinion")

            if not modification_request.strip():
                print("\n⚠️  No modification opinion entered, please try again.")
                return self._ask_user_for_review_decision(item_name, agent_name)

            print()
            print("✅ Received your modification opinion...")
            print()

            return modification_request

    def _ask_user_for_clarification(self) -> str:
        """Ask user for answer to NEED_CLARIFICATION (interactive mode).

        Returns:
            str: User's answer
        """
        from cafe.ui.inquirer_prompts import prompt_multiline

        return prompt_multiline("Please answer the question")

    def _process_review_decision(
        self,
        choice: str,
        prev_data: Dict[str, Any],
        phase_name: str,
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> "PhaseResult | str":
        """Handle user's decision on READY_FOR_REVIEW.

        Args:
            choice: "confirm" or modification opinion content
            prev_data: Previous round iteration data
            phase_name: Phase name (for messages, e.g. "Implementation plan", "Requirements")
            phase_specific_data: Phase-specific data (for saving history)

        Returns:
            PhaseResult: If confirm
            str: If requesting modification, return modification opinion
        """
        if choice == "confirm":
            # Save user confirmation as a new iteration
            self._save_user_input(
                user_input="confirm",
                phase_specific_data=phase_specific_data or {},
            )
            self._update_iteration_history(
                phase_specific_data={
                    "response": "User confirmed",
                    "user_action": "confirm",
                },
                prompt="",
                agent_cli=None,
                agent_session_id=None,
                allowed_tools=None,
                status_code=PhaseStatusCode.CONFIRMED,
            )
            self._save_progress(PhaseStatusCode.CONFIRMED)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"{phase_name} completed in {self.iteration} iteration(s)",
                data={
                    "iterations": self.iteration,
                    "final_response": prev_data.get("response", ""),
                    "status_code": PhaseStatusCode.CONFIRMED.value,
                },
            )
        else:
            # choice is the modification request
            return choice

    def _get_status_file(self) -> Path:
        """Get the path to status.json file.

        Returns:
            Path to status.json in {phase_dir}/status.json

        For different phases:
            - SpecPhase: .cafe/issues/myissue/spec/status.json
            - PlanPhase: .cafe/issues/myissue/plan/status.json
            - DevelopPhase: .cafe/issues/myissue/develop/status.json
        """
        if not hasattr(self, "phase_dir"):
            raise AttributeError("Phase must have 'phase_dir' attribute")
        return Path(self.phase_dir) / "status.json"

    def _detect_written_output_files(self) -> List[Path]:
        """Detect if agent wrote output files before failure.

        Base implementation returns empty list (No recovery).
        Subclasses should override this method to check phase-specific output files.

        Returns:
            List[Path]: List of file paths written by agent
        """
        return []

    def _recover_from_written_files(
        self,
        written_files: List[Path],
        valid_status_codes: List[PhaseStatusCode],
    ) -> tuple[Optional[str], Optional[PhaseStatusCode]]:
        """Attempt to recover agent response from written files.

        Args:
            written_files: List of files written by agent before failure
            valid_status_codes: Valid status codes for this phase

        Returns:
            Tuple[Optional[str], Optional[PhaseStatusCode]]:
                - recovered_response: Recovered response content
                - extracted_status_code: Status code extracted from file
                Return if recovery fails (None, None)
        """
        if not written_files:
            return None, None

        # Read first (primary) output file
        try:
            primary_file = written_files[0]
            recovered_response = primary_file.read_text(encoding="utf-8")

            # Extract status code from file content
            from cafe.core.status_codes import StatusCodeParser
            status_code = StatusCodeParser.extract(
                recovered_response,
                valid_codes=valid_status_codes,
            )

            return recovered_response, status_code
        except Exception as e:
            # Log recovery failure but do not raise
            print(f"⚠️  Failed to recover from written files: {e}")
            return None, None

    def _save_progress(self, status_code: PhaseStatusCode) -> None:
        """Save phase progress to status.json (common method).

        Args:
            status_code: Phase status code (CONFIRMED, READY_FOR_REVIEW, NEED_CLARIFICATION, etc.)
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "phase_name"):
            raise AttributeError("Phase must have 'phase_name' attribute (e.g., 'spec', 'plan')")

        status_file = self._get_status_file()
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Determine phase status based on status code
        complete_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.READY_FOR_REVIEW]
        phase_status = PhaseStatus.COMPLETED if status_code in complete_codes else PhaseStatus.IN_PROGRESS

        # Set end_time when phase completes
        end_time = datetime.now().astimezone() if phase_status == PhaseStatus.COMPLETED else None

        progress = PhaseProgress(
            phase=self.phase_name,
            status=phase_status,
            status_code=status_code.value,
            timestamp=datetime.now().astimezone(),
            iteration=self.iteration,
            message=f"Phase completed with {status_code.value}" if phase_status == PhaseStatus.COMPLETED else f"Iteration {self.iteration}",
            end_time=end_time,
        )

        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

    def _load_progress(self) -> Optional["PhaseProgress"]:
        """Load phase progress from status.json (common method).

        Returns:
            PhaseProgress if file exists, None otherwise
        """
        status_file = self._get_status_file()
        if not status_file.exists():
            return None

        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        from cafe.core.types import PhaseProgress
        return PhaseProgress.from_dict(data)

    def _get_phase_timestamp(self, phase_name: str) -> Optional[str]:
        """Get the timestamp of the latest iteration for a specified phase.

        Args:
            phase_name: Name of the phase (e.g., 'develop', 'review', 'plan')

        Returns:
            ISO format timestamp string if phase status exists, None otherwise
        """
        phase_status_file = self.issue_dir / phase_name / "status.json"
        if not phase_status_file.exists():
            return None

        try:
            with open(phase_status_file, 'r', encoding='utf-8') as f:
                phase_status = json.load(f)
            return phase_status.get("timestamp")
        except (json.JSONDecodeError, KeyError, IOError):
            return None

    def _print_token_usage_summary(self) -> None:
        """Display token usage summary (common method).

        This method gets total token usage from agent_manager and displays summary.
        Suitable for displaying cost statistics when phase completes.
        """
        if not hasattr(self, "agent_manager"):
            return

        token_usage = self.agent_manager.get_total_token_usage()

        print()
        print("=" * 60)
        print("📊 Token Usage Summary")
        print("=" * 60)
        print(f"Input tokens:              {token_usage.input_tokens:,}")
        print(f"Output tokens:             {token_usage.output_tokens:,}")
        print(f"Cache creation tokens:     {token_usage.cache_creation_input_tokens:,}")
        print(f"Cache read tokens:         {token_usage.cache_read_input_tokens:,}")
        print(f"Total cost:                ${token_usage.total_cost_usd:.4f}")
        print("=" * 60)
        print()

    def _handle_need_clarification_input(
        self,
        prev_data: dict,
        agent_display_name: str = "agent",
    ) -> Any:
        """Handle user input for NEED_CLARIFICATION status (common method).

        This method encapsulates standard processing flow for NEED_CLARIFICATION status:
        1. Interactive mode: Call _ask_user_for_clarification() to prompt user input
        2. Non-interactive mode: Use self.user_input (clear after use)
        3. Validate user input is not empty
        4. Return user input or PhaseResult (error status)

        Args:
            prev_data: Previous round iteration data (read from history JSON)
            agent_display_name: Agent display name (e.g. "PM", "Developer"), used for confirmation messages

        Returns:
            str: User-entered clarification
            PhaseResult: If failed (FAILED)
        """
        # Check required attributes
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "interactive"):
            raise AttributeError("Phase must have 'interactive' attribute")
        if not hasattr(self, "user_input"):
            raise AttributeError("Phase must have 'user_input' attribute")

        # Need user to answer question
        if self.interactive:
            if not hasattr(self, "_ask_user_for_clarification"):
                raise AttributeError("Phase must implement '_ask_user_for_clarification' method")
            clarification = self._ask_user_for_clarification()
        else:
            clarification = self.user_input
            # Non-interactive mode: Clear after use to ensure no reuse
            self.user_input = ""
            
            if not clarification:
                # Non-interactive but no input provided → Fail immediately
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"{self.phase_name.capitalize()} phase failed after iteration {self.iteration - 1}: received NEED_CLARIFICATION in non-interactive mode without user input",
                    data={
                        "iterations": self.iteration - 1,
                        "last_response": prev_data.get("response", ""),
                        "status_code": "CAFE_NEED_CLARIFICATION",
                    },
                )

        # Handle user answer (no longer distinguish interactive/non-interactive)
        if not clarification.strip():
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message="User provided no response to clarification questions",
                data={
                    "iterations": self.iteration - 1,
                    "last_response": prev_data.get("response", ""),
                },
            )

        if self.interactive:
            print()
            print(f"✅ Received your answer, sending to {agent_display_name} for processing...")
            print()

        return clarification

    def _load_previous_iteration_data(self) -> Optional[dict]:
        """Load previous round iteration data (common method).

        Returns:
            dict: Previous round iteration data, return None if not exists
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "phase_dir"):
            raise AttributeError("Phase must have 'phase_dir' attribute")

        if self.iteration == 1:
            return None

        prev_iteration_dir = self._get_iteration_dir(self.iteration - 1)
        prev_context_file = prev_iteration_dir / "context.json"
        if not prev_context_file.exists():
            return None

        with open(prev_context_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _merge_allowed_tools(
        self,
        base_allowed_tools: List[str],
        approved_tools_from_denials: Optional[List[str]] = None,
    ) -> List[str]:
        """Merge base allowed tools and previous round's allowed tools (common method).

        This method will:
        1. Read allowed_tools from previous iteration
        2. Filter out file-specific tools from previous iteration (e.g., edit(file_path), write(file_path))
        3. Merge base_allowed_tools + filtered previous tools + newly approved tools
        4. Remove duplicates

        Args:
            base_allowed_tools: Base tool list for this phase
            approved_tools_from_denials: Newly approved tools from this round's permission denials (default empty list)

        Returns:
            Merged and deduplicated tool list
        """
        approved_tools_from_denials = approved_tools_from_denials or []

        # Load previous iteration's allowed tools
        prev_allowed_tools: List[str] = []
        prev_data = self._load_previous_iteration_data()
        if prev_data and prev_data.get("allowed_tools"):
            prev_allowed_tools = prev_data.get("allowed_tools", [])

        # Filter out file-specific tools from previous iteration
        # File-specific tools have format: tool_name(file_path)
        # We want to keep only generic tools (no parentheses) from previous iteration
        filtered_prev_tools = [
            tool for tool in prev_allowed_tools
            if "(" not in tool  # Filter out tools with parentheses (file-specific)
        ]

        # Merge: base tools + filtered previous iteration's tools + newly approved tools from denials
        # Use set to avoid duplicates, then convert back to list
        return list(set(base_allowed_tools + filtered_prev_tools + approved_tools_from_denials))

    def _get_allowed_directories(self) -> List[str]:
        """Get list of allowed directories.

        Return list of directories that need to be accessed by underlying CLI tools.
        Default returns [".cafe"], allowing all CLI tools to read .cafe directory.

        Returns:
            List of allowed directories
        """
        return [".cafe"]

    def _handle_previous_permission_denials(self) -> tuple[List[str], str]:
        """Handle previous round's permission denials, return approved tools and user input.

        This method handles two modes:
        1. Interactive mode: Ask user one by one whether to approve each denied tool
        2. Non-interactive mode: Use self.approved_denial_indices to approve tools

        Returns:
            tuple[List[str], str]: (List of approved tools, user's additional notes about permissions)
        """
        from cafe.core.types import PermissionDenial

        # Load previous round iteration data
        prev_data = self._load_previous_iteration_data()
        if not prev_data:
            return ([], "")

        # Check if there are permission_denials
        permission_denials_data = prev_data.get("permission_denials", [])
        if not permission_denials_data:
            return ([], "")

        # Convert dict to PermissionDenial objects
        permission_denials = [
            PermissionDenial(**denial_data) for denial_data in permission_denials_data
        ]

        # Collect approved indices
        approved_indices: List[int] = []

        if self.interactive:
            # Interactive mode: First display all permission requests, then ask one by one
            print("\nThe following permission requests were denied in previous execution:\n")
            for idx, denial in enumerate(permission_denials):
                # Display tool name and main parameters
                if "file_path" in denial.tool_input:
                    print(f"[{idx}] {denial.tool_name}({denial.tool_input['file_path']})")
                elif "command" in denial.tool_input:
                    print(f"[{idx}] {denial.tool_name}({denial.tool_input['command']})")
                else:
                    print(f"[{idx}] {denial.tool_name}(...)")

            print()  # Empty line separator

            # Ask for each permission one by one
            for idx, denial in enumerate(permission_denials):
                # Display tool to authorize
                if "file_path" in denial.tool_input:
                    tool_desc = f"{denial.tool_name}({denial.tool_input['file_path']})"
                elif "command" in denial.tool_input:
                    tool_desc = f"{denial.tool_name}({denial.tool_input['command']})"
                else:
                    tool_desc = f"{denial.tool_name}"

                response = input(f"Approve or not [{idx}] {tool_desc}？(y/n): ").strip().lower()
                if response == 'y':
                    approved_indices.append(idx)

            # Ask user if there are additional notes
            print()
            from cafe.ui.inquirer_prompts import prompt_multiline
            user_input = prompt_multiline(
                "Regarding these permissions, any additional notes or instructions for agent? (Can be left blank)"
            )
        else:
            # Non-interactive mode: Use default approved_denial_indices
            if not hasattr(self, "approved_denial_indices"):
                raise AttributeError("Phase must have 'approved_denial_indices' attribute in non-interactive mode")
            if not hasattr(self, "user_input"):
                raise AttributeError("Phase must have 'user_input' attribute in non-interactive mode")

            approved_indices = self.approved_denial_indices
            user_input = self.user_input

        # Convert approved indices to allowed_tools format
        approved_tools: List[str] = []
        for idx in approved_indices:
            if idx < len(permission_denials):
                denial = permission_denials[idx]
                approved_tools.append(denial.to_allowed_tool_pattern())

        return (approved_tools, user_input)

    def _load_current_iteration_data(self) -> Optional[dict]:
        """Load current iteration data (common method).

        Used to resume interrupted iteration (has user_input but no response).

        Returns:
            dict: Current iteration data, return None if not exists
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "phase_dir"):
            raise AttributeError("Phase must have 'phase_dir' attribute")

        current_iteration_dir = self._get_iteration_dir(self.iteration)
        current_context_file = current_iteration_dir / "context.json"
        if not current_context_file.exists():
            return None

        with open(current_context_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_iteration_counter(self) -> int:
        """Load latest iteration number from iterations.jsonl or context files.

        If last iteration has no response (interrupted), return previous complete iteration number,
        this way next execution will reuse interrupted iteration number.

        Returns:
            Latest complete iteration number, return 0 if no history
        """
        if not hasattr(self, "phase_dir"):
            raise AttributeError("Phase must have 'phase_dir' attribute")

        phase_dir = Path(self.phase_dir)
        if not phase_dir.exists():
            return 0

        # Read iterations.jsonl first
        iterations_file = phase_dir / "iterations.jsonl"
        if iterations_file.exists():
            iterations = self._read_iterations_index()
            if iterations:
                # Return last iteration number
                return iterations[-1].get("iteration", 0)

        # Fallback: read from iteration_XXX/context.json
        iteration_dirs = sorted(phase_dir.glob("iteration_*"))
        if not iteration_dirs:
            return 0

        # Search from back to front for first complete iteration (has status_code)
        for iteration_dir in reversed(iteration_dirs):
            context_file = iteration_dir / "context.json"
            if not context_file.exists():
                continue

            with open(context_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check if has status_code (complete iteration)
            # Use status_code instead of response because response may exist but checklist validation not passed
            if "status_code" in data and data["status_code"]:
                return data.get("iteration", 0)

        # All iterations incomplete, return 0
        return 0

    def _check_if_already_completed(
        self,
        complete_status_codes: List[PhaseStatusCode],
        force: bool = False,
    ) -> Optional[PhaseResult]:
        """Check if phase is already completed (common method).

        If status.json shows phase is completed and status code is in complete_status_codes,
        return COMPLETED result, otherwise return None.

        Args:
            complete_status_codes: List of status codes indicating completion (e.g. [CONFIRMED] or [READY_FOR_REVIEW])
            force: If True, skip completion check and allow re-execution (default: False)

        Returns:
            PhaseResult If completed, None if not completed
        """
        # If force is True, skip the check and allow re-execution
        if force:
            return None

        if not hasattr(self, "agent_manager"):
            raise AttributeError("Phase must have 'agent_manager' attribute")

        status_file = self._get_status_file()
        if not status_file.exists():
            return None

        with open(status_file, "r", encoding="utf-8") as f:
            status_data = json.load(f)

        # Check if completed and status code matches
        if status_data.get("status") != "completed":
            return None

        status_code_value = status_data.get("status_code", "")
        for complete_code in complete_status_codes:
            if status_code_value == complete_code.value:
                # Completed, return result
                token_usage = self.agent_manager.get_total_token_usage()
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"Phase already completed with status {status_code_value}",
                    data={
                        "iterations": status_data.get("iteration", 0),
                        "status_code": status_code_value,
                        "token_usage": {
                            "input_tokens": token_usage.input_tokens,
                            "output_tokens": token_usage.output_tokens,
                            "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                            "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                            "total_cost_usd": token_usage.total_cost_usd,
                        },
                    },
                )

        return None

    def _check_max_iterations(
        self,
        max_iterations: int,
        phase_name: str = "Phase",
    ) -> Optional[PhaseResult]:
        """Check if maximum iteration count exceeded (common method).

        Args:
            max_iterations: Maximum iteration count
            phase_name: Phase name (for error messages)

        Returns:
            PhaseResult If exceeded maximum, None if not exceeded
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")

        if self.iteration > max_iterations:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"{phase_name} exceeded maximum iterations ({max_iterations}). Did not converge.",
                data={
                    "iterations": self.iteration - 1,
                    "max_iterations": max_iterations,
                },
            )

        return None

    def _execute_and_handle_agent_response(
        self,
        agent_name: str,
        user_input: str,
        valid_status_codes: List[PhaseStatusCode],
        allowed_tools: Optional[List[str]] = None,
        continue_codes: Optional[List[PhaseStatusCode]] = None,
        complete_codes: Optional[List[PhaseStatusCode]] = None,
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> tuple[Optional[PhaseResult], str]:
        """Execute complete agent interaction loop: generate prompt, execute agent, handle status code (common method).

        This method encapsulates standard agent execution flow for all phases to use.
        Phase-specific logic should be through:
        1. _generate_prompt() - Generate phase-specific prompt
        2. _get_completion_data() - Provide phase-specific completion data
        3. Execute phase-specific post-processing after call (e.g. sync to GitHub)

        Args:
            agent_name: Agent name
            user_input: User input for this round
            valid_status_codes: List of valid status codes
            allowed_tools: List of allowed tools
            continue_codes: Status codes that should continue loop
            complete_codes: Status codes indicating near completion
            phase_specific_data: Phase-specific data (passed to _execute_agent_iteration)

        Returns:
            (PhaseResult If should end phase, None if should continue next round, agent response)
        """
        # Generate prompt for this iteration (subclass implements this)
        if not hasattr(self, '_generate_prompt'):
            raise AttributeError("Phase must implement '_generate_prompt' method")

        # Call _generate_prompt with or without user_input parameter
        # Try with user_input first, fall back to no parameter
        import inspect
        sig = inspect.signature(self._generate_prompt)
        if len(sig.parameters) > 0:
            prompt = self._generate_prompt(user_input)
        else:
            prompt = self._generate_prompt()

        # Execute agent iteration using common method
        response, status_code = self._execute_agent_iteration(
            agent_name=agent_name,
            prompt=prompt,
            user_input=user_input,
            valid_status_codes=valid_status_codes,
            allowed_tools=allowed_tools or ["write", "read"],
            phase_specific_data=phase_specific_data,
        )

        # Validate checklist completion for complete codes only (after NEED_CLARIFICATION handled)
        # Only validate for CONFIRMED and complete_codes (e.g., READY_FOR_REVIEW)
        # Skip validation for continue_codes (e.g., NEED_CLARIFICATION, NEED_PERMISSION)
        complete_codes = complete_codes or []
        continue_codes = continue_codes or []

        # Universal rule: NEED_PERMISSION should never trigger checklist validation
        # Automatically move NEED_PERMISSION from complete_codes to continue_codes if present
        if PhaseStatusCode.NEED_PERMISSION in complete_codes:
            complete_codes = [code for code in complete_codes if code != PhaseStatusCode.NEED_PERMISSION]
            if PhaseStatusCode.NEED_PERMISSION not in continue_codes:
                continue_codes = list(continue_codes) + [PhaseStatusCode.NEED_PERMISSION]

        should_validate_checklist = (
            status_code == PhaseStatusCode.CONFIRMED or
            status_code in complete_codes
        )

        if should_validate_checklist and status_code not in continue_codes:
            print(f"\n🔍 Validating checklist completion...")
            final_response, final_status_code, validation_passed = self._validate_and_retry_checklist_completion(
                agent_name=agent_name,
                prompt=prompt,
                user_input=user_input,
                valid_status_codes=valid_status_codes,
                allowed_tools=allowed_tools or ["write", "read"],
                max_retries=3,
            )

            # Update response and status_code with validated results
            if validation_passed:
                response = final_response
                status_code = final_status_code

                # Checklist validation passed - now we can save status_code to context.json
                iteration_dir = self._get_iteration_dir(self.iteration)
                context_file = iteration_dir / "context.json"
                if context_file.exists():
                    with open(context_file, "r", encoding="utf-8") as f:
                        context_data = json.load(f)
                    context_data["status_code"] = status_code.value if status_code else None
                    with open(context_file, "w", encoding="utf-8") as f:
                        json.dump(context_data, f, ensure_ascii=False, indent=2)

                # Save progress (phase-specific logic)
                if hasattr(self, "_save_progress"):
                    self._save_progress(status_code)  # type: ignore
            else:
                # Validation failed after max retries - alert user
                print(f"⚠️  Checklist validation failed after maximum retries")
                # Continue with original response and status_code

        # Use base class method to handle standard status codes
        result = self._handle_standard_status_codes(
            status_code=status_code,
            response=response,
            complete_codes=complete_codes or [],
            continue_codes=continue_codes or [],
        )

        return result, response

    def _handle_standard_status_codes(
        self,
        status_code: Optional[PhaseStatusCode],
        response: str,
        continue_codes: Optional[List[PhaseStatusCode]] = None,
        complete_codes: Optional[List[PhaseStatusCode]] = None,
    ) -> Optional[PhaseResult]:
        """Handle standard status codes, return PhaseResult or None (indicates continue loop).

        This method encapsulates common status code handling logic:
        - NO_RESPONSE: Return FAILED
        - codes in continue_codes: Return None (continue loop)
        - codes in complete_codes: Return None (continue loop, but usually handle completion logic in next round)
        - None (no status code): interactive mode returns None, non-interactive returns IN_PROGRESS

        Args:
            status_code: Status code extracted from agent response
            response: Agent's response content
            continue_codes: Status codes that should continue loop（e.g. NEED_CLARIFICATION）
            complete_codes: Status codes indicating near completion（e.g. READY_FOR_REVIEW, CONFIRMED）

        Returns:
            PhaseResult If should end phase, None if should continue next round
        """
        # Check required attributes
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "interactive"):
            raise AttributeError("Phase must have 'interactive' attribute")

        continue_codes = continue_codes or []
        complete_codes = complete_codes or []

        # Handle NO_RESPONSE
        if status_code == PhaseStatusCode.NO_RESPONSE:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Agent returned no response in iteration {self.iteration}",
                data={
                    "iterations": self.iteration,
                    "status_code": status_code.value,
                },
            )

        # Handle complete codes (e.g., CONFIRMED)
        # For CONFIRMED status, print token usage and return completed result
        if status_code == PhaseStatusCode.CONFIRMED:
            self._print_token_usage_summary()

            token_usage = self.agent_manager.get_total_token_usage()
            result_data = {
                "iterations": self.iteration,
                "final_response": response,
                "status_code": status_code.value,
                "token_usage": {
                    "input_tokens": token_usage.input_tokens,
                    "output_tokens": token_usage.output_tokens,
                    "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                    "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                    "total_cost_usd": token_usage.total_cost_usd,
                }
            }

            # Allow phases to add phase-specific data
            if hasattr(self, '_get_completion_data'):
                phase_data = self._get_completion_data()
                result_data.update(phase_data)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Phase completed in {self.iteration} iteration(s)",
                data=result_data,
                token_usage=token_usage,
            )

        # Handle other complete codes (e.g., READY_FOR_REVIEW)
        # Both interactive and non-interactive: complete immediately
        # If user wants to continue, they can run the command again manually
        if status_code in complete_codes:
            self._print_token_usage_summary()
            token_usage = self.agent_manager.get_total_token_usage()
            result_data = {
                "iterations": self.iteration,
                "final_response": response,
                "status_code": status_code.value,
                "token_usage": {
                    "input_tokens": token_usage.input_tokens,
                    "output_tokens": token_usage.output_tokens,
                    "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                    "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                    "total_cost_usd": token_usage.total_cost_usd,
                }
            }
            if hasattr(self, '_get_completion_data'):
                phase_data = self._get_completion_data()
                result_data.update(phase_data)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Phase completed in {self.iteration} iteration(s)",
                data=result_data,
                token_usage=token_usage,
            )

        # Handle continue codes (e.g., NEED_CLARIFICATION)
        # After removing while loops: complete immediately, user runs command again manually if needed
        if status_code in continue_codes:
            self._print_token_usage_summary()
            token_usage = self.agent_manager.get_total_token_usage()
            result_data = {
                "iterations": self.iteration,
                "final_response": response,
                "status_code": status_code.value,
                "token_usage": {
                    "input_tokens": token_usage.input_tokens,
                    "output_tokens": token_usage.output_tokens,
                    "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                    "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                    "total_cost_usd": token_usage.total_cost_usd,
                }
            }
            if hasattr(self, '_get_completion_data'):
                phase_data = self._get_completion_data()
                result_data.update(phase_data)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Phase completed in {self.iteration} iteration(s) - {status_code.value}",
                data=result_data,
                token_usage=token_usage,
            )

        # Handle no status code found
        if status_code is None:
            if self.interactive:
                # Interactive mode: continue iteration
                return None
            else:
                # Non-interactive mode: exit and wait for next call
                return PhaseResult(
                    status=PhaseStatus.IN_PROGRESS,
                    message=f"Iteration {self.iteration}: No status code found, need more iterations",
                    data={
                        "iterations": self.iteration,
                        "status_code": None,
                    },
                )

        # Unknown status code - continue
        return None

    def _validate_and_retry_checklist_completion(
        self,
        agent_name: str,
        prompt: str,
        user_input: str,
        valid_status_codes: List[PhaseStatusCode],
        allowed_tools: Optional[List[str]] = None,
        max_retries: int = 3,
    ) -> tuple[str, Optional[PhaseStatusCode], bool]:
        """Validate checklist completion and retry if incomplete.

        This method validates that all checklist items are completed. If unchecked
        items are found, it will re-invoke the agent to complete them, up to max_retries times.

        Args:
            agent_name: Agent name
            prompt: Original prompt sent to agent
            user_input: User input for this iteration
            valid_status_codes: Valid status codes for this phase
            allowed_tools: Tools available to agent
            max_retries: Maximum number of retry attempts (default: 3)

        Returns:
            tuple[final_response, final_status_code, validation_passed]:
                - final_response: Agent's final response (may be merged from multiple attempts)
                - final_status_code: Final status code
                - validation_passed: True if validation passed, False if max retries reached

        Raises:
            AttributeError: If phase lacks required attributes
        """
        from cafe.utils.checklist_validator import validate_checklist
        from cafe.core.status_codes import StatusCodeParser

        # Check required attributes
        if not hasattr(self, "phase_dir"):
            raise AttributeError("Phase must have 'phase_dir' attribute")
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")

        # Get checklist path
        iteration_dir = self._get_iteration_dir(self.iteration)
        checklist_path = iteration_dir / "checklist.md"

        # Check if checklist exists, if not rebuild it
        if not checklist_path.exists() or checklist_path.stat().st_size == 0:
            print(f"⚠️  Checklist file not found or empty, rebuilding...")
            try:
                self._rebuild_checklist_for_iteration(self.iteration)
            except Exception as e:
                print(f"⚠️  Failed to rebuild checklist: {e}")
                # Continue without checklist validation if rebuild fails

        # Validate checklist
        try:
            result = validate_checklist(checklist_path)
        except FileNotFoundError:
            # If still not found after rebuild attempt, skip validation
            print(f"⚠️  Checklist file still not found after rebuild, skipping validation")
            # Get current response from context
            context_file = iteration_dir / "context.json"
            if context_file.exists():
                with open(context_file, "r", encoding="utf-8") as f:
                    context_data = json.load(f)
                    response = context_data.get("response", "")
                    status_code = StatusCodeParser.extract(response, valid_codes=valid_status_codes)
                    return response, status_code, True
            return "", None, True

        if result.is_complete:
            print(f"✅ Checklist validation passed - all items completed")
            # Get current response from context
            context_file = iteration_dir / "context.json"
            if context_file.exists():
                with open(context_file, "r", encoding="utf-8") as f:
                    context_data = json.load(f)
                    response = context_data.get("response", "")
                    status_code = StatusCodeParser.extract(response, valid_codes=valid_status_codes)
                    return response, status_code, True
            return "", None, True

        # Checklist incomplete - start retry loop
        print(f"⚠️  Checklist validation failed - {result.unchecked_count} unchecked items found")

        retry_count = 0
        while retry_count < max_retries:
            retry_count += 1
            print(f"\n⚠️  Re-invoking agent to complete checklist items... (attempt {retry_count}/{max_retries})")

            # Generate retry prompt with safe path conversion
            from cafe.utils.git_utils import to_cwd_relative_path
            try:
                checklist_display_path = to_cwd_relative_path(checklist_path)
            except (ValueError, OSError):
                # Fallback to str if relative path conversion fails
                checklist_display_path = str(checklist_path)

            retry_prompt = f"""Your previous response was received, but the checklist at {checklist_display_path} still has unchecked items.

Please review the checklist file, complete all remaining tasks, update the checklist by marking completed items with [x], and re-submit your status code.

Do NOT return a status code until ALL checklist items are marked as complete [x].
"""

            # Execute agent with retry prompt
            try:
                retry_response, _, _, _, retry_streaming_log = self.agent_manager.execute(
                    agent_name,
                    retry_prompt,
                    allowed_tools=allowed_tools,
                    allowed_directories=self._get_allowed_directories(),
                )

                # Extract status code from retry response
                retry_status_code = StatusCodeParser.extract(
                    retry_response,
                    valid_codes=valid_status_codes,
                )

                # Validate checklist again
                retry_result = validate_checklist(checklist_path)

                if retry_result.is_complete:
                    print(f"✅ Checklist validation passed after retry {retry_count}")

                    # Merge streaming logs
                    context_file = iteration_dir / "context.json"
                    original_streaming_log = []
                    original_response = ""
                    if context_file.exists():
                        with open(context_file, "r", encoding="utf-8") as f:
                            context_data = json.load(f)
                            original_streaming_log = context_data.get("streaming_log", [])
                            original_response = context_data.get("response", "")

                    merged_streaming_log = original_streaming_log + retry_streaming_log

                    # Merge streaming.jsonl files
                    streaming_jsonl_file = iteration_dir / "streaming.jsonl"
                    if streaming_jsonl_file.exists():
                        # Append retry streaming log to existing file
                        with open(streaming_jsonl_file, "a", encoding="utf-8") as f:
                            for log_entry in retry_streaming_log:
                                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

                    # Update context.json with final response and merged streaming_log
                    # Note: response is NOT merged, only keep the last one
                    if context_file.exists():
                        with open(context_file, "r", encoding="utf-8") as f:
                            context_data = json.load(f)

                        context_data["response"] = retry_response  # Keep last response only
                        context_data["streaming_log"] = merged_streaming_log
                        context_data["checklist_validation_attempts"] = retry_count
                        context_data["status_code"] = retry_status_code.value if retry_status_code else None

                        with open(context_file, "w", encoding="utf-8") as f:
                            json.dump(context_data, f, ensure_ascii=False, indent=2)

                    return retry_response, retry_status_code, True

                else:
                    print(f"⚠️  Checklist still has {retry_result.unchecked_count} unchecked items after retry {retry_count}")

            except Exception as e:
                print(f"⚠️  Failed to retry checklist completion: {e}")
                # Continue to next retry

        # Max retries reached
        print(f"\n❌ Maximum retry attempts ({max_retries}) reached. Checklist still incomplete.")
        print(f"   Please complete the checklist manually and re-run the command.")

        # Return the last response and status code
        context_file = iteration_dir / "context.json"
        if context_file.exists():
            with open(context_file, "r", encoding="utf-8") as f:
                context_data = json.load(f)
                response = context_data.get("response", "")
                status_code = StatusCodeParser.extract(response, valid_codes=valid_status_codes)
                return response, status_code, False

        return "", None, False

    def _rebuild_checklist_for_iteration(self, iteration: int) -> None:
        """Rebuild checklist for current iteration.

        Subclasses should override this method to provide phase-specific checklist generation.
        This default implementation creates a minimal placeholder checklist to prevent total failure.

        Args:
            iteration: Iteration number
        """
        # Get phase name for the placeholder
        phase_name = self.__class__.__name__.replace("Phase", "").lower()

        iteration_dir = self._get_iteration_dir(iteration)
        checklist_path = iteration_dir / "checklist.md"

        # Create minimal placeholder checklist
        placeholder_content = f"""## Execution Steps Checklist

[ ] Review and complete all required tasks for this phase
[ ] Verify all work is complete before returning status code
[ ] Return appropriate status code

## Important Notes Checklist

[ ] ✅ Complete all tasks according to phase requirements
[ ] ✅ Return ONLY the status code in your response

---

⚠️ **Note**: This is a placeholder checklist generated automatically because the original
checklist.md was missing or empty. The {phase_name} phase should implement proper
checklist generation by overriding `_rebuild_checklist_for_iteration()`.
"""

        # Ensure directory exists
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Write placeholder checklist
        checklist_path.write_text(placeholder_content, encoding="utf-8")

        print(f"⚠️  WARNING: Checklist rebuild not properly implemented for {phase_name} phase!")
        print(f"   Created placeholder checklist at {checklist_path}")
        print(f"   Phase should override _rebuild_checklist_for_iteration() for proper checklist generation.")

    def _get_checklist_completion_reminder(self) -> str:
        """Get checklist completion reminder text for prompts.

        This method generates a reminder text that can be inserted into phase prompts
        to emphasize the requirement that all checklist items must be completed before
        returning a status code.

        Returns:
            str: Checklist completion reminder text

        Raises:
            AttributeError: If phase lacks required attributes
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")

        iteration_dir = self._get_iteration_dir(self.iteration)
        checklist_path = iteration_dir / "checklist.md"

        try:
            from cafe.utils.git_utils import to_cwd_relative_path
            checklist_relative = to_cwd_relative_path(checklist_path)
        except (ValueError, OSError):
            try:
                checklist_relative = str(checklist_path.relative_to(Path.cwd()))
            except ValueError:
                checklist_relative = str(checklist_path)

        return f"""
⚠️ **IMPORTANT - Checklist Completion Requirement:**

Before returning ANY status code, you MUST:
1. Review and complete ALL items in {checklist_relative}
2. Mark each completed item with [x] (change [ ] to [x])
3. Verify that NO unchecked items [ ] remain in the checklist
4. ONLY return a status code after ALL checklist items are marked as complete [x]

The system will verify checklist completion. If unchecked items remain, you will be asked to complete them.
"""

    def _analyze_missing_status_code(
        self,
        agent_name: str,
        valid_status_codes: List[PhaseStatusCode],
    ) -> Optional[PhaseStatusCode]:
        """When response has no status code, call agent to analyze status.

        This method handles cases where some agents do not return status code in response.
        It uses phase-specific prompt to ask agent to analyze current status.

        Args:
            agent_name: Agent name
            valid_status_codes: Valid status codes for this phase

        Returns:
            Analyzed PhaseStatusCode, return None if cannot analyze
        """
        # Get phase-specific analysis prompt
        prompt = self._get_status_analysis_prompt()
        if not prompt:
            return None

        # Call agent to analyze status (only needs response)
        response, _, _, _, _ = self.agent_manager.execute(
            agent_name, prompt, allowed_directories=self._get_allowed_directories()
        )

        # Extract status code from response
        from cafe.core.status_codes import StatusCodeParser
        return StatusCodeParser.extract(response, valid_codes=valid_status_codes)

    def _analyze_missing_status_code_with_logging(
        self,
        agent_name: str,
        valid_status_codes: List[PhaseStatusCode],
        original_response: str,
    ) -> tuple[Optional[str], Optional[PhaseStatusCode]]:
        """When response has no status code, call agent to analyze status (with logging).

        This method is enhanced version of _analyze_missing_status_code, returns analysis response for recording.

        Args:
            agent_name: Agent name
            valid_status_codes: Valid status codes for this phase
            original_response: Original response (for logging)

        Returns:
            tuple[analysis_response, status_code]:
                - analysis_response: Analysis call response content (may be None)
                - status_code: Extracted status code (may be None)
        """
        # Get phase-specific analysis prompt
        prompt = self._get_status_analysis_prompt()
        if not prompt:
            return None, None

        try:
            # Call agent to analyze status (needs read permission)
            allowed_tools = ["read", "grep", "glob", "ls"]
            response, _, _, _, _ = self.agent_manager.execute(
                agent_name,
                prompt,
                allowed_tools=allowed_tools,
                allowed_directories=self._get_allowed_directories()
            )

            # Extract status code from response
            from cafe.core.status_codes import StatusCodeParser
            status_code = StatusCodeParser.extract(response, valid_codes=valid_status_codes)

            return response, status_code
        except Exception as e:
            # Analysis failed, return None
            print(f"⚠️  Status code analysis failed: {e}")
            return None, None

    def _write_status_code_error_log(
        self,
        original_response: str,
        valid_status_codes: List[PhaseStatusCode],
        status_code: Optional[PhaseStatusCode],
        analysis_attempted: bool,
        analysis_response: Optional[str],
        cli_command_args: Optional[List[str]],
        multiple_codes_found: Optional[List[PhaseStatusCode]] = None,
    ) -> None:
        """Write status code error log to execution_error_{num}.log.

        Write log when encountering the following situations:
        - Original response has no status code
        - Original response has multiple status codes
        - Analysis attempt failed

        Args:
            original_response: Original agent response
            valid_status_codes: Valid status codes
            status_code: Final extracted status code (may be None)
            analysis_attempted: Whether analysis was attempted
            analysis_response: Analysis call response (if any)
            cli_command_args: CLI command arguments
            multiple_codes_found: If multiple status codes found, list all found codes
        """
        if not hasattr(self, "phase_dir") or not hasattr(self, "iteration"):
            return

        # Only write log when needed: no status code, performed analysis, or found multiple codes
        if status_code is not None and not analysis_attempted and not multiple_codes_found:
            return

        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        error_file = iteration_dir / "error.json"

        # Determine error type
        if multiple_codes_found:
            error_type = "Multiple status codes"
        elif status_code is None:
            error_type = "Missing status code"
        else:
            error_type = "Status code required analysis"

        # Create error data
        error_data = {
            "error": error_type,
            "error_type": error_type.replace(" ", "_").upper(),
            "is_critical": False,
            "timestamp": datetime.now().astimezone().isoformat(),
            "issue": self.issue_dir.name if hasattr(self, 'issue_dir') else 'unknown',
            "iteration": self.iteration,
            "phase": self.phase_name if hasattr(self, 'phase_name') else 'unknown',
            "original_response": original_response,
            "valid_status_codes": [code.value for code in valid_status_codes],
            "extracted_status_code": status_code.value if status_code else None,
            "multiple_codes_found": [code.value for code in multiple_codes_found] if multiple_codes_found else None,
            "analysis_attempted": analysis_attempted,
            "analysis_response": analysis_response if analysis_attempted else None,
            "cli_command_args": cli_command_args,
        }

        # Write error.json
        with open(error_file, "w", encoding="utf-8") as f:
            json.dump(error_data, f, ensure_ascii=False, indent=2)

    def _get_status_analysis_prompt(self) -> Optional[str]:
        """Get prompt for analyzing status code (subclass override).

        When agent response has no status code, this prompt is used to call agent again.
        Subclasses should override this method to provide phase-specific analysis prompt.

        Returns:
            Prompt for analyzing status code, return None if analysis not supported
        """
        return None

    def _read_issue_config(self, config_path: Path) -> Optional[Dict[str, Any]]:
        """Read issue configuration from config.yaml (common method).

        Args:
            config_path: Path to config.yaml file

        Returns:
            Config data as dict if file exists, None otherwise
        """
        if not config_path.exists():
            return None

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
            return config_data if config_data else None
        except (yaml.YAMLError, IOError):
            return None

    def _write_issue_config(self, config_path: Path, config_data: Dict[str, Any]) -> None:
        """Write issue configuration to config.yaml (common method).

        Args:
            config_path: Path to config.yaml file
            config_data: Config data to write
        """
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

    def _get_issue_config_value(self, config_file: Path, key: str) -> Optional[Any]:
        """Read a value from issue config file (common method).

        Args:
            config_file: Path to config.yaml file
            key: Config key to read (e.g., "issue_id", "base_branch", "pr.auto_create")
                 Supports dot notation for nested keys

        Returns:
            Config value if found, None otherwise
        """
        config_data = self._read_issue_config(config_file)
        if not config_data:
            return None

        # Support dot notation for nested keys (e.g., "pr.auto_create")
        if '.' in key:
            keys = key.split('.')
            value = config_data
            for k in keys:
                if isinstance(value, dict):
                    value = value.get(k)
                    if value is None:
                        return None
                else:
                    return None
            return value
        else:
            return config_data.get(key)

    def _get_issue_dir(self, git_ops: "GitOperations") -> Path:
        """Get issue directory path based on current Git branch (common method).

        Args:
            git_ops: GitOperations instance to get current branch

        Returns:
            Path to issue directory (.cafe/issues/{branch_name}/)
        """
        from pathlib import Path
        current_branch = git_ops.get_current_branch()
        return Path(f".cafe/issues/{current_branch}")

    def _get_current_branch_commits(self, git_ops: "GitOperations", base_branch: str) -> str:
        """Get commits from current branch that are not in base branch (common method).

        This method gets new commits added on current feature branch, excluding commits on base branch.
        Use git log base_branch..HEAD format to only show current branch commits.

        Args:
            git_ops: GitOperations instance to get commits
            base_branch: Base branch name (e.g., "main", "develop")

        Returns:
            String containing commit list in oneline format
        """
        return git_ops.get_commits_between(base=base_branch, head="HEAD")

    def _get_versioned_file_path(
        self,
        phase_name: str,
        iteration: int,
        phase_dir: Path,
    ) -> Path:
        """Get path of versioned file (common method).

        Args:
            phase_name: Phase name (e.g. "spec", "plan", "review")
            iteration: Iteration number (1-based)
            phase_dir: Phase directory path

        Returns:
            Path object of versioned file (format: iteration_XXX/output.md)

        Examples:
            >>> _get_versioned_file_path("spec", 1, Path(".cafe/issues/myissue/spec"))
            Path('.cafe/issues/myissue/spec/iteration_001/output.md')
        """
        iteration_dir = phase_dir / f"iteration_{iteration:03d}"
        return iteration_dir / "output.md"

    def _get_next_iteration_number(
        self,
        phase_name: str,
        phase_dir: Path,
    ) -> int:
        """Get next iteration number (common method).

        Args:
            phase_name: Phase name (e.g. "spec", "plan", "review")
            phase_dir: Phase directory path

        Returns:
            Next iteration number (starting from 1)

        Raises:
            ValueError: If already has 999 files
        """
        # Check if directory exists
        if not phase_dir.exists():
            return 1

        # Count based on iteration_XXX directories with context.json files
        # This prevents interrupted executions from affecting version numbers
        existing_iterations = sorted(phase_dir.glob("iteration_*/context.json"))
        if not existing_iterations:
            return 1

        count = len(existing_iterations)

        # Check if exceeds 999
        if count >= 999:
            raise ValueError("Cannot exceed 999")

        # Check if the last iteration was interrupted (has no status_code)
        last_context_file = existing_iterations[-1]
        try:
            import json
            with open(last_context_file, 'r', encoding='utf-8') as f:
                last_iteration_data = json.load(f)

            # If last iteration has no status_code, it was interrupted
            # Return the same iteration number to retry (don't increment)
            # Use status_code instead of response because response may exist but checklist validation not passed
            if not last_iteration_data.get("status_code"):
                return count
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            # If we can't read the file, treat it as corrupted/interrupted
            return count

        return count + 1

    def _copy_previous_version(
        self,
        phase_name: str,
        iteration: int,
        phase_dir: Path,
    ) -> None:
        """Copy previous version file to new version (common method).

        Args:
            phase_name: Phase name (e.g. "spec", "plan", "review")
            iteration: Current iteration number
            phase_dir: Phase directory path

        Note:
            - If first round (iteration=1), do not perform any action
            - If immediate previous version does not exist, find the latest existing output.md to copy
            - If no previous output.md exists at all, do not perform any action
        """
        # Do nothing for first iteration
        if iteration == 1:
            return

        # Get current file path
        current_file = self._get_versioned_file_path(phase_name, iteration, phase_dir)

        # Try to find the latest existing output.md from previous iterations
        # Start from iteration-1 and work backwards
        source_file = None
        for i in range(iteration - 1, 0, -1):
            candidate = self._get_versioned_file_path(phase_name, i, phase_dir)
            if candidate.exists():
                source_file = candidate
                break

        # Copy if we found a source file
        if source_file:
            import shutil
            # Ensure parent directory exists
            current_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, current_file)

    def _get_latest_versioned_file(
        self,
        phase_name: str,
        phase_dir: Path,
    ) -> Optional[Path]:
        """Get path of latest version file (common method).

        Args:
            phase_name: Phase name (e.g. "spec", "plan", "review")
            phase_dir: Phase directory path

        Returns:
            Path object of latest version file, return None if no versions exist

        Examples:
            >>> _get_latest_versioned_file("spec", Path(".cafe/issues/myissue/spec"))
            Path('.cafe/issues/myissue/spec/iteration_003/output.md')  # Assuming 3 versions
        """
        # Find all iteration directories
        iteration_dirs = sorted(phase_dir.glob("iteration_*"))

        # Search from latest to earliest for first output.md
        for iteration_dir in reversed(iteration_dirs):
            output_file = iteration_dir / "output.md"
            if output_file.exists():
                return output_file

        return None


def ensure_agent_file_exists(agent_name: str, agent_role: str, cafe_dir: Path = Path(".cafe")) -> None:
    """Check if agent md file exists, if not report error and prompt user to reset.

    Args:
        agent_name: Agent name (e.g. "Roger", "David", "Richard")
        agent_role: Agent role directory name (e.g. "pm", "developer", "reviewer")
        cafe_dir: CAFE config directory path (default ".cafe")

    Raises:
        FileNotFoundError: When agent md file does not exist

    Examples:
        >>> ensure_agent_file_exists("Roger", "pm")  # Normal case, will not throw error
        >>> ensure_agent_file_exists("Roger", "pm")  # If file does not exist, will throw FileNotFoundError
    """
    from cafe.agents.manager import AgentManager
    
    # Check if agent md file exists
    agent_file = cafe_dir / AgentManager.AGENTS_DIR / agent_role / f"{agent_name}.md"

    if not agent_file.exists():
        from rich.console import Console
        console = Console()
        console.print(f"[red]✗ Agent file not found: {agent_file}[/red]")
        console.print(f"[yellow]ℹ Please run 'cafe agent default' to reset default agent files[/yellow]")
        raise FileNotFoundError(
            f"Agent file not found: {agent_file}\n"
            f"Run 'cafe agent default' to reset default agent files"
        )
