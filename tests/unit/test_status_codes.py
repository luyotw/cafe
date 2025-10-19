"""Tests for status codes system."""

import pytest

from aaf.core.status_codes import (
    PhaseStatusCode,
    StatusCodeParser,
    generate_status_code_prompt,
)


class TestStatusCodeParser:
    """Test status code parsing."""

    def test_extract_from_first_line(self) -> None:
        """測試從第一行提取狀態碼"""
        response = "CONFIRMED\n需求已經很清楚了。"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_case_insensitive(self) -> None:
        """測試大小寫不敏感"""
        response = "confirmed\n需求已經很清楚了。"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_from_middle_of_text(self) -> None:
        """測試從文本中間提取狀態碼"""
        response = "我仔細審查過程式碼，看起來很好。LGTM！"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.LGTM

    def test_extract_with_valid_codes_filter(self) -> None:
        """測試使用 valid_codes 過濾"""
        response = "CONFIRMED"
        valid_codes = [
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.CONFIRMED,
        ]

        code = StatusCodeParser.extract(response, valid_codes)

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_with_valid_codes_filter_rejects_invalid(self) -> None:
        """測試 valid_codes 會拒絕不在清單中的狀態碼"""
        response = "LGTM"
        valid_codes = [
            PhaseStatusCode.CONFIRMED,
            PhaseStatusCode.NEED_CLARIFICATION,
        ]

        code = StatusCodeParser.extract(response, valid_codes)

        # LGTM 不在 valid_codes 中，應該找不到
        assert code is None

    def test_extract_returns_none_for_empty_response(self) -> None:
        """測試空回應回傳 None"""
        code = StatusCodeParser.extract("")

        assert code is None

    def test_extract_returns_none_when_no_code_found(self) -> None:
        """測試找不到狀態碼時回傳 None"""
        response = "這是一個沒有狀態碼的回應。"

        code = StatusCodeParser.extract(response)

        assert code is None

    def test_extract_prioritizes_longer_codes(self) -> None:
        """測試優先匹配較長的狀態碼"""
        # NEEDS_MAJOR_CHANGES 包含 NEEDS_CHANGES，應該匹配較長的
        response = "NEEDS_MAJOR_CHANGES"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.NEEDS_MAJOR_CHANGES

    def test_extract_multiple_codes_returns_first_valid(self) -> None:
        """測試多個狀態碼時回傳第一個有效的"""
        response = "CONFIRMED\n但是可能需要 RETRY"

        code = StatusCodeParser.extract(response)

        # 應該回傳第一行的 CONFIRMED
        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_with_whitespace(self) -> None:
        """測試處理空白字元"""
        response = "  CONFIRMED  \n需求清楚。"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.CONFIRMED


class TestStatusCodeClassification:
    """Test status code classification methods."""

    def test_is_success(self) -> None:
        """測試成功狀態碼判斷"""
        assert StatusCodeParser.is_success(PhaseStatusCode.COMPLETED) is True
        assert StatusCodeParser.is_success(PhaseStatusCode.CONFIRMED) is True
        assert StatusCodeParser.is_success(PhaseStatusCode.APPROVED) is True
        assert StatusCodeParser.is_success(PhaseStatusCode.LGTM) is True
        assert StatusCodeParser.is_success(PhaseStatusCode.COMMITTED) is True

        assert StatusCodeParser.is_success(PhaseStatusCode.FAILED) is False
        assert StatusCodeParser.is_success(PhaseStatusCode.RETRY) is False
        assert StatusCodeParser.is_success(None) is False

    def test_is_failure(self) -> None:
        """測試失敗狀態碼判斷"""
        assert StatusCodeParser.is_failure(PhaseStatusCode.FAILED) is True
        assert StatusCodeParser.is_failure(PhaseStatusCode.REJECTED) is True
        assert StatusCodeParser.is_failure(PhaseStatusCode.PERMISSION_DENIED) is True

        assert StatusCodeParser.is_failure(PhaseStatusCode.COMPLETED) is False
        assert StatusCodeParser.is_failure(PhaseStatusCode.RETRY) is False
        assert StatusCodeParser.is_failure(None) is False

    def test_is_retry(self) -> None:
        """測試重試狀態碼判斷"""
        assert StatusCodeParser.is_retry(PhaseStatusCode.RETRY) is True
        assert StatusCodeParser.is_retry(PhaseStatusCode.NEED_CLARIFICATION) is True
        assert StatusCodeParser.is_retry(PhaseStatusCode.NEEDS_CHANGES) is True
        assert StatusCodeParser.is_retry(PhaseStatusCode.NEEDS_MAJOR_CHANGES) is True
        assert StatusCodeParser.is_retry(PhaseStatusCode.NEED_PERMISSION) is True

        assert StatusCodeParser.is_retry(PhaseStatusCode.COMPLETED) is False
        assert StatusCodeParser.is_retry(PhaseStatusCode.FAILED) is False
        assert StatusCodeParser.is_retry(None) is False

    def test_needs_human_input(self) -> None:
        """測試需要人工介入的狀態碼判斷"""
        assert StatusCodeParser.needs_human_input(PhaseStatusCode.MANUAL_REVIEW) is True
        assert StatusCodeParser.needs_human_input(PhaseStatusCode.NEED_PERMISSION) is True
        assert StatusCodeParser.needs_human_input(PhaseStatusCode.NEED_CLARIFICATION) is True

        assert StatusCodeParser.needs_human_input(PhaseStatusCode.COMPLETED) is False
        assert StatusCodeParser.needs_human_input(PhaseStatusCode.RETRY) is False
        assert StatusCodeParser.needs_human_input(None) is False


class TestGenerateStatusCodePrompt:
    """Test status code prompt generation."""

    def test_generate_basic_prompt(self) -> None:
        """測試產生基本的狀態碼提示"""
        codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION]
        descriptions = {
            PhaseStatusCode.CONFIRMED: "需求已確認",
            PhaseStatusCode.NEED_CLARIFICATION: "需要更多資訊",
        }

        prompt = generate_status_code_prompt(codes, descriptions)

        assert "CONFIRMED" in prompt
        assert "NEED_CLARIFICATION" in prompt
        assert "需求已確認" in prompt
        assert "需要更多資訊" in prompt
        assert "範例回應格式" in prompt

    def test_prompt_includes_all_codes(self) -> None:
        """測試提示包含所有狀態碼"""
        codes = [
            PhaseStatusCode.APPROVED,
            PhaseStatusCode.NEEDS_CHANGES,
            PhaseStatusCode.NEEDS_MAJOR_CHANGES,
        ]
        descriptions = {
            PhaseStatusCode.APPROVED: "審核通過",
            PhaseStatusCode.NEEDS_CHANGES: "需要小幅修改",
            PhaseStatusCode.NEEDS_MAJOR_CHANGES: "需要大幅重構",
        }

        prompt = generate_status_code_prompt(codes, descriptions)

        for code in codes:
            assert code.value in prompt

    def test_prompt_uses_first_code_in_example(self) -> None:
        """測試範例使用第一個狀態碼"""
        codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.REJECTED]
        descriptions = {
            PhaseStatusCode.CONFIRMED: "確認",
            PhaseStatusCode.REJECTED: "拒絕",
        }

        prompt = generate_status_code_prompt(codes, descriptions)

        # 範例應該使用第一個狀態碼
        lines = prompt.split('\n')
        example_section = False
        for line in lines:
            if "範例回應格式" in line:
                example_section = True
            elif example_section and line.strip():
                # 第一個非空行應該是第一個狀態碼
                assert codes[0].value in line
                break


class TestPhaseStatusCodeEnum:
    """Test PhaseStatusCode enum."""

    def test_all_codes_are_uppercase(self) -> None:
        """測試所有狀態碼都是大寫"""
        for code in PhaseStatusCode:
            assert code.value == code.value.upper()
            assert code.value.isalpha() or '_' in code.value

    def test_code_values_are_simple_english(self) -> None:
        """測試狀態碼都是簡單的英文"""
        for code in PhaseStatusCode:
            # 只包含字母和底線
            assert all(c.isalpha() or c == '_' for c in code.value)
            # 不會太長（節省 token）
            assert len(code.value) <= 25

    def test_enum_can_be_converted_to_string(self) -> None:
        """測試 enum 可以轉成字串"""
        code = PhaseStatusCode.CONFIRMED

        # str(enum) returns the full name, use .value for just the value
        assert code.value == "CONFIRMED"
        assert isinstance(code, str)  # PhaseStatusCode inherits from str
