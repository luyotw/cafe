"""測試 AbstractCLI 抽象基底類別."""

import pytest
from abc import ABC

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import AgentConfig, AgentCLI, TokenUsage


def test_abstract_cli_cannot_be_instantiated():
    """測試抽象類別無法直接實例化."""
    config = AgentConfig(name="test", cli=AgentCLI.CLAUDE)

    # 嘗試直接實例化抽象類別應該會引發 TypeError
    with pytest.raises(TypeError):
        AbstractCLI(config)


def test_abstract_cli_is_abc():
    """測試 AbstractCLI 是 ABC 的子類別."""
    assert issubclass(AbstractCLI, ABC)


def test_abstract_cli_has_required_methods():
    """測試 AbstractCLI 定義了所有必要的抽象方法."""
    required_methods = {
        'build_command',
        'parse_response',
        'translate_allowed_tools',
        'add_directories',
        'get_output_format',
        'extract_session_id',
    }

    # 取得所有抽象方法
    abstract_methods = set(AbstractCLI.__abstractmethods__)

    # 確認所有必要方法都被定義為抽象方法
    assert required_methods.issubset(abstract_methods)


class ConcreteCLI(AbstractCLI):
    """具體實作的 CLI 類別，用於測試."""

    def build_command(self, prompt, allowed_tools=None, allowed_directories=None):
        return ["test-cli", "-p", prompt]

    def parse_response(self, output_lines, streaming_log=None):
        return "test response", TokenUsage(), []

    def translate_allowed_tools(self, tools):
        return tools

    def add_directories(self, cmd, directories):
        return cmd

    def get_output_format(self):
        return []

    def extract_session_id(self, output_lines):
        return None


def test_concrete_cli_can_be_instantiated():
    """測試具體實作的 CLI 類別可以正常實例化."""
    config = AgentConfig(name="test", cli=AgentCLI.CLAUDE)
    cli = ConcreteCLI(config)

    assert cli.config == config
    assert cli.config.name == "test"
    assert cli.config.cli == AgentCLI.CLAUDE
