"""Review-interaction mixin for Phase – user decisions, clarification, permission, host-execution followup."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from InquirerPy.separator import Separator

from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus
from cafe.ui.chat import launch_chat_session
from cafe.ui.inquirer_prompts import prompt_list, prompt_multiline


class PhaseReviewMixin:
    """Mixin with user-interaction/review helpers for Phase.

    Covers READY_FOR_REVIEW decisions, NEED_CLARIFICATION / NEED_PERMISSION
    input handling, editor integration, questions.xml validation, and
    host-execution follow-up guidance.
    """

    def _ask_user_for_review_decision(
        self,
        item_name: str = "content",
        agent_name: str = "Developer",
        role: str = "developer",
        output_file: Optional[Path] = None,
        display_callback: Optional[Callable[[], None]] = None,
        edit_option_label: Optional[str] = None,
    ) -> str:
        """Ask user for decision on READY_FOR_REVIEW (interactive mode).

        Args:
            item_name: Item name to confirm (e.g. "plan", "code", "requirements")
            agent_name: Agent name (e.g. "Developer", "PM", "Reviewer")
            role: Step-declared agent role for chat, resolved by the active playbook.
            output_file: Optional path to the agent's output file. When provided,
                         its content is re-printed after returning from chat so the
                         user can re-read it before making a decision.
            display_callback: Optional callable to invoke after returning from chat
                              to re-display content (e.g. Rich Syntax diff). Takes
                              precedence over output_file when both are provided.
            edit_option_label: Optional label text for Edit option.
                               When provided with output_file, adds the
                               provided label to the review menu.

        Returns:
            str: "confirm" or modification opinion content
        """
        issue_name = getattr(self, "issue_name", None) or ""

        print(f"{agent_name} thinks {item_name} is complete. Please confirm:")

        while True:
            # Use prompt_list for better UX with arrow keys
            choices = [
                {"name": "Confirm - Continue", "value": "c"},
                {"name": "Request modification - Send feedback", "value": "m"},
            ]
            if edit_option_label and output_file:
                choices.append({"name": edit_option_label, "value": "edit"})
            choices.extend([
                Separator(),
                {"name": f"Chat with {agent_name}", "value": "chat"},
            ])

            choice = prompt_list(
                "Please select an option",
                choices,
                default=None,
            )

            if choice == "chat":
                launch_chat_session(role, issue_name)
                if display_callback is not None:
                    display_callback()
                elif output_file and output_file.exists():
                    print()
                    print(f"{'=' * 60}")
                    print(output_file.read_text())
                    print(f"{'=' * 60}")
                continue

            if choice == "edit":
                if output_file is None or not output_file.exists():
                    print("\n⚠️  Current output file not found, please try another action.")
                    continue

                if self._open_file_with_editor(output_file):
                    if display_callback is not None:
                        display_callback()
                    else:
                        print()
                        print(f"{'=' * 60}")
                        print(output_file.read_text())
                        print(f"{'=' * 60}")
                continue

            if choice == "c":
                return "confirm"

            # choice == "m"
            modification_request = prompt_multiline("Please enter modification opinion")

            if not modification_request.strip():
                print("\n⚠️  No modification opinion entered, please try again.")
                continue

            print()
            print("✅ Received your modification opinion...")
            print()

            return modification_request

    def _open_file_with_editor(self, file_path: Path) -> bool:
        """Open a file in user's editor.

        Args:
            file_path: File path to edit

        Returns:
            True if editor exits successfully, False otherwise.
        """
        editor = os.environ.get("EDITOR", "vim")

        try:
            subprocess.run([editor, str(file_path)], check=True)
            return True
        except subprocess.CalledProcessError:
            print("Error: Failed to edit file")
            return False
        except FileNotFoundError:
            print(f"Error: Editor '{editor}' not found")
            print("Set EDITOR environment variable or install vim")
            return False

    def _ask_user_for_clarification(
        self, role: Optional[str] = None, agent_name: Optional[str] = None
    ) -> str:
        """Ask user for answer to NEED_CLARIFICATION (interactive mode).

        When role is provided, shows a select prompt with a "Chat with agent"
        option before the multiline text prompt.

        Args:
            role: Step-declared agent role for inline chat, resolved by the active playbook.
                  When provided, a "Chat with [agent_name]" option is shown.
            agent_name: Display name of the agent (e.g. "Roger", "David"). Used in
                        the "Chat with [agent_name]" label. Falls back to role if not given.

        Returns:
            str: User's answer
        """
        if role is None:
            return prompt_multiline("Please answer the question")

        issue_name = getattr(self, "issue_name", None) or ""
        chat_label = agent_name or role

        while True:
            choices = [
                {"name": "Answer question (text input)", "value": "answer"},
                Separator(),
                {"name": f"Chat with {chat_label}", "value": "chat"},
            ]

            selection = prompt_list("Please select an option", choices, default=None)

            if selection == "chat":
                launch_chat_session(role, issue_name)
                continue

            return prompt_multiline("Please answer the question")

    def _validate_and_retry_questions_xml(
        self,
        xml_path: Path,
        agent_name: str,
        allowed_tools: List[str],
        max_retries: int = 3,
    ) -> bool:
        """Validate questions.xml and retry with agent if invalid.

        Args:
            xml_path: Path to questions.xml file
            agent_name: Agent name for retry execution
            allowed_tools: Tools allowed for agent
            max_retries: Maximum number of retry attempts (default: 3)

        Returns:
            True if XML is valid (or was fixed), False otherwise
        """
        from cafe.core.questions_schema import validate_questions_xml

        # Require agent_manager attribute
        if not hasattr(self, 'agent_manager'):
            raise AttributeError("Phase must have 'agent_manager' attribute to validate questions XML")

        # Check if file exists
        if not xml_path.exists():
            return False

        # First validation attempt
        if validate_questions_xml(xml_path):
            return True

        # Retry loop: ask agent to fix invalid XML
        for retry in range(max_retries):
            print(f"\n⚠️  questions.xml format is invalid, asking agent to fix... (attempt {retry + 1}/{max_retries})")

            retry_prompt = (
                f"The questions XML file at {xml_path} has invalid format. "
                f"Please fix it so that:\n"
                f"- Root element is <questions>\n"
                f"- Each <question> has a unique id attribute, a <title>, and <options> with at least one <option>\n"
                f"- The file is well-formed XML\n\n"
                f"Read the file, fix the issues, and write the corrected XML back to {xml_path}."
            )

            try:
                self.agent_manager.execute(
                    agent_name,
                    retry_prompt,
                    allowed_tools=allowed_tools,
                )
            except Exception as e:
                print(f"⚠️  Error during XML fix retry: {e}")
                continue

            # Check if fixed
            if xml_path.exists() and validate_questions_xml(xml_path):
                print(f"✓ Agent successfully fixed questions.xml")
                return True

        # All retries failed - delete invalid file and fallback
        print(f"\n❌ questions.xml still invalid after {max_retries} attempts, falling back to original Q&A")
        if xml_path.exists():
            xml_path.unlink()
        return False

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
                        "status_code": "need_clarification",
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

    def _handle_need_permission_input(
        self,
        prev_data: dict,
        agent_display_name: str = "agent",
    ) -> Any:
        """Handle user input for NEED_PERMISSION when no structured denials exist."""
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "interactive"):
            raise AttributeError("Phase must have 'interactive' attribute")
        if not hasattr(self, "user_input"):
            raise AttributeError("Phase must have 'user_input' attribute")

        if self.interactive:
            permission_input = prompt_multiline(
                f"{agent_display_name} needs permission or guidance. Describe what is allowed."
            )
        else:
            permission_input = self.user_input
            self.user_input = ""

            if not permission_input:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=(
                        f"{self.phase_name.capitalize()} phase failed after iteration {self.iteration - 1}: "
                        "received NEED_PERMISSION in non-interactive mode without user input"
                    ),
                    data={
                        "iterations": self.iteration - 1,
                        "last_response": prev_data.get("response", ""),
                        "status_code": "need_permission",
                    },
                )

        if not permission_input.strip():
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message="User provided no response to permission request",
                data={
                    "iterations": self.iteration - 1,
                    "last_response": prev_data.get("response", ""),
                    "status_code": "need_permission",
                },
            )

        if self.interactive:
            print()
            print(f"✅ Received your answer, sending to {agent_display_name} for processing...")
            print()

        return permission_input

    def _build_host_execution_followup(self, iteration: int) -> str:
        """Build follow-up guidance from a previous host execution log.

        Returns an empty string when no host execution log exists or when every
        recorded command succeeded.
        """
        if not hasattr(self, "phase_dir"):
            raise AttributeError("Phase must have 'phase_dir' attribute")

        host_execution_file = self._get_iteration_dir(iteration) / "host_execution.json"
        if not host_execution_file.exists():
            return ""

        try:
            records = json.loads(host_execution_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ""

        if not isinstance(records, list):
            return ""

        failed_records = [
            record for record in records
            if isinstance(record, dict) and not record.get("ok", False)
        ]
        if not failed_records:
            return ""

        from cafe.utils.git_utils import to_cwd_relative_path

        try:
            display_path = to_cwd_relative_path(host_execution_file)
        except (ValueError, OSError):
            display_path = str(host_execution_file.resolve())

        first_failed = failed_records[0]
        command = str(first_failed.get("command") or "").strip()
        stderr = str(first_failed.get("stderr") or "").strip()
        stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        stderr_excerpt = "\n".join(stderr_lines[:8])

        lines = [
            "The host environment already attempted the previously blocked command, but it failed.",
            f"Review the execution log at {display_path}.",
        ]
        if command:
            lines.append(f"Failed command: {command}")
        if stderr_excerpt:
            lines.append("Failure summary:")
            lines.append(stderr_excerpt)
        lines.append(
            "This is not a new permission request. Continue from the current repo state and decide the next step."
        )
        return "\n".join(lines)
