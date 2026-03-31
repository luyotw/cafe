"""測試 cafe.agents.cli 模組的 __init__.py."""

import pytest


def test_cli_module_all_exports():
    """測試 __all__ 包含所有預期的類別."""
    from cafe.agents.cli import __all__

    expected_exports = [
        "AbstractCLI",
        "ClaudeCLI",
        "CodexCLI",
        "CopilotCLI",
        "CursorCLI",
        "GeminiCLI",
    ]

    for export in expected_exports:
        assert export in __all__, f"{export} should be in __all__"


def test_can_import_abstract_cli():
    """測試可以成功導入 AbstractCLI."""
    from cafe.agents.cli import AbstractCLI

    assert AbstractCLI is not None


def test_can_import_claude_cli():
    """測試可以成功導入 ClaudeCLI."""
    from cafe.agents.cli import ClaudeCLI

    assert ClaudeCLI is not None


def test_can_import_codex_cli():
    """測試可以成功導入 CodexCLI."""
    from cafe.agents.cli import CodexCLI

    assert CodexCLI is not None


def test_can_import_gemini_cli():
    """測試可以成功導入 GeminiCLI."""
    from cafe.agents.cli import GeminiCLI

    assert GeminiCLI is not None
