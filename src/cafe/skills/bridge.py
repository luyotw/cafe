"""Compatibility helpers for consuming skill content from legacy phase code."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from cafe.skills.exceptions import SkillDiscoveryError
from cafe.skills.loader import SkillLoader


def load_skill_body(name: str, *, context: Optional[Dict[str, str]] = None) -> str:
    """Load one skill body with placeholder replacement."""
    loader = SkillLoader()
    loader.discover()
    return loader.activate(name, context=context)


def load_skill_reference(name: str, ref: str) -> str:
    """Load one skill reference file."""
    loader = SkillLoader()
    loader.discover()
    return loader.get_reference(name, ref)


def try_load_skill_body(name: str, *, context: Optional[Dict[str, str]] = None) -> str:
    """Best-effort skill activation for compatibility paths."""
    try:
        return load_skill_body(name, context=context).strip()
    except (SkillDiscoveryError, FileNotFoundError, OSError):
        return ""


def try_load_skill_reference(name: str, ref: str) -> str:
    """Best-effort skill reference load for compatibility paths."""
    try:
        return load_skill_reference(name, ref)
    except (SkillDiscoveryError, FileNotFoundError, OSError):
        return ""
