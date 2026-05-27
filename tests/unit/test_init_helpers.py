"""Tests for init_helpers module helper functions."""

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
    """Tests for check_available_clis function."""

    def test_check_available_clis_with_all_installed(self) -> None:
        """Test when all CLIs are installed."""
        with patch("shutil.which") as mock_which:
            # Mock all CLIs as installed
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
        """Test when only some CLIs are installed."""
        with patch("shutil.which") as mock_which:
            # Only claude and gemini are installed
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
        """Test when no CLIs are installed."""
        with patch("shutil.which", return_value=None):
            available = check_available_clis()

            assert len(available) == 0


class TestParseAgentFile:
    """Tests for parse_agent_file function."""

    def test_parse_agent_file_with_complete_frontmatter(self, tmp_path: Path) -> None:
        """Test parsing an agent file with complete front matter."""
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
        """Test that filename is used as name when front matter lacks name field."""
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
        """Test that default description is shown when front matter lacks description."""
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
        """Test parsing an agent file without front matter."""
        agent_file = tmp_path / "John.md"
        agent_file.write_text("Just content without frontmatter.\n")

        result = parse_agent_file(agent_file)

        assert result["name"] == "John"
        assert result["description"] == "(No description)"

    def test_parse_agent_file_empty_file(self, tmp_path: Path) -> None:
        """Test parsing an empty agent file."""
        agent_file = tmp_path / "Empty.md"
        agent_file.write_text("")

        result = parse_agent_file(agent_file)

        assert result["name"] == "Empty"
        assert result["description"] == "(No description)"


class TestListAvailableAgents:
    """Tests for list_available_agents function."""

    def test_list_available_agents_with_valid_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test listing valid agent files including system and global agents."""
        from cafe.utils.config import get_global_cafe_dir

        # Mock get_global_cafe_dir to return test directory
        global_cafe_dir = tmp_path / "global_cafe"
        monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_cafe_dir)

        # Set up global test directory structure
        global_pm_dir = global_cafe_dir / "agents" / "pm"
        global_pm_dir.mkdir(parents=True)

        # Set up test files
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
        """Test listing agents when global directory is empty but system defaults exist."""
        from cafe.utils.config import get_global_cafe_dir

        # Mock get_global_cafe_dir to return empty test directory
        global_cafe_dir = tmp_path / "global_cafe"
        monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_cafe_dir)

        # Set up empty global directory
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
        """Test that non-.md files are ignored when listing agents."""
        from cafe.utils.config import get_global_cafe_dir

        # Mock get_global_cafe_dir to return test directory
        global_cafe_dir = tmp_path / "global_cafe"
        monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_cafe_dir)

        # Set up global test directory structure
        global_pm_dir = global_cafe_dir / "agents" / "pm"
        global_pm_dir.mkdir(parents=True)

        # Create .md file
        alice = global_pm_dir / "Alice.md"
        alice.write_text(
            """---
name: Alice
description: 注重細節 PM
---
"""
        )

        # Create non-.md file (should be ignored)
        readme = global_pm_dir / "README.txt"
        readme.write_text("This is a readme")

        agents = list_available_agents("pm")

        # Should have system agents + Alice (but not README.txt)
        names = [agent[0] for agent in agents]
        assert "Alice" in names
        assert "Roger" in names
        # Verify only .md files are included
        for agent in agents:
            assert agent[2].suffix == ".md"


class TestCopyDataDirectory:
    """Tests for copy_data_directory function."""

    def test_copy_data_directory_success(self, tmp_path: Path) -> None:
        """Test successful directory copy."""
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
        """Test that FileNotFoundError is raised when source directory does not exist."""
        source = tmp_path / "nonexistent"
        dest = tmp_path / "dest"

        with pytest.raises(FileNotFoundError):
            copy_data_directory(str(source), str(dest))

    def test_copy_data_directory_permission_error(self, tmp_path: Path) -> None:
        """Test that PermissionError is raised on permission failure."""
        source = tmp_path / "source"
        source.mkdir()
        (source / "file.txt").write_text("content")

        dest = tmp_path / "dest"

        with patch("shutil.copytree", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                copy_data_directory(str(source), str(dest))

    def test_copy_data_directory_overwrites_existing(self, tmp_path: Path) -> None:
        """Test incremental copy into an existing directory."""
        source = tmp_path / "source"
        source.mkdir()
        (source / "file.txt").write_text("new content")

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "old_file.txt").write_text("old content")

        copy_data_directory(str(source), str(dest))

        assert dest.exists()
        assert (dest / "file.txt").read_text() == "new content"
        # Old files are preserved because copytree uses dirs_exist_ok
        assert (dest / "old_file.txt").exists()
        assert (dest / "old_file.txt").read_text() == "old content"


class TestCopyAgentsToLocal:
    """Tests for copy_agents_to_local function."""

    def _setup_system_agents(self, system_dir: Path) -> None:
        """Set up mock system agents directory structure."""
        for role in ["pm", "developer", "reviewer"]:
            role_dir = system_dir / role
            role_dir.mkdir(parents=True)
        (system_dir / "pm" / "Roger.md").write_text("# Roger (system)")
        (system_dir / "developer" / "David.md").write_text("# David (system)")
        (system_dir / "reviewer" / "Richard.md").write_text("# Richard (system)")

    def test_copy_agents_with_system_defaults_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that system default agents are copied when no global custom exists."""
        from cafe.ui.init_helpers import copy_agents_to_local

        # Set up system agents directory
        system_dir = tmp_path / "system_agents"
        self._setup_system_agents(system_dir)

        # Set up empty global directory
        global_dir = tmp_path / "global_cafe"
        (global_dir / "agents" / "pm").mkdir(parents=True)
        (global_dir / "agents" / "developer").mkdir(parents=True)
        (global_dir / "agents" / "reviewer").mkdir(parents=True)

        # Set up local .cafe directory
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_agents_dir", lambda: system_dir
        )

        results = copy_agents_to_local(cafe_dir)

        # Verify all system agents were copied
        assert (cafe_dir / "agents" / "pm" / "Roger.md").exists()
        assert (cafe_dir / "agents" / "developer" / "David.md").exists()
        assert (cafe_dir / "agents" / "reviewer" / "Richard.md").exists()

        # Verify results contain correct source types
        for filename, source_type, success in results:
            assert success is True
            assert source_type == "system default"

    def test_copy_agents_global_custom_overrides_system(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that global custom agents take priority over system defaults."""
        from cafe.ui.init_helpers import copy_agents_to_local

        # Set up system agents directory
        system_dir = tmp_path / "system_agents"
        self._setup_system_agents(system_dir)

        # Set up global custom agent with same name as system Roger
        global_dir = tmp_path / "global_cafe"
        (global_dir / "agents" / "pm").mkdir(parents=True)
        (global_dir / "agents" / "developer").mkdir(parents=True)
        (global_dir / "agents" / "reviewer").mkdir(parents=True)
        (global_dir / "agents" / "pm" / "Roger.md").write_text("# Roger (custom)")

        # Set up local .cafe directory
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_agents_dir", lambda: system_dir
        )

        results = copy_agents_to_local(cafe_dir)

        # Verify global custom version was used
        assert (cafe_dir / "agents" / "pm" / "Roger.md").read_text() == "# Roger (custom)"

        # Verify Roger's source type is custom
        roger_results = [r for r in results if "Roger.md" in r[0]]
        assert len(roger_results) == 1
        assert roger_results[0][1] == "custom"
        assert roger_results[0][2] is True

    def test_copy_agents_overwrites_existing_local_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that existing local agent files are overwritten during copy."""
        from cafe.ui.init_helpers import copy_agents_to_local

        # Set up system agents directory
        system_dir = tmp_path / "system_agents"
        self._setup_system_agents(system_dir)

        # Set up empty global directory
        global_dir = tmp_path / "global_cafe"
        (global_dir / "agents" / "pm").mkdir(parents=True)
        (global_dir / "agents" / "developer").mkdir(parents=True)
        (global_dir / "agents" / "reviewer").mkdir(parents=True)

        # Set up local .cafe directory with an old version of Roger
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

        # Verify old version was overwritten
        assert (cafe_dir / "agents" / "pm" / "Roger.md").read_text() == "# Roger (system)"

    def test_copy_agents_handles_copy_error_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that copy errors are handled gracefully without raising exceptions."""
        from cafe.ui.init_helpers import copy_agents_to_local

        # Set up system agents directory
        system_dir = tmp_path / "system_agents"
        self._setup_system_agents(system_dir)

        # Set up empty global directory
        global_dir = tmp_path / "global_cafe"
        (global_dir / "agents" / "pm").mkdir(parents=True)
        (global_dir / "agents" / "developer").mkdir(parents=True)
        (global_dir / "agents" / "reviewer").mkdir(parents=True)

        # Set up local .cafe directory
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._get_system_agents_dir", lambda: system_dir
        )

        # Mock shutil.copy2 to raise PermissionError
        with patch("shutil.copy2", side_effect=PermissionError("Permission denied")):
            results = copy_agents_to_local(cafe_dir)

        # Verify all copies failed but function did not raise
        for filename, source_type, success in results:
            assert success is False


class TestCopyTemplatesToLocal:
    """Tests for copy_templates_to_local function."""

    def _setup_system_templates(self, system_dir: Path) -> None:
        """Set up mock system templates directory structure."""
        for phase in ["plan", "spec"]:
            phase_dir = system_dir / phase
            phase_dir.mkdir(parents=True)
        (system_dir / "plan" / "default.md").write_text("# Default Plan (system)")
        (system_dir / "plan" / "simple.md").write_text("# Simple Plan (system)")
        (system_dir / "spec" / "default.md").write_text("# Default Spec (system)")

    @staticmethod
    def _fake_discover(system_dir: Path):
        """Return a fake discovery callable for monkeypatching."""

        def _impl():
            return [
                (phase_dir.name, phase_dir)
                for phase_dir in sorted(system_dir.iterdir())
                if phase_dir.is_dir()
            ]

        return _impl

    def test_copy_templates_with_system_defaults_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that system default templates are copied when no global custom exists."""
        from cafe.ui.init_helpers import copy_templates_to_local

        # Set up system templates directory
        system_dir = tmp_path / "system_templates"
        self._setup_system_templates(system_dir)

        # Set up empty global directory
        global_dir = tmp_path / "global_cafe"
        (global_dir / "templates" / "plan").mkdir(parents=True)
        (global_dir / "templates" / "spec").mkdir(parents=True)

        # Set up local .cafe directory
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._discover_builtin_template_types", self._fake_discover(system_dir)
        )

        results = copy_templates_to_local(cafe_dir)

        # Verify all system templates were copied
        assert (cafe_dir / "templates" / "plan" / "default.md").exists()
        assert (cafe_dir / "templates" / "plan" / "simple.md").exists()
        assert (cafe_dir / "templates" / "spec" / "default.md").exists()

        # Verify results contain correct source types
        for filename, source_type, success in results:
            assert success is True
            assert source_type == "system default"

    def test_copy_templates_global_custom_overrides_system(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that global custom templates take priority over system defaults."""
        from cafe.ui.init_helpers import copy_templates_to_local

        # Set up system templates directory
        system_dir = tmp_path / "system_templates"
        self._setup_system_templates(system_dir)

        # Set up global custom template with same name as system default.md
        global_dir = tmp_path / "global_cafe"
        (global_dir / "templates" / "plan").mkdir(parents=True)
        (global_dir / "templates" / "spec").mkdir(parents=True)
        (global_dir / "templates" / "plan" / "default.md").write_text(
            "# Default Plan (custom)"
        )

        # Set up local .cafe directory
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._discover_builtin_template_types", self._fake_discover(system_dir)
        )

        results = copy_templates_to_local(cafe_dir)

        # Verify global custom version was used
        assert (
            cafe_dir / "templates" / "plan" / "default.md"
        ).read_text() == "# Default Plan (custom)"

        # Verify default.md's source type is custom
        default_results = [r for r in results if "default.md" in r[0] and "plan" in r[0]]
        assert len(default_results) == 1
        assert default_results[0][1] == "custom"

    def test_copy_templates_overwrites_existing_local_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that existing local template files are overwritten during copy."""
        from cafe.ui.init_helpers import copy_templates_to_local

        # Set up system templates directory
        system_dir = tmp_path / "system_templates"
        self._setup_system_templates(system_dir)

        # Set up empty global directory
        global_dir = tmp_path / "global_cafe"
        (global_dir / "templates" / "plan").mkdir(parents=True)
        (global_dir / "templates" / "spec").mkdir(parents=True)

        # Set up local .cafe directory with an old version of default template
        cafe_dir = tmp_path / "project" / ".cafe"
        (cafe_dir / "templates" / "plan").mkdir(parents=True)
        (cafe_dir / "templates" / "plan" / "default.md").write_text(
            "# Default Plan (old local)"
        )

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._discover_builtin_template_types", self._fake_discover(system_dir)
        )

        copy_templates_to_local(cafe_dir)

        # Verify old version was overwritten
        assert (
            cafe_dir / "templates" / "plan" / "default.md"
        ).read_text() == "# Default Plan (system)"

    def test_copy_templates_handles_copy_error_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that copy errors are handled gracefully without raising exceptions."""
        from cafe.ui.init_helpers import copy_templates_to_local

        # Set up system templates directory
        system_dir = tmp_path / "system_templates"
        self._setup_system_templates(system_dir)

        # Set up empty global directory
        global_dir = tmp_path / "global_cafe"
        (global_dir / "templates" / "plan").mkdir(parents=True)
        (global_dir / "templates" / "spec").mkdir(parents=True)

        # Set up local .cafe directory
        cafe_dir = tmp_path / "project" / ".cafe"
        cafe_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "cafe.utils.config.get_global_cafe_dir", lambda: global_dir
        )
        monkeypatch.setattr(
            "cafe.ui.init_helpers._discover_builtin_template_types", self._fake_discover(system_dir)
        )

        # Mock shutil.copy2 to raise PermissionError
        with patch("shutil.copy2", side_effect=PermissionError("Permission denied")):
            results = copy_templates_to_local(cafe_dir)

        # Verify all copies failed but function did not raise
        for filename, source_type, success in results:
            assert success is False
