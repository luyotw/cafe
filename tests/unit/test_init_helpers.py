"""測試 init_helpers 模組輔助函式"""

from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.ui.init_helpers import (
    check_available_clis,
    copy_data_directory,
    list_available_agents,
    parse_agent_file,
)


class TestCheckAvailableClis:
    """測試檢查可用 CLI 工具功能"""

    def test_check_available_clis_with_all_installed(self) -> None:
        """測試所有 CLI 都已安裝情況"""
        with patch("shutil.which") as mock_which:
            # 模擬所有 CLI 都已安裝
            mock_which.side_effect = lambda x: (
                f"/usr/bin/{x}"
                if x
                in [
                    "claude",
                    "gemini",
                    "cursor-agent",
                    "copilot",
                ]
                else None
            )

            available = check_available_clis()

            assert "claude" in available
            assert "gemini" in available
            assert "cursor-agent" in available
            assert "copilot" in available
            assert len(available) == 4

    def test_check_available_clis_with_partial_installed(self) -> None:
        """測試部分 CLI 已安裝情況"""
        with patch("shutil.which") as mock_which:
            # 只有 claude and gemini 安裝
            mock_which.side_effect = lambda x: (
                f"/usr/bin/{x}"
                if x
                in [
                    "claude",
                    "gemini",
                ]
                else None
            )

            available = check_available_clis()

            assert "claude" in available
            assert "gemini" in available
            assert "cursor-agent" not in available
            assert "copilot" not in available
            assert len(available) == 2

    def test_check_available_clis_with_none_installed(self) -> None:
        """測試沒有 CLI 安裝情況"""
        with patch("shutil.which", return_value=None):
            available = check_available_clis()

            assert len(available) == 0


class TestParseAgentFile:
    """測試解析 agent 檔案功能"""

    def test_parse_agent_file_with_complete_frontmatter(self, tmp_path: Path) -> None:
        """測試解析包含完整 front matter 檔案"""
        agent_file = tmp_path / "Roger.md"
        agent_file.write_text(
            """---
name: Roger
description: 經驗豐富 Product Manager
---

Agent content here.
"""
        )

        result = parse_agent_file(agent_file)

        assert result["name"] == "Roger"
        assert result["description"] == "經驗豐富 Product Manager"

    def test_parse_agent_file_missing_name(self, tmp_path: Path) -> None:
        """測試 front matter 缺少 name 時使用檔名"""
        agent_file = tmp_path / "David.md"
        agent_file.write_text(
            """---
description: 專門負責功能開發 agent
---

Agent content here.
"""
        )

        result = parse_agent_file(agent_file)

        assert result["name"] == "David"
        assert result["description"] == "專門負責功能開發 agent"

    def test_parse_agent_file_missing_description(self, tmp_path: Path) -> None:
        """測試 front matter 缺少 description 時顯示預設值"""
        agent_file = tmp_path / "Richard.md"
        agent_file.write_text(
            """---
name: Richard
---

Agent content here.
"""
        )

        result = parse_agent_file(agent_file)

        assert result["name"] == "Richard"
        assert result["description"] == "(No description)"

    def test_parse_agent_file_no_frontmatter(self, tmp_path: Path) -> None:
        """測試沒有 front matter 檔案"""
        agent_file = tmp_path / "John.md"
        agent_file.write_text("Just content without frontmatter.\n")

        result = parse_agent_file(agent_file)

        assert result["name"] == "John"
        assert result["description"] == "(No description)"

    def test_parse_agent_file_empty_file(self, tmp_path: Path) -> None:
        """測試空檔案"""
        agent_file = tmp_path / "Empty.md"
        agent_file.write_text("")

        result = parse_agent_file(agent_file)

        assert result["name"] == "Empty"
        assert result["description"] == "(No description)"


class TestListAvailableAgents:
    """測試列出可用 agents 功能"""

    def test_list_available_agents_with_valid_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試列出有效 agent 檔案（包含系統和全域）"""
        from cafe.utils.config import get_global_cafe_dir
        
        # Mock get_global_cafe_dir to return test directory
        global_cafe_dir = tmp_path / "global_cafe"
        monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_cafe_dir)
        
        # 建立全域測試目錄結構
        global_pm_dir = global_cafe_dir / "agents" / "pm"
        global_pm_dir.mkdir(parents=True)

        # 建立測試檔案
        alice = global_pm_dir / "Alice.md"
        alice.write_text(
            """---
name: Alice
description: 注重細節 PM
---
"""
        )

        agents = list_available_agents("pm")

        # Should have system agents (Roger, 范曉燁) + global agent (Alice)
        assert len(agents) >= 3
        names = [agent[0] for agent in agents]
        assert "Roger" in names  # system default
        assert "Alice" in names  # custom
        
        # Verify source types
        alice_agent = [a for a in agents if a[0] == "Alice"][0]
        assert alice_agent[3] == "custom"

    def test_list_available_agents_with_empty_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試空全域 agent 目錄（但仍有系統預設 agents）"""
        from cafe.utils.config import get_global_cafe_dir
        
        # Mock get_global_cafe_dir to return empty test directory
        global_cafe_dir = tmp_path / "global_cafe"
        monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_cafe_dir)
        
        # 建立空的全域目錄
        global_pm_dir = global_cafe_dir / "agents" / "pm"
        global_pm_dir.mkdir(parents=True)

        agents = list_available_agents("pm")

        # Should still have system default agents (Roger, 范曉燁)
        assert len(agents) >= 2
        names = [agent[0] for agent in agents]
        assert "Roger" in names

    def test_list_available_agents_ignores_non_md_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試忽略非 .md 檔案"""
        from cafe.utils.config import get_global_cafe_dir
        
        # Mock get_global_cafe_dir to return test directory
        global_cafe_dir = tmp_path / "global_cafe"
        monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_cafe_dir)
        
        # 建立全域測試目錄結構
        global_pm_dir = global_cafe_dir / "agents" / "pm"
        global_pm_dir.mkdir(parents=True)

        # 建立 .md 檔案
        alice = global_pm_dir / "Alice.md"
        alice.write_text(
            """---
name: Alice
description: 注重細節 PM
---
"""
        )

        # 建立非 .md 檔案（應該被忽略）
        readme = global_pm_dir / "README.txt"
        readme.write_text("This is a readme")

        agents = list_available_agents("pm")

        # Should have system agents + Alice (但不包含 README.txt)
        names = [agent[0] for agent in agents]
        assert "Alice" in names
        assert "Roger" in names
        # Verify only .md files are included
        for agent in agents:
            assert agent[2].suffix == ".md"


class TestCopyDataDirectory:
    """測試複製目錄功能"""

    def test_copy_data_directory_success(self, tmp_path: Path) -> None:
        """測試成功複製目錄"""
        source = tmp_path / "source"
        source.mkdir()
        (source / "file1.txt").write_text("content1")
        (source / "file2.txt").write_text("content2")

        dest = tmp_path / "dest"

        copy_data_directory(str(source), str(dest))

        assert dest.exists()
        assert (dest / "file1.txt").read_text() == "content1"
        assert (dest / "file2.txt").read_text() == "content2"

    def test_copy_data_directory_source_not_exists(self, tmp_path: Path) -> None:
        """測試來源目錄不存在時拋出錯誤"""
        source = tmp_path / "nonexistent"
        dest = tmp_path / "dest"

        with pytest.raises(FileNotFoundError):
            copy_data_directory(str(source), str(dest))

    def test_copy_data_directory_permission_error(self, tmp_path: Path) -> None:
        """測試權限錯誤時拋出異常"""
        source = tmp_path / "source"
        source.mkdir()
        (source / "file.txt").write_text("content")

        dest = tmp_path / "dest"

        with patch("shutil.copytree", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                copy_data_directory(str(source), str(dest))

    def test_copy_data_directory_overwrites_existing(self, tmp_path: Path) -> None:
        """測試增量拷貝既有目錄"""
        source = tmp_path / "source"
        source.mkdir()
        (source / "file.txt").write_text("new content")

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "old_file.txt").write_text("old content")

        copy_data_directory(str(source), str(dest))

        assert dest.exists()
        assert (dest / "file.txt").read_text() == "new content"
        # 舊檔案會被保留（因為使用增量拷貝）
        assert (dest / "old_file.txt").exists()
        assert (dest / "old_file.txt").read_text() == "old content"


class TestCopyAgentsToLocal:
    """測試將 agent 檔案複製到本地 .cafe 目錄"""

    def _setup_system_agents(self, system_dir: Path) -> None:
        """建立模擬系統 agent 目錄結構"""
        for role in ["pm", "developer", "reviewer"]:
            role_dir = system_dir / role
            role_dir.mkdir(parents=True)
        (system_dir / "pm" / "Roger.md").write_text("# Roger (system)")
        (system_dir / "developer" / "David.md").write_text("# David (system)")
        (system_dir / "reviewer" / "Richard.md").write_text("# Richard (system)")

    def test_copy_agents_with_system_defaults_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試僅有系統預設 agent 時，系統檔案被複製到本地"""
        from cafe.ui.init_helpers import copy_agents_to_local

        # 建立系統 agent 目錄
        system_dir = tmp_path / "system_agents"
        self._setup_system_agents(system_dir)

        # 建立空的全域目錄
        global_dir = tmp_path / "global_cafe"
        (global_dir / "agents" / "pm").mkdir(parents=True)
        (global_dir / "agents" / "developer").mkdir(parents=True)
        (global_dir / "agents" / "reviewer").mkdir(parents=True)

        # 建立本地 .cafe 目錄
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_agents_dir", lambda: system_dir
        )

        results = copy_agents_to_local(cafe_dir)

        # 驗證所有系統 agent 被複製
        assert (cafe_dir / "agents" / "pm" / "Roger.md").exists()
        assert (cafe_dir / "agents" / "developer" / "David.md").exists()
        assert (cafe_dir / "agents" / "reviewer" / "Richard.md").exists()

        # 驗證回傳結果包含正確的來源類型
        for filename, source_type, success in results:
            assert success is True
            assert source_type == "system default"

    def test_copy_agents_global_custom_overrides_system(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試全域自定義 agent 優先於系統預設"""
        from cafe.ui.init_helpers import copy_agents_to_local

        # 建立系統 agent 目錄
        system_dir = tmp_path / "system_agents"
        self._setup_system_agents(system_dir)

        # 建立全域自定義 agent（與系統 Roger 同名）
        global_dir = tmp_path / "global_cafe"
        (global_dir / "agents" / "pm").mkdir(parents=True)
        (global_dir / "agents" / "developer").mkdir(parents=True)
        (global_dir / "agents" / "reviewer").mkdir(parents=True)
        (global_dir / "agents" / "pm" / "Roger.md").write_text("# Roger (custom)")

        # 建立本地 .cafe 目錄
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_agents_dir", lambda: system_dir
        )

        results = copy_agents_to_local(cafe_dir)

        # 驗證全域自定義版本被使用
        assert (cafe_dir / "agents" / "pm" / "Roger.md").read_text() == "# Roger (custom)"

        # 驗證 Roger 的來源類型為 custom
        roger_results = [r for r in results if "Roger.md" in r[0]]
        assert len(roger_results) == 1
        assert roger_results[0][1] == "custom"
        assert roger_results[0][2] is True

    def test_copy_agents_overwrites_existing_local_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試複製時覆寫本地既有 agent 檔案"""
        from cafe.ui.init_helpers import copy_agents_to_local

        # 建立系統 agent 目錄
        system_dir = tmp_path / "system_agents"
        self._setup_system_agents(system_dir)

        # 建立空的全域目錄
        global_dir = tmp_path / "global_cafe"
        (global_dir / "agents" / "pm").mkdir(parents=True)
        (global_dir / "agents" / "developer").mkdir(parents=True)
        (global_dir / "agents" / "reviewer").mkdir(parents=True)

        # 建立本地 .cafe 目錄，並放入舊版 agent
        cafe_dir = tmp_path / "project" / ".cafe"
        (cafe_dir / "agents" / "pm").mkdir(parents=True)
        (cafe_dir / "agents" / "pm" / "Roger.md").write_text("# Roger (old local)")

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_agents_dir", lambda: system_dir
        )

        copy_agents_to_local(cafe_dir)

        # 驗證舊版本被覆寫
        assert (cafe_dir / "agents" / "pm" / "Roger.md").read_text() == "# Roger (system)"

    def test_copy_agents_handles_copy_error_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試複製時遇到錯誤能優雅處理並回傳失敗訊息"""
        from cafe.ui.init_helpers import copy_agents_to_local

        # 建立系統 agent 目錄
        system_dir = tmp_path / "system_agents"
        self._setup_system_agents(system_dir)

        # 建立空的全域目錄
        global_dir = tmp_path / "global_cafe"
        (global_dir / "agents" / "pm").mkdir(parents=True)
        (global_dir / "agents" / "developer").mkdir(parents=True)
        (global_dir / "agents" / "reviewer").mkdir(parents=True)

        # 建立本地 .cafe 目錄
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_agents_dir", lambda: system_dir
        )

        # Mock shutil.copy2 拋出 PermissionError
        with patch("shutil.copy2", side_effect=PermissionError("Permission denied")):
            results = copy_agents_to_local(cafe_dir)

        # 驗證所有複製均失敗，但函式不拋出例外
        for filename, source_type, success in results:
            assert success is False


class TestCopyTemplatesToLocal:
    """測試將 template 檔案複製到本地 .cafe 目錄"""

    def _setup_system_templates(self, system_dir: Path) -> None:
        """建立模擬系統 template 目錄結構"""
        for phase in ["plan", "spec"]:
            phase_dir = system_dir / phase
            phase_dir.mkdir(parents=True)
        (system_dir / "plan" / "default.md").write_text("# Default Plan (system)")
        (system_dir / "plan" / "simple.md").write_text("# Simple Plan (system)")
        (system_dir / "spec" / "default.md").write_text("# Default Spec (system)")

    def test_copy_templates_with_system_defaults_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試僅有系統預設 template 時，系統檔案被複製到本地"""
        from cafe.ui.init_helpers import copy_templates_to_local

        # 建立系統 template 目錄
        system_dir = tmp_path / "system_templates"
        self._setup_system_templates(system_dir)

        # 建立空的全域目錄
        global_dir = tmp_path / "global_cafe"
        (global_dir / "templates" / "plan").mkdir(parents=True)
        (global_dir / "templates" / "spec").mkdir(parents=True)

        # 建立本地 .cafe 目錄
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_templates_dir", lambda: system_dir
        )

        results = copy_templates_to_local(cafe_dir)

        # 驗證所有系統 template 被複製
        assert (cafe_dir / "templates" / "plan" / "default.md").exists()
        assert (cafe_dir / "templates" / "plan" / "simple.md").exists()
        assert (cafe_dir / "templates" / "spec" / "default.md").exists()

        # 驗證回傳結果包含正確的來源類型
        for filename, source_type, success in results:
            assert success is True
            assert source_type == "system default"

    def test_copy_templates_global_custom_overrides_system(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試全域自定義 template 優先於系統預設"""
        from cafe.ui.init_helpers import copy_templates_to_local

        # 建立系統 template 目錄
        system_dir = tmp_path / "system_templates"
        self._setup_system_templates(system_dir)

        # 建立全域自定義 template（與系統 default.md 同名）
        global_dir = tmp_path / "global_cafe"
        (global_dir / "templates" / "plan").mkdir(parents=True)
        (global_dir / "templates" / "spec").mkdir(parents=True)
        (global_dir / "templates" / "plan" / "default.md").write_text(
            "# Default Plan (custom)"
        )

        # 建立本地 .cafe 目錄
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_templates_dir", lambda: system_dir
        )

        results = copy_templates_to_local(cafe_dir)

        # 驗證全域自定義版本被使用
        assert (
            cafe_dir / "templates" / "plan" / "default.md"
        ).read_text() == "# Default Plan (custom)"

        # 驗證 default.md 的來源類型為 custom
        default_results = [r for r in results if "default.md" in r[0] and "plan" in r[0]]
        assert len(default_results) == 1
        assert default_results[0][1] == "custom"

    def test_copy_templates_overwrites_existing_local_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試複製時覆寫本地既有 template 檔案"""
        from cafe.ui.init_helpers import copy_templates_to_local

        # 建立系統 template 目錄
        system_dir = tmp_path / "system_templates"
        self._setup_system_templates(system_dir)

        # 建立空的全域目錄
        global_dir = tmp_path / "global_cafe"
        (global_dir / "templates" / "plan").mkdir(parents=True)
        (global_dir / "templates" / "spec").mkdir(parents=True)

        # 建立本地 .cafe 目錄，並放入舊版 template
        cafe_dir = tmp_path / "project" / ".cafe"
        (cafe_dir / "templates" / "plan").mkdir(parents=True)
        (cafe_dir / "templates" / "plan" / "default.md").write_text(
            "# Default Plan (old local)"
        )

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_templates_dir", lambda: system_dir
        )

        copy_templates_to_local(cafe_dir)

        # 驗證舊版本被覆寫
        assert (
            cafe_dir / "templates" / "plan" / "default.md"
        ).read_text() == "# Default Plan (system)"

    def test_copy_templates_handles_copy_error_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試複製時遇到錯誤能優雅處理並回傳失敗訊息"""
        from cafe.ui.init_helpers import copy_templates_to_local

        # 建立系統 template 目錄
        system_dir = tmp_path / "system_templates"
        self._setup_system_templates(system_dir)

        # 建立空的全域目錄
        global_dir = tmp_path / "global_cafe"
        (global_dir / "templates" / "plan").mkdir(parents=True)
        (global_dir / "templates" / "spec").mkdir(parents=True)

        # 建立本地 .cafe 目錄
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_templates_dir", lambda: system_dir
        )

        # Mock shutil.copy2 拋出 PermissionError
        with patch("shutil.copy2", side_effect=PermissionError("Permission denied")):
            results = copy_templates_to_local(cafe_dir)

        # 驗證所有複製均失敗，但函式不拋出例外
        for filename, source_type, success in results:
            assert success is False
