"""Tests for ConfigManager."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import yaml

from cafe.utils.config import ConfigManager, ConfigError
from cafe.core.types import WorkflowMode, AgentCLI


class TestConfigManagerBasics:
    """Test basic ConfigManager functionality."""

    def test_init_config_manager(self, tmp_path: Path) -> None:
        """測試初始化 ConfigManager"""
        config_dir = tmp_path / ".cafe"
        manager = ConfigManager(config_dir=str(config_dir))

        assert manager.config_dir == config_dir
        assert config_dir.exists()

    def test_init_creates_config_dir(self, tmp_path: Path) -> None:
        """測試初始化時建立設定目錄"""
        config_dir = tmp_path / ".cafe"
        assert not config_dir.exists()

        ConfigManager(config_dir=str(config_dir))

        assert config_dir.exists()


class TestLoadConfig:
    """Test config loading."""

    def test_load_existing_config(self, tmp_path: Path) -> None:
        """測試載入現有設定檔"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"

        config_data = {
            "workflow_mode": "github",
            "default_agent": "claude",
            "auto_approve_read": True,
        }
        config_file.write_text(yaml.dump(config_data))

        manager = ConfigManager(config_dir=str(config_dir))
        config = manager.load_config()

        assert config["workflow_mode"] == "github"
        assert config["default_agent"] == "claude"
        assert config["auto_approve_read"] is True

    def test_load_nonexistent_config_returns_defaults(self, tmp_path: Path) -> None:
        """測試載入不存在的設定檔回傳預設值"""
        config_dir = tmp_path / ".cafe"
        manager = ConfigManager(config_dir=str(config_dir))

        config = manager.load_config()

        assert config is not None
        assert "agents" in config
        assert "defaults" in config
        assert "workflow_mode" in config["defaults"]

    def test_load_invalid_yaml_raises_error(self, tmp_path: Path) -> None:
        """測試載入無效的 YAML 拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text("invalid: yaml: content: [")

        manager = ConfigManager(config_dir=str(config_dir))

        with pytest.raises(ConfigError, match="Failed to load config"):
            manager.load_config()


class TestDefaultConfig:
    """Test default configuration."""

    def test_get_default_config(self) -> None:
        """測試取得預設設定"""
        manager = ConfigManager()
        config = manager.get_default_config()

        assert "agents" in config
        assert isinstance(config["agents"], dict)
        assert "defaults" in config

    def test_default_config_structure(self) -> None:
        """測試預設設定結構"""
        manager = ConfigManager()
        config = manager.get_default_config()

        # Check agents structure
        assert "pm" in config["agents"]
        assert "developer" in config["agents"]
        assert "reviewer" in config["agents"]

        # Check each agent has name and cli
        assert config["agents"]["pm"]["name"] == "Roger"
        assert "cli" in config["agents"]["pm"]

        assert config["agents"]["developer"]["name"] == "David"
        assert "cli" in config["agents"]["developer"]

        assert config["agents"]["reviewer"]["name"] == "Richard"
        assert "cli" in config["agents"]["reviewer"]

    def test_default_config_defaults_section(self) -> None:
        """測試預設設定的 defaults 區塊"""
        manager = ConfigManager()
        config = manager.get_default_config()

        assert config["defaults"]["workflow_mode"] == "local"
        assert config["defaults"]["interactive"] is True


class TestSaveConfig:
    """Test config saving."""

    def test_save_config(self, tmp_path: Path) -> None:
        """測試儲存設定"""
        config_dir = tmp_path / ".cafe"
        manager = ConfigManager(config_dir=str(config_dir))

        config = {
            "workflow_mode": "github",
            "default_agent": "claude",
        }

        manager.save_config(config)

        # Verify file was created
        config_file = config_dir / "config.yaml"
        assert config_file.exists()

        # Verify content
        saved_data = yaml.safe_load(config_file.read_text())
        assert saved_data["workflow_mode"] == "github"
        assert saved_data["default_agent"] == "claude"

    def test_save_overwrites_existing_config(self, tmp_path: Path) -> None:
        """測試儲存會覆寫現有設定"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text("old: value")

        manager = ConfigManager(config_dir=str(config_dir))
        new_config = {"new": "value"}
        manager.save_config(new_config)

        saved_data = yaml.safe_load(config_file.read_text())
        assert "old" not in saved_data
        assert saved_data["new"] == "value"


class TestValidateConfig:
    """Test config validation."""

    def test_validate_valid_config(self) -> None:
        """測試驗證有效的設定"""
        manager = ConfigManager()
        config = {
            "workflow_mode": "github",
            "agents": [
                {"name": "Roger", "tool": "claude"},
                {"name": "David", "tool": "claude"},
            ],
        }

        result = manager.validate_config(config)

        assert result is True

    def test_validate_invalid_workflow_mode(self) -> None:
        """測試驗證無效的 workflow_mode"""
        manager = ConfigManager()
        config = {
            "workflow_mode": "invalid_mode",
            "agents": [],
        }

        with pytest.raises(ConfigError, match="Invalid workflow_mode"):
            manager.validate_config(config)

    def test_validate_invalid_agent_tool(self) -> None:
        """測試驗證無效的 agent cli"""
        manager = ConfigManager()
        config = {
            "workflow_mode": "local",
            "agents": [
                {"name": "Roger", "cli": "invalid_cli"},
            ],
        }

        with pytest.raises(ConfigError, match="Invalid agent"):
            manager.validate_config(config)

    def test_validate_missing_required_field(self) -> None:
        """測試驗證缺少必要欄位"""
        manager = ConfigManager()
        config = {
            # Missing workflow_mode
            "agents": [],
        }

        with pytest.raises(ConfigError, match="Missing required field"):
            manager.validate_config(config)


class TestGetConfigValue:
    """Test getting config values."""

    def test_get_existing_value(self, tmp_path: Path) -> None:
        """測試取得存在的設定值"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text(yaml.dump({"workflow_mode": "github"}))

        manager = ConfigManager(config_dir=str(config_dir))
        value = manager.get("workflow_mode")

        assert value == "github"

    def test_get_nonexistent_value_returns_default(self, tmp_path: Path) -> None:
        """測試取得不存在的值回傳預設"""
        manager = ConfigManager(config_dir=str(tmp_path / ".cafe"))
        value = manager.get("nonexistent_key", default="default_value")

        assert value == "default_value"

    def test_get_nested_value(self, tmp_path: Path) -> None:
        """測試取得巢狀設定值"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_data = {
            "database": {
                "host": "localhost",
                "port": 5432,
            }
        }
        config_file.write_text(yaml.dump(config_data))

        manager = ConfigManager(config_dir=str(config_dir))
        value = manager.get("database.host")

        assert value == "localhost"


class TestSetConfigValue:
    """Test setting config values."""

    def test_set_value(self, tmp_path: Path) -> None:
        """測試設定值"""
        manager = ConfigManager(config_dir=str(tmp_path / ".cafe"))
        manager.set("workflow_mode", "github")

        value = manager.get("workflow_mode")
        assert value == "github"

    def test_set_nested_value(self, tmp_path: Path) -> None:
        """測試設定巢狀值"""
        manager = ConfigManager(config_dir=str(tmp_path / ".cafe"))
        manager.set("database.host", "localhost")

        value = manager.get("database.host")
        assert value == "localhost"

    def test_set_persists_to_file(self, tmp_path: Path) -> None:
        """測試設定會持久化到檔案"""
        config_dir = tmp_path / ".cafe"
        manager = ConfigManager(config_dir=str(config_dir))
        manager.set("workflow_mode", "github")

        # Create new manager to load from file
        manager2 = ConfigManager(config_dir=str(config_dir))
        value = manager2.get("workflow_mode")

        assert value == "github"


class TestResetConfig:
    """Test config reset."""

    def test_reset_to_defaults(self, tmp_path: Path) -> None:
        """測試重置為預設值"""
        manager = ConfigManager(config_dir=str(tmp_path / ".cafe"))
        original_cli = manager.get("agents.pm.cli")

        # Set some custom values
        manager.set("agents.pm.cli", "gemini")
        assert manager.get("agents.pm.cli") == "gemini"

        # Reset
        manager.reset()

        # Should be back to default
        config = manager.get("agents")
        assert config["pm"]["cli"] == original_cli

    def test_reset_persists_to_file(self, tmp_path: Path) -> None:
        """測試重置會持久化到檔案"""
        config_dir = tmp_path / ".cafe"
        manager = ConfigManager(config_dir=str(config_dir))
        original_cli = manager.get("agents.pm.cli")

        manager.set("agents.pm.cli", "gemini")
        manager.reset()

        # Load with new manager
        manager2 = ConfigManager(config_dir=str(config_dir))
        config = manager2.load_config()

        assert config["agents"]["pm"]["cli"] == original_cli


class TestAliasResolution:
    """Test alias resolution for convenience shortcuts."""

    def test_resolve_agent_shortcut(self) -> None:
        """測試解析 agent CLI 快捷方式"""
        manager = ConfigManager()

        assert manager._resolve_alias("pm") == "agents.pm.cli"
        assert manager._resolve_alias("dev") == "agents.developer.cli"  # dev is alias for developer
        assert manager._resolve_alias("reviewer") == "agents.reviewer.cli"

    def test_resolve_agent_with_property(self) -> None:
        """測試解析帶屬性的 agent key"""
        manager = ConfigManager()

        assert manager._resolve_alias("pm.cli") == "agents.pm.cli"
        assert manager._resolve_alias("pm.name") == "agents.pm.name"
        assert manager._resolve_alias("dev.cli") == "agents.developer.cli"  # dev is alias for developer

    def test_resolve_non_agent_key(self) -> None:
        """測試非 agent key 不做轉換"""
        manager = ConfigManager()

        assert manager._resolve_alias("defaults.workflow_mode") == "defaults.workflow_mode"
        assert manager._resolve_alias("other.key") == "other.key"

    def test_set_with_alias(self, tmp_path: Path) -> None:
        """測試使用 alias 設定值"""
        manager = ConfigManager(config_dir=str(tmp_path / ".cafe"))

        # Use alias
        manager.set("pm", "gemini")

        # Should be stored in agents.pm.cli
        assert manager.get("agents.pm.cli") == "gemini"

    def test_set_with_agent_property_alias(self, tmp_path: Path) -> None:
        """測試使用 agent.property alias 設定值"""
        manager = ConfigManager(config_dir=str(tmp_path / ".cafe"))

        # Use shorthand
        manager.set("pm.name", "NewPM")

        # Should be stored in agents.pm.name
        assert manager.get("agents.pm.name") == "NewPM"


class TestMergeConfig:
    """Test config merging."""

    def test_merge_configs(self) -> None:
        """測試合併設定"""
        manager = ConfigManager()

        base = {
            "a": 1,
            "b": 2,
            "nested": {"x": 10},
        }

        override = {
            "b": 3,
            "c": 4,
            "nested": {"y": 20},
        }

        result = manager.merge_config(base, override)

        assert result["a"] == 1
        assert result["b"] == 3  # Overridden
        assert result["c"] == 4
        assert result["nested"]["x"] == 10
        assert result["nested"]["y"] == 20

    def test_merge_preserves_original(self) -> None:
        """測試合併不修改原始資料"""
        manager = ConfigManager()

        base = {"a": 1}
        override = {"b": 2}

        result = manager.merge_config(base, override)

        assert "b" not in base
        assert "a" not in override
