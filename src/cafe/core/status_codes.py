"""Status codes for phase execution control."""

from enum import Enum


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
