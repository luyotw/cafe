"""測試 ensure_agent_file_exists 函數。"""

from pathlib import Path

import pytest

from cafe.core.phase import ensure_agent_file_exists


class TestEnsureAgentFileExists:
    """測試 ensure_agent_file_exists 函數。"""

    def test_agent_file_exists_roger(self, tmp_path: Path) -> None:
        """測試 Roger agent 檔案存在時不會拋出錯誤。"""
        # 建立 agent 目錄結構
        cafe_dir = tmp_path / ".cafe"
        pm_dir = cafe_dir / "agents" / "pm"
        pm_dir.mkdir(parents=True)

        # 建立 Roger.md
        roger_file = pm_dir / "Roger.md"
        roger_file.write_text("# Roger - PM Agent")

        # 應該不會拋出錯誤
        ensure_agent_file_exists("Roger", "pm", cafe_dir)

    def test_agent_file_exists_david(self, tmp_path: Path) -> None:
        """測試 David agent 檔案存在時不會拋出錯誤。"""
        # 建立 agent 目錄結構
        cafe_dir = tmp_path / ".cafe"
        dev_dir = cafe_dir / "agents" / "developer"
        dev_dir.mkdir(parents=True)

        # 建立 David.md
        david_file = dev_dir / "David.md"
        david_file.write_text("# David - Developer Agent")

        # 應該不會拋出錯誤
        ensure_agent_file_exists("David", "developer", cafe_dir)

    def test_agent_file_exists_john(self, tmp_path: Path) -> None:
        """測試 John agent 檔案存在時不會拋出錯誤。"""
        # 建立 agent 目錄結構
        cafe_dir = tmp_path / ".cafe"
        dev_dir = cafe_dir / "agents" / "developer"
        dev_dir.mkdir(parents=True)

        # 建立 John.md
        john_file = dev_dir / "John.md"
        john_file.write_text("# John - Developer Agent")

        # 應該不會拋出錯誤
        ensure_agent_file_exists("John", "developer", cafe_dir)

    def test_agent_file_exists_richard(self, tmp_path: Path) -> None:
        """測試 Richard agent 檔案存在時不會拋出錯誤。"""
        # 建立 agent 目錄結構
        cafe_dir = tmp_path / ".cafe"
        reviewer_dir = cafe_dir / "agents" / "reviewer"
        reviewer_dir.mkdir(parents=True)

        # 建立 Richard.md
        richard_file = reviewer_dir / "Richard.md"
        richard_file.write_text("# Richard - Reviewer Agent")

        # 應該不會拋出錯誤
        ensure_agent_file_exists("Richard", "reviewer", cafe_dir)

    def test_agent_file_not_exists_raises_error(self, tmp_path: Path) -> None:
        """測試 agent 檔案不存在時會拋出 FileNotFoundError。"""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)

        # 不建立 agent 檔案，應該拋出錯誤
        with pytest.raises(FileNotFoundError) as exc_info:
            ensure_agent_file_exists("Roger", "pm", cafe_dir)

        # 驗證錯誤訊息包含提示
        error_msg = str(exc_info.value)
        assert "Agent file not found" in error_msg
        assert "cafe agent default" in error_msg

    def test_agent_file_not_exists_error_message_content(self, tmp_path: Path) -> None:
        """測試 agent 檔案不存在時錯誤訊息包含完整路徑。"""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)

        with pytest.raises(FileNotFoundError) as exc_info:
            ensure_agent_file_exists("David", "developer", cafe_dir)

        error_msg = str(exc_info.value)
        expected_path = str(cafe_dir / "agents" / "developer" / "David.md")
        assert expected_path in error_msg

    def test_unknown_agent_name_raises_value_error(self, tmp_path: Path) -> None:
        """測試未知的 agent 名稱會拋出 FileNotFoundError（因為檔案不存在）。"""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)

        # 現在不再檢查 agent 名稱，只檢查檔案是否存在
        # 所以應該拋出 FileNotFoundError 而不是 ValueError
        with pytest.raises(FileNotFoundError) as exc_info:
            ensure_agent_file_exists("UnknownAgent", "unknown_role", cafe_dir)

        assert "Agent file not found" in str(exc_info.value)

    def test_agent_directory_exists_but_file_missing(self, tmp_path: Path) -> None:
        """測試 agent 目錄存在但檔案不存在的情況。"""
        cafe_dir = tmp_path / ".cafe"
        pm_dir = cafe_dir / "agents" / "pm"
        pm_dir.mkdir(parents=True)

        # 目錄存在但沒有 Roger.md 檔案
        with pytest.raises(FileNotFoundError) as exc_info:
            ensure_agent_file_exists("Roger", "pm", cafe_dir)

        error_msg = str(exc_info.value)
        assert "Agent file not found" in error_msg
        assert "Roger.md" in error_msg

    def test_default_cafe_dir_parameter(self, tmp_path: Path, monkeypatch) -> None:
        """測試使用預設的 cafe_dir 參數（Path('.cafe')）。"""
        # 切換到 tmp_path 目錄
        monkeypatch.chdir(tmp_path)

        # 建立 .cafe/agents/pm/Roger.md
        cafe_dir = tmp_path / ".cafe"
        pm_dir = cafe_dir / "agents" / "pm"
        pm_dir.mkdir(parents=True)
        roger_file = pm_dir / "Roger.md"
        roger_file.write_text("# Roger")

        # 使用預設參數（應該使用當前目錄的 .cafe）
        ensure_agent_file_exists("Roger", "pm")

    def test_agents_directory_not_exists(self, tmp_path: Path) -> None:
        """測試 agents 目錄完全不存在的情況。"""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)

        # 不建立 agents 目錄
        with pytest.raises(FileNotFoundError):
            ensure_agent_file_exists("Roger", "pm", cafe_dir)

    def test_all_known_agents(self, tmp_path: Path) -> None:
        """測試所有已知的 agent 名稱都能正確映射到角色目錄。"""
        cafe_dir = tmp_path / ".cafe"

        # 建立所有 agent 檔案
        agents_config = {
            "Roger": "pm",
            "David": "developer",
            "John": "developer",
            "Richard": "reviewer",
        }

        for agent_name, role in agents_config.items():
            role_dir = cafe_dir / "agents" / role
            role_dir.mkdir(parents=True, exist_ok=True)
            agent_file = role_dir / f"{agent_name}.md"
            agent_file.write_text(f"# {agent_name}")

        # 驗證所有 agent 都能通過檢查
        for agent_name, role in agents_config.items():
            ensure_agent_file_exists(agent_name, role, cafe_dir)
