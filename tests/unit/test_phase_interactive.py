"""Tests for Phase base class interactive parameter."""

import pytest
from cafe.core.phase import Phase
from cafe.core.types import PhaseResult, PhaseStatus


class DummyPhase(Phase):
    """Dummy phase for testing Phase base class."""

    def execute(self) -> PhaseResult:
        """Dummy execute method."""
        return PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="test",
            data={"interactive": self.interactive}
        )


class TestPhaseInteractiveParameter:
    """測試 Phase 基礎類別 interactive 參數"""

    def test_default_interactive_is_true(self):
        """測試預設 interactive 為 True"""
        # Act
        phase = DummyPhase()
        
        # Assert
        assert phase.interactive is True

    def test_can_set_interactive_to_false(self):
        """測試可以設定 interactive 為 False"""
        # Act
        phase = DummyPhase(interactive=False)
        
        # Assert
        assert phase.interactive is False

    def test_can_set_interactive_to_true_explicitly(self):
        """測試可以明確設定 interactive 為 True"""
        # Act
        phase = DummyPhase(interactive=True)
        
        # Assert
        assert phase.interactive is True

    def test_subclass_inherits_interactive(self):
        """測試子類別繼承 interactive 屬性"""
        # Act
        phase = DummyPhase(interactive=False)
        result = phase.execute()
        
        # Assert
        assert result.data["interactive"] is False

    def test_multiple_instances_independent(self):
        """測試多個 Phase 實例 interactive 互相獨立"""
        # Act
        phase1 = DummyPhase(interactive=True)
        phase2 = DummyPhase(interactive=False)
        
        # Assert
        assert phase1.interactive is True
        assert phase2.interactive is False
