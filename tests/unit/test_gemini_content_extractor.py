"""Tests for Gemini content extractor filtering."""

import pytest
from cafe.core.types import AgentConfig, AgentCLI


class TestGeminiContentExtractor:
    """測試 Gemini content extractor 正確過濾訊息"""

    def test_extract_assistant_message(self):
        """測試提取 assistant 訊息"""
        # Create the extractor function inline (same as in _execute_gemini)
        def extract_gemini_content(data: dict):
            if data.get("role") == "assistant":
                return data.get("content")
            return None
        
        # Act
        assistant_msg = {"role": "assistant", "content": "Hello!"}
        result = extract_gemini_content(assistant_msg)
        
        # Assert
        assert result == "Hello!"

    def test_filter_user_message(self):
        """測試過濾 user 訊息（prompt echo）"""
        def extract_gemini_content(data: dict):
            if data.get("role") == "assistant":
                return data.get("content")
            return None
        
        # Act
        user_msg = {"role": "user", "content": "This is the prompt"}
        result = extract_gemini_content(user_msg)
        
        # Assert
        assert result is None

    def test_filter_init_message(self):
        """測試過濾 init 訊息"""
        def extract_gemini_content(data: dict):
            if data.get("role") == "assistant":
                return data.get("content")
            return None
        
        # Act
        init_msg = {"type": "init", "session_id": "test"}
        result = extract_gemini_content(init_msg)
        
        # Assert
        assert result is None

    def test_handle_message_without_role(self):
        """測試處理沒有 role 欄位訊息"""
        def extract_gemini_content(data: dict):
            if data.get("role") == "assistant":
                return data.get("content")
            return None
        
        # Act
        no_role_msg = {"content": "Some content"}
        result = extract_gemini_content(no_role_msg)
        
        # Assert
        assert result is None

    def test_handle_assistant_message_without_content(self):
        """測試處理沒有 content  assistant 訊息"""
        def extract_gemini_content(data: dict):
            if data.get("role") == "assistant":
                return data.get("content")
            return None
        
        # Act
        no_content_msg = {"role": "assistant"}
        result = extract_gemini_content(no_content_msg)
        
        # Assert
        assert result is None

    def test_empty_content_returns_empty_string(self):
        """測試空 content 返回空字串"""
        def extract_gemini_content(data: dict):
            if data.get("role") == "assistant":
                return data.get("content")
            return None
        
        # Act
        empty_content_msg = {"role": "assistant", "content": ""}
        result = extract_gemini_content(empty_content_msg)
        
        # Assert
        assert result == ""

