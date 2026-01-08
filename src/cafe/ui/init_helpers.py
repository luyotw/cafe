"""Helper functions for cafe init command."""

import shutil
from pathlib import Path
from typing import Dict, List

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
