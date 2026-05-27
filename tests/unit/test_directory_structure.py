"""測試專案目錄結構."""

from pathlib import Path

import pytest


class TestDirectoryStructure:
    """測試專案目錄結構是否符合預期."""

    def test_templates_bundled_under_owning_skills(self) -> None:
        """測試 templates 已搬到 owning skill 的 assets/ 目錄底下."""
        spec_templates = Path("src/cafe/data/skills/spec/assets/templates")
        plan_templates = Path("src/cafe/data/skills/plan/assets/templates")
        assert spec_templates.is_dir(), (
            "Spec templates should live under spec skill's assets/templates/"
        )
        assert plan_templates.is_dir(), (
            "Plan templates should live under plan skill's assets/templates/"
        )

    def test_templates_plan_default_exists_under_skill_assets(self) -> None:
        """測試 plan default.md 模板存在於 plan skill 的 assets/ 底下."""
        default_template = Path(
            "src/cafe/data/skills/plan/assets/templates/default.md"
        )
        assert default_template.is_file(), (
            "src/cafe/data/skills/plan/assets/templates/default.md should exist"
        )

    def test_legacy_templates_directory_removed(self) -> None:
        """舊的 src/cafe/data/templates/ 目錄已移除."""
        legacy = Path("src/cafe/data/templates")
        assert not legacy.exists(), (
            "src/cafe/data/templates should be removed; templates live under skill assets/"
        )

    def test_src_cafe_templates_plan_does_not_exist(self) -> None:
        """測試 src/cafe/templates/plan 目錄不存在（模板內容已移除）."""
        old_templates_plan_dir = Path("src/cafe/templates/plan")
        assert not old_templates_plan_dir.exists(), "src/cafe/templates/plan should not exist"

    def test_agents_directory_structure_in_package_data(self) -> None:
        """測試 agents 目錄結構符合預期（在 package data 中）."""
        # Check subdirectories exist
        assert (Path("src/cafe/data/agents/pm")).exists(), "src/cafe/data/agents/pm should exist"
        assert (Path("src/cafe/data/agents/developer")).exists(), "src/cafe/data/agents/developer should exist"
        assert (Path("src/cafe/data/agents/reviewer")).exists(), "src/cafe/data/agents/reviewer should exist"

        # Check agent files are in correct subdirectories
        assert (Path("src/cafe/data/agents/pm/Roger.md")).exists(), "Roger.md should be in src/cafe/data/agents/pm/"
        assert (Path("src/cafe/data/agents/developer/David.md")).exists(), "David.md should be in src/cafe/data/agents/developer/"
        assert (Path("src/cafe/data/agents/developer/Nick.md")).exists(), "Nick.md should be in src/cafe/data/agents/developer/"
        assert (Path("src/cafe/data/agents/reviewer/Richard.md")).exists(), "Richard.md should be in src/cafe/data/agents/reviewer/"

    def test_repo_root_agents_and_templates_do_not_exist(self) -> None:
        """測試 repo root  agents and templates 目錄不存在（已移到 package data）."""
        assert not Path("agents").exists(), "agents directory should not exist at repo root"
        assert not Path("templates").exists(), "templates directory should not exist at repo root"
