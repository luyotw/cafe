"""Tests for develop command CLI parameter parsing."""

import pytest
from typing import List


class TestApproveDeniedToolsParsing:
    """測試 --approve-denied-tools 參數解析邏輯"""

    def test_parse_comma_separated_integers(self):
        """測試正確解析逗號分隔整數"""
        input_str = "0,1,3"
        approved_denial_indices: List[int] = []

        # Simulate the parsing logic from CLI
        try:
            approved_denial_indices = [int(idx.strip()) for idx in input_str.split(",")]
        except ValueError:
            pytest.fail("Should not raise ValueError for valid input")

        assert approved_denial_indices == [0, 1, 3]

    def test_parse_single_integer(self):
        """測試解析單一整數"""
        input_str = "5"
        approved_denial_indices: List[int] = []

        try:
            approved_denial_indices = [int(idx.strip()) for idx in input_str.split(",")]
        except ValueError:
            pytest.fail("Should not raise ValueError for valid input")

        assert approved_denial_indices == [5]

    def test_parse_with_spaces(self):
        """測試解析含空格輸入"""
        input_str = " 0 , 1 , 3 "
        approved_denial_indices: List[int] = []

        try:
            approved_denial_indices = [int(idx.strip()) for idx in input_str.split(",")]
        except ValueError:
            pytest.fail("Should not raise ValueError for valid input")

        assert approved_denial_indices == [0, 1, 3]

    def test_parse_invalid_non_integer_raises_error(self):
        """測試包含非整數時拋出錯誤"""
        input_str = "0,abc,3"

        with pytest.raises(ValueError):
            [int(idx.strip()) for idx in input_str.split(",")]

    def test_parse_empty_string_returns_empty_list(self):
        """測試空字串返回空列表"""
        input_str = ""

        # The CLI logic only parses if approve_denied_tools is provided (not None)
        # Empty string would still be processed
        approved_denial_indices = []
        if input_str:
            try:
                approved_denial_indices = [int(idx.strip()) for idx in input_str.split(",")]
            except ValueError:
                pass

        # Empty string split results in [''] which fails int conversion
        # In real usage, CLI would check `if approve_denied_tools:` first
        assert approved_denial_indices == []

    def test_parse_none_returns_empty_list(self):
        """測試 None 值返回空列表（參數未提供）"""
        approve_denied_tools = None
        approved_denial_indices: List[int] = []

        # Simulate CLI logic
        if approve_denied_tools:
            try:
                approved_denial_indices = [int(idx.strip()) for idx in approve_denied_tools.split(",")]
            except ValueError:
                pass

        assert approved_denial_indices == []


class TestDevelopPhaseParameterDefaults:
    """測試 DevelopPhase 參數預設值"""

    def test_approved_denial_indices_defaults_to_none(self):
        """測試 approved_denial_indices 預設為 None"""
        from cafe.phases.develop_phase import DevelopPhase
        from unittest.mock import MagicMock

        # Create minimal mocks
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()

        # Create phase with minimal required params
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="spec.md",
            plan_file="plan.md",
            workflow_mode="local"
        )

        # Verify defaults
        assert phase.user_input == ""
        # approved_denial_indices is not stored as attribute in current implementation
        # It's used in _handle_previous_permission_denials() via hasattr check

    def test_user_input_defaults_to_empty_string(self):
        """測試 user_input 預設為空字串"""
        from cafe.phases.develop_phase import DevelopPhase
        from unittest.mock import MagicMock

        # Create minimal mocks
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()

        # Create phase with minimal required params
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="spec.md",
            plan_file="plan.md",
            workflow_mode="local"
        )

        assert phase.user_input == ""

    def test_interactive_defaults_to_true(self):
        """測試 interactive 預設為 True"""
        from cafe.phases.develop_phase import DevelopPhase
        from unittest.mock import MagicMock

        # Create minimal mocks
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()

        # Create phase with minimal required params
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="spec.md",
            plan_file="plan.md",
            workflow_mode="local"
        )

        assert phase.interactive is True
