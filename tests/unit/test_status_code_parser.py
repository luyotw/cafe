"""Tests for StatusCodeParser (re-homed from legacy plan-phase status code tests)."""

from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser


def test_extract_status_code_in_middle_of_response() -> None:
    response = "分析結果：\nready_for_review\n實作分析已完成."
    assert StatusCodeParser.extract(
        response,
        valid_codes=[PhaseStatusCode.READY_FOR_REVIEW, PhaseStatusCode.CONFIRMED],
    ) == PhaseStatusCode.READY_FOR_REVIEW


def test_extract_case_insensitive_status_code() -> None:
    response = "cafe_ready_for_review\n實作分析已完成."
    assert StatusCodeParser.extract(
        response,
        valid_codes=[PhaseStatusCode.READY_FOR_REVIEW, PhaseStatusCode.CONFIRMED],
    ) == PhaseStatusCode.READY_FOR_REVIEW


def test_extract_need_permission_token_from_response() -> None:
    response = "need_permission\n請在 IDE 中授權後再繼續。"
    assert StatusCodeParser.extract(
        response,
        valid_codes=[PhaseStatusCode.NEED_PERMISSION, PhaseStatusCode.CONFIRMED],
    ) == PhaseStatusCode.NEED_PERMISSION


def test_extract_returns_none_when_multiple_distinct_codes_present() -> None:
    response = "ready_for_review\nneed_clarification"
    assert StatusCodeParser.extract(
        response,
        valid_codes=[
            PhaseStatusCode.READY_FOR_REVIEW,
            PhaseStatusCode.NEED_CLARIFICATION,
        ],
    ) is None
