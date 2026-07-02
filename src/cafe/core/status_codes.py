"""Step outcome tokens and (legacy) free-text parser for workflow execution.

Active:
- ``PhaseStatusCode`` enum — agent-declared step outcome tokens.
- ``PLAYBOOK_INTENT_KEYS`` — keys allowed in playbook step ``on`` maps.
- ``transition_map_key`` — collapses outcome tokens to playbook transition keys.

Legacy (kept for mock/legacy agent paths only):
- ``StatusCodeParser`` — extracts a ``PhaseStatusCode`` from a free-text agent
  response. Built-in skills and playbooks write structured batons via
  ``next_step.txt`` and do not depend on this parser. See issue #316 for the
  migration plan.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Set

# Keys allowed in playbook step ``on`` maps (plus ``default``).
PLAYBOOK_INTENT_KEYS: frozenset[str] = frozenset(
    {
        "await_agent",
        "confirm_output",
        "alignment_checkpoint",
        "need_clarification",
        "need_permission",
        "no_changes_needed",
        "workflow_complete",
        "manual_handoff",
    }
)


class PhaseStatusCode(str, Enum):
    """Agent-declared step outcomes.

    Values are plain tokens (no ``CAFE_`` prefix). Some values collapse to
    playbook ``on`` keys via :func:`transition_map_key`; others already match
    a playbook intent key.
    """

    AWAIT_AGENT = "await_agent"
    CONFIRM_OUTPUT = "confirm_output"
    ALIGNMENT_CHECKPOINT = "alignment_checkpoint"
    NEED_CLARIFICATION = "need_clarification"
    NEED_PERMISSION = "need_permission"
    WORKFLOW_COMPLETE = "workflow_complete"
    MANUAL_HANDOFF = "manual_handoff"

    CONFIRMED = "confirmed"
    READY_FOR_REVIEW = "ready_for_review"
    NEEDS_CHANGES = "needs_changes"
    NO_CHANGES_NEEDED = "no_changes_needed"
    CONFIRMED_SKIP_REVIEW = "skip_review"
    REJECTED = "rejected"
    NO_RESPONSE = "no_response"


def transition_map_key(code: PhaseStatusCode) -> str:
    """Map a phase outcome to a playbook ``on`` transition key."""
    if code.value in PLAYBOOK_INTENT_KEYS:
        return code.value
    collapsed: dict[PhaseStatusCode, str] = {
        PhaseStatusCode.CONFIRMED: "await_agent",
        PhaseStatusCode.NO_CHANGES_NEEDED: "no_changes_needed",
        PhaseStatusCode.NO_RESPONSE: "await_agent",
        PhaseStatusCode.READY_FOR_REVIEW: "confirm_output",
        PhaseStatusCode.ALIGNMENT_CHECKPOINT: "alignment_checkpoint",
        PhaseStatusCode.NEED_CLARIFICATION: "need_clarification",
        PhaseStatusCode.NEED_PERMISSION: "need_permission",
        PhaseStatusCode.NEEDS_CHANGES: "manual_handoff",
        PhaseStatusCode.CONFIRMED_SKIP_REVIEW: "manual_handoff",
        PhaseStatusCode.REJECTED: "manual_handoff",
    }
    return collapsed.get(code, "manual_handoff")


def step_on_declares(step_def: dict, intent_key: str) -> bool:
    """Return whether the playbook step ``on`` map includes *intent_key*."""
    on_map = step_def.get("on")
    if not isinstance(on_map, dict):
        return False
    return intent_key in on_map


class StatusCodeParser:
    """Legacy free-text → ``PhaseStatusCode`` parser.

    Used only by mock agents and legacy agent paths. New built-in skills and
    playbooks must write structured batons to ``next_step.txt`` instead of
    relying on free-text status codes.
    """

    @staticmethod
    def extract(response: str, valid_codes: Optional[List[PhaseStatusCode]] = None) -> Optional[PhaseStatusCode]:
        if not response:
            return None
        all_codes = StatusCodeParser.extract_all(response, valid_codes)
        if len(all_codes) > 1:
            return None

        first_line = response.strip().split("\n")[0].strip()
        lowered = first_line.lower()
        allowed = {c.value.lower(): c for c in (valid_codes or list(PhaseStatusCode))}
        if lowered in allowed:
            return allowed[lowered]

        for token, code in sorted(allowed.items(), key=lambda item: len(item[0]), reverse=True):
            if token in lowered:
                return code

        if valid_codes:
            haystack = response.lower()
            for code in sorted(valid_codes, key=lambda item: len(item.value), reverse=True):
                if code.value.lower() in haystack:
                    return code

        for code in sorted(PhaseStatusCode, key=lambda item: len(item.value), reverse=True):
            if code.value.lower() in response.lower():
                if valid_codes is None or code in valid_codes:
                    return code
        return None

    @staticmethod
    def extract_all(response: str, valid_codes: Optional[List[PhaseStatusCode]] = None) -> Set[PhaseStatusCode]:
        if not response:
            return set()

        found_codes: Set[PhaseStatusCode] = set()
        haystack = response.lower()
        codes_to_check = sorted(
            valid_codes if valid_codes else list(PhaseStatusCode),
            key=lambda item: len(item.value),
            reverse=True,
        )
        for code in codes_to_check:
            token = code.value.lower()
            if token in haystack:
                if any(found.value.startswith(token) for found in found_codes):
                    continue
                found_codes = {
                    found for found in found_codes if not token.startswith(found.value.lower())
                }
                found_codes.add(code)
        return found_codes

    @staticmethod
    def is_success(code: Optional[PhaseStatusCode]) -> bool:
        return code in {PhaseStatusCode.CONFIRMED, PhaseStatusCode.AWAIT_AGENT}

    @staticmethod
    def is_failure(code: Optional[PhaseStatusCode]) -> bool:
        return code in set()

    @staticmethod
    def is_retry(code: Optional[PhaseStatusCode]) -> bool:
        return code in {
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.NEEDS_CHANGES,
            PhaseStatusCode.NEED_PERMISSION,
            PhaseStatusCode.MANUAL_HANDOFF,
        }

    @staticmethod
    def needs_human_input(code: Optional[PhaseStatusCode]) -> bool:
        return code in {
            PhaseStatusCode.NEED_PERMISSION,
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.ALIGNMENT_CHECKPOINT,
            PhaseStatusCode.READY_FOR_REVIEW,
            PhaseStatusCode.CONFIRM_OUTPUT,
        }


def generate_status_code_prompt(valid_codes: List[PhaseStatusCode], descriptions: dict) -> str:
    """Generate prompt text instructing the agent which outcome token to return."""
    lines = [
        "Return exactly one outcome token on the first line of your response (snake_case, no legacy prefixes):",
        "",
    ]
    for code in valid_codes:
        description = descriptions.get(code, "")
        lines.append(f"- {code.value}: {description}")
    lines.extend(
        [
            "",
            "**Response format:**",
            "- Return ONLY the token on the first line",
            "- Do NOT include any summary or explanation",
        ]
    )
    return "\n".join(lines)
