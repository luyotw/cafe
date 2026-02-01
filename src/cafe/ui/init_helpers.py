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


# Agent 角色列表
_AGENT_ROLES = ["pm", "developer", "reviewer"]

# Template 類型列表
_TEMPLATE_TYPES = ["plan", "spec"]


def _get_system_agents_dir() -> Path:
    """取得系統預設 agent 目錄路徑。

    Returns:
        系統 agent 目錄路徑 (src/cafe/data/agents/)
    """
    return Path(__file__).parent.parent / "data" / "agents"


def _get_system_templates_dir() -> Path:
    """取得系統預設 template 目錄路徑。

    Returns:
        系統 template 目錄路徑 (src/cafe/data/templates/)
    """
    return Path(__file__).parent.parent / "data" / "templates"


def copy_agents_to_local(cafe_dir: Path) -> List[Tuple[str, str, bool]]:
    """將 agent 檔案從全域自定義或系統預設目錄複製到本地 .cafe 目錄。

    全域自定義 (~/.cafe/agents/<role>/) 優先於系統預設 (src/cafe/data/agents/<role>/)。
    複製目標為 .cafe/agents/<role>/，會覆寫既有本地檔案。

    Args:
        cafe_dir: 本地 .cafe 目錄路徑

    Returns:
        複製結果列表，每個元素為 (相對路徑, 來源類型, 是否成功) 的元組
    """
    from cafe.utils.config import get_global_cafe_dir

    results: List[Tuple[str, str, bool]] = []
    system_agents_dir = _get_system_agents_dir()
    global_agents_dir = get_global_cafe_dir() / "agents"

    for role in _AGENT_ROLES:
        # 收集該角色的所有 agent，全域自定義覆蓋系統預設
        files: Dict[str, Tuple[Path, str]] = {}

        # 先加入系統預設
        system_role_dir = system_agents_dir / role
        if system_role_dir.exists():
            for agent_file in system_role_dir.glob("*.md"):
                files[agent_file.name] = (agent_file, "system default")

        # 再加入全域自定義（同名會覆蓋）
        global_role_dir = global_agents_dir / role
        if global_role_dir.exists():
            for agent_file in global_role_dir.glob("*.md"):
                files[agent_file.name] = (agent_file, "custom")

        # 複製到本地
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
    """將 template 檔案從全域自定義或系統預設目錄複製到本地 .cafe 目錄。

    全域自定義 (~/.cafe/templates/<phase>/) 優先於系統預設 (src/cafe/data/templates/<phase>/)。
    複製目標為 .cafe/templates/<phase>/，會覆寫既有本地檔案。

    Args:
        cafe_dir: 本地 .cafe 目錄路徑

    Returns:
        複製結果列表，每個元素為 (相對路徑, 來源類型, 是否成功) 的元組
    """
    from cafe.utils.config import get_global_cafe_dir

    results: List[Tuple[str, str, bool]] = []
    system_templates_dir = _get_system_templates_dir()
    global_templates_dir = get_global_cafe_dir() / "templates"

    for template_type in _TEMPLATE_TYPES:
        # 收集該類型的所有 template，全域自定義覆蓋系統預設
        files: Dict[str, Tuple[Path, str]] = {}

        # 先加入系統預設
        system_type_dir = system_templates_dir / template_type
        if system_type_dir.exists():
            for template_file in system_type_dir.glob("*.md"):
                files[template_file.name] = (template_file, "system default")

        # 再加入全域自定義（同名會覆蓋）
        global_type_dir = global_templates_dir / template_type
        if global_type_dir.exists():
            for template_file in global_type_dir.glob("*.md"):
                files[template_file.name] = (template_file, "custom")

        # 複製到本地
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
