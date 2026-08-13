"""Tests for ConfigManager."""

import pytest
from pathlib import Path
import yaml

from cafe.utils.config import ConfigManager, ConfigError


@pytest.fixture
def config_with_file(tmp_path):
    """Create a ConfigManager with an existing config file."""
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    return ConfigManager(config_dir=str(tmp_path / ".cafe"))


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

    def test_load_nonexistent_config_raises_error(self, tmp_path: Path) -> None:
        """測試載入不存在設定檔拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        manager = ConfigManager(config_dir=str(config_dir))

        with pytest.raises(ConfigError, match="Configuration file not found"):
            manager.load_config()

    def test_load_invalid_yaml_raises_error(self, tmp_path: Path) -> None:
        """測試載入無效 YAML 拋出錯誤"""
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

        assert config["python_bin"] == "python3"
        assert "agents" not in config

    def test_default_config_structure(self) -> None:
        """測試預設設定結構"""
        manager = ConfigManager()
        config = manager.get_default_config()

        assert config["auto"]["max_review_iterations"] == 5


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
        """測試驗證有效設定"""
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

    def test_validate_rejects_non_mapping(self) -> None:
        manager = ConfigManager()
        with pytest.raises(ConfigError, match="mapping"):
            manager.validate_config([])


class TestGetConfigValue:
    """Test getting config values."""

    def test_get_existing_value(self, tmp_path: Path) -> None:
        """測試取得存在設定值"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text(yaml.dump({"workflow_mode": "github"}))

        manager = ConfigManager(config_dir=str(config_dir))
        value = manager.get("workflow_mode")

        assert value == "github"

    def test_get_nonexistent_value_returns_default(self, config_with_file) -> None:
        """測試取得不存在值回傳預設"""
        value = config_with_file.get("nonexistent_key", default="default_value")

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

    def test_set_value(self, config_with_file) -> None:
        """測試設定值"""
        config_with_file.set("workflow_mode", "github")

        value = config_with_file.get("workflow_mode")
        assert value == "github"

    def test_set_nested_value(self, config_with_file) -> None:
        """測試設定巢狀值"""
        config_with_file.set("database.host", "localhost")

        value = config_with_file.get("database.host")
        assert value == "localhost"

    def test_set_persists_to_file(self, config_with_file) -> None:
        """測試設定會持久化到檔案"""
        config_with_file.set("workflow_mode", "github")

        # Create new manager to load from file
        manager2 = ConfigManager(config_dir=str(config_with_file.config_dir))
        value = manager2.get("workflow_mode")

        assert value == "github"


class TestResetConfig:
    """Test config reset."""

    def test_reset_to_defaults(self, config_with_file) -> None:
        """測試重置為預設值"""
        # Set some custom values
        config_with_file.set("python_bin", "custom-python")
        assert config_with_file.get("python_bin") == "custom-python"

        # Reset
        config_with_file.reset()

        # Should be back to default (from get_default_config)
        assert config_with_file.get("python_bin") == "python3"

    def test_reset_persists_to_file(self, config_with_file) -> None:
        """測試重置會持久化到檔案"""
        config_with_file.set("python_bin", "custom-python")
        config_with_file.reset()

        # Load with new manager
        manager2 = ConfigManager(config_dir=str(config_with_file.config_dir))
        manager2.load_config()
        assert manager2.get("python_bin") == "python3"


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


class TestConfigUnicodeHandling:
    """Test Unicode character handling in config serialization."""

    def test_save_config_with_unicode_characters(self, tmp_path: Path) -> None:
        """Test that config with Unicode characters (e.g., Chinese names) saves and loads correctly."""
        config_dir = tmp_path / ".cafe"
        config_manager = ConfigManager(config_dir=str(config_dir))

        unicode_config = {"project_label": "黃建", "python_bin": "python3"}

        # Save config
        config_manager.save_config(unicode_config)

        loaded = config_manager.load_config()
        assert loaded["project_label"] == "黃建"

    def test_config_file_contains_readable_unicode(self, tmp_path: Path) -> None:
        """Test that saved config file contains readable Unicode (not escape sequences)."""
        config_dir = tmp_path / ".cafe"
        config_manager = ConfigManager(config_dir=str(config_dir))

        # Create config with Unicode
        unicode_config = {"project_label": "黃建"}

        config_manager.save_config(unicode_config)

        # Read the raw file content
        config_file = config_dir / "config.yaml"
        file_content = config_file.read_text(encoding="utf-8")

        # Verify that the file contains actual Unicode characters, not escape sequences
        # Should contain "黃建" not "\u9ec3\u5efa"
        assert "黃建" in file_content
        assert "\\u" not in file_content or "\\u" in file_content and "9ec3" not in file_content

    def test_save_and_load_preserves_unicode_roundtrip(self, tmp_path: Path) -> None:
        """Test that saving and loading config preserves Unicode through multiple roundtrips."""
        config_dir = tmp_path / ".cafe"
        config_manager = ConfigManager(config_dir=str(config_dir))

        original_config = {"project_label": "黃建", "python_bin": "python3"}

        config_manager.save_config(original_config)
        config_manager.load_config()
        loaded_once = config_manager.load_config()

        config_manager.save_config({"python_bin": "python3"})
        config_manager.load_config()
        loaded_twice = config_manager.load_config()
        assert loaded_once["project_label"] == "黃建"
        assert "project_label" not in loaded_twice


class TestGetAllowedDirectories:
    """Test ConfigManager.get_allowed_directories()."""

    def test_get_allowed_directories_returns_list_from_config(self, tmp_path: Path) -> None:
        """config.yaml 含 allowed_directories 時應回傳對應 list。"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "agents: {}\nallowed_directories:\n  - src\n  - tests\n"
        )
        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.get_allowed_directories() == ["src", "tests"]

    def test_get_allowed_directories_returns_empty_when_missing(self, tmp_path: Path) -> None:
        """config.yaml 未含 allowed_directories 時應回傳空 list。"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("agents: {}\n")
        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.get_allowed_directories() == []

    def test_get_allowed_directories_returns_empty_when_invalid_type(self, tmp_path: Path) -> None:
        """allowed_directories 為 null 或非 list 時應回傳空 list，不拋例外。"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("agents: {}\nallowed_directories: null\n")
        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.get_allowed_directories() == []

        (config_dir / "config.yaml").write_text("agents: {}\nallowed_directories: src\n")
        manager2 = ConfigManager(config_dir=str(config_dir))
        manager2.load_config()
        assert manager2.get_allowed_directories() == []


class TestAllowedDirectoryMutations:
    """Test ConfigManager list/add/remove allowed_directories helpers."""

    def test_list_allowed_directories_returns_empty_when_missing(self, tmp_path: Path) -> None:
        """config.yaml 未含 allowed_directories 時 list 應回傳空 list。"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("agents: {}\n")
        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.list_allowed_directories() == []

    def test_add_allowed_directory_appends_and_persists(self, tmp_path: Path, monkeypatch) -> None:
        """add_allowed_directory 應追加新路徑並寫入 config.yaml。"""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("agents: {}\n")
        (tmp_path / "docs").mkdir()

        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.add_allowed_directory("docs") is True
        assert manager.get_allowed_directories() == ["docs"]

        reloaded = ConfigManager(config_dir=str(config_dir))
        reloaded.load_config()
        assert reloaded.get_allowed_directories() == ["docs"]

    def test_add_allowed_directory_dedupes_duplicate(self, tmp_path: Path, monkeypatch) -> None:
        """重複加入相同路徑時應回傳 False 且不產生第二筆。"""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("agents: {}\nallowed_directories:\n  - src\n")
        (tmp_path / "src").mkdir()

        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.add_allowed_directory("src") is False
        assert manager.get_allowed_directories() == ["src"]

    def test_add_allowed_directory_dedupes_equivalent_paths(self, tmp_path: Path, monkeypatch) -> None:
        """正規化後等價的路徑不應重複加入。"""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("agents: {}\n")
        (tmp_path / "src").mkdir()

        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.add_allowed_directory("./src") is True
        assert manager.add_allowed_directory("src") is False
        assert manager.get_allowed_directories() == ["src"]

    def test_add_allowed_directory_preserves_other_config_keys(self, tmp_path: Path, monkeypatch) -> None:
        """新增 allowed directory 時應保留其他 config 鍵與順序。"""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        original = "agents: {}\nallowed_directories:\n  - src\nextra: kept\n"
        (config_dir / "config.yaml").write_text(original)
        (tmp_path / "tests").mkdir()

        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.add_allowed_directory("tests") is True

        saved = (config_dir / "config.yaml").read_text()
        assert saved.index("agents:") < saved.index("allowed_directories:")
        assert "extra: kept" in saved
        assert manager.get_allowed_directories() == ["src", "tests"]

    def test_remove_allowed_directory_removes_existing(self, tmp_path: Path) -> None:
        """remove_allowed_directory 應移除既有項目並持久化。"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "agents: {}\nallowed_directories:\n  - src\n  - tests\n"
        )

        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.remove_allowed_directory("src") is True
        assert manager.get_allowed_directories() == ["tests"]

        reloaded = ConfigManager(config_dir=str(config_dir))
        reloaded.load_config()
        assert reloaded.get_allowed_directories() == ["tests"]

    def test_remove_allowed_directory_noop_when_missing(self, tmp_path: Path) -> None:
        """移除不存在的路徑時應回傳 False 且 list 不變。"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("agents: {}\nallowed_directories:\n  - src\n")

        manager = ConfigManager(config_dir=str(config_dir))
        manager.load_config()
        assert manager.remove_allowed_directory("nope") is False
        assert manager.get_allowed_directories() == ["src"]


class TestValidateDirectoriesExist:
    """Test validate_directories_exist()."""

    def test_validate_directories_exist_passes_when_all_exist(self, tmp_path: Path) -> None:
        """全部目錄存在時不拋例外。"""
        from cafe.utils.config import validate_directories_exist
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        validate_directories_exist(["src", "tests"], tmp_path)  # must not raise

    def test_validate_directories_exist_raises_when_missing(self, tmp_path: Path) -> None:
        """有目錄不存在時拋 ConfigError，訊息含缺失名稱。"""
        from cafe.utils.config import validate_directories_exist
        (tmp_path / "src").mkdir()
        with pytest.raises(ConfigError) as exc_info:
            validate_directories_exist(["src", "nope"], tmp_path)
        assert "nope" in str(exc_info.value)

    def test_validate_directories_exist_lists_all_missing(self, tmp_path: Path) -> None:
        """多個目錄不存在時，例外訊息同時含所有缺失項目。"""
        from cafe.utils.config import validate_directories_exist
        with pytest.raises(ConfigError) as exc_info:
            validate_directories_exist(["alpha", "beta"], tmp_path)
        msg = str(exc_info.value)
        assert "alpha" in msg
        assert "beta" in msg

    def test_validate_directories_exist_treats_file_as_missing(self, tmp_path: Path) -> None:
        """路徑存在但為檔案（非目錄）時視為缺失。"""
        from cafe.utils.config import validate_directories_exist
        (tmp_path / "notadir").write_text("content")
        with pytest.raises(ConfigError) as exc_info:
            validate_directories_exist(["notadir"], tmp_path)
        assert "notadir" in str(exc_info.value)

    def test_validate_directories_exist_allows_existing_absolute_path(self, tmp_path: Path) -> None:
        """絕對路徑可由 operator 授權給 cross-repo playbooks 使用。"""
        from cafe.utils.config import validate_directories_exist
        outside = tmp_path.parent
        validate_directories_exist([str(outside)], tmp_path)

    def test_validate_directories_exist_rejects_missing_absolute_path(self, tmp_path: Path) -> None:
        """絕對路徑仍需存在且必須是目錄。"""
        from cafe.utils.config import validate_directories_exist
        outside = tmp_path.parent / "missing-absolute-dir"
        with pytest.raises(ConfigError) as exc_info:
            validate_directories_exist([str(outside)], tmp_path)
        assert str(outside) in str(exc_info.value)

    def test_validate_directories_exist_rejects_parent_traversal(self, tmp_path: Path) -> None:
        """含有 .. 的路徑即使解析後存在也應拒絕。"""
        from cafe.utils.config import validate_directories_exist
        sibling = tmp_path.parent / "sibling"
        sibling.mkdir(exist_ok=True)
        with pytest.raises(ConfigError) as exc_info:
            validate_directories_exist(["../sibling"], tmp_path)
        assert "../sibling" in str(exc_info.value)

    def test_validate_directories_exist_noop_for_empty_list(self, tmp_path: Path) -> None:
        """空 list 時不拋例外。"""
        from cafe.utils.config import validate_directories_exist
        validate_directories_exist([], tmp_path)  # must not raise
