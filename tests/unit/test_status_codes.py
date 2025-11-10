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
        response = "AAF_CONFIRMED\n需求已經很清楚了。"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_case_insensitive(self) -> None:
        """測試大小寫不敏感"""
        response = "aaf_confirmed\n需求已經很清楚了。"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_from_middle_of_text(self) -> None:
        """測試從文本中間提取狀態碼"""
        response = "我仔細審查過需求，確認沒問題。AAF_CONFIRMED！"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_with_valid_codes_filter(self) -> None:
        """測試使用 valid_codes 過濾"""
        response = "AAF_CONFIRMED"
        valid_codes = [
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.CONFIRMED,
        ]

        code = StatusCodeParser.extract(response, valid_codes)

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_with_valid_codes_filter_rejects_invalid(self) -> None:
        """測試 valid_codes 會拒絕不在清單中的狀態碼"""
        response = "AAF_LGTM"
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

    def test_extract_with_prefix_on_first_line(self) -> None:
        """測試第一行有前綴符號時仍能正確提取"""
        response = "● AAF_NEEDS_CHANGES\n\n修正建議"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.NEEDS_CHANGES

    def test_extract_first_line_code_takes_priority_over_later_mentions(self) -> None:
        """測試第一行的狀態碼優先於後續出現的狀態碼"""
        response = """● AAF_NEEDS_CHANGES

## Code Review Feedback

文中提到 AAF_CONFIRMED 作為範例。
"""
        valid_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEEDS_CHANGES]

        code = StatusCodeParser.extract(response, valid_codes)

        # 應該回傳第一行的 NEEDS_CHANGES，而不是後面的 CONFIRMED
        assert code == PhaseStatusCode.NEEDS_CHANGES

    def test_extract_multiple_codes_returns_first_valid(self) -> None:
        """測試多個狀態碼時回傳第一個有效的"""
        response = "AAF_CONFIRMED\n但是可能需要 AAF_RETRY"

        code = StatusCodeParser.extract(response)

        # 應該回傳第一行的 CONFIRMED
        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_with_whitespace(self) -> None:
        """測試處理空白字元"""
        response = "  AAF_CONFIRMED  \n需求清楚。"

        code = StatusCodeParser.extract(response)

        assert code == PhaseStatusCode.CONFIRMED

    def test_extract_avoids_false_positive_in_content(self) -> None:
        """測試避免內容中的單字誤判為狀態碼"""
        # 如果沒有 AAF_ 前綴，"CONFIRMED" 這個單字會被誤判
        response = "The user has CONFIRMED that the feature is working correctly."

        code = StatusCodeParser.extract(response)

        # 應該找不到狀態碼（因為需要 AAF_CONFIRMED 格式）
        assert code is None


class TestStatusCodeClassification:
    """Test status code classification methods."""

    def test_is_success(self) -> None:
        """測試成功狀態碼判斷"""
        assert StatusCodeParser.is_success(PhaseStatusCode.CONFIRMED) is True

        assert StatusCodeParser.is_success(PhaseStatusCode.REJECTED) is False
        assert StatusCodeParser.is_success(PhaseStatusCode.NEED_CLARIFICATION) is False
        assert StatusCodeParser.is_success(None) is False

    def test_is_failure(self) -> None:
        """測試失敗狀態碼判斷"""
        assert StatusCodeParser.is_failure(PhaseStatusCode.REJECTED) is True

        assert StatusCodeParser.is_failure(PhaseStatusCode.CONFIRMED) is False
        assert StatusCodeParser.is_failure(PhaseStatusCode.NEED_CLARIFICATION) is False
        assert StatusCodeParser.is_failure(None) is False

    def test_is_retry(self) -> None:
        """測試重試狀態碼判斷"""
        assert StatusCodeParser.is_retry(PhaseStatusCode.NEED_CLARIFICATION) is True
        assert StatusCodeParser.is_retry(PhaseStatusCode.NEEDS_CHANGES) is True
        assert StatusCodeParser.is_retry(PhaseStatusCode.NEED_PERMISSION) is True

        assert StatusCodeParser.is_retry(PhaseStatusCode.CONFIRMED) is False
        assert StatusCodeParser.is_retry(PhaseStatusCode.REJECTED) is False
        assert StatusCodeParser.is_retry(None) is False

    def test_needs_human_input(self) -> None:
        """測試需要人工介入的狀態碼判斷"""
        assert StatusCodeParser.needs_human_input(PhaseStatusCode.NEED_PERMISSION) is True
        assert StatusCodeParser.needs_human_input(PhaseStatusCode.NEED_CLARIFICATION) is True
        assert StatusCodeParser.needs_human_input(PhaseStatusCode.READY_FOR_REVIEW) is True

        assert StatusCodeParser.needs_human_input(PhaseStatusCode.CONFIRMED) is False
        assert StatusCodeParser.needs_human_input(PhaseStatusCode.NEEDS_CHANGES) is False
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

        assert "AAF_CONFIRMED" in prompt
        assert "AAF_NEED_CLARIFICATION" in prompt
        assert "需求已確認" in prompt
        assert "需要更多資訊" in prompt
        assert "範例回應格式" in prompt

    def test_prompt_includes_all_codes(self) -> None:
        """測試提示包含所有狀態碼"""
        codes = [
            PhaseStatusCode.CONFIRMED,
            PhaseStatusCode.NEEDS_CHANGES,
            PhaseStatusCode.REJECTED,
        ]
        descriptions = {
            PhaseStatusCode.CONFIRMED: "審核通過",
            PhaseStatusCode.NEEDS_CHANGES: "需要小幅修改",
            PhaseStatusCode.REJECTED: "需要拒絕",
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
            # Check format: AAF_CODE_NAME
            assert code.value.startswith("AAF_")
            # Name part should only contain letters and underscores
            assert all(c.isalpha() or c == '_' for c in code.value)

    def test_code_values_are_simple_english(self) -> None:
        """測試狀態碼都是簡單的英文"""
        for code in PhaseStatusCode:
            # 格式為 AAF_CODE_NAME
            assert code.value.startswith("AAF_")
            # 去掉前綴後只包含字母和底線
            name_part = code.value[4:]  # Remove "AAF_"
            assert all(c.isalpha() or c == '_' for c in name_part)
            # 不會太長（節省 token）
            assert len(code.value) <= 29  # AAF_ (4) + 25

    def test_enum_can_be_converted_to_string(self) -> None:
        """測試 enum 可以轉成字串"""
        code = PhaseStatusCode.CONFIRMED

        # str(enum) returns the full name, use .value for just the value
        assert code.value == "AAF_CONFIRMED"
        assert isinstance(code, str)  # PhaseStatusCode inherits from str
