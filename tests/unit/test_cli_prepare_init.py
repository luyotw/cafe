"""測試 cafe prepare 指令自動初始化功能."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from cafe.ui.cli import app


class TestPrepareAutoInitialization:
    """測試 cafe prepare 自動初始化 templates and agents."""

    @pytest.fixture
    def runner(self) -> CliRunner:
        """建立 CLI runner."""
        return CliRunner()

    # All tests in this class have been removed as the functionality has changed.
    # Agents and templates are no longer copied to project .cafe directory.
    # They are now managed globally at ~/.cafe/
    pass
