"""Tests for template manager."""

from pathlib import Path

import pytest

from cafe.templates.manager import TemplateManager


class TestTemplateManager:
    """Tests for TemplateManager class."""

    def test_init_creates_template_directory(self, tmp_path: Path) -> None:
        """測試初始化時建立模版目錄"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        expected_dir = config_dir / "templates" / "plan"
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_add_template_copies_file(self, tmp_path: Path) -> None:
        """測試新增模版會複製檔案"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        # 建立來源檔案
        source_file = tmp_path / "my-template.md"
        source_file.write_text("## Template Content\n\nTest template")

        # 新增模版
        manager.add_template(str(source_file), "my-template")

        # 驗證檔案已複製
        template_path = config_dir / "templates" / "plan" / "my-template.md"
        assert template_path.exists()
        assert template_path.read_text() == "## Template Content\n\nTest template"

    def test_add_template_auto_adds_md_extension(self, tmp_path: Path) -> None:
        """測試新增模版時自動加上 .md 副檔名"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        source_file = tmp_path / "template.md"
        source_file.write_text("Content")

        # 不提供 .md 副檔名
        manager.add_template(str(source_file), "mytemplate")

        # 驗證檔案名稱有 .md
        template_path = config_dir / "templates" / "plan" / "mytemplate.md"
        assert template_path.exists()

    def test_add_template_source_not_found_raises_error(self, tmp_path: Path) -> None:
        """測試來源檔案不存在時拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        with pytest.raises(FileNotFoundError, match="Source file not found"):
            manager.add_template("nonexistent.md", "test")

    def test_add_template_invalid_name_raises_error(self, tmp_path: Path) -> None:
        """測試無效的模版名稱會拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        source_file = tmp_path / "template.md"
        source_file.write_text("Content")

        # 測試包含路徑分隔符號的名稱
        with pytest.raises(ValueError, match="Invalid template name"):
            manager.add_template(str(source_file), "path/to/template")

        with pytest.raises(ValueError, match="Invalid template name"):
            manager.add_template(str(source_file), "path\\to\\template")

        # 測試空名稱
        with pytest.raises(ValueError, match="Invalid template name"):
            manager.add_template(str(source_file), "")

    def test_list_templates_returns_all_templates(self, tmp_path: Path) -> None:
        """測試列出所有模版"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        # 建立多個模版
        template_dir = config_dir / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)

        (template_dir / "template1.md").write_text("Content 1")
        (template_dir / "template2.md").write_text("Content 2")
        (template_dir / "default.md").write_text("Default content")

        templates = manager.list_templates()

        # 驗證回傳的模版名稱（不包含 .md 副檔名）
        assert sorted(templates) == ["default", "template1", "template2"]

    def test_list_templates_empty_directory(self, tmp_path: Path) -> None:
        """測試空目錄時回傳 default template"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        templates = manager.list_templates()
        # TemplateManager 自動建立 default template
        assert templates == ["default"]

    def test_list_templates_ignores_non_md_files(self, tmp_path: Path) -> None:
        """測試只列出 .md 檔案"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        template_dir = config_dir / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)

        (template_dir / "template1.md").write_text("Content")
        (template_dir / "template2.txt").write_text("Not markdown")
        (template_dir / "template3.json").write_text("{}")

        templates = manager.list_templates()
        # 包含 default + template1（忽略 .txt 和 .json）
        assert sorted(templates) == ["default", "template1"]

    def test_remove_template_deletes_file(self, tmp_path: Path) -> None:
        """測試刪除模版"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        # 建立模版
        template_dir = config_dir / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        template_path = template_dir / "test.md"
        template_path.write_text("Content")

        # 刪除模版
        manager.remove_template("test")

        # 驗證已刪除
        assert not template_path.exists()

    def test_remove_template_with_md_extension(self, tmp_path: Path) -> None:
        """測試刪除模版時可以提供 .md 副檔名"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        template_dir = config_dir / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        template_path = template_dir / "test.md"
        template_path.write_text("Content")

        # 使用 .md 副檔名刪除
        manager.remove_template("test.md")

        assert not template_path.exists()

    def test_remove_template_not_found_raises_error(self, tmp_path: Path) -> None:
        """測試刪除不存在的模版時拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        with pytest.raises(FileNotFoundError, match="Template not found"):
            manager.remove_template("nonexistent")

    def test_get_template_path_returns_path(self, tmp_path: Path) -> None:
        """測試取得模版路徑"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        template_dir = config_dir / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        template_path = template_dir / "test.md"
        template_path.write_text("Content")

        # 取得路徑
        path = manager.get_template_path("test")
        assert path == template_path
        assert path.exists()

    def test_get_template_path_with_md_extension(self, tmp_path: Path) -> None:
        """測試取得模版路徑時可以提供 .md 副檔名"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        template_dir = config_dir / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        template_path = template_dir / "test.md"
        template_path.write_text("Content")

        path = manager.get_template_path("test.md")
        assert path == template_path

    def test_get_template_path_not_found_returns_none(self, tmp_path: Path) -> None:
        """測試模版不存在時回傳 None"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        path = manager.get_template_path("nonexistent")
        assert path is None

    def test_template_exists_returns_true_when_exists(self, tmp_path: Path) -> None:
        """測試模版存在時回傳 True"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        template_dir = config_dir / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "test.md").write_text("Content")

        assert manager.template_exists("test") is True
        assert manager.template_exists("test.md") is True

    def test_template_exists_returns_false_when_not_exists(self, tmp_path: Path) -> None:
        """測試模版不存在時回傳 False"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        assert manager.template_exists("nonexistent") is False
