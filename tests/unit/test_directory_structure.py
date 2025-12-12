"""測試專案目錄結構。"""

from pathlib import Path

import pytest


class TestDirectoryStructure:
    """測試專案目錄結構是否符合預期。"""

    def test_templates_directory_exists_at_root(self) -> None:
        """測試 templates 目錄存在於根目錄。"""
        templates_dir = Path("templates")
        assert templates_dir.exists(), "templates directory should exist at root"
        assert templates_dir.is_dir(), "templates should be a directory"

    def test_templates_plan_default_exists(self) -> None:
        """測試 templates/plan/default.md 檔案存在。"""
        default_template = Path("templates/plan/default.md")
        assert default_template.exists(), "templates/plan/default.md should exist"
        assert default_template.is_file(), "default.md should be a file"

    def test_src_cafe_templates_plan_does_not_exist(self) -> None:
        """測試 src/cafe/templates/plan 目錄不存在（模板內容已移除）。"""
        old_templates_plan_dir = Path("src/cafe/templates/plan")
        assert not old_templates_plan_dir.exists(), "src/cafe/templates/plan should not exist"

    def test_agents_directory_structure(self) -> None:
        """測試 agents 目錄結構符合預期。"""
        # Check subdirectories exist
        assert (Path("agents/pm")).exists(), "agents/pm should exist"
        assert (Path("agents/developer")).exists(), "agents/developer should exist"
        assert (Path("agents/reviewer")).exists(), "agents/reviewer should exist"

        # Check agent files are in correct subdirectories
        assert (Path("agents/pm/Roger.md")).exists(), "Roger.md should be in agents/pm/"
        assert (Path("agents/developer/David.md")).exists(), "David.md should be in agents/developer/"
        assert (Path("agents/developer/John.md")).exists(), "John.md should be in agents/developer/"
        assert (Path("agents/reviewer/Richard.md")).exists(), "Richard.md should be in agents/reviewer/"
