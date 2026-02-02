"""Helper functions for cafe init command."""

import shutil
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from cafe.core.types import AgentCLI


def check_available_clis() -> List[str]:
    """Check available CLI tools on the system.

    Returns:
        List of available CLI tools
    """
    available_clis = []

    # Check all supported CLI tools
    for cli in AgentCLI:
        if shutil.which(cli.value):
            available_clis.append(cli.value)

    return available_clis


def parse_agent_file(file_path: Path) -> Dict[str, str]:
    """Parse agent file's front matter.

    Args:
        file_path: agent file path

    Returns:
        Dictionary containing name and description
    """
    content = file_path.read_text()

    # Default values
    name = file_path.stem  # Use filename (without extension)
    description = "(No description)"

    # Try to parse YAML front matter
    # Front matter format: ---\nkey: value\n---
    if content.startswith("---"):
        # Find the second --- position
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter_content = parts[1]
            try:
                frontmatter = yaml.safe_load(frontmatter_content)
                if isinstance(frontmatter, dict):
                    name = frontmatter.get("name", name)
                    description = frontmatter.get("description", description)
            except yaml.YAMLError:
                # If YAML parsing fails, keep default values
                pass

    return {"name": name, "description": description}


def list_available_agents(role: str) -> List[tuple[str, str, Path, str]]:
    """List all available agents for specified role from system and global directories.

    Args:
        role: Role name (pm, developer, reviewer)

    Returns:
        List of (name, description, file_path, source_type) tuples
        where source_type is "system default" or "custom"
        Global agents take precedence over system agents with the same name.
    """
    from cafe.utils.config import get_global_cafe_dir
    
    agents = {}  # Use dict to handle name collisions
    
    # First, collect system agents (from package data)
    package_data_dir = Path(__file__).parent.parent / "data" / "agents" / role
    if package_data_dir.exists():
        for agent_file in package_data_dir.glob("*.md"):
            parsed = parse_agent_file(agent_file)
            name = parsed["name"]
            agents[name] = (name, parsed["description"], agent_file, "system default")
    
    # Then, collect global agents (override system if name collision)
    global_agents_dir = get_global_cafe_dir() / "agents" / role
    if global_agents_dir.exists():
        for agent_file in global_agents_dir.glob("*.md"):
            parsed = parse_agent_file(agent_file)
            name = parsed["name"]
            agents[name] = (name, parsed["description"], agent_file, "custom")
    
    # Return sorted list
    return sorted(agents.values(), key=lambda x: x[0])


def copy_data_directory(source: str, destination: str) -> None:
    """Helper function to copy directory.

    Args:
        source: Source directory path
        destination: Destination directory path

    Raises:
        FileNotFoundError: Source directory does not exist
        PermissionError: Insufficient permissions
    """
    source_path = Path(source)
    dest_path = Path(destination)

    if not source_path.exists():
        raise FileNotFoundError(f"Source directory not found: {source}")

    # Copy directory (incremental copy)
    shutil.copytree(source_path, dest_path, dirs_exist_ok=True)


def _get_system_agents_dir() -> Path:
    """Get the system default agents directory path.

    Returns:
        Path to system agents directory (src/cafe/data/agents/)
    """
    return Path(__file__).parent.parent / "data" / "agents"


def _get_system_templates_dir() -> Path:
    """Get the system default templates directory path.

    Returns:
        Path to system templates directory (src/cafe/data/templates/)
    """
    return Path(__file__).parent.parent / "data" / "templates"


def copy_agents_to_local(cafe_dir: Path) -> List[Tuple[str, str, bool]]:
    """Copy agent files from global custom or system default directories to local .cafe.

    Global custom (~/.cafe/agents/<role>/) takes precedence over system default
    (src/cafe/data/agents/<role>/). Copies to .cafe/agents/<role>/, overwriting
    any existing local files.

    Args:
        cafe_dir: Path to the local .cafe directory

    Returns:
        List of (relative_path, source_type, success) tuples for each file
    """
    from cafe.utils.config import get_global_cafe_dir

    results: List[Tuple[str, str, bool]] = []
    system_agents_dir = _get_system_agents_dir()
    global_agents_dir = get_global_cafe_dir() / "agents"

    # Derive roles from the system agents directory structure
    roles = [d.name for d in system_agents_dir.iterdir() if d.is_dir()] if system_agents_dir.exists() else []

    for role in roles:
        # Collect all agents for this role; global custom overrides system default
        files: Dict[str, Tuple[Path, str]] = {}

        # Add system defaults first
        system_role_dir = system_agents_dir / role
        if system_role_dir.exists():
            for agent_file in system_role_dir.glob("*.md"):
                files[agent_file.name] = (agent_file, "system default")

        # Overlay global custom files (same-name overrides)
        global_role_dir = global_agents_dir / role
        if global_role_dir.exists():
            for agent_file in global_role_dir.glob("*.md"):
                files[agent_file.name] = (agent_file, "custom")

        # Copy to local .cafe directory
        local_role_dir = cafe_dir / "agents" / role
        local_role_dir.mkdir(parents=True, exist_ok=True)

        for filename, (source_path, source_type) in files.items():
            relative_path = f"agents/{role}/{filename}"
            try:
                shutil.copy2(source_path, local_role_dir / filename)
                results.append((relative_path, source_type, True))
            except (PermissionError, OSError):
                results.append((relative_path, source_type, False))

    return results


def copy_templates_to_local(cafe_dir: Path) -> List[Tuple[str, str, bool]]:
    """Copy template files from global custom or system default directories to local .cafe.

    Global custom (~/.cafe/templates/<phase>/) takes precedence over system default
    (src/cafe/data/templates/<phase>/). Copies to .cafe/templates/<phase>/, overwriting
    any existing local files.

    Args:
        cafe_dir: Path to the local .cafe directory

    Returns:
        List of (relative_path, source_type, success) tuples for each file
    """
    from cafe.utils.config import get_global_cafe_dir

    results: List[Tuple[str, str, bool]] = []
    system_templates_dir = _get_system_templates_dir()
    global_templates_dir = get_global_cafe_dir() / "templates"

    # Derive template types from the system templates directory structure
    template_types = [d.name for d in system_templates_dir.iterdir() if d.is_dir()] if system_templates_dir.exists() else []

    for template_type in template_types:
        # Collect all templates for this type; global custom overrides system default
        files: Dict[str, Tuple[Path, str]] = {}

        # Add system defaults first
        system_type_dir = system_templates_dir / template_type
        if system_type_dir.exists():
            for template_file in system_type_dir.glob("*.md"):
                files[template_file.name] = (template_file, "system default")

        # Overlay global custom files (same-name overrides)
        global_type_dir = global_templates_dir / template_type
        if global_type_dir.exists():
            for template_file in global_type_dir.glob("*.md"):
                files[template_file.name] = (template_file, "custom")

        # Copy to local .cafe directory
        local_type_dir = cafe_dir / "templates" / template_type
        local_type_dir.mkdir(parents=True, exist_ok=True)

        for filename, (source_path, source_type) in files.items():
            relative_path = f"templates/{template_type}/{filename}"
            try:
                shutil.copy2(source_path, local_type_dir / filename)
                results.append((relative_path, source_type, True))
            except (PermissionError, OSError):
                results.append((relative_path, source_type, False))

    return results
