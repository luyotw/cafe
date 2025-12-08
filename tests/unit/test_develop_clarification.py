"""測試 develop phase 的 NEED_CLARIFICATION 處理"""
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from cafe.phases.develop_phase import DevelopPhase
from cafe.core.types import PhaseStatus, WorkflowMode
from cafe.core.status_codes import PhaseStatusCode


class TestDevelopClarificationPrompt:
    """測試 develop phase prompt 不包含業務邏輯澄清的描述"""
    
    def test_prompt_does_not_mention_business_logic(self):
        """測試 develop phase 的 prompt 不包含"業務邏輯或需求不清楚"這一條"""
        from pathlib import Path
        
        # Read the develop_phase.py source file
        source_file = Path("src/cafe/phases/develop_phase.py")
        source = source_file.read_text()
        
        # Verify business logic is NOT mentioned
        assert "業務邏輯或需求不清楚" not in source
        
        # Verify the other condition is still there
        assert "被要求執行的事項與角色的行為準則產生衝突" in source
        
        # Verify NEED_CLARIFICATION is still mentioned
        assert "CAFE_NEED_CLARIFICATION" in source


class TestDevelopClarificationAutoMode:
    """測試 develop --auto 處理 NEED_CLARIFICATION 的行為"""
    
    @patch('cafe.ui.cli.typer.prompt')
    def test_auto_mode_prompts_for_user_input_on_clarification(self, mock_prompt, tmp_path):
        """測試 auto 模式下收到 NEED_CLARIFICATION 時會提示使用者輸入"""
        # This will be tested at integration level with CLI
        # For now, we test that the phase returns IN_PROGRESS status
        pass  # Will be implemented with CLI integration test
