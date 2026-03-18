"""測試互動式問答 UI 流程"""

from unittest.mock import MagicMock, call, patch

import pytest

from cafe.core.questions_schema import Question
from cafe.ui.interactive_qa import (
    BACK_SENTINEL,
    OTHER_SENTINEL,
    interactive_qa_flow,
)


def _make_questions(count=3):
    """建立測試用問題列表"""
    return [
        Question(
            id=str(i + 1),
            title=f"Question {i + 1}?",
            options=[f"Option {i + 1}A", f"Option {i + 1}B"],
        )
        for i in range(count)
    ]


class TestInteractiveQaFlowBasic:
    """測試基本流程：逐題作答並確認"""

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_basic_flow_select_options_and_confirm(self, mock_inquirer):
        """測試使用者依序選擇選項後確認，回傳正確格式"""
        questions = _make_questions(2)

        # Mock: Q1 選 "Option 1A", Q2 選 "Option 2B", 確認 "Confirm and continue"
        mock_select = MagicMock()
        mock_select.execute = MagicMock(
            side_effect=["Option 1A", "Option 2B", "Confirm and continue"]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        assert "Q1: Question 1?" in result
        assert "A1: Option 1A" in result
        assert "Q2: Question 2?" in result
        assert "A2: Option 2B" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_return_format_has_qa_pairs(self, mock_inquirer):
        """測試回傳格式包含 Q/A 對"""
        questions = _make_questions(1)

        mock_select = MagicMock()
        mock_select.execute = MagicMock(
            side_effect=["Option 1A", "Confirm and continue"]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        lines = result.strip().split("\n")
        assert any("Q1:" in line for line in lines)
        assert any("A1:" in line for line in lines)


class TestInteractiveQaFlowOther:
    """測試 'Other' 自由文字輸入流程"""

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_other_option_triggers_text_input(self, mock_inquirer):
        """測試選擇 Other 時觸發文字輸入"""
        questions = _make_questions(1)

        mock_select = MagicMock()
        mock_text = MagicMock()

        # Q1 選 OTHER_SENTINEL, 文字輸入 "My custom answer", 確認
        mock_select.execute = MagicMock(
            side_effect=[OTHER_SENTINEL, "Confirm and continue"]
        )
        mock_text.execute = MagicMock(return_value="My custom answer")

        mock_inquirer.select.return_value = mock_select
        mock_inquirer.text.return_value = mock_text

        result = interactive_qa_flow(questions)

        assert "A1: My custom answer" in result
        mock_inquirer.text.assert_called_once()


class TestInteractiveQaFlowBack:
    """測試 'Back' 導覽行為"""

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_back_returns_to_previous_question(self, mock_inquirer):
        """測試選擇 Back 回到前一題，並保留前一次的回答"""
        questions = _make_questions(2)

        mock_select = MagicMock()
        # Q1 選 "Option 1A"
        # Q2 選 BACK_SENTINEL (回到 Q1)
        # Q1 再選 "Option 1B"
        # Q2 選 "Option 2A"
        # 確認
        mock_select.execute = MagicMock(
            side_effect=[
                "Option 1A",
                BACK_SENTINEL,
                "Option 1B",
                "Option 2A",
                "Confirm and continue",
            ]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        # 最終結果應使用修改後的回答
        assert "A1: Option 1B" in result
        assert "A2: Option 2A" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_question_1_has_no_back_option(self, mock_inquirer):
        """測試第一題不顯示 Back 選項"""
        questions = _make_questions(2)

        mock_select = MagicMock()
        mock_select.execute = MagicMock(
            side_effect=["Option 1A", "Option 2A", "Confirm and continue"]
        )
        mock_inquirer.select.return_value = mock_select

        interactive_qa_flow(questions)

        # 檢查第一次 inquirer.select 呼叫的 choices 不包含 BACK_SENTINEL
        first_call_kwargs = mock_inquirer.select.call_args_list[0]
        choices = first_call_kwargs.kwargs.get("choices", first_call_kwargs[1].get("choices", []))
        choice_values = [c if isinstance(c, str) else getattr(c, "value", c) for c in choices]
        assert BACK_SENTINEL not in choice_values

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_question_2_has_back_option(self, mock_inquirer):
        """測試第二題以後有 Back 選項"""
        questions = _make_questions(2)

        mock_select = MagicMock()
        mock_select.execute = MagicMock(
            side_effect=["Option 1A", "Option 2A", "Confirm and continue"]
        )
        mock_inquirer.select.return_value = mock_select

        interactive_qa_flow(questions)

        # 第二次呼叫 (Q2) 的 choices 應包含 BACK_SENTINEL
        second_call_kwargs = mock_inquirer.select.call_args_list[1]
        choices = second_call_kwargs.kwargs.get("choices", second_call_kwargs[1].get("choices", []))
        choice_values = []
        for c in choices:
            if isinstance(c, str):
                choice_values.append(c)
            elif isinstance(c, dict):
                choice_values.append(c.get("value", c))
            else:
                choice_values.append(getattr(c, "value", c))
        assert BACK_SENTINEL in choice_values


class TestInteractiveQaFlowOtherAnswerMemorization:
    """測試 'Other' 自由文字答案在返回時被記憶"""

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_other_answer_memorized_when_back_navigation(self, mock_inquirer):
        """測試使用 Back 返回時，先前透過 Other 輸入的答案會預填到文字輸入框"""
        questions = _make_questions(2)

        mock_select = MagicMock()
        mock_text = MagicMock()

        # Q1 選 OTHER_SENTINEL, 文字輸入 "custom answer"
        # Q2 選 BACK_SENTINEL (回到 Q1)
        # Q1 再次選 OTHER_SENTINEL, 文字輸入時應預填 "custom answer"
        # Q1 輸入 "modified answer"
        # Q2 選 "Option 2A"
        # 確認
        mock_select.execute = MagicMock(
            side_effect=[
                OTHER_SENTINEL,
                BACK_SENTINEL,
                OTHER_SENTINEL,
                "Option 2A",
                "Confirm and continue",
            ]
        )
        mock_text.execute = MagicMock(
            side_effect=["custom answer", "modified answer"]
        )

        mock_inquirer.select.return_value = mock_select
        mock_inquirer.text.return_value = mock_text

        result = interactive_qa_flow(questions)

        # 檢查第二次 inquirer.text 呼叫時有傳入 default 參數
        assert mock_inquirer.text.call_count == 2
        second_text_call = mock_inquirer.text.call_args_list[1]
        assert "default" in second_text_call.kwargs
        assert second_text_call.kwargs["default"] == "custom answer"

        # 最終結果應為修改後的答案
        assert "A1: modified answer" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_other_answer_memorized_in_modify_flow(self, mock_inquirer):
        """測試在修改流程中，先前透過 Other 輸入的答案會預填到文字輸入框"""
        questions = _make_questions(2)

        mock_select = MagicMock()
        mock_text = MagicMock()

        # Q1 選 OTHER_SENTINEL, 文字輸入 "first answer"
        # Q2 選 "Option 2A"
        # 確認畫面選 "Modify an answer..."
        # 選擇修改 Q1
        # 重新回答 Q1 選 OTHER_SENTINEL, 文字輸入時應預填 "first answer"
        # 輸入 "modified answer"
        # 確認畫面選 "Confirm and continue"
        mock_select.execute = MagicMock(
            side_effect=[
                OTHER_SENTINEL,
                "Option 2A",
                "Modify an answer...",
                "1",  # 選擇修改 Q1 (id)
                OTHER_SENTINEL,
                "Confirm and continue",
            ]
        )
        mock_text.execute = MagicMock(
            side_effect=["first answer", "modified answer"]
        )

        mock_inquirer.select.return_value = mock_select
        mock_inquirer.text.return_value = mock_text

        result = interactive_qa_flow(questions)

        # 檢查第二次 inquirer.text 呼叫時有傳入 default 參數
        assert mock_inquirer.text.call_count == 2
        second_text_call = mock_inquirer.text.call_args_list[1]
        assert "default" in second_text_call.kwargs
        assert second_text_call.kwargs["default"] == "first answer"

        # 最終結果應為修改後的答案
        assert "A1: modified answer" in result


class TestInteractiveQaFlowCheckbox:
    """測試 checkbox (multi_select) 問題流程"""

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_checkbox_question_uses_checkbox_prompt(self, mock_inquirer):
        """測試 multi_select=True 的問題使用 checkbox 而非 select"""
        questions = [
            Question(id="1", title="Select features?", options=["A", "B", "C"], multi_select=True),
        ]

        mock_checkbox = MagicMock()
        mock_checkbox.execute = MagicMock(return_value=["A", "C"])
        mock_inquirer.checkbox.return_value = mock_checkbox

        mock_select = MagicMock()
        mock_select.execute = MagicMock(side_effect=["Confirm and continue"])
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        mock_inquirer.checkbox.assert_called_once()
        mock_inquirer.text.assert_not_called()
        assert "A1: A, C" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_checkbox_custom_input_when_other_selected(self, mock_inquirer):
        """測試 checkbox 選了 Other 才跳出文字輸入"""
        questions = [
            Question(id="1", title="Select items?", options=["A", "B"], multi_select=True),
        ]

        mock_checkbox = MagicMock()
        mock_checkbox.execute = MagicMock(return_value=["A", OTHER_SENTINEL])
        mock_inquirer.checkbox.return_value = mock_checkbox

        mock_text = MagicMock()
        mock_text.execute = MagicMock(return_value="Custom item")
        mock_inquirer.text.return_value = mock_text

        mock_select = MagicMock()
        mock_select.execute = MagicMock(side_effect=["Confirm and continue"])
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        mock_inquirer.text.assert_called_once()
        assert "A1: A, Custom item" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_checkbox_no_text_prompt_when_other_not_selected(self, mock_inquirer):
        """測試 checkbox 沒選 Other 時不跳出文字輸入"""
        questions = [
            Question(id="1", title="Select items?", options=["A", "B"], multi_select=True),
        ]

        mock_checkbox = MagicMock()
        mock_checkbox.execute = MagicMock(return_value=[])
        mock_inquirer.checkbox.return_value = mock_checkbox

        mock_select = MagicMock()
        mock_select.execute = MagicMock(side_effect=["Confirm and continue"])
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        mock_inquirer.text.assert_not_called()
        assert "A1: (none selected)" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_checkbox_only_other_selected(self, mock_inquirer):
        """測試 checkbox 只選 Other 並輸入自定義內容"""
        questions = [
            Question(id="1", title="Select items?", options=["A", "B"], multi_select=True),
        ]

        mock_checkbox = MagicMock()
        mock_checkbox.execute = MagicMock(return_value=[OTHER_SENTINEL])
        mock_inquirer.checkbox.return_value = mock_checkbox

        mock_text = MagicMock()
        mock_text.execute = MagicMock(return_value="My custom item")
        mock_inquirer.text.return_value = mock_text

        mock_select = MagicMock()
        mock_select.execute = MagicMock(side_effect=["Confirm and continue"])
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        assert "A1: My custom item" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_mixed_select_and_checkbox_questions(self, mock_inquirer):
        """測試混合單選和複選問題的流程"""
        questions = [
            Question(id="1", title="Pick one?", options=["A", "B"]),
            Question(id="2", title="Select many?", options=["X", "Y", "Z"], multi_select=True),
        ]

        mock_select = MagicMock()
        mock_select.execute = MagicMock(side_effect=["A", "Confirm and continue"])
        mock_inquirer.select.return_value = mock_select

        mock_checkbox = MagicMock()
        mock_checkbox.execute = MagicMock(return_value=["X", "Z"])
        mock_inquirer.checkbox.return_value = mock_checkbox

        result = interactive_qa_flow(questions)

        mock_inquirer.text.assert_not_called()
        assert "A1: A" in result
        assert "A2: X, Z" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_checkbox_modify_flow(self, mock_inquirer):
        """測試修改 checkbox 問題的流程"""
        questions = [
            Question(id="1", title="Select items?", options=["A", "B", "C"], multi_select=True),
        ]

        mock_checkbox = MagicMock()
        # 第一次選 A, B；修改時選 B, C
        mock_checkbox.execute = MagicMock(side_effect=[["A", "B"], ["B", "C"]])
        mock_inquirer.checkbox.return_value = mock_checkbox

        mock_text = MagicMock()
        mock_text.execute = MagicMock(return_value="")  # skip custom input both times
        mock_inquirer.text.return_value = mock_text

        mock_select = MagicMock()
        mock_select.execute = MagicMock(
            side_effect=["Modify an answer...", "1", "Confirm and continue"]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        assert "A1: B, C" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_checkbox_choices_include_other(self, mock_inquirer):
        """測試 checkbox 選項包含 Other，讓使用者可以直接在選單中看到自定義輸入選項"""
        questions = [
            Question(id="1", title="Select items?", options=["A", "B"], multi_select=True),
        ]

        mock_checkbox = MagicMock()
        mock_checkbox.execute = MagicMock(return_value=["A"])
        mock_inquirer.checkbox.return_value = mock_checkbox

        mock_select = MagicMock()
        mock_select.execute = MagicMock(side_effect=["Confirm and continue"])
        mock_inquirer.select.return_value = mock_select

        interactive_qa_flow(questions)

        # Verify checkbox choices include OTHER_SENTINEL
        checkbox_call = mock_inquirer.checkbox.call_args
        choices = checkbox_call.kwargs.get("choices", checkbox_call[1].get("choices", []))
        choice_values = [c.get("value") if isinstance(c, dict) else c for c in choices]
        assert OTHER_SENTINEL in choice_values


class TestInteractiveQaFlowSummaryAndModify:
    """測試摘要確認與修改流程"""

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_modify_flow_re_answers_selected_question(self, mock_inquirer):
        """測試修改流程：選擇修改後回到摘要"""
        questions = _make_questions(2)

        mock_select = MagicMock()
        # Q1 選 "Option 1A", Q2 選 "Option 2A"
        # 確認畫面選 "Modify an answer..."
        # 選擇修改 Q1
        # 重新回答 Q1 選 "Option 1B"
        # 確認畫面選 "Confirm and continue"
        mock_select.execute = MagicMock(
            side_effect=[
                "Option 1A",
                "Option 2A",
                "Modify an answer...",
                "1",  # 選擇修改 Q1 (id)
                "Option 1B",  # 重新回答
                "Confirm and continue",
            ]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        assert "A1: Option 1B" in result
        assert "A2: Option 2A" in result

    @patch("cafe.ui.interactive_qa.inquirer")
    def test_confirm_returns_formatted_answers(self, mock_inquirer):
        """測試確認後回傳格式化結果"""
        questions = _make_questions(3)

        mock_select = MagicMock()
        mock_select.execute = MagicMock(
            side_effect=[
                "Option 1A",
                "Option 2B",
                "Option 3A",
                "Confirm and continue",
            ]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions)

        assert "Q1: Question 1?\nA1: Option 1A" in result
        assert "Q2: Question 2?\nA2: Option 2B" in result
        assert "Q3: Question 3?\nA3: Option 3A" in result
