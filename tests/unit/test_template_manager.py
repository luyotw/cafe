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
        """測試無效模版名稱會拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        source_file = tmp_path / "template.md"
        source_file.write_text("Content")

        # 測試包含路徑分隔符號名稱
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

        templates = manager.list_templates()

        # 驗證回傳模版名稱（不包含 .md 副檔名）
        # 包含內建模版（bug, default, simple）和自訂模版（template1, template2）
        assert sorted(templates) == ["bug", "default", "simple", "template1", "template2"]

    def test_list_templates_empty_directory(self, tmp_path: Path) -> None:
        """測試空目錄時回傳內建 templates"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir))

        templates = manager.list_templates()
        # TemplateManager 自動建立所有內建 plan templates
        assert sorted(templates) == ["bug", "default", "simple"]

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
        # 包含內建模版（bug, default, simple）+ template1（忽略 .txt and .json）
        assert sorted(templates) == ["bug", "default", "simple", "template1"]

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
        """測試刪除不存在模版時拋出錯誤"""
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

    def test_init_spec_template_creates_spec_directory(self, tmp_path: Path) -> None:
        """測試初始化 spec 模版時建立 spec 目錄"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir), template_type="spec")

        expected_dir = config_dir / "templates" / "spec"
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_spec_template_manager_list_templates(self, tmp_path: Path) -> None:
        """測試 spec 模版管理器列出模版"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir), template_type="spec")

        # 建立模版
        template_dir = config_dir / "templates" / "spec"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("Default spec")
        (template_dir / "simple.md").write_text("Simple spec")

        templates = manager.list_templates()
        assert "default" in templates
        assert "simple" in templates

    def test_spec_template_add_and_get(self, tmp_path: Path) -> None:
        """測試新增和取得 spec 模版"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir), template_type="spec")

        # 建立來源檔案
        source_file = tmp_path / "custom-spec.md"
        source_file.write_text("## Custom Spec Template")

        # 新增模版
        manager.add_template(str(source_file), "custom")

        # 驗證檔案已複製到 spec 目錄
        template_path = config_dir / "templates" / "spec" / "custom.md"
        assert template_path.exists()
        assert template_path.read_text() == "## Custom Spec Template"

        # 取得路徑
        path = manager.get_template_path("custom")
        assert path == template_path

    def test_init_copies_all_builtin_plan_templates(self, tmp_path: Path) -> None:
        """測試初始化時自動複製所有內建 plan 模版"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir), template_type="plan")

        template_dir = config_dir / "templates" / "plan"

        # 驗證 default.md 存在
        assert (template_dir / "default.md").exists()

        # 驗證 simple.md 存在
        assert (template_dir / "simple.md").exists()

        # 驗證 bug.md 存在
        assert (template_dir / "bug.md").exists()

        # 驗證可以列出所有模版
        templates = manager.list_templates()
        assert "default" in templates
        assert "simple" in templates
        assert "bug" in templates

    def test_init_copies_all_builtin_spec_templates(self, tmp_path: Path) -> None:
        """測試初始化時自動複製所有內建 spec 模版"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(str(config_dir), template_type="spec")

        template_dir = config_dir / "templates" / "spec"

        # 驗證 default.md 存在
        assert (template_dir / "default.md").exists()

        # 驗證 simple.md 存在
        assert (template_dir / "simple.md").exists()

        # 驗證 detailed.md 存在
        assert (template_dir / "detailed.md").exists()

        # 驗證可以列出所有模版
        templates = manager.list_templates()
        assert "default" in templates
        assert "simple" in templates
        assert "detailed" in templates

    def test_init_does_not_overwrite_existing_templates(self, tmp_path: Path) -> None:
        """測試初始化時不會覆蓋已存在的模版"""
        config_dir = tmp_path / ".cafe"
        template_dir = config_dir / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)

        # 建立自訂內容的模版
        custom_content = "## My Custom Default Template"
        (template_dir / "default.md").write_text(custom_content)

        # 初始化 manager
        manager = TemplateManager(str(config_dir), template_type="plan")

        # 驗證自訂內容未被覆蓋
        assert (template_dir / "default.md").read_text() == custom_content
