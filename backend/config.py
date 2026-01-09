"""
Configuration management using pydantic-settings.

Loads configuration from environment variables with validation.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All settings are validated at startup to fail fast if configuration is invalid.
    """

    # Notion Configuration
    notion_api_key: str = Field(
        ...,
        description="Notion API integration token"
    )
    notion_database_id: str = Field(
        ...,
        description="Notion database ID for memo storage"
    )

    # Supabase Configuration
    supabase_url: str = Field(
        ...,
        description="Supabase project URL"
    )
    supabase_key: str = Field(
        ...,
        description="Supabase API key (anon or service role)"
    )

    # AI Service Configuration
    openai_api_key: str = Field(
        ...,
        description="OpenAI API key for GPT models"
    )
    anthropic_api_key: str = Field(
        ...,
        description="Anthropic API key for Claude models"
    )

    # Application Configuration
    environment: str = Field(
        default="development",
        description="Application environment: development, staging, production"
    )
    host: str = Field(
        default="0.0.0.0",
        description="API server host"
    )
    port: int = Field(
        default=8000,
        description="API server port"
    )
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated list of allowed CORS origins"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment.lower() == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache to ensure settings are loaded only once.
    This is the recommended pattern from FastAPI documentation.

    Returns:
        Settings: Application settings instance
    """
    return Settings()


# Convenience export
settings = get_settings()
