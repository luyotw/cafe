"""Template management utilities for CAFE."""

import shutil
from pathlib import Path
from typing import List, Optional


class TemplateManager:
    """Manage plan templates."""

    def __init__(self, config_dir: str = ".cafe"):
        """Initialize template manager.

        Args:
            config_dir: CAFE configuration directory
        """
        self.template_dir = Path(config_dir) / "templates" / "plan"
        self.template_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_default_template()

    def add_template(self, source_path: str, template_name: str) -> None:
        """Add a new template from a file.

        Args:
            source_path: Path to the source template file
            template_name: Name for the template (without .md extension)

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

        dest = self.template_dir / template_name
        shutil.copy2(source, dest)

    def list_templates(self) -> List[str]:
        """List all available templates.

        Returns:
            List of template names (without .md extension)
        """
        if not self.template_dir.exists():
            return []

        templates = []
        for file in self.template_dir.glob("*.md"):
            templates.append(file.stem)  # Get filename without .md extension

        return sorted(templates)

    def remove_template(self, template_name: str) -> None:
        """Remove a template.

        Args:
            template_name: Name of the template to remove (with or without .md)

        Raises:
            FileNotFoundError: If template doesn't exist
        """
        # Ensure .md extension
        if not template_name.endswith(".md"):
            template_name = f"{template_name}.md"

        template_path = self.template_dir / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_name}")

        template_path.unlink()

    def get_template_path(self, template_name: str) -> Optional[Path]:
        """Get the path to a template file.

        Args:
            template_name: Name of the template (with or without .md)

        Returns:
            Path to the template file, or None if not found
        """
        # Ensure .md extension
        if not template_name.endswith(".md"):
            template_name = f"{template_name}.md"

        template_path = self.template_dir / template_name
        if template_path.exists():
            return template_path

        return None

    def template_exists(self, template_name: str) -> bool:
        """Check if a template exists.

        Args:
            template_name: Name of the template (with or without .md)

        Returns:
            True if template exists, False otherwise
        """
        return self.get_template_path(template_name) is not None

    def _ensure_default_template(self) -> None:
        """Ensure default template exists by copying from package if needed."""
        default_template_path = self.template_dir / "default.md"

        # If default template already exists, don't overwrite
        if default_template_path.exists():
            return

        # Find the package template directory
        # The template.py is in src/cafe/utils/, so package templates are in src/cafe/templates/
        package_template = Path(__file__).parent.parent / "templates" / "plan" / "default.md"

        if package_template.exists():
            shutil.copy2(package_template, default_template_path)
