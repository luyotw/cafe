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

    NO_RESPONSE = "CAFE_NO_RESPONSE"
    CONFIRMED = "CAFE_CONFIRMED"
    NEED_CLARIFICATION = "CAFE_NEED_CLARIFICATION"
    REJECTED = "CAFE_REJECTED"
    READY_FOR_REVIEW = "CAFE_READY_FOR_REVIEW"
    NEEDS_CHANGES = "CAFE_NEEDS_CHANGES"
    NO_CHANGES_NEEDED = "CAFE_NO_CHANGES_NEEDED"
    CONFIRMED_SKIP_REVIEW = "CAFE_CONFIRMED_SKIP_REVIEW"
    NEED_PERMISSION = "CAFE_NEED_PERMISSION"


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
        if not response:
            return None
        response = StatusCodeParser._normalize_aliases(response.upper())
        all_codes = StatusCodeParser.extract_all(response, valid_codes)
        if len(all_codes) > 1:
            return None

        first_line = response.strip().split("\n")[0].strip().upper()
        try:
            code = PhaseStatusCode(first_line)
            if valid_codes is None or code in valid_codes:
                return code
        except ValueError:
            pass

        for code in sorted(PhaseStatusCode, key=lambda item: len(item.value), reverse=True):
            if code.value in first_line and (valid_codes is None or code in valid_codes):
                return code

        if valid_codes:
            for code in valid_codes:
                if code.value in response:
                    return code

        for code in sorted(PhaseStatusCode, key=lambda item: len(item.value), reverse=True):
            if code.value in response and (valid_codes is None or code in valid_codes):
                return code
        return None

    @staticmethod
    def extract_all(response: str, valid_codes: Optional[List[PhaseStatusCode]] = None) -> Set[PhaseStatusCode]:
        if not response:
            return set()

        found_codes: Set[PhaseStatusCode] = set()
        response_upper = StatusCodeParser._normalize_aliases(response.upper())
        codes_to_check = sorted(
            valid_codes if valid_codes else list(PhaseStatusCode),
            key=lambda item: len(item.value),
            reverse=True,
        )
        for code in codes_to_check:
            if code.value in response_upper:
                if any(found.value.startswith(code.value) for found in found_codes):
                    continue
                found_codes = {
                    found for found in found_codes if not code.value.startswith(found.value)
                }
                found_codes.add(code)
        return found_codes

    @staticmethod
    def is_success(code: Optional[PhaseStatusCode]) -> bool:
        return code in {PhaseStatusCode.CONFIRMED}

    @staticmethod
    def is_failure(code: Optional[PhaseStatusCode]) -> bool:
        return code in set()

    @staticmethod
    def is_retry(code: Optional[PhaseStatusCode]) -> bool:
        return code in {
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.NEEDS_CHANGES,
            PhaseStatusCode.NEED_PERMISSION,
        }

    @staticmethod
    def needs_human_input(code: Optional[PhaseStatusCode]) -> bool:
        return code in {
            PhaseStatusCode.NEED_PERMISSION,
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.READY_FOR_REVIEW,
        }


def generate_status_code_prompt(valid_codes: List[PhaseStatusCode], descriptions: dict) -> str:
    """Generate prompt text instructing agent to use status codes."""
    lines = [
        "Please clearly indicate the status code on the first line of your response (must include CAFE_ prefix):",
        "",
    ]
    for code in valid_codes:
        description = descriptions.get(code, "")
        lines.append(f"- {code.value}: {description}")
    lines.extend(
        [
            "",
            "**Response format:**",
            "- Return ONLY the status code on the first line",
            "- Do NOT include any summary or explanation",
        ]
    )
    return "\n".join(lines)
