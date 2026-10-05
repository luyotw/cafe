"""Mode-neutral tool declarations shared by execution and read-only inspection."""

from __future__ import annotations

from typing import Iterable


def normalize_allowed_tools(raw_tools: Iterable[str]) -> list[str]:
    names = {
        "Read": "read",
        "Edit": "edit",
        "Write": "write",
        "Grep": "grep",
        "Glob": "glob",
        "LS": "ls",
        "Ls": "ls",
        "Bash": "bash",
        "WebFetch": "web_fetch",
        "WebSearch": "web_search",
    }
    normalized = []
    for tool in raw_tools:
        if not tool:
            continue
        name, separator, remainder = tool.partition("(")
        name = names.get(name, name[:1].lower() + name[1:])
        normalized.append(name + separator + remainder)
    return normalized


def runtime_granted_tools(grants: Iterable[str]) -> list[str]:
    """Expand the existing public grant vocabulary without granting authority."""
    grants = set(grants)
    tools = []
    if "web_research" in grants:
        tools.extend(["web_fetch", "web_search"])
    if "git_inspection" in grants:
        tools.extend(["bash(git log)", "bash(git diff)", "bash(git show)", "bash(git status)"])
    return tools
