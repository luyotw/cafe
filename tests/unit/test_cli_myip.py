"""Tests for cafe myip command."""

from unittest.mock import patch
from urllib.error import URLError

from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


class TestMyipCommand:
    """測試 myip 指令"""

    def test_myip_success(self) -> None:
        """測試成功取得外網 IP 時輸出正確的 IPv4 位址"""
        with patch("cafe.ui.cli._fetch_external_ip", return_value="203.0.113.1"):
            result = runner.invoke(app, ["myip"])
        assert result.exit_code == 0
        assert "203.0.113.1" in result.output

    def test_myip_network_failure(self) -> None:
        """測試網路連線失敗時顯示錯誤訊息並回傳 exit code 1"""
        with patch("cafe.ui.cli._fetch_external_ip", side_effect=URLError("Network unreachable")):
            result = runner.invoke(app, ["myip"])
        assert result.exit_code == 1
        assert result.output != ""
