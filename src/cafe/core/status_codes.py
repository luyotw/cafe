"""Status codes for phase execution control."""

from enum import Enum
from typing import List, Optional, Set


class PhaseStatusCode(str, Enum):
    """Status codes that agents can return to control workflow.

    These codes are designed to be:
    - Prefixed with CAFE_ to avoid false positives
    - Simple English words
    - Token-efficient
    - Easy for agents to understand and return
    """

    # ========== Universal Status Codes ==========
    NO_RESPONSE = "CAFE_NO_RESPONSE"       # Agent returned empty response

    # ========== Requirements & Analysis Phase ==========
    CONFIRMED = "CAFE_CONFIRMED"                   # Requirements/Analysis/Review confirmed
    NEED_CLARIFICATION = "CAFE_NEED_CLARIFICATION" # Need more information
    REJECTED = "CAFE_REJECTED"                     # DEPRECATED: No longer used
    READY_FOR_REVIEW = "CAFE_READY_FOR_REVIEW"     # Plan ready for user review

    # ========== Review Phase ==========
    NEEDS_CHANGES = "CAFE_NEEDS_CHANGES"   # Code needs changes

    # ========== Develop Phase ==========
    NO_CHANGES_NEEDED = "CAFE_NO_CHANGES_NEEDED"  # Developer believes no changes needed (dispute reviewer)
    CONFIRMED_SKIP_REVIEW = "CAFE_CONFIRMED_SKIP_REVIEW"  # User confirmed to skip review phase (development completed)

    # ========== Authorization ==========
    NEED_PERMISSION = "CAFE_NEED_PERMISSION"       # Need user permission


class StatusCodeParser:
    """Parser for extracting status codes from agent responses."""

    STATUS_CODE_ALIASES = {
        "CAFE_SPEC_READY": PhaseStatusCode.READY_FOR_REVIEW.value,
        "CAFE_PLAN_READY": PhaseStatusCode.READY_FOR_REVIEW.value,
        "CAFE_DEVELOP_DONE": PhaseStatusCode.CONFIRMED.value,
        "CAFE_PR_READY": PhaseStatusCode.READY_FOR_REVIEW.value,
    }

    @classmethod
    def _normalize_aliases(cls, response: str) -> str:
        normalized = response
        for alias, canonical in cls.STATUS_CODE_ALIASES.items():
            normalized = normalized.replace(alias, canonical)
        return normalized

    @staticmethod
    def extract(response: str, valid_codes: Optional[List[PhaseStatusCode]] = None) -> Optional[PhaseStatusCode]:
        """Extract status code from agent response.

        Args:
            response: Agent response text
            valid_codes: Optional list of valid codes for this context

        Returns:
            Extracted status code or None if not found or multiple different codes found

        Examples:
            >>> extract("CAFE_CONFIRMED\\nThe requirements are clear.")
            PhaseStatusCode.CONFIRMED

            >>> extract("I think this is good. CAFE_CONFIRMED!")
            PhaseStatusCode.CONFIRMED

            >>> extract("CAFE_NEED_CLARIFICATION\\nCAFE_READY_FOR_REVIEW\\nContent...")
            None  # Multiple different codes found, treated as abnormal state
        """
        if not response:
            return None
        response = StatusCodeParser._normalize_aliases(response.upper())

        # First, check if there are multiple different status codes
        all_codes = StatusCodeParser.extract_all(response, valid_codes)
        if len(all_codes) > 1:
            # Multiple different codes found, treat as abnormal state
            return None

        # If only one type of code or none, continue with original logic
        # (to maintain backward compatibility with priority: first line > entire response)
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
        response_upper = response

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
    def extract_all(response: str, valid_codes: Optional[List[PhaseStatusCode]] = None) -> Set[PhaseStatusCode]:
        """Extract all status codes from agent response.

        Args:
            response: Agent response text
            valid_codes: Optional list of valid codes for this context

        Returns:
            Set of all found status codes (empty set if none found)

        Examples:
            >>> extract_all("CAFE_CONFIRMED\\nCAFE_NEED_CLARIFICATION\\nContent...")
            {PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION}

            >>> extract_all("CAFE_NEED_CLARIFICATION\\nCAFE_NEED_CLARIFICATION\\nContent...")
            {PhaseStatusCode.NEED_CLARIFICATION}
        """
        if not response:
            return set()

        found_codes: Set[PhaseStatusCode] = set()
        response_upper = StatusCodeParser._normalize_aliases(response.upper())

        # Search for all status codes in the response
        # Sort by length descending so longer (more specific) codes are checked first
        codes_to_check = sorted(
            valid_codes if valid_codes else list(PhaseStatusCode),
            key=lambda x: len(x.value),
            reverse=True,
        )
        for code in codes_to_check:
            if code.value in response_upper:
                # Skip if this code is a prefix of an already-found longer code
                if any(found.value.startswith(code.value) for found in found_codes):
                    continue
                # Remove any already-found codes that are prefixes of this code
                found_codes = {
                    found for found in found_codes
                    if not code.value.startswith(found.value)
                }
                found_codes.add(code)

        return found_codes

    @staticmethod
    def is_success(code: Optional[PhaseStatusCode]) -> bool:
        """Check if status code indicates success.

        Args:
            code: Status code to check

        Returns:
            True if code indicates success
        """
        success_codes = {
            PhaseStatusCode.CONFIRMED,
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
        failure_codes: set[PhaseStatusCode] = set()
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
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.NEEDS_CHANGES,
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
            PhaseStatusCode.NEED_PERMISSION,
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.READY_FOR_REVIEW,
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
        "Please clearly indicate the status code on the first line of your response (must include CAFE_ prefix):",
        ""
    ]

    for code in valid_codes:
        description = descriptions.get(code, "")
        lines.append(f"- {code.value}: {description}")

    lines.extend([
        "",
        "**Response format:**",
        "- Return ONLY the status code on the first line",
        "- Do NOT include any summary or explanation",
    ])

    return "\n".join(lines)
