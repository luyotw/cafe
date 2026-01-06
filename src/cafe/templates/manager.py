"""Template management utilities for CAFE."""

import shutil
from pathlib import Path
from typing import List, Optional


class TemplateManager:
    """Manage plan and spec templates."""

    def __init__(self, config_dir: str = ".cafe", template_type: str = "plan"):
        """Initialize template manager.

        Args:
            config_dir: CAFE configuration directory
            template_type: Type of template to manage ('plan' or 'spec')
        """
        self.template_type = template_type
        self.template_dir = Path(config_dir) / "templates" / template_type
        self.template_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_default_templates()

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

    def _ensure_default_templates(self) -> None:
        """Ensure default templates exist by copying from package data if needed."""
        # Dynamically discover all .md template files from package data
        package_data_dir = Path(__file__).parent.parent / "data" / "templates" / self.template_type
        repo_root = Path(__file__).parent.parent.parent.parent
        repo_template_dir = repo_root / "templates" / self.template_type

        # Collect template files from both locations
        template_sources = []
        if package_data_dir.exists():
            template_sources.extend(package_data_dir.glob("*.md"))
        if repo_template_dir.exists():
            template_sources.extend(repo_template_dir.glob("*.md"))

        # Copy each discovered template if it doesn't exist in target directory
        for source_template in template_sources:
            template_name = source_template.name
            target_path = self.template_dir / template_name

            # If template already exists, don't overwrite
            if target_path.exists():
                continue

            # Determine which source to use (package data first, then repo)
            package_data_template = package_data_dir / template_name if package_data_dir.exists() else None
            repo_template = repo_template_dir / template_name if repo_template_dir.exists() else None

            if package_data_template and package_data_template.exists():
                shutil.copy2(package_data_template, target_path)
            elif repo_template and repo_template.exists():
                shutil.copy2(repo_template, target_path)
