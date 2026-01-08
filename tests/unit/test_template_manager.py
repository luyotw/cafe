"""Tests for template manager."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.templates.manager import TemplateManager


class TestTemplateManager:
    """Tests for TemplateManager class."""

    def test_init_does_not_create_project_directory(self, tmp_path: Path) -> None:
        """測試初始化時不再創建專案層級的模版目錄"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        # Should NOT create project-level directory
        project_template_dir = config_dir / "templates" / "plan"
        assert not project_template_dir.exists()

    def test_add_template_copies_file(self, tmp_path: Path) -> None:
        """測試新增模版會複製檔案到全域目錄"""
        # Mock global directory
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                config_dir = tmp_path / ".cafe"
                manager = TemplateManager()

                # 建立來源檔案
                source_file = tmp_path / "my-template.md"
                source_file.write_text("## Template Content\n\nTest template")

                # 新增模版
                manager.add_template(str(source_file), "my-template")

                # 驗證檔案已複製到全域目錄
                global_template_path = fake_home / ".cafe" / "templates" / "plan" / "my-template.md"
                assert global_template_path.exists()
                assert global_template_path.read_text() == "## Template Content\n\nTest template"

    def test_add_template_auto_adds_md_extension(self, tmp_path: Path) -> None:
        """測試新增模版時自動加上 .md 副檔名"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                config_dir = tmp_path / ".cafe"
                manager = TemplateManager()

                source_file = tmp_path / "template.md"
                source_file.write_text("Content")

                # 不提供 .md 副檔名
                manager.add_template(str(source_file), "mytemplate")

                # 驗證檔案名稱有 .md
                global_template_path = fake_home / ".cafe" / "templates" / "plan" / "mytemplate.md"
                assert global_template_path.exists()

    def test_add_template_source_not_found_raises_error(self, tmp_path: Path) -> None:
        """測試來源檔案不存在時拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        with pytest.raises(FileNotFoundError, match="Source file not found"):
            manager.add_template("nonexistent.md", "test")

    def test_add_template_invalid_name_raises_error(self, tmp_path: Path) -> None:
        """測試無效模版名稱會拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

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

    def test_add_template_raises_file_exists_error_if_duplicate(self, tmp_path: Path) -> None:
        """測試新增重複模版時拋出 FileExistsError"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                manager = TemplateManager()

                # 建立來源檔案
                source_file = tmp_path / "my-template.md"
                source_file.write_text("## Template Content\n\nTest template")

                # 第一次新增成功
                manager.add_template(str(source_file), "my-template")

                # 第二次新增同名模版時應該拋出錯誤
                with pytest.raises(FileExistsError, match="Template 'my-template.md' already exists"):
                    manager.add_template(str(source_file), "my-template")

    def test_list_templates_returns_all_templates(self, tmp_path: Path) -> None:
        """測試列出所有模版（系統 + 全域）"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)
        
        # 在全域目錄建立模版
        (global_template_dir / "template1.md").write_text("Content 1")
        (global_template_dir / "template2.md").write_text("Content 2")
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            config_dir = tmp_path / ".cafe"
            manager = TemplateManager()
            
            templates = manager.list_templates()
            template_names = [t[0] for t in templates]
            
            # 應該包含系統 templates (bug, default, simple) 和全域 templates (template1, template2)
            assert "bug" in template_names
            assert "default" in template_names
            assert "simple" in template_names
            assert "template1" in template_names
            assert "template2" in template_names

    def test_list_templates_empty_directory(self, tmp_path: Path) -> None:
        """測試空目錄時回傳內建 templates（系統目錄）"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            config_dir = tmp_path / ".cafe"
            manager = TemplateManager()

            templates = manager.list_templates()
            template_names = [t[0] for t in templates]
            # 應該只有系統 templates
            assert sorted(template_names) == ["bug", "default", "simple"]

    def test_list_templates_ignores_non_md_files(self, tmp_path: Path) -> None:
        """測試只列出 .md 檔案"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)

        (global_template_dir / "template1.md").write_text("Content")
        (global_template_dir / "template2.txt").write_text("Not markdown")
        (global_template_dir / "template3.json").write_text("{}")

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            config_dir = tmp_path / ".cafe"
            manager = TemplateManager()
            
            templates = manager.list_templates()
            template_names = [t[0] for t in templates]
            # 包含內建模版（bug, default, simple）+ template1（忽略 .txt and .json）
            assert "bug" in template_names
            assert "default" in template_names
            assert "simple" in template_names
            assert "template1" in template_names
            assert "template2" not in template_names  # .txt 被忽略
            assert "template3" not in template_names  # .json 被忽略

    def test_remove_template_deletes_file(self, tmp_path: Path) -> None:
        """測試刪除模版從全域目錄"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)
        template_path = global_template_dir / "test.md"
        template_path.write_text("Content")

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                config_dir = tmp_path / ".cafe"
                manager = TemplateManager()

                # 刪除模版
                manager.remove_template("test")

                # 驗證已從全域目錄刪除
                assert not template_path.exists()

    def test_remove_template_with_md_extension(self, tmp_path: Path) -> None:
        """測試刪除模版時可以提供 .md 副檔名"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)
        template_path = global_template_dir / "test.md"
        template_path.write_text("Content")

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                config_dir = tmp_path / ".cafe"
                manager = TemplateManager()

                # 使用 .md 副檔名刪除
                manager.remove_template("test.md")

                assert not template_path.exists()

    def test_remove_template_not_found_raises_error(self, tmp_path: Path) -> None:
        """測試刪除不存在模版時拋出錯誤"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        with pytest.raises(FileNotFoundError, match="Template not found"):
            manager.remove_template("nonexistent")

    def test_get_template_path_returns_path(self, tmp_path: Path) -> None:
        """測試取得模版路徑（從全域或系統目錄）"""
        # 測試從系統目錄取得
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        # 取得系統 template 路徑
        path = manager.get_template_path("default")
        assert path is not None
        assert path.exists()
        assert "default.md" in str(path)

    def test_get_template_path_with_md_extension(self, tmp_path: Path) -> None:
        """測試取得模版路徑時可以提供 .md 副檔名"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        path = manager.get_template_path("default.md")
        assert path is not None
        assert path.exists()
        assert "default.md" in str(path)

    def test_get_template_path_not_found_returns_none(self, tmp_path: Path) -> None:
        """測試模版不存在時回傳 None"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        path = manager.get_template_path("nonexistent")
        assert path is None

    def test_template_exists_returns_true_when_exists(self, tmp_path: Path) -> None:
        """測試模版存在時回傳 True（系統 template）"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        # 測試系統 template
        assert manager.template_exists("default") is True
        assert manager.template_exists("default.md") is True

    def test_template_exists_returns_false_when_not_exists(self, tmp_path: Path) -> None:
        """測試模版不存在時回傳 False"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        assert manager.template_exists("nonexistent") is False

    def test_init_spec_template_does_not_create_project_directory(self, tmp_path: Path) -> None:
        """測試初始化 spec 模版時不再創建專案層級的 spec 目錄"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(template_type="spec")

        # Should NOT create project-level directory
        project_spec_dir = config_dir / "templates" / "spec"
        assert not project_spec_dir.exists()

    def test_spec_template_manager_list_templates(self, tmp_path: Path) -> None:
        """測試 spec 模版管理器列出模版"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(template_type="spec")

        # 建立模版
        template_dir = config_dir / "templates" / "spec"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("Default spec")
        (template_dir / "simple.md").write_text("Simple spec")

        templates = manager.list_templates()
        template_names = [t[0] for t in templates]
        assert "default" in template_names
        assert "simple" in template_names

    def test_spec_template_add_and_get(self, tmp_path: Path) -> None:
        """測試新增和取得 spec 模版（全域目錄）"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                config_dir = tmp_path / ".cafe"
                manager = TemplateManager(template_type="spec")

                # 建立來源檔案
                source_file = tmp_path / "custom-spec.md"
                source_file.write_text("## Custom Spec Template")

                # 新增模版
                manager.add_template(str(source_file), "custom")

                # 驗證檔案已複製到全域 spec 目錄
                global_template_path = fake_home / ".cafe" / "templates" / "spec" / "custom.md"
                assert global_template_path.exists()
                assert global_template_path.read_text() == "## Custom Spec Template"

                # 取得路徑
                path = manager.get_template_path("custom")
                assert path == global_template_path

    def test_init_copies_all_builtin_plan_templates(self, tmp_path: Path) -> None:
        """測試初始化時可以列出所有內建 plan 模版（從系統目錄）"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(template_type="plan")

        # 驗證可以列出所有系統模版
        templates = manager.list_templates()
        template_names = [t[0] for t in templates]
        assert "default" in template_names
        assert "simple" in template_names
        assert "bug" in template_names
        
        # 驗證可以取得系統 template 路徑
        assert manager.get_template_path("default") is not None
        assert manager.get_template_path("simple") is not None
        assert manager.get_template_path("bug") is not None

    def test_init_copies_all_builtin_spec_templates(self, tmp_path: Path) -> None:
        """測試初始化時可以列出所有內建 spec 模版（從系統目錄）"""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(template_type="spec")

        # 驗證可以列出所有系統模版
        templates = manager.list_templates()
        template_names = [t[0] for t in templates]
        assert "default" in template_names
        assert "simple" in template_names
        assert "detailed" in template_names
        
        # 驗證可以取得系統 template 路徑
        assert manager.get_template_path("default") is not None
        assert manager.get_template_path("simple") is not None
        assert manager.get_template_path("detailed") is not None

    def test_init_does_not_overwrite_existing_templates(self, tmp_path: Path) -> None:
        """測試初始化時全域自定義 template 優先於系統 template"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)

        # 在全域目錄建立自訂內容的模版
        custom_content = "## My Custom Default Template"
        (global_template_dir / "default.md").write_text(custom_content)

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            config_dir = tmp_path / ".cafe"
            # 初始化 manager
            manager = TemplateManager(template_type="plan")

            # 驗證取得的是全域自訂版本
            path = manager.get_template_path("default")
            assert path == global_template_dir / "default.md"
            assert path.read_text() == custom_content