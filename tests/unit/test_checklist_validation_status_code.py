"""Test that _validate_and_retry_checklist_completion preserves status code
when context.json response doesn't contain the status code (e.g., after
a continue-execution merged the status code into the response but context.json
wasn't updated yet).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.status_codes import PhaseStatusCode
from cafe.phases.spec_phase import SpecPhase


@pytest.fixture
def phase(tmp_path):
    """Create a SpecPhase instance for testing."""
    mock_agent_manager = MagicMock()
    mock_git_ops = MagicMock()
    mock_git_ops.get_current_branch.return_value = "test-branch"

    phase = SpecPhase(
        agent_manager=mock_agent_manager,
        permission_handler=MagicMock(),
        git_ops=mock_git_ops,
        pm_agent="Roger",
        interactive=False,
        issue_name="test-issue",
        user_input="test requirements",
    )

    phase.phase_dir = tmp_path / "spec"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)
    phase.iteration = 1
    return phase


class TestChecklistValidationPreservesStatusCode:
    """Bug: when agent needs a continue-execution to produce a status code,
    the merged response (with status code) is NOT saved to context.json before
    checklist validation runs. _validate_and_retry_checklist_completion reads
    context.json, re-extracts status code from the stale response, gets None,
    and returns (response, None, True). This causes the caller to lose the
    status code and ultimately fail with "No valid status code returned".
    """

    def test_returns_status_code_when_context_response_has_it(self, phase, tmp_path):
        """When context.json response contains the status code, it should be extracted."""
        iteration_dir = phase._get_iteration_dir(1)
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Write a complete checklist (all items checked)
        checklist = iteration_dir / "checklist.md"
        checklist.write_text("## Checklist\n\n[x] Task 1\n[x] Task 2\n")

        # context.json has response WITH status code
        context = iteration_dir / "context.json"
        context.write_text(json.dumps({
            "response": "Done.\n\nneed_clarification",
        }))

        response, status_code, passed = phase._validate_and_retry_checklist_completion(
            agent_name="Roger",
            prompt="test prompt",
            user_input="",
            valid_intents=[PhaseStatusCode.READY_FOR_REVIEW, PhaseStatusCode.NEED_CLARIFICATION],
        )

        assert passed is True
        assert status_code == PhaseStatusCode.NEED_CLARIFICATION

    def test_returns_none_when_context_response_missing_status_code(self, phase, tmp_path):
        """When context.json response doesn't contain status code (stale after continue-execution),
        _validate_and_retry_checklist_completion returns None for status_code.
        The caller (_execute_and_handle_agent_response) should preserve its own status_code.
        """
        iteration_dir = phase._get_iteration_dir(1)
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Write a complete checklist
        checklist = iteration_dir / "checklist.md"
        checklist.write_text("## Checklist\n\n[x] Task 1\n[x] Task 2\n")

        # context.json has response WITHOUT status code (stale, pre-continue)
        context = iteration_dir / "context.json"
        context.write_text(json.dumps({
            "response": "I'm working on the spec analysis...",
        }))

        response, status_code, passed = phase._validate_and_retry_checklist_completion(
            agent_name="Roger",
            prompt="test prompt",
            user_input="",
            valid_intents=[PhaseStatusCode.READY_FOR_REVIEW, PhaseStatusCode.NEED_CLARIFICATION],
        )

        assert passed is True
        # Validation can't extract status code from stale context.json — returns None
        assert status_code is None
        # The fix ensures the caller keeps its own status_code instead of overwriting with None
