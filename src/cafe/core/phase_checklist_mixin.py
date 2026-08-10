"""Checklist-validation mixin for Phase – retry, rebuild, and completion-reminder helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from cafe.core.session_continuation import SessionContinuation
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import AgentCLI


class PhaseChecklistMixin:
    """Mixin with checklist validation, retry, rebuild, and reminder helpers for Phase.

    Covers:
    - _validate_and_retry_checklist_completion: validates checklist items and retries
      with the agent up to max_retries times.
    - _rebuild_checklist_for_iteration: rebuilds a placeholder checklist when missing.
    - _get_checklist_completion_reminder: generates reminder text for prompts.
    """

    def _validate_and_retry_checklist_completion(
        self,
        agent_name: str,
        prompt: str,
        user_input: str,
        valid_intents: List[PhaseStatusCode],
        allowed_tools: Optional[List[str]] = None,
        max_read_only_commands: Optional[int] = None,
        max_retries: int = 3,
    ) -> tuple[str, Optional[PhaseStatusCode], bool]:
        """Validate checklist completion and retry if incomplete.

        This method validates that all checklist items are completed. If unchecked
        items are found, it will re-invoke the agent to complete them, up to max_retries times.

        Args:
            agent_name: Agent name
            prompt: Original prompt sent to agent
            user_input: User input for this iteration
            valid_intents: Valid status codes for this phase
            allowed_tools: Tools available to agent
            max_read_only_commands: Declared read-only progress guard limit, if enabled
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
            context_file = self._resolve_iteration_context_file(iteration_dir)
            if context_file.exists():
                with open(context_file, "r", encoding="utf-8") as f:
                    context_data = json.load(f)
                    response = context_data.get("response", "")
                    status_code = self._extract_status_code_from_response(
                        response,
                        valid_codes=valid_intents,
                    )
                    return response, status_code, True
            return "", None, True

        if result.is_complete:
            print(f"✅ Checklist validation passed - all items completed")
            # Get current response from context
            context_file = self._resolve_iteration_context_file(iteration_dir)
            if context_file.exists():
                with open(context_file, "r", encoding="utf-8") as f:
                    context_data = json.load(f)
                    response = context_data.get("response", "")
                    status_code = self._extract_status_code_from_response(
                        response,
                        valid_codes=valid_intents,
                    )
                    return response, status_code, True
            return "", None, True

        # Checklist incomplete - start retry loop
        print(f"⚠️  Checklist validation failed - {result.unchecked_count} unchecked items found")

        retry_count = 0
        while retry_count < max_retries:
            retry_count += 1
            print(
                f"\n⚠️  Re-invoking agent to complete checklist items... (attempt {retry_count}/{max_retries})"
            )

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
                execute_kwargs = {
                    "allowed_tools": allowed_tools,
                    "allowed_directories": self._get_allowed_directories(),
                }
                execute_method = self.agent_manager.execute
                if self._call_accepts_keyword(execute_method, "phase_name"):
                    execute_kwargs["phase_name"] = getattr(self, "phase_name", None)
                if self._call_accepts_keyword(execute_method, "continuation"):
                    execute_kwargs["continuation"] = self._current_session_continuation()
                if self._call_accepts_keyword(execute_method, "max_read_only_commands"):
                    execute_kwargs["max_read_only_commands"] = max_read_only_commands
                retry_response, retry_token_usage, _, _, retry_streaming_log, retry_model = (
                    self.agent_manager.execute(
                        agent_name,
                        retry_prompt,
                        **execute_kwargs,
                    )
                )
                self._merge_iteration_token_usage(retry_token_usage)

                actual_cli = getattr(self.agent_manager, "get_last_cli", lambda: None)()
                actual_session_id = getattr(
                    self.agent_manager,
                    "get_last_session_id",
                    lambda: None,
                )()
                if (
                    isinstance(actual_cli, AgentCLI)
                    and isinstance(actual_session_id, str)
                    and actual_session_id
                ):
                    self._session_continuation = SessionContinuation.resume_exact(
                        actual_cli,
                        actual_session_id,
                    )

                # Extract status code from retry response
                retry_status_code = self._extract_status_code_from_response(
                    retry_response,
                    valid_codes=valid_intents,
                )

                # Validate checklist again
                retry_result = validate_checklist(checklist_path)

                if retry_result.is_complete and retry_status_code is not None:
                    print(f"✅ Checklist validation passed after retry {retry_count}")

                    # Merge streaming logs
                    context_file = self._resolve_iteration_context_file(iteration_dir)
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

                    # Update iteration.json with final response and merged streaming_log
                    # Note: response is NOT merged, only keep the last one
                    if context_file.exists():
                        with open(context_file, "r", encoding="utf-8") as f:
                            context_data = json.load(f)

                        context_data["response"] = retry_response  # Keep last response only
                        context_data["streaming_log"] = merged_streaming_log
                        context_data["checklist_validation_attempts"] = retry_count
                        context_data["status_code"] = (
                            retry_status_code.value if retry_status_code else None
                        )
                        # Update model if retry returned one
                        if retry_model is not None:
                            context_data["model"] = retry_model

                        with open(context_file, "w", encoding="utf-8") as f:
                            json.dump(context_data, f, ensure_ascii=False, indent=2)

                    return retry_response, retry_status_code, True

                elif not retry_result.is_complete:
                    print(
                        f"⚠️  Checklist still has {retry_result.unchecked_count} unchecked items after retry {retry_count}"
                    )
                else:
                    # Checklist is complete but no valid status code extracted
                    print(
                        f"⚠️  Checklist complete but failed to extract valid status code from retry response (attempt {retry_count}/{max_retries})"
                    )

            except Exception as e:
                print(f"⚠️  Failed to retry checklist completion: {e}")
                # Continue to next retry

        # Max retries reached
        print(f"\n❌ Maximum retry attempts ({max_retries}) reached. Checklist still incomplete.")
        print(f"   Please complete the checklist manually and re-run the command.")

        # Return the last response and status code
        context_file = self._resolve_iteration_context_file(iteration_dir)
        if context_file.exists():
            with open(context_file, "r", encoding="utf-8") as f:
                context_data = json.load(f)
                response = context_data.get("response", "")
                status_code = self._extract_status_code_from_response(
                    response,
                    valid_codes=valid_intents,
                )
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
        print(
            f"   Phase should override _rebuild_checklist_for_iteration() for proper checklist generation."
        )

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
