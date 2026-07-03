"""Template management utilities for CAFE."""

import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from cafe.utils.config import get_global_cafe_dir


class TemplateManager:
    """Manage plan and spec templates."""

    def __init__(self, template_type: str = "plan"):
        """Initialize template manager.

        Args:
            template_type: Type of template to manage ('plan' or 'spec')
        """
        self.template_type = template_type

    def _builtin_dir(self) -> Path:
        """Builtin template directory under the owning skill's assets/.

        Templates are owned by the skill that produces them (spec → spec
        skill, plan → plan skill). The owning skill's directory name matches
        the template type.
        """
        from cafe.skills.loader import canonical_skill_name

        return (
            Path(__file__).parent.parent
            / "data"
            / "skills"
            / canonical_skill_name(self.template_type)
            / "assets"
            / "templates"
        )

    def add_template(self, source_path: str, template_name: str) -> Path:
        """Add a new template from a file to global directory.

        Args:
            source_path: Path to the source template file
            template_name: Name for the template (without .md extension)

        Returns:
            Path to the created template file

        Raises:
            FileNotFoundError: If source file doesn't exist
            ValueError: If template name is invalid
        """
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        if not template_name or "/" in template_name or "\\" in template_name:
            raise ValueError(f"Invalid template name: {template_name}")

        # Ensure .md extension
        if not template_name.endswith(".md"):
            template_name = f"{template_name}.md"

        # Save to global directory
        global_template_dir = get_global_cafe_dir() / "templates" / self.template_type
        global_template_dir.mkdir(parents=True, exist_ok=True)
        dest = global_template_dir / template_name

        # Check if template already exists
        if dest.exists():
            raise FileExistsError(
                f"Template '{template_name}' already exists in global directory. "
                f"Use 'cafe template edit' to modify it."
            )

        shutil.copy2(source, dest)
        return dest

    def list_templates(self) -> List[Tuple[str, str]]:
        """List all available templates from system, global, and local directories.

        Returns:
            List of (template_name, source_type) tuples where source_type is
            "system", "custom" (global), or "local".
            Local/global files identical to system originals are marked "system".
        """
        templates = {}  # name -> (source_type, file_path)
        system_contents = {}  # name -> content (for comparison)

        # First, collect system templates (bundled under owning skill's assets/)
        package_data_dir = self._builtin_dir()
        if package_data_dir.exists():
            for file in package_data_dir.glob("*.md"):
                template_name = file.stem
                templates[template_name] = ("system", file)
                system_contents[template_name] = file.read_text()

        # Then, collect global templates (override system if name collision)
        global_template_dir = get_global_cafe_dir() / "templates" / self.template_type
        if global_template_dir.exists():
            for file in global_template_dir.glob("*.md"):
                template_name = file.stem
                # If content matches system, keep as "system"
                if template_name in system_contents and file.read_text() == system_contents[template_name]:
                    templates[template_name] = ("system", file)
                else:
                    templates[template_name] = ("custom", file)

        # Finally, collect local templates by searching upward from cwd
        current = Path.cwd().resolve()
        while current != current.parent:
            local_dir = current / ".cafe" / "templates" / self.template_type
            if local_dir.exists():
                for file in local_dir.glob("*.md"):
                    template_name = file.stem
                    if template_name in system_contents and file.read_text() == system_contents[template_name]:
                        templates[template_name] = ("system", file)
                    else:
                        templates[template_name] = ("custom", file)
                break
            current = current.parent

        # Return sorted list of (name, source_type) tuples
        return sorted([(name, source_type) for name, (source_type, _) in templates.items()])

    def list_custom_templates(self) -> List[str]:
        """List only custom (user-created) templates from global directory.

        Returns:
            List of template names (without .md extension)
        """
        templates = []

        # Collect global templates only
        global_template_dir = get_global_cafe_dir() / "templates" / self.template_type
        if global_template_dir.exists():
            for file in global_template_dir.glob("*.md"):
                templates.append(file.stem)

        return sorted(templates)

    def remove_template(self, template_name: str) -> None:
        """Remove a template from global directory.

        Args:
            template_name: Name of the template to remove (with or without .md)

        Raises:
            FileNotFoundError: If template doesn't exist
        """
        # Ensure .md extension
        if not template_name.endswith(".md"):
            template_name = f"{template_name}.md"

        # Remove from global directory
        global_template_dir = get_global_cafe_dir() / "templates" / self.template_type
        template_path = global_template_dir / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_name}")

        template_path.unlink()

    def get_template_path(self, template_name: str) -> Optional[Path]:
        """Get the path to a template file.

        Searches in order: local .cafe/templates/ first, then global
        ~/.cafe/templates/, then system directory (package data).

        Args:
            template_name: Name of the template (with or without .md)

        Returns:
            Path to the template file, or None if not found
        """
        # Ensure .md extension
        if not template_name.endswith(".md"):
            template_name = f"{template_name}.md"

        # Search upward from cwd for .cafe/templates/ (works in worktrees and subdirectories)
        current = Path.cwd().resolve()
        while current != current.parent:
            local_path = current / ".cafe" / "templates" / self.template_type / template_name
            if local_path.exists():
                return local_path
            current = current.parent

        # Fall back to global ~/.cafe/templates/
        global_template_dir = get_global_cafe_dir() / "templates" / self.template_type
        global_path = global_template_dir / template_name
        if global_path.exists():
            return global_path

        # Fall back to system default (bundled under owning skill's assets/)
        package_data_dir = self._builtin_dir()
        system_path = package_data_dir / template_name
        if system_path.exists():
            return system_path

        return None

    def template_exists(self, template_name: str) -> bool:
        """Check if a template exists.

        Args:
            template_name: Name of the template (with or without .md)

        Returns:
            True if template exists, False otherwise
        """
        return self.get_template_path(template_name) is not None
