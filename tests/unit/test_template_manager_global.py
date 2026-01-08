"""測試 TemplateManager 全域目錄支援."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.templates.manager import TemplateManager


class TestTemplateManagerGlobalSupport:
    """測試 TemplateManager 全域配置目錄支援."""

    def test_list_templates_merges_system_and_global(self) -> None:
        """測試 list_templates() 合併系統和全域 templates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            global_template_dir = fake_home / ".cafe" / "templates" / "plan"
            global_template_dir.mkdir(parents=True)
            
            # 創建全域 template
            (global_template_dir / "custom-global.md").write_text("Custom global template")
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                with tempfile.TemporaryDirectory() as tmpdir2:
                    config_dir = Path(tmpdir2) / ".cafe"
                    manager = TemplateManager(template_type="plan")
                    
                    templates = manager.list_templates()
                    
                    # 應該包含系統預設 templates (bug, default, simple) 和全域 custom template
                    template_names = [t[0] for t in templates]
                    assert "bug" in template_names
                    assert "default" in template_names
                    assert "simple" in template_names
                    assert "custom-global" in template_names

    def test_list_templates_returns_tuples_with_source_type(self) -> None:
        """測試 list_templates() 回傳包含來源類型的 tuple."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            global_template_dir = fake_home / ".cafe" / "templates" / "plan"
            global_template_dir.mkdir(parents=True)
            
            # 創建全域 template
            (global_template_dir / "custom.md").write_text("Custom template")
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                with tempfile.TemporaryDirectory() as tmpdir2:
                    config_dir = Path(tmpdir2) / ".cafe"
                    manager = TemplateManager(template_type="plan")
                    
                    templates = manager.list_templates()
                    
                    # 檢查格式: List[tuple[str, str]]
                    assert all(isinstance(t, tuple) and len(t) == 2 for t in templates)
                    
                    # 檢查系統 template 的來源類型
                    system_templates = [t for t in templates if t[1] == "system"]
                    assert any(t[0] == "default" for t in system_templates)
                    
                    # 檢查全域 template 的來源類型
                    custom_templates = [t for t in templates if t[1] == "custom"]
                    assert any(t[0] == "custom" for t in custom_templates)

    def test_list_templates_global_overrides_system_on_name_collision(self) -> None:
        """測試名稱衝突時全域 template 優先."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            global_template_dir = fake_home / ".cafe" / "templates" / "plan"
            global_template_dir.mkdir(parents=True)
            
            # 創建與系統同名的全域 template
            (global_template_dir / "default.md").write_text("Custom default")
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                with tempfile.TemporaryDirectory() as tmpdir2:
                    config_dir = Path(tmpdir2) / ".cafe"
                    manager = TemplateManager(template_type="plan")
                    
                    templates = manager.list_templates()
                    
                    # 找出 "default" template
                    default_templates = [t for t in templates if t[0] == "default"]
                    
                    # 應該只有一個 default，且來源為 custom
                    assert len(default_templates) == 1
                    assert default_templates[0][1] == "custom"

    def test_get_template_path_searches_global_first(self) -> None:
        """測試 get_template_path() 優先從全域搜尋."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            global_template_dir = fake_home / ".cafe" / "templates" / "plan"
            global_template_dir.mkdir(parents=True)
            
            # 創建全域 template
            global_custom = global_template_dir / "my-custom.md"
            global_custom.write_text("Global custom content")
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                with tempfile.TemporaryDirectory() as tmpdir2:
                    config_dir = Path(tmpdir2) / ".cafe"
                    manager = TemplateManager(template_type="plan")
                    
                    path = manager.get_template_path("my-custom")
                    
                    # 應該回傳全域路徑
                    assert path == global_custom
                    assert path.read_text() == "Global custom content"

    def test_get_template_path_falls_back_to_system(self) -> None:
        """測試 get_template_path() 在全域找不到時回到系統目錄."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                with tempfile.TemporaryDirectory() as tmpdir2:
                    config_dir = Path(tmpdir2) / ".cafe"
                    manager = TemplateManager(template_type="plan")
                    
                    # 查找系統 template
                    path = manager.get_template_path("default")
                    
                    # 應該找到系統的 default template
                    assert path is not None
                    assert path.exists()
                    assert "default.md" in str(path)

    def test_get_template_path_prefers_global_over_system(self) -> None:
        """測試同名 template 時優先使用全域版本."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            global_template_dir = fake_home / ".cafe" / "templates" / "plan"
            global_template_dir.mkdir(parents=True)
            
            # 創建與系統同名的全域 template，內容不同
            (global_template_dir / "default.md").write_text("CUSTOM DEFAULT")
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                with tempfile.TemporaryDirectory() as tmpdir2:
                    config_dir = Path(tmpdir2) / ".cafe"
                    manager = TemplateManager(template_type="plan")
                    
                    path = manager.get_template_path("default")
                    
                    # 應該回傳全域路徑且內容正確
                    assert path == global_template_dir / "default.md"
                    assert path.read_text() == "CUSTOM DEFAULT"

    def test_list_templates_for_spec_type(self) -> None:
        """測試 spec 類型的 template 列表合併."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            global_template_dir = fake_home / ".cafe" / "templates" / "spec"
            global_template_dir.mkdir(parents=True)
            
            # 創建全域 spec template
            (global_template_dir / "custom-spec.md").write_text("Custom spec")
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                with tempfile.TemporaryDirectory() as tmpdir2:
                    config_dir = Path(tmpdir2) / ".cafe"
                    manager = TemplateManager(template_type="spec")
                    
                    templates = manager.list_templates()
                    template_names = [t[0] for t in templates]
                    
                    # 應該包含系統 spec templates 和全域 custom spec
                    assert "default" in template_names
                    assert "simple" in template_names
                    assert "detailed" in template_names
                    assert "custom-spec" in template_names

    def test_add_template_rejects_duplicate_names(self) -> None:
        """測試 add_template() 拒絕同名 templates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            global_template_dir = fake_home / ".cafe" / "templates" / "plan"
            global_template_dir.mkdir(parents=True)
            
            # 創建已存在的 template
            existing_template = global_template_dir / "existing.md"
            existing_template.write_text("Existing template")
            
            # 創建要添加的新 template 源文件
            source_file = Path(tmpdir) / "new.md"
            source_file.write_text("New template content")
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                manager = TemplateManager(template_type="plan")
                
                # 嘗試添加同名 template 應該失敗
                with pytest.raises(FileExistsError) as exc_info:
                    manager.add_template(str(source_file), "existing")
                
                assert "already exists" in str(exc_info.value)
                assert "cafe template edit" in str(exc_info.value)

