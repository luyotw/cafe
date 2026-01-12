"""測試 develop phase  NEED_CLARIFICATION 處理"""
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from cafe.phases.develop_phase import DevelopPhase
from cafe.core.types import PhaseStatus
from cafe.core.status_codes import PhaseStatusCode


class TestDevelopClarificationPrompt:
    """測試 develop phase prompt 不包含業務邏輯澄清描述"""
    
    def test_prompt_does_not_mention_business_logic(self):
        """測試 develop phase  prompt 不包含"業務邏輯or需求不清楚"這一條"""
        from pathlib import Path
        
        # Read the develop_phase.py source file
        source_file = Path("src/cafe/phases/develop_phase.py")
        source = source_file.read_text()
        
        # Verify business logic is NOT mentioned
        assert "業務邏輯or需求不清楚" not in source
        
        # Verify the other condition is still there
        assert "conflict with the agent's behavioral guidelines" in source.lower() or "conflict" in source.lower()
        
        # Verify NEED_CLARIFICATION is still mentioned
        assert "CAFE_NEED_CLARIFICATION" in source
