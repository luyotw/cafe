"""測試 init_helpers 模組的輔助函式"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.ui.init_helpers import (
    check_available_clis,
    copy_data_directory,
    list_available_agents,
    parse_agent_file,
)


class TestCheckAvailableClis:
    """測試檢查可用 CLI 工具的功能"""

    def test_check_available_clis_with_all_installed(self) -> None:
        """測試所有 CLI 都已安裝的情況"""
        with patch("shutil.which") as mock_which:
            # 模擬所有 CLI 都已安裝
            mock_which.side_effect = lambda x: f"/usr/bin/{x}" if x in [
                "claude",
                "gemini",
                "cursor-agent",
                "copilot",
            ] else None

            available = check_available_clis()

            assert "claude" in available
            assert "gemini" in available
            assert "cursor-agent" in available
            assert "copilot" in available
            assert len(available) == 4

    def test_check_available_clis_with_partial_installed(self) -> None:
        """測試部分 CLI 已安裝的情況"""
        with patch("shutil.which") as mock_which:
            # 只有 claude 和 gemini 安裝
            mock_which.side_effect = lambda x: f"/usr/bin/{x}" if x in [
                "claude",
                "gemini",
            ] else None

            available = check_available_clis()

            assert "claude" in available
            assert "gemini" in available
            assert "cursor-agent" not in available
            assert "copilot" not in available
            assert len(available) == 2

    def test_check_available_clis_with_none_installed(self) -> None:
        """測試沒有 CLI 安裝的情況"""
        with patch("shutil.which", return_value=None):
            available = check_available_clis()

            assert len(available) == 0


class TestParseAgentFile:
    """測試解析 agent 檔案的功能"""

    def test_parse_agent_file_with_complete_frontmatter(self, tmp_path: Path) -> None:
        """測試解析包含完整 front matter 的檔案"""
        agent_file = tmp_path / "Roger.md"
        agent_file.write_text(
            """---
name: Roger
description: 經驗豐富的 Product Manager
---

Agent content here.
"""
        )

        result = parse_agent_file(agent_file)

        assert result["name"] == "Roger"
        assert result["description"] == "經驗豐富的 Product Manager"

    def test_parse_agent_file_missing_name(self, tmp_path: Path) -> None:
        """測試 front matter 缺少 name 時使用檔名"""
        agent_file = tmp_path / "David.md"
        agent_file.write_text(
            """---
description: 專門負責功能開發的 agent
---

Agent content here.
"""
        )

        result = parse_agent_file(agent_file)

        assert result["name"] == "David"
        assert result["description"] == "專門負責功能開發的 agent"

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
        assert result["description"] == "(無描述)"

    def test_parse_agent_file_no_frontmatter(self, tmp_path: Path) -> None:
        """測試沒有 front matter 的檔案"""
        agent_file = tmp_path / "John.md"
        agent_file.write_text("Just content without frontmatter.\n")

        result = parse_agent_file(agent_file)

        assert result["name"] == "John"
        assert result["description"] == "(無描述)"

    def test_parse_agent_file_empty_file(self, tmp_path: Path) -> None:
        """測試空檔案"""
        agent_file = tmp_path / "Empty.md"
        agent_file.write_text("")

        result = parse_agent_file(agent_file)

        assert result["name"] == "Empty"
        assert result["description"] == "(無描述)"


class TestListAvailableAgents:
    """測試列出可用 agents 的功能"""

    def test_list_available_agents_with_valid_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試列出有效的 agent 檔案"""
        # 建立測試目錄結構
        agents_dir = tmp_path / ".cafe" / "agents"
        pm_dir = agents_dir / "pm"
        pm_dir.mkdir(parents=True)

        # 建立測試檔案
        roger = pm_dir / "Roger.md"
        roger.write_text(
            """---
name: Roger
description: 經驗豐富的 PM
---
"""
        )

        alice = pm_dir / "Alice.md"
        alice.write_text(
            """---
name: Alice
description: 注重細節的 PM
---
"""
        )

        # 使用 monkeypatch 改變當前目錄
        monkeypatch.chdir(tmp_path)

        agents = list_available_agents("pm")

        assert len(agents) == 2
        names = [agent[0] for agent in agents]
        assert "Roger" in names
        assert "Alice" in names

    def test_list_available_agents_with_empty_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試空的 agent 目錄"""
        agents_dir = tmp_path / ".cafe" / "agents"
        pm_dir = agents_dir / "pm"
        pm_dir.mkdir(parents=True)

        # 使用 monkeypatch 改變當前目錄
        monkeypatch.chdir(tmp_path)

        agents = list_available_agents("pm")

        assert len(agents) == 0

    def test_list_available_agents_ignores_non_md_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試忽略非 .md 檔案"""
        agents_dir = tmp_path / ".cafe" / "agents"
        pm_dir = agents_dir / "pm"
        pm_dir.mkdir(parents=True)

        # 建立 .md 檔案
        roger = pm_dir / "Roger.md"
        roger.write_text(
            """---
name: Roger
description: PM
---
"""
        )

        # 建立非 .md 檔案
        (pm_dir / "README.txt").write_text("readme")
        (pm_dir / "config.yaml").write_text("config")

        # 使用 monkeypatch 改變當前目錄
        monkeypatch.chdir(tmp_path)

        agents = list_available_agents("pm")

        assert len(agents) == 1
        assert agents[0][0] == "Roger"


class TestCopyDataDirectory:
    """測試複製目錄的功能"""

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
        """測試覆蓋既有目錄"""
        source = tmp_path / "source"
        source.mkdir()
        (source / "file.txt").write_text("new content")

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "old_file.txt").write_text("old content")

        copy_data_directory(str(source), str(dest))

        assert dest.exists()
        assert (dest / "file.txt").read_text() == "new content"
        # 舊檔案應該被移除（因為 copytree 會覆蓋整個目錄）
        assert not (dest / "old_file.txt").exists()
