"""Helper functions for cafe init command."""

import re
import shutil
from pathlib import Path
from typing import Dict, List

from cafe.core.types import AgentCLI


def check_available_clis() -> List[str]:
    """檢查系統上可用的 CLI 工具。

    Returns:
        可用的 CLI 工具列表
    """
    available_clis = []

    # 檢查所有支援的 CLI 工具
    for cli in AgentCLI:
        if shutil.which(cli.value):
            available_clis.append(cli.value)

    return available_clis


def parse_agent_file(file_path: Path) -> Dict[str, str]:
    """解析 agent 檔案的 front matter。

    Args:
        file_path: agent 檔案路徑

    Returns:
        包含 name 和 description 的字典
    """
    content = file_path.read_text()

    # 預設值
    name = file_path.stem  # 使用檔名（不含副檔名）
    description = "(無描述)"

    # 嘗試解析 YAML front matter
    # Front matter 格式: ---\nkey: value\n---
    frontmatter_pattern = r"^---\s*\n(.*?)\n---"
    match = re.search(frontmatter_pattern, content, re.DOTALL | re.MULTILINE)

    if match:
        frontmatter_content = match.group(1)

        # 提取 name
        name_match = re.search(r"^name:\s*(.+)$", frontmatter_content, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip()

        # 提取 description
        desc_match = re.search(
            r"^description:\s*(.+)$", frontmatter_content, re.MULTILINE
        )
        if desc_match:
            description = desc_match.group(1).strip()

    return {"name": name, "description": description}


def list_available_agents(role: str) -> List[tuple[str, str, Path]]:
    """列出指定角色的所有可用 agent。

    Args:
        role: 角色名稱（pm, developer, reviewer）

    Returns:
        (name, description, file_path) 的列表
    """
    agents_dir = Path(".cafe") / "agents" / role

    if not agents_dir.exists():
        return []

    agents = []
    for agent_file in agents_dir.glob("*.md"):
        parsed = parse_agent_file(agent_file)
        agents.append((parsed["name"], parsed["description"], agent_file))

    return agents


def copy_data_directory(source: str, destination: str) -> None:
    """複製目錄的輔助函式。

    Args:
        source: 來源目錄路徑
        destination: 目標目錄路徑

    Raises:
        FileNotFoundError: 來源目錄不存在
        PermissionError: 權限不足
    """
    source_path = Path(source)
    dest_path = Path(destination)

    if not source_path.exists():
        raise FileNotFoundError(f"Source directory not found: {source}")

    # 如果目標已存在，先刪除
    if dest_path.exists():
        shutil.rmtree(dest_path)

    # 複製目錄
    shutil.copytree(source_path, dest_path)
