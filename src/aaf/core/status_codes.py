"""Status codes for phase execution control."""

from enum import Enum
from typing import List, Optional


class PhaseStatusCode(str, Enum):
    """Status codes that agents can return to control workflow.

    These codes are designed to be:
    - Prefixed with AAF_ to avoid false positives
    - Simple English words
    - Token-efficient
    - Easy for agents to understand and return
    """

    # ========== Universal Status Codes ==========
    COMPLETED = "AAF_COMPLETED"           # Phase successfully completed
    FAILED = "AAF_FAILED"                 # Phase failed, stop workflow
    RETRY = "AAF_RETRY"                   # Retry this phase
    MANUAL_REVIEW = "AAF_MANUAL_REVIEW"   # Need human review
    SKIP = "AAF_SKIP"                     # Skip this phase

    # ========== Requirements & Analysis Phase ==========
    CONFIRMED = "AAF_CONFIRMED"                   # Requirements/Analysis confirmed
    NEED_CLARIFICATION = "AAF_NEED_CLARIFICATION" # Need more information
    REJECTED = "AAF_REJECTED"                     # Requirements/Analysis rejected

    # ========== Review Phase ==========
    APPROVED = "AAF_APPROVED"             # Code review approved
    LGTM = "AAF_LGTM"                     # Looks Good To Me (approved)
    NEEDS_CHANGES = "AAF_NEEDS_CHANGES"   # Code needs changes
    NEEDS_MAJOR_CHANGES = "AAF_NEEDS_MAJOR_CHANGES"  # Major refactoring needed

    # ========== Implementation Phase ==========
    COMMITTED = "AAF_COMMITTED"           # Code committed successfully
    NO_CHANGES = "AAF_NO_CHANGES"         # No code changes needed

    # ========== Authorization ==========
    NEED_PERMISSION = "AAF_NEED_PERMISSION"       # Need user permission
    PERMISSION_GRANTED = "AAF_PERMISSION_GRANTED" # Permission granted
    PERMISSION_DENIED = "AAF_PERMISSION_DENIED"   # Permission denied


class StatusCodeParser:
    """Parser for extracting status codes from agent responses."""

    @staticmethod
    def extract(response: str, valid_codes: Optional[List[PhaseStatusCode]] = None) -> Optional[PhaseStatusCode]:
        """Extract status code from agent response.

        Args:
            response: Agent response text
            valid_codes: Optional list of valid codes for this context

        Returns:
            Extracted status code or None if not found

        Examples:
            >>> extract("AAF_CONFIRMED\\nThe requirements are clear.")
            PhaseStatusCode.CONFIRMED

            >>> extract("I think this is good. AAF_LGTM!")
            PhaseStatusCode.LGTM
        """
        if not response:
            return None

        # Check first line (recommended format)
        first_line = response.strip().split('\n')[0].strip().upper()

        # Try exact match on first line
        try:
            code = PhaseStatusCode(first_line)
            if valid_codes is None or code in valid_codes:
                return code
        except ValueError:
            pass

        # Try to find status code in first line (with potential prefix/suffix)
        # Check longer codes first to avoid partial matches
        for code in sorted(PhaseStatusCode, key=lambda x: len(x.value), reverse=True):
            if code.value in first_line:
                if valid_codes is None or code in valid_codes:
                    return code

        # Search in entire response (fallback)
        response_upper = response.upper()

        # If valid_codes specified, prioritize those
        if valid_codes:
            for code in valid_codes:
                if code.value in response_upper:
                    return code

        # Otherwise, try all codes (ordered by specificity)
        # Check longer/more specific codes first
        for code in sorted(PhaseStatusCode, key=lambda x: len(x.value), reverse=True):
            if code.value in response_upper:
                if valid_codes is None or code in valid_codes:
                    return code

        return None

    @staticmethod
    def is_success(code: Optional[PhaseStatusCode]) -> bool:
        """Check if status code indicates success.

        Args:
            code: Status code to check

        Returns:
            True if code indicates success
        """
        success_codes = {
            PhaseStatusCode.COMPLETED,
            PhaseStatusCode.CONFIRMED,
            PhaseStatusCode.APPROVED,
            PhaseStatusCode.LGTM,
            PhaseStatusCode.COMMITTED,
        }
        return code in success_codes

    @staticmethod
    def is_failure(code: Optional[PhaseStatusCode]) -> bool:
        """Check if status code indicates failure.

        Args:
            code: Status code to check

        Returns:
            True if code indicates failure
        """
        failure_codes = {
            PhaseStatusCode.FAILED,
            PhaseStatusCode.REJECTED,
            PhaseStatusCode.PERMISSION_DENIED,
        }
        return code in failure_codes

    @staticmethod
    def is_retry(code: Optional[PhaseStatusCode]) -> bool:
        """Check if status code indicates retry/continue.

        Args:
            code: Status code to check

        Returns:
            True if code indicates retry/continue
        """
        retry_codes = {
            PhaseStatusCode.RETRY,
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.NEEDS_CHANGES,
            PhaseStatusCode.NEEDS_MAJOR_CHANGES,
            PhaseStatusCode.NEED_PERMISSION,
        }
        return code in retry_codes

    @staticmethod
    def needs_human_input(code: Optional[PhaseStatusCode]) -> bool:
        """Check if status code requires human input.

        Args:
            code: Status code to check

        Returns:
            True if human input needed
        """
        human_input_codes = {
            PhaseStatusCode.MANUAL_REVIEW,
            PhaseStatusCode.NEED_PERMISSION,
            PhaseStatusCode.NEED_CLARIFICATION,
        }
        return code in human_input_codes


def generate_status_code_prompt(valid_codes: List[PhaseStatusCode], descriptions: dict) -> str:
    """Generate prompt text instructing agent to use status codes.

    Args:
        valid_codes: List of valid status codes for this phase
        descriptions: Dict mapping codes to their descriptions

    Returns:
        Formatted prompt text

    Example:
        >>> codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION]
        >>> desc = {
        ...     PhaseStatusCode.CONFIRMED: "Requirements are clear",
        ...     PhaseStatusCode.NEED_CLARIFICATION: "Need more info"
        ... }
        >>> print(generate_status_code_prompt(codes, desc))
    """
    lines = [
        "請在回應的第一行明確標示狀態碼（必須包含 AAF_ 前綴）：",
        ""
    ]

    for code in valid_codes:
        description = descriptions.get(code, "")
        lines.append(f"- {code.value}: {description}")

    lines.extend([
        "",
        "範例回應格式：",
        f"{valid_codes[0].value}",
        "（接著是詳細說明...）"
    ])

    return "\n".join(lines)
