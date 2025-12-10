"""Tests for Phase._get_issue_config_value() method with nested keys."""

from pathlib import Path
import yaml
import pytest

from cafe.core.phase import Phase
from cafe.core.types import PhaseResult, PhaseStatus


class TestPhaseGetIssueConfigValue:
    """Test Phase._get_issue_config_value() with nested key support."""

    def test_get_simple_config_value(self, tmp_path):
        """測試讀取簡單的頂層配置值"""
        config_file = tmp_path / "config.yaml"
        config_data = {
            "base_branch": "main",
            "issue_id": "123"
        }
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        # Create a minimal Phase subclass for testing
        class TestPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED, message="test")

        phase = TestPhase()

        assert phase._get_issue_config_value(config_file, "base_branch") == "main"
        assert phase._get_issue_config_value(config_file, "issue_id") == "123"

    def test_get_nested_config_value(self, tmp_path):
        """測試讀取嵌套的配置值（使用點記法）"""
        config_file = tmp_path / "config.yaml"
        config_data = {
            "base_branch": "main",
            "pr": {
                "auto_create": False
            }
        }
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        class TestPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED, message="test")

        phase = TestPhase()

        # Test nested key with dot notation
        assert phase._get_issue_config_value(config_file, "pr.auto_create") is False

    def test_get_deeply_nested_config_value(self, tmp_path):
        """測試讀取多層嵌套的配置值"""
        config_file = tmp_path / "config.yaml"
        config_data = {
            "features": {
                "review": {
                    "enabled": True,
                    "mode": "local"
                }
            }
        }
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        class TestPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED, message="test")

        phase = TestPhase()

        assert phase._get_issue_config_value(config_file, "features.review.enabled") is True
        assert phase._get_issue_config_value(config_file, "features.review.mode") == "local"

    def test_get_nonexistent_nested_key(self, tmp_path):
        """測試讀取不存在的嵌套鍵時返回 None"""
        config_file = tmp_path / "config.yaml"
        config_data = {
            "pr": {
                "auto_create": True
            }
        }
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        class TestPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED, message="test")

        phase = TestPhase()

        assert phase._get_issue_config_value(config_file, "pr.nonexistent") is None
        assert phase._get_issue_config_value(config_file, "nonexistent.key") is None

    def test_get_value_from_nonexistent_file(self, tmp_path):
        """測試從不存在的配置檔讀取時返回 None"""
        config_file = tmp_path / "nonexistent.yaml"

        class TestPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED, message="test")

        phase = TestPhase()

        assert phase._get_issue_config_value(config_file, "any.key") is None
