"""Tests for status code definitions and extraction helpers."""

from cafe.core.phase import Phase
from cafe.core.status_codes import (
    PhaseStatusCode,
    effective_step_handoff_intents,
    effective_step_status_codes,
)


class TestPhaseStatusCodeEnum:
    """Test PhaseStatusCode enum."""

    def test_all_codes_are_snake_case_tokens(self) -> None:
        """Outcome tokens use snake_case without legacy prefixes."""
        for code in PhaseStatusCode:
            assert code.value == code.value.lower()
            assert "_" in code.value or code.value.isalpha()
            assert not code.value.startswith("CAFE_")

    def test_enum_can_be_converted_to_string(self) -> None:
        """測試 enum 可以轉成字串"""
        code = PhaseStatusCode.CONFIRMED
        assert code.value == "confirmed"
        assert isinstance(code, str)

    def test_omitted_valid_intents_are_derived_from_declared_transitions(self) -> None:
        actual = effective_step_status_codes(
            {
                "on": {
                    "await_agent": "plan",
                    "need_clarification": "spec",
                }
            }
        )

        assert PhaseStatusCode.CONFIRMED in actual
        assert PhaseStatusCode.NEED_CLARIFICATION in actual
        assert PhaseStatusCode.ALIGNMENT_CHECKPOINT not in actual

    def test_terminal_transition_exposes_workflow_complete_baton(self) -> None:
        actual = effective_step_handoff_intents(
            {"on": {"await_agent": "_done"}}
        )

        assert "await_agent" in actual
        assert "workflow_complete" in actual
        assert "alignment_checkpoint" not in actual

    def test_active_alignment_block_exposes_compatibility_baton(self) -> None:
        actual = effective_step_handoff_intents(
            {
                "alignment": {},
                "on": {"await_agent": "_done"},
            }
        )

        assert "alignment_checkpoint" in actual

    def test_disabled_alignment_block_does_not_expose_compatibility_baton(self) -> None:
        actual = effective_step_handoff_intents(
            {
                "alignment": {"enabled": False},
                "on": {"await_agent": "_done"},
            }
        )

        assert "alignment_checkpoint" not in actual


class TestPhaseStatusExtraction:
    """Test status extraction from response text."""

    def test_extract_status_code_returns_match(self) -> None:
        """測試可從回應中提取狀態碼"""
        response = "confirmed\ndone"

        code = Phase._extract_status_code_from_response(
            response,
            valid_codes=[PhaseStatusCode.CONFIRMED],
        )

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_status_code_filters_valid_codes(self) -> None:
        """測試會依 valid_codes 過濾狀態碼"""
        response = "confirmed\ndone"

        code = Phase._extract_status_code_from_response(
            response,
            valid_codes=[PhaseStatusCode.NEED_CLARIFICATION],
        )

        assert code is None

    def test_extract_status_code_returns_none_for_multiple_codes(self) -> None:
        """測試多種狀態碼時回傳 None"""
        response = "confirmed\nneed_clarification\ndone"

        code = Phase._extract_status_code_from_response(
            response,
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
        )
        assert code is None
