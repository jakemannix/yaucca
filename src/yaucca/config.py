"""Configuration management for yaucca.

Loads settings from environment variables with sensible defaults for local development.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudConfig(BaseSettings):
    """yaucca cloud server connection settings."""

    model_config = SettingsConfigDict(env_prefix="YAUCCA_", env_file=".env", extra="ignore")

    url: str = Field(default="http://YAUCCA_URL_env_var_is_unset:0", alias="YAUCCA_URL", description="yaucca cloud server URL")
    auth_token: str | None = Field(default=None, alias="YAUCCA_AUTH_TOKEN", description="Bearer token for cloud API")
    required: bool = Field(default=False, alias="YAUCCA_REQUIRED", description="If true, hooks fail hard (exit 1) when cloud is unreachable")


class SummarizationConfig(BaseSettings):
    """Settings for LLM-based session summarization."""

    model_config = SettingsConfigDict(env_prefix="YAUCCA_SUMMARY_", env_file=".env", extra="ignore")

    enabled: bool = Field(default=True, description="Toggle summarization on/off")
    model: str = Field(default="", description="Model for claude -p --model (empty = default)")
    min_exchanges: int = Field(default=8, description="New exchanges threshold to trigger summarization")
    min_chars: int = Field(default=10000, description="New chars threshold to trigger summarization")
    timeout: int = Field(default=90, description="Seconds for claude -p subprocess")
    max_transcript_chars: int = Field(default=100_000, description="Truncation limit for long transcripts")
    claude_command: str = Field(default="claude", description="Path to claude CLI")


class Settings(BaseSettings):
    """Aggregated settings for yaucca."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cloud: CloudConfig = Field(default_factory=CloudConfig)
    summary: SummarizationConfig = Field(default_factory=SummarizationConfig)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
