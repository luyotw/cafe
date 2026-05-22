"""Base class for workflow phases."""

from __future__ import annotations

import json
import logging
import inspect
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cafe.core.git import GitOperations

from cafe.core.phase_checklist_mixin import PhaseChecklistMixin
from cafe.core.phase_sandbox_mixin import PhaseSandboxMixin
from cafe.core.phase_review_mixin import PhaseReviewMixin
from cafe.core.phase_state_mixin import PhaseStateMixin

# Backward-compat re-export for test imports
from cafe.core.phase_state_mixin import ensure_agent_file_exists  # noqa: F401
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, TokenUsage


class Phase(PhaseStateMixin, PhaseSandboxMixin, PhaseReviewMixin, PhaseChecklistMixin, ABC):
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

    def _handle_keyboard_interrupt(self, phase_name: str, data: Optional[Dict[str, Any]] = None) -> PhaseResult:
        """Unified KeyboardInterrupt handling for all phases.
        
        This method should be called in the except KeyboardInterrupt block of each phase's execute().
        It displays pause message and returns IN_PROGRESS result with user_interrupted flag.
        
        Args:
            phase_name: Name of the phase (e.g., "spec", "develop", "review")
            data: Additional data to include in the result (e.g., iterations, file paths)
            
        Returns:
            PhaseResult with IN_PROGRESS status and user_interrupted flag set to True
        """
        issue_name = getattr(self, 'issue_name', None) or getattr(self, 'issue_id', 'unknown')
        iteration = getattr(self, 'iteration', None)
        
        print("\n\n⏸️  Paused by user (Ctrl+C).")
        if iteration is not None:
            print(f"💾 Progress saved. Current iteration: {iteration}")
        print(f"📝 To resume, run: cafe {phase_name} {issue_name if issue_name != 'unknown' else ''}")
        
        result_data = data or {}
        result_data["user_interrupted"] = True
        if iteration is not None:
            result_data.setdefault("iterations", iteration)
        
        return PhaseResult(
            status=PhaseStatus.IN_PROGRESS,
            message="Paused by user - can resume later",
            data=result_data,
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

        # Save user_input to dedicated markdown file
        # Don't overwrite existing non-empty user_input.md (e.g., from PR phase)
        user_input_file = iteration_dir / "user_input.md"
        if not user_input_file.exists() or user_input_file.stat().st_size == 0:
            with open(user_input_file, "w", encoding="utf-8") as f:
                f.write(user_input)

        # Create initial context data (user_input is stored in user_input.md,
        # no longer duplicated into context.json)
        context_data: Dict[str, Any] = {
            "iteration": self.iteration,
            "timestamp": datetime.now().astimezone().isoformat(),
        }

        # Add phase-specific initial data
        if phase_specific_data:
            context_data.update(phase_specific_data)

        # Save as iteration.json file
        context_file = iteration_dir / "iteration.json"
        with open(context_file, "w", encoding="utf-8") as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)

    def _load_user_input(self, iteration: int) -> str:
        """Load user_input from user_input.md.

        Args:
            iteration: Iteration number to load from

        Returns:
            User input string, or empty string if not found
        """
        iteration_dir = self._get_iteration_dir(iteration)
        user_input_file = iteration_dir / "user_input.md"

        if user_input_file.exists():
            return user_input_file.read_text(encoding="utf-8")

        return ""

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
        token_usage: Optional["TokenUsage"] = None,
        model: Optional[str] = None,
        persist_status: bool = True,
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
            token_usage: Token usage stats
            model: Model name used
            persist_status: Whether to persist status_code into iteration metadata
        """
        # Ensure phase_dir exists
        if not hasattr(self, "phase_dir"):
            raise AttributeError(
                "Phase must have 'phase_dir' attribute to use _update_iteration_history"
            )

        # Get iteration directory and context file
        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        context_file = self._resolve_iteration_context_file(iteration_dir)

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

        # Initialize agent metadata fields if not present (ensures all fields exist)
        if "prompt" not in context_data:
            context_data["prompt"] = None
        if "cli" not in context_data:
            context_data["cli"] = None
        if "session_id" not in context_data:
            context_data["session_id"] = None
        if "allowed_tools" not in context_data:
            context_data["allowed_tools"] = None
        if "denied_tools" not in context_data:
            context_data["denied_tools"] = None
        if "cli_command_args" not in context_data:
            context_data["cli_command_args"] = None
        if "model" not in context_data:
            context_data["model"] = None

        # Update shared agent metadata (only if provided, to preserve existing values)
        if prompt is not None:
            context_data["prompt"] = prompt
        if agent_cli is not None:
            context_data["cli"] = agent_cli
        if agent_session_id is not None:
            context_data["session_id"] = agent_session_id
        if allowed_tools is not None:
            context_data["allowed_tools"] = allowed_tools
        if denied_tools is not None:
            context_data["denied_tools"] = denied_tools
        if cli_command_args is not None:
            context_data["cli_command_args"] = cli_command_args
        if persist_status and status_code is not None:
            context_data["status_code"] = status_code.value
        elif not persist_status:
            context_data.pop("status_code", None)

        # Update model (only if provided, to preserve existing value)
        if model is not None:
            context_data["model"] = model

        # Save stats (token usage) if provided
        if token_usage is not None:
            context_data["stats"] = token_usage.model_dump()

        # Save end_time for this iteration
        context_data["end_time"] = datetime.now().astimezone().isoformat()

        # Save updated iteration.json file
        context_file = iteration_dir / "iteration.json"
        with open(context_file, "w", encoding="utf-8") as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)

        # Note: streaming.jsonl is saved earlier in _execute_agent_iteration
        # to preserve raw format before iteration.json processing

        # Append one record to iterations.jsonl
        iteration_index_data = {
            "iteration": self.iteration,
            "timestamp": context_data.get("timestamp", datetime.now().astimezone().isoformat()),
            "end_time": context_data.get("end_time"),
            "has_error": "error" in context_data,
        }
        if persist_status and status_code is not None:
            iteration_index_data["status"] = status_code.value
        try:
            self._append_iteration_index(iteration_index_data)
        except Exception as e:
            logger.warning(f"Failed to append iteration index: {e}")

    def _record_active_agent_cli(
        self,
        *,
        agent_name: str,
        agent_cli: Optional[str],
        model: Optional[str],
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Remember the last successful CLI for chat handoff resolution."""
        if not agent_cli:
            return
        issue_dir = getattr(self, "issue_dir", None)
        if issue_dir is None:
            return

        active_file = Path(issue_dir) / "active_clis.json"
        try:
            if active_file.exists():
                raw = json.loads(active_file.read_text(encoding="utf-8"))
                data = raw if isinstance(raw, dict) else {}
            else:
                data = {}

            extra = phase_specific_data or {}
            data[agent_name] = {
                "cli": agent_cli,
                "model": model,
                "step_name": extra.get("step_name"),
                "updated_at": datetime.now().astimezone().isoformat(),
            }
            active_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("failed to record active CLI for %s: %s", agent_name, exc)

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

    @staticmethod
    def _extract_status_code_from_response(
        response: str,
        valid_codes: Optional[List[PhaseStatusCode]] = None,
    ) -> Optional[PhaseStatusCode]:
        """Extract a single status code from plain text response.

        Returns None when no status code is found or when multiple different
        status codes appear in the same response.
        """
        if not response:
            return None
        return StatusCodeParser.extract(response, valid_codes=valid_codes)

    @staticmethod
    def _context_marks_completed(
        context: Dict[str, Any],
        *,
        valid_codes: Optional[List[PhaseStatusCode]] = None,
    ) -> bool:
        """Return True when iteration context represents a completed iteration."""
        if context.get("end_time"):
            return True

        raw_status = context.get("status_code")
        if isinstance(raw_status, str) and raw_status:
            return True

        response = context.get("response")
        if not isinstance(response, str) or not response.strip():
            return False

        parsed = StatusCodeParser.extract(response, valid_codes=valid_codes)
        if parsed is not None:
            return True

        return False

    @staticmethod
    def _context_status_code(
        context: Dict[str, Any],
        *,
        valid_codes: Optional[List[PhaseStatusCode]] = None,
    ) -> Optional[str]:
        """Return a status-like code from iteration context when one is available."""
        raw_status = context.get("status_code")
        if isinstance(raw_status, str) and raw_status:
            return raw_status

        response = context.get("response")
        if not isinstance(response, str) or not response.strip():
            return None

        parsed = StatusCodeParser.extract(response, valid_codes=valid_codes)
        if parsed is not None:
            return parsed.value

        return None

    def _execute_agent_iteration(
        self,
        agent_name: str,
        prompt: str,
        user_input: str,
        valid_intents: List[PhaseStatusCode],
        require_status_code: bool = True,
        persist_status: bool = True,
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
            valid_intents: Valid status codes accepted by this phase
            require_status_code: Whether missing/invalid status code should trigger retry/failure
            persist_status: Whether to persist status into phase metadata files
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
        allowed_directories = self._get_allowed_directories()
        cli_command_args = self.agent_manager.preview_cli_command_args(
            agent_name,
            prompt,
            allowed_tools=allowed_tools,
            allowed_directories=allowed_directories,
        )
        cli_environment = self.agent_manager.preview_cli_environment(agent_name) or {}
        cli_environment_preview = {
            key: value
            for key, value in cli_environment.items()
            if key in {"CODEX_HOME", "CLAUDE_CONFIG_DIR", "GEMINI_HOME", "COPILOT_HOME"}
        }

        # 3. Save prompt to iteration.json (before executing agent)
        iteration_dir = self._get_iteration_dir(self.iteration)
        context_file = self._resolve_iteration_context_file(iteration_dir)
        if context_file.exists():
            with open(context_file, "r", encoding="utf-8") as f:
                context_data = json.load(f)
            context_data["prompt"] = prompt
            context_data["cli"] = agent_cli
            context_data["session_id"] = agent_session_id
            context_data["allowed_tools"] = allowed_tools
            context_data["denied_tools"] = denied_tools
            context_data["cli_command_args"] = cli_command_args
            context_data["cli_environment"] = cli_environment_preview
            with open(context_file, "w", encoding="utf-8") as f:
                json.dump(context_data, f, ensure_ascii=False, indent=2)

        # 4. Execute agent (with error recovery)
        # Prepare streaming.jsonl file path for real-time writing
        streaming_jsonl_file = iteration_dir / "streaming.jsonl"
        pre_execution_output_snapshot = self._snapshot_output_files(self._detect_written_output_files())

        # Initialize cumulative token usage for this iteration
        from cafe.core.types import TokenUsage
        cumulative_token_usage = TokenUsage()

        def accumulate_token_usage(target: TokenUsage, source: TokenUsage) -> None:
            """Accumulate token usage from source to target."""
            target.input_tokens += source.input_tokens
            target.output_tokens += source.output_tokens
            target.cache_creation_input_tokens += source.cache_creation_input_tokens
            target.cache_read_input_tokens += source.cache_read_input_tokens
            target.total_cost_usd += source.total_cost_usd
            if source.turn_usages:
                target.turn_usages.extend(source.turn_usages)
            if source.duration_ms is not None:
                if target.duration_ms is None:
                    target.duration_ms = source.duration_ms
                else:
                    target.duration_ms += source.duration_ms
            if source.duration_api_ms is not None:
                if target.duration_api_ms is None:
                    target.duration_api_ms = source.duration_api_ms
                else:
                    target.duration_api_ms += source.duration_api_ms

        # Track model separately (use latest value, don't accumulate)
        model: Optional[str] = None
        execution_phase_name = None
        if phase_specific_data:
            raw_step_name = phase_specific_data.get("step_name")
            if isinstance(raw_step_name, str):
                execution_phase_name = raw_step_name
        if execution_phase_name is None:
            execution_phase_name = getattr(self, "phase_name", None)

        try:
            execute_kwargs = {
                "allowed_tools": allowed_tools,
                "allowed_directories": allowed_directories,
                "streaming_output_file": str(streaming_jsonl_file),
            }
            execute_signature = inspect.signature(self.agent_manager.execute)
            if (
                "phase_name" in execute_signature.parameters
                or any(
                    param.kind == inspect.Parameter.VAR_KEYWORD
                    for param in execute_signature.parameters.values()
                )
            ):
                execute_kwargs["phase_name"] = execution_phase_name

            response, token_usage, permission_denials, cli_command_args, streaming_log, model = self.agent_manager.execute(
                agent_name,
                prompt,
                **execute_kwargs,
            )

            actual_agent_cli = getattr(self.agent_manager, "get_last_cli", lambda: None)()
            if (
                actual_agent_cli is not None
                and hasattr(actual_agent_cli, "value")
                and isinstance(actual_agent_cli.value, str)
            ):
                agent_cli = actual_agent_cli.value

            last_session_getter = getattr(self.agent_manager, "get_last_session_id", None)
            has_valid_last_session = False
            if callable(last_session_getter):
                actual_session_id = last_session_getter()
                if isinstance(actual_session_id, str) or actual_session_id is None:
                    agent_session_id = actual_session_id
                    has_valid_last_session = True
            if not has_valid_last_session:
                agent_session_id = agent_executor.config.session_id

            self._record_active_agent_cli(
                agent_name=agent_name,
                agent_cli=agent_cli,
                model=model,
                phase_specific_data=phase_specific_data,
            )

            # Accumulate token usage for this iteration
            accumulate_token_usage(cumulative_token_usage, token_usage)

        except Exception as e:
            # Agent execution failed - attempt recovery
            from cafe.agents.executor import AgentExecutionError
            from cafe.core.types import CriticalPhaseError

            display_error = getattr(e, "display_message", None) or str(e)
            print(f"⚠️  Agent execution failed: {display_error}")

            # 4a. Check if it's a critical error - fail immediately without recovery
            is_critical_error = (
                isinstance(e, AgentExecutionError) and 
                hasattr(e, "error_type") and 
                e.error_type in ("rate_limit", "cli_not_found", "cli_unavailable", "model_not_found")
            )
            
            if is_critical_error:
                print(f"❌ Critical error detected ({e.error_type}) - stopping execution\n")

                # Update iteration context with error info
                if context_file.exists():
                    with open(context_file, "r", encoding="utf-8") as f:
                        context_data = json.load(f)

                    context_data["response"] = None
                    if persist_status:
                        context_data["status_code"] = None
                    else:
                        context_data.pop("status_code", None)
                    context_data["error"] = str(e)
                    context_data["display_error"] = display_error
                    context_data["error_type"] = e.error_type
                    context_data["is_critical"] = True

                    with open(context_file, "w", encoding="utf-8") as f:
                        json.dump(context_data, f, ensure_ascii=False, indent=2)

                # Create a CriticalError wrapper to signal this should stop the workflow
                raise CriticalPhaseError(
                    message=display_error,
                    error_type=e.error_type,
                    phase_name=getattr(self, '__class__', type(self)).__name__
                ) from e

            # 4b. Check if agent wrote output files
            written_files = self._detect_written_output_files()
            changed_written_files = self._filter_changed_output_files(
                written_files,
                pre_execution_output_snapshot,
            )

            # 4c. Attempt to recover response from written files
            recovered_response, recovered_status_code = self._recover_from_written_files(
                changed_written_files,
                valid_intents,
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
                "changed_written_files": [str(f) for f in changed_written_files],
                "recovered_response": bool(recovered_response),
                "recovered_status": recovered_status_code.value if recovered_status_code else None,
            }
            with open(error_file, "w", encoding="utf-8") as f:
                json.dump(error_data, f, ensure_ascii=False, indent=2)

            if recovered_response and recovered_status_code:
                # 4e. Recovery successful - treat as partial success
                print(f"✅ Recovered response from {changed_written_files[0].name}")
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
                    model=model,
                    persist_status=persist_status,
                )

                # Save progress
                if persist_status and hasattr(self, "_save_progress") and status_code is not None:
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
                    if persist_status:
                        history_data["status_code"] = None
                    else:
                        history_data.pop("status_code", None)
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
            # Note: streaming.jsonl is already written in real-time by executor
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
                token_usage=cumulative_token_usage,
                model=model,
                persist_status=persist_status,
            )
            return response, no_response_status

        if not require_status_code:
            self._update_iteration_history(
                phase_specific_data={
                    "response": response,
                    "permission_denials": [denial.model_dump() for denial in permission_denials],
                    "streaming_log": streaming_log,
                },
                prompt=prompt,
                agent_cli=agent_cli,
                agent_session_id=agent_session_id,
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                cli_command_args=cli_command_args,
                status_code=None,
                token_usage=cumulative_token_usage,
                model=model,
                persist_status=persist_status,
            )
            return response, None

        # 6. Extract status code
        status_code = self._extract_status_code_from_response(
            response,
            valid_codes=valid_intents,
        )
        if (
            status_code is None
            and permission_denials
            and PhaseStatusCode.NEED_PERMISSION in valid_intents
        ):
            status_code = PhaseStatusCode.NEED_PERMISSION
        elif status_code is None:
            inferred_human_input_status = self._infer_human_input_status_from_response(response)
            if inferred_human_input_status in valid_intents:
                status_code = inferred_human_input_status

        # 6.1. Record missing status code in error log (single-pass mode, no retry loop)
        if require_status_code and status_code is None:
            self._write_status_code_error_log(
                original_response=response,
                valid_intents=valid_intents,
                status_code=status_code,
                analysis_attempted=False,
                analysis_response=None,
                cli_command_args=cli_command_args,
                multiple_codes_found=None,
            )

        # 6.2. Missing required status code is now a hard failure in single-pass mode.
        if require_status_code and status_code is None:
            error_msg = "Agent did not return a valid status code"
            print(f"\n❌ {error_msg}")
            raise ValueError(error_msg)

        # 7. Update history (save metadata only, NOT response/status_code yet)
        # Response and status_code will be saved after checklist validation passes
        phase_data = {
            "response": response,
            "permission_denials": [denial.model_dump() for denial in permission_denials],
            "streaming_log": streaming_log
        }

        # Note: streaming.jsonl is already written in real-time by executor
        self._update_iteration_history(
            phase_specific_data=phase_data,
            prompt=prompt,
            agent_cli=agent_cli,
            agent_session_id=agent_session_id,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            cli_command_args=cli_command_args,
            status_code=None,  # Don't save status_code yet - will be saved after checklist validation
            token_usage=cumulative_token_usage,
            model=model,
            persist_status=persist_status,
        )

        # 8. Don't save progress yet - will be saved after checklist validation
        # (Removed: _save_progress will be called after checklist validation)

        return response, status_code

    def _get_status_file(self) -> Path:
        """Get the path to status.json file.

        Returns:
            Path to status.json in {phase_dir}/status.json

        For workflow steps:
            - GenericPhase: .cafe/issues/myissue/{step}/iteration_001/iteration.json
            - Legacy phase classes may still use {phase_dir}/status.json (being retired)
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

    @staticmethod
    def _snapshot_output_files(files: List[Path]) -> Dict[Path, Optional[str]]:
        """Capture file contents before agent execution."""
        snapshot: Dict[Path, Optional[str]] = {}
        for file_path in files:
            if file_path.exists():
                snapshot[file_path] = file_path.read_text(encoding="utf-8")
            else:
                snapshot[file_path] = None
        return snapshot

    @staticmethod
    def _filter_changed_output_files(
        files: List[Path],
        snapshot: Dict[Path, Optional[str]],
    ) -> List[Path]:
        """Keep only files whose contents changed during this execution."""
        changed: List[Path] = []
        for file_path in files:
            if not file_path.exists():
                continue
            previous_content = snapshot.get(file_path)
            current_content = file_path.read_text(encoding="utf-8")
            if previous_content != current_content:
                changed.append(file_path)
        return changed

    def _recover_from_written_files(
        self,
        written_files: List[Path],
        valid_intents: List[PhaseStatusCode],
    ) -> tuple[Optional[str], Optional[PhaseStatusCode]]:
        """Attempt to recover agent response from written files.

        Args:
            written_files: List of files written by agent before failure
            valid_intents: Valid status codes for this phase

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
            status_code = self._extract_status_code_from_response(
                recovered_response,
                valid_codes=valid_intents,
            )

            return recovered_response, status_code
        except Exception as e:
            # Log recovery failure but do not raise
            print(f"⚠️  Failed to recover from written files: {e}")
            return None, None

    def _save_progress(
        self,
        status_code: PhaseStatusCode,
        complete_codes: Optional[List[PhaseStatusCode]] = None,
    ) -> None:
        """Save phase progress to status.json (common method).

        Args:
            status_code: Phase status code (CONFIRMED, READY_FOR_REVIEW, NEED_CLARIFICATION, etc.)
            complete_codes: Optional list of status codes that indicate completion.
                           If not provided, defaults to [CONFIRMED, READY_FOR_REVIEW]
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "phase_name"):
            raise AttributeError("Phase must have 'phase_name' attribute (e.g., 'spec', 'plan')")

        status_file = self._get_status_file()
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Determine phase status based on status code
        if complete_codes is None:
            complete_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.READY_FOR_REVIEW]
        phase_status = PhaseStatus.COMPLETED if status_code in complete_codes else PhaseStatus.IN_PROGRESS

        # Always set end_time since status.json reflects the last iteration
        end_time = datetime.now().astimezone()

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

    def _get_phase_end_time(self, phase_name: str) -> Optional[str]:
        """Get the end_time of the latest iteration for a specified phase.

        Args:
            phase_name: Name of the phase (e.g., 'develop', 'review', 'plan')

        Returns:
            ISO format end_time string if phase status exists and has end_time, None otherwise
        """
        latest_context = self._get_latest_iteration_context(phase_name, require_completed=True)
        if latest_context:
            end_time = latest_context.get("end_time")
            if isinstance(end_time, str) and end_time:
                return end_time

        phase_status_file = self.issue_dir / phase_name / "status.json"
        if not phase_status_file.exists():
            return None

        try:
            with open(phase_status_file, 'r', encoding='utf-8') as f:
                phase_status = json.load(f)
            return phase_status.get("end_time")
        except (json.JSONDecodeError, KeyError, IOError):
            return None

    def _get_latest_iteration_context(
        self,
        phase_name: str,
        *,
        require_completed: bool = False,
        valid_codes: Optional[List[PhaseStatusCode]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Load the latest iteration context for a phase.

        Args:
            phase_name: Name of the phase (e.g., "develop", "review", "pr")
            require_completed: When True, skip incomplete iterations
            valid_codes: Optional valid status codes for completion detection

        Returns:
            Parsed context dict for the latest matching iteration, or None
        """
        phase_dir = self.issue_dir / phase_name
        if not phase_dir.exists():
            return None

        for context_file in reversed(sorted(
            [self._resolve_iteration_context_file(d) for d in phase_dir.glob("iteration_*") if d.is_dir()],
            key=lambda p: p.parent.name,
        )):
            try:
                with open(context_file, "r", encoding="utf-8") as f:
                    context = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

            if require_completed and not self._context_marks_completed(
                context,
                valid_codes=valid_codes,
            ):
                continue
            return context

        return None

    def _print_token_usage_summary(self) -> None:
        """Display token usage summary (common method).

        This method gets total token usage from agent_manager and displays summary.
        Suitable for displaying cost statistics when phase completes.
        """
        if not hasattr(self, "agent_manager"):
            return

        try:
            token_usage = self.agent_manager.get_total_token_usage()
            model = self.agent_manager.get_last_model()

            # Verify we have real token usage data (not mocks)
            if not isinstance(token_usage.input_tokens, int):
                return
        except (AttributeError, TypeError):
            return

        print()
        print("=" * 60)
        print("📊 Token Usage Summary")
        print("=" * 60)

        # Model
        model_str = model if model else "--"
        print(f"Model:                     {model_str}")

        # Input tokens
        input_str = f"{token_usage.input_tokens:,}" if token_usage.input_tokens > 0 else "--"
        print(f"Input tokens:              {input_str}")

        # Output tokens
        output_str = f"{token_usage.output_tokens:,}" if token_usage.output_tokens > 0 else "--"
        print(f"Output tokens:             {output_str}")

        # Cache creation tokens
        cache_create_str = f"{token_usage.cache_creation_input_tokens:,}" if token_usage.cache_creation_input_tokens > 0 else "--"
        print(f"Cache creation tokens:     {cache_create_str}")

        # Cache read tokens
        cache_read_str = f"{token_usage.cache_read_input_tokens:,}" if token_usage.cache_read_input_tokens > 0 else "--"
        print(f"Cache read tokens:         {cache_read_str}")

        # Duration (API)
        if token_usage.duration_api_ms is not None:
            duration_api_sec = token_usage.duration_api_ms / 1000
            duration_api_str = f"{duration_api_sec:.1f}s"
        else:
            duration_api_str = "--"
        print(f"Duration (API):            {duration_api_str}")

        # Duration (total)
        if token_usage.duration_ms is not None:
            duration_sec = token_usage.duration_ms / 1000
            duration_str = f"{duration_sec:.1f}s"
        else:
            duration_str = "--"
        print(f"Duration (total):          {duration_str}")

        # Total cost
        cost_str = f"${token_usage.total_cost_usd:.4f}" if token_usage.total_cost_usd > 0 else "--"
        print(f"Total cost:                {cost_str}")

        print("=" * 60)
        print()

    def _resolve_iteration_context_file(self, iteration_dir: Path) -> Path:
        """Return the iteration context file path, with backward-compatible fallback.

        Prefers iteration.json; falls back to context.json (old name) with a
        deprecation warning. Returns the iteration.json path even when neither
        file exists (callers should check .exists() before reading).
        """
        new_path = iteration_dir / "iteration.json"
        if new_path.exists():
            return new_path

        old_path = iteration_dir / "context.json"
        if old_path.exists():
            logger.warning(
                "[deprecation] context.json found in %s; please rename to iteration.json",
                iteration_dir,
            )
            return old_path

        return new_path

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
        prev_context_file = self._resolve_iteration_context_file(prev_iteration_dir)
        if not prev_context_file.exists():
            return None

        with open(prev_context_file, "r", encoding="utf-8") as f:
            return json.load(f)

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
        current_context_file = self._resolve_iteration_context_file(current_iteration_dir)
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

        # Fallback: read from iteration_XXX/iteration.json (or context.json)
        iteration_dirs = sorted(phase_dir.glob("iteration_*"))
        if not iteration_dirs:
            return 0

        # Search from back to front for first complete iteration
        for iteration_dir in reversed(iteration_dirs):
            context_file = self._resolve_iteration_context_file(iteration_dir)
            if not context_file.exists():
                continue

            with open(context_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if self._context_marks_completed(data):
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

        status_code_value = str(status_data.get("status_code", "") or "")
        if not status_code_value:
            latest_context = self._get_latest_iteration_context(
                self.phase_name,
                require_completed=True,
                valid_codes=complete_status_codes,
            )
            if latest_context:
                status_code_value = self._context_status_code(
                    latest_context,
                    valid_codes=complete_status_codes,
                ) or ""
        for complete_code in complete_status_codes:
            if status_code_value == complete_code.value:
                self._cleanup_sandbox_approval_artifacts()
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
        valid_intents: List[PhaseStatusCode],
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
            valid_intents: List of valid status codes
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
            valid_intents=valid_intents,
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
                valid_intents=valid_intents,
                allowed_tools=allowed_tools or ["write", "read"],
                max_retries=3,
            )

            # Update response and status_code with validated results
            # Keep existing status_code if validation couldn't extract one
            # (context.json response may be stale after continue-execution)
            if validation_passed:
                response = final_response
                if final_status_code is not None:
                    status_code = final_status_code

                # Checklist validation passed - now we can save status_code to iteration.json
                iteration_dir = self._get_iteration_dir(self.iteration)
                context_file = self._resolve_iteration_context_file(iteration_dir)
                if context_file.exists():
                    with open(context_file, "r", encoding="utf-8") as f:
                        context_data = json.load(f)
                    context_data["status_code"] = status_code.value if status_code else None
                    with open(context_file, "w", encoding="utf-8") as f:
                        json.dump(context_data, f, ensure_ascii=False, indent=2)

                # Save progress (phase-specific logic)
                if hasattr(self, "_save_progress") and status_code is not None:
                    self._save_progress(status_code)  # type: ignore
            else:
                # Validation failed after max retries - alert user
                print(f"⚠️  Checklist validation failed after maximum retries")
                # Continue with original response and status_code
        else:
            # No checklist validation needed - save status_code to iteration.json directly
            if status_code is None:
                raise ValueError(f"Failed to extract status code from agent response in iteration {self.iteration}")
            iteration_dir = self._get_iteration_dir(self.iteration)
            context_file = self._resolve_iteration_context_file(iteration_dir)
            if context_file.exists():
                with open(context_file, "r", encoding="utf-8") as f:
                    context_data = json.load(f)
                context_data["status_code"] = status_code.value
                with open(context_file, "w", encoding="utf-8") as f:
                    json.dump(context_data, f, ensure_ascii=False, indent=2)

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
            self._cleanup_sandbox_approval_artifacts()
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
            self._cleanup_sandbox_approval_artifacts()
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
            self._cleanup_sandbox_approval_artifacts()
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
