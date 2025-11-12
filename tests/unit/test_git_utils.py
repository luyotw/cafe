"""Tests for git_utils module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

from cafe.utils.git_utils import rewrite_commit_message


class TestRewriteCommitMessage:
    """測試 rewrite_commit_message 函數"""

    def test_rewrite_commit_message_success(self, tmp_path):
        """測試成功修改 commit message"""
        # Mock subprocess.run
        with patch('cafe.utils.git_utils.subprocess.run') as mock_run:
            # 設定 rebase 成功
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            
            success, msg = rewrite_commit_message("abc123", "fix: new message")
            
            assert success is True
            assert "Rewrote commit abc123" in msg
            
            # 確認有呼叫 git rebase
            assert mock_run.called
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == 'git'
            assert call_args[1] == 'rebase'
            assert '--exec' in call_args

    def test_rewrite_commit_message_with_custom_base_branch(self, tmp_path):
        """測試使用自訂 base branch"""
        with patch('cafe.utils.git_utils.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            
            success, msg = rewrite_commit_message("abc123", "fix: new", base_branch="develop")
            
            assert success is True
            
            # 確認使用了正確的 base branch
            call_args = mock_run.call_args[0][0]
            assert 'develop' in call_args

    def test_rewrite_commit_message_rebase_fails(self, tmp_path):
        """測試 rebase 失敗的情況"""
        with patch('cafe.utils.git_utils.subprocess.run') as mock_run:
            # 第一次呼叫 (rebase) 失敗
            # 第二次呼叫 (rebase --abort) 成功
            mock_run.side_effect = [
                MagicMock(returncode=1, stderr='Rebase conflict', stdout=''),
                MagicMock(returncode=0, stderr='', stdout=''),  # abort
            ]
            
            success, msg = rewrite_commit_message("abc123", "fix: new message")
            
            assert success is False
            assert "Rebase failed" in msg
            assert "Rebase conflict" in msg
            
            # 確認有呼叫 rebase --abort
            assert mock_run.call_count == 2
            abort_call = mock_run.call_args_list[1][0][0]
            assert 'rebase' in abort_call
            assert '--abort' in abort_call

    def test_rewrite_commit_message_creates_temp_file(self, tmp_path):
        """測試有建立暫存檔"""
        with patch('cafe.utils.git_utils.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            
            with patch('tempfile.NamedTemporaryFile') as mock_temp:
                mock_file = MagicMock()
                mock_file.name = '/tmp/test_message.txt'
                mock_file.__enter__ = MagicMock(return_value=mock_file)
                mock_file.__exit__ = MagicMock(return_value=None)
                mock_temp.return_value = mock_file
                
                with patch('os.unlink') as mock_unlink:
                    rewrite_commit_message("abc123", "fix: new message")
                    
                    # 確認有寫入 message
                    mock_file.write.assert_called_once_with("fix: new message")
                    
                    # 確認有清理暫存檔
                    mock_unlink.assert_called_once_with('/tmp/test_message.txt')

    def test_rewrite_commit_message_exception_handling(self):
        """測試異常處理"""
        with patch('cafe.utils.git_utils.subprocess.run') as mock_run:
            # 拋出異常
            mock_run.side_effect = Exception("Git command failed")
            
            success, msg = rewrite_commit_message("abc123", "fix: new message")
            
            assert success is False
            assert "Error:" in msg
            assert "Git command failed" in msg

    def test_rewrite_commit_message_exec_command_format(self):
        """測試 exec 指令格式正確"""
        with patch('cafe.utils.git_utils.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            
            with patch('tempfile.NamedTemporaryFile') as mock_temp:
                mock_file = MagicMock()
                mock_file.name = '/tmp/msg.txt'
                mock_file.__enter__ = MagicMock(return_value=mock_file)
                mock_file.__exit__ = MagicMock(return_value=None)
                mock_temp.return_value = mock_file
                
                with patch('os.unlink'):
                    rewrite_commit_message("abc123", "fix: message")
                    
                    # 檢查 exec 指令包含正確的 SHA 和 message file
                    call_args = mock_run.call_args[0][0]
                    exec_cmd = call_args[-1]  # 最後一個參數是 exec 指令
                    
                    assert "abc123" in exec_cmd
                    assert "/tmp/msg.txt" in exec_cmd
                    assert "git commit --amend" in exec_cmd
                    assert "--allow-empty" in exec_cmd
                    assert "--no-edit" in exec_cmd

    def test_rewrite_commit_message_short_sha_in_result(self):
        """測試結果訊息包含短 SHA (前7碼)"""
        with patch('cafe.utils.git_utils.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            
            success, msg = rewrite_commit_message("abcdef1234567890", "fix: message")
            
            assert success is True
            # 應該只顯示前7碼
            assert "abcdef1" in msg
            assert "abcdef1234567890" not in msg
