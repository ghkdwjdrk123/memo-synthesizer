"""
Unit tests for config validation (notion_database_id vs notion_parent_page_id).

Tests:
1. Validator accepts database_id only (parent_page_id=None)
2. Validator accepts parent_page_id only (database_id=None)
3. Validator accepts both present (should work)
4. Validator rejects both missing (raises ValueError)
"""

import pytest
from pydantic import ValidationError
from config import Settings


class TestConfigValidator:
    """Tests for notion config validator"""

    def test_accepts_database_id_only(self, monkeypatch):
        """Validator accepts database_id only (parent_page_id=None)"""
        # Clear all environment variables first to avoid .env file pollution
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)

        # Set environment variables
        monkeypatch.setenv("NOTION_API_KEY", "test-api-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "test-database-id")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-supabase-key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        # Should not raise
        settings = Settings(_env_file=None)  # Don't load .env file
        assert settings.notion_database_id == "test-database-id"
        assert settings.notion_parent_page_id is None

    def test_accepts_parent_page_id_only(self, monkeypatch):
        """Validator accepts parent_page_id only (database_id=None)"""
        # Clear first
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)

        # Set environment variables
        monkeypatch.setenv("NOTION_API_KEY", "test-api-key")
        monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "test-parent-page-id")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-supabase-key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        # Should not raise
        settings = Settings(_env_file=None)
        assert settings.notion_database_id is None
        assert settings.notion_parent_page_id == "test-parent-page-id"

    def test_accepts_both_present(self, monkeypatch):
        """Validator accepts both database_id and parent_page_id present"""
        # Clear first
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)

        # Set environment variables
        monkeypatch.setenv("NOTION_API_KEY", "test-api-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "test-database-id")
        monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "test-parent-page-id")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-supabase-key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        # Should not raise
        settings = Settings(_env_file=None)
        assert settings.notion_database_id == "test-database-id"
        assert settings.notion_parent_page_id == "test-parent-page-id"

    def test_rejects_both_missing(self, monkeypatch):
        """Validator rejects both database_id and parent_page_id missing"""
        # Clear first
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)

        # Set environment variables (without NOTION_DATABASE_ID or NOTION_PARENT_PAGE_ID)
        monkeypatch.setenv("NOTION_API_KEY", "test-api-key")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-supabase-key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        # Should raise ValidationError
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)

        # Check error message
        error_message = str(exc_info.value)
        assert "Either NOTION_DATABASE_ID or NOTION_PARENT_PAGE_ID must be provided" in error_message

    def test_rejects_both_empty_strings(self, monkeypatch):
        """Validator rejects both database_id and parent_page_id as empty strings"""
        # Clear first
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)

        # Set environment variables with empty strings
        monkeypatch.setenv("NOTION_API_KEY", "test-api-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "")
        monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-supabase-key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        # Empty strings are treated as None by pydantic, so should raise ValidationError
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)

        error_message = str(exc_info.value)
        assert "Either NOTION_DATABASE_ID or NOTION_PARENT_PAGE_ID must be provided" in error_message

    def test_database_id_takes_precedence_in_usage(self, monkeypatch):
        """When both are present, code can choose which to use (validator allows both)"""
        # Clear first
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)

        # Set environment variables with both
        monkeypatch.setenv("NOTION_API_KEY", "test-api-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "test-database-id")
        monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "test-parent-page-id")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-supabase-key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        settings = Settings(_env_file=None)

        # Both should be available
        assert settings.notion_database_id is not None
        assert settings.notion_parent_page_id is not None

        # Application logic (in pipeline.py) will choose which to use
        # This test just verifies validator allows both
