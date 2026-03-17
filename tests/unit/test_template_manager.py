"""Tests for template manager."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.templates.manager import TemplateManager


class TestTemplateManager:
    """Tests for TemplateManager class."""

    def test_init_does_not_create_project_directory(self, tmp_path: Path) -> None:
        """Test that initialization does not create a project-level template directory."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        # Should NOT create project-level directory
        project_template_dir = config_dir / "templates" / "plan"
        assert not project_template_dir.exists()

    def test_add_template_copies_file(self, tmp_path: Path) -> None:
        """Test that adding a template copies the file to the global directory."""
        # Mock global directory
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                config_dir = tmp_path / ".cafe"
                manager = TemplateManager()

                # Create source file
                source_file = tmp_path / "my-template.md"
                source_file.write_text("## Template Content\n\nTest template")

                # Add template
                manager.add_template(str(source_file), "my-template")

                # Verify file was copied to global directory
                global_template_path = fake_home / ".cafe" / "templates" / "plan" / "my-template.md"
                assert global_template_path.exists()
                assert global_template_path.read_text() == "## Template Content\n\nTest template"

    def test_add_template_auto_adds_md_extension(self, tmp_path: Path) -> None:
        """Test that adding a template automatically appends the .md extension."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                config_dir = tmp_path / ".cafe"
                manager = TemplateManager()

                source_file = tmp_path / "template.md"
                source_file.write_text("Content")

                # Do not provide .md extension
                manager.add_template(str(source_file), "mytemplate")

                # Verify .md extension was added
                global_template_path = fake_home / ".cafe" / "templates" / "plan" / "mytemplate.md"
                assert global_template_path.exists()

    def test_add_template_source_not_found_raises_error(self, tmp_path: Path) -> None:
        """Test that a missing source file raises an error."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        with pytest.raises(FileNotFoundError, match="Source file not found"):
            manager.add_template("nonexistent.md", "test")

    def test_add_template_invalid_name_raises_error(self, tmp_path: Path) -> None:
        """Test that an invalid template name raises an error."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        source_file = tmp_path / "template.md"
        source_file.write_text("Content")

        # Test name containing path separators
        with pytest.raises(ValueError, match="Invalid template name"):
            manager.add_template(str(source_file), "path/to/template")

        with pytest.raises(ValueError, match="Invalid template name"):
            manager.add_template(str(source_file), "path\\to\\template")

        # Test empty name
        with pytest.raises(ValueError, match="Invalid template name"):
            manager.add_template(str(source_file), "")

    def test_add_template_raises_file_exists_error_if_duplicate(self, tmp_path: Path) -> None:
        """Test that adding a duplicate template raises FileExistsError."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                manager = TemplateManager()

                # Create source file
                source_file = tmp_path / "my-template.md"
                source_file.write_text("## Template Content\n\nTest template")

                # First add succeeds
                manager.add_template(str(source_file), "my-template")

                # Second add with the same name should raise an error
                with pytest.raises(FileExistsError, match="Template 'my-template.md' already exists"):
                    manager.add_template(str(source_file), "my-template")

    def test_list_templates_returns_all_templates(self, tmp_path: Path) -> None:
        """Test listing all templates (system + global)."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)
        
        # Create templates in global directory
        (global_template_dir / "template1.md").write_text("Content 1")
        (global_template_dir / "template2.md").write_text("Content 2")
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            config_dir = tmp_path / ".cafe"
            manager = TemplateManager()
            
            templates = manager.list_templates()
            template_names = [t[0] for t in templates]
            
            # Should include system templates (bug, default, simple) and global templates (template1, template2)
            assert "bug" in template_names
            assert "default" in template_names
            assert "simple" in template_names
            assert "template1" in template_names
            assert "template2" in template_names

    def test_list_templates_empty_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that an empty directory returns only built-in templates (system directory)."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            config_dir = tmp_path / ".cafe"
            manager = TemplateManager()

            templates = manager.list_templates()
            template_names = [t[0] for t in templates]
            # Should only contain system templates
            assert sorted(template_names) == ["bug", "default", "simple"]

    def test_list_templates_ignores_non_md_files(self, tmp_path: Path) -> None:
        """Test that only .md files are listed."""
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
            # Includes built-in templates (bug, default, simple) + template1 (ignores .txt and .json)
            assert "bug" in template_names
            assert "default" in template_names
            assert "simple" in template_names
            assert "template1" in template_names
            assert "template2" not in template_names  # .txt is ignored
            assert "template3" not in template_names  # .json is ignored

    def test_remove_template_deletes_file(self, tmp_path: Path) -> None:
        """Test deleting a template from the global directory."""
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

                # Remove template
                manager.remove_template("test")

                # Verify it was deleted from the global directory
                assert not template_path.exists()

    def test_remove_template_with_md_extension(self, tmp_path: Path) -> None:
        """Test that removing a template accepts a .md extension."""
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

                # Remove using .md extension
                manager.remove_template("test.md")

                assert not template_path.exists()

    def test_remove_template_not_found_raises_error(self, tmp_path: Path) -> None:
        """Test that removing a nonexistent template raises an error."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        with pytest.raises(FileNotFoundError, match="Template not found"):
            manager.remove_template("nonexistent")

    def test_get_template_path_returns_path(self, tmp_path: Path) -> None:
        """Test getting a template path (from global or system directory)."""
        # Test getting from system directory
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        # Get system template path
        path = manager.get_template_path("default")
        assert path is not None
        assert path.exists()
        assert "default.md" in str(path)

    def test_get_template_path_with_md_extension(self, tmp_path: Path) -> None:
        """Test that get_template_path accepts a .md extension."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        path = manager.get_template_path("default.md")
        assert path is not None
        assert path.exists()
        assert "default.md" in str(path)

    def test_get_template_path_not_found_returns_none(self, tmp_path: Path) -> None:
        """Test that get_template_path returns None when the template does not exist."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        path = manager.get_template_path("nonexistent")
        assert path is None

    def test_template_exists_returns_true_when_exists(self, tmp_path: Path) -> None:
        """Test that template_exists returns True for an existing system template."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        # Test system template
        assert manager.template_exists("default") is True
        assert manager.template_exists("default.md") is True

    def test_template_exists_returns_false_when_not_exists(self, tmp_path: Path) -> None:
        """Test that template_exists returns False when the template does not exist."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager()

        assert manager.template_exists("nonexistent") is False

    def test_init_spec_template_does_not_create_project_directory(self, tmp_path: Path) -> None:
        """Test that initializing a spec template does not create a project-level spec directory."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(template_type="spec")

        # Should NOT create project-level directory
        project_spec_dir = config_dir / "templates" / "spec"
        assert not project_spec_dir.exists()

    def test_spec_template_manager_list_templates(self, tmp_path: Path) -> None:
        """Test listing templates with the spec template manager."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(template_type="spec")

        # Create templates
        template_dir = config_dir / "templates" / "spec"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("Default spec")
        (template_dir / "simple.md").write_text("Simple spec")

        templates = manager.list_templates()
        template_names = [t[0] for t in templates]
        assert "default" in template_names
        assert "simple" in template_names

    def test_spec_template_add_and_get(self, tmp_path: Path) -> None:
        """Test adding and getting a spec template (global directory)."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                config_dir = tmp_path / ".cafe"
                manager = TemplateManager(template_type="spec")

                # Create source file
                source_file = tmp_path / "custom-spec.md"
                source_file.write_text("## Custom Spec Template")

                # Add template
                manager.add_template(str(source_file), "custom")

                # Verify file was copied to global spec directory
                global_template_path = fake_home / ".cafe" / "templates" / "spec" / "custom.md"
                assert global_template_path.exists()
                assert global_template_path.read_text() == "## Custom Spec Template"

                # Get path
                path = manager.get_template_path("custom")
                assert path == global_template_path

    def test_init_copies_all_builtin_plan_templates(self, tmp_path: Path) -> None:
        """Test that all built-in plan templates can be listed (from system directory)."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(template_type="plan")

        # Verify all system templates can be listed
        templates = manager.list_templates()
        template_names = [t[0] for t in templates]
        assert "default" in template_names
        assert "simple" in template_names
        assert "bug" in template_names

        # Verify system template paths can be retrieved
        assert manager.get_template_path("default") is not None
        assert manager.get_template_path("simple") is not None
        assert manager.get_template_path("bug") is not None

    def test_init_copies_all_builtin_spec_templates(self, tmp_path: Path) -> None:
        """Test that all built-in spec templates can be listed (from system directory)."""
        config_dir = tmp_path / ".cafe"
        manager = TemplateManager(template_type="spec")

        # Verify all system templates can be listed
        templates = manager.list_templates()
        template_names = [t[0] for t in templates]
        assert "default" in template_names
        assert "simple" in template_names
        assert "detailed" in template_names

        # Verify system template paths can be retrieved
        assert manager.get_template_path("default") is not None
        assert manager.get_template_path("simple") is not None
        assert manager.get_template_path("detailed") is not None

    def test_init_does_not_overwrite_existing_templates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that global custom templates take priority over system templates when no local .cafe exists."""
        # chdir to a directory without a local .cafe to ensure local paths do not interfere
        monkeypatch.chdir(tmp_path)

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)

        # Create a template with custom content in the global directory
        custom_content = "## My Custom Default Template"
        (global_template_dir / "default.md").write_text(custom_content)

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            # Initialize manager
            manager = TemplateManager(template_type="plan")

            # Verify the global custom version is retrieved
            path = manager.get_template_path("default")
            assert path == global_template_dir / "default.md"
            assert path.read_text() == custom_content


class TestTemplateManagerLocalPath:
    """Tests for TemplateManager.get_template_path local path priority."""

    def test_local_cafe_template_has_highest_priority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that local .cafe/templates/ takes priority over global and system,
        even when CWD is a subdirectory of the repo root."""
        # Set up repo root with local template
        repo_root = tmp_path / "repo"
        local_template_dir = repo_root / ".cafe" / "templates" / "plan"
        local_template_dir.mkdir(parents=True)
        (local_template_dir / "default.md").write_text("# Local default")

        # Set up global template
        fake_home = tmp_path / "home"
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)
        (global_template_dir / "default.md").write_text("# Global default")

        # Run from a subdirectory to verify repo-root-relative lookup
        subdir = repo_root / "src" / "nested"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        with patch("cafe.utils.git_utils.get_repo_root", return_value=repo_root):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                manager = TemplateManager(template_type="plan")
                path = manager.get_template_path("default")

        # Local .cafe/ path should be an absolute path under repo root
        assert path is not None
        assert path == repo_root / ".cafe" / "templates" / "plan" / "default.md"

    def test_falls_back_to_global_when_no_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that global ~/.cafe/templates/ is used when no local template exists."""
        # Repo root with no local templates
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        # Set up global template
        fake_home = tmp_path / "home"
        global_template_dir = fake_home / ".cafe" / "templates" / "plan"
        global_template_dir.mkdir(parents=True)
        (global_template_dir / "custom.md").write_text("# Global custom")

        # Run from a subdirectory
        subdir = repo_root / "subdir"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        with patch("cafe.utils.git_utils.get_repo_root", return_value=repo_root):
            with patch("cafe.templates.manager.get_global_cafe_dir", return_value=fake_home / ".cafe"):
                manager = TemplateManager(template_type="plan")
                path = manager.get_template_path("custom")

        assert path is not None
        assert path == global_template_dir / "custom.md"