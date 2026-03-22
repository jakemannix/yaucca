"""Configuration management for yaucca.

Loads settings from environment variables with sensible defaults for local development.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LettaConfig(BaseSettings):
    """Letta server connection settings (deprecated — used only for migration)."""

    model_config = SettingsConfigDict(env_prefix="LETTA_", env_file=".env", extra="ignore")

    base_url: str = Field(default="http://localhost:8283", description="Letta server URL")
    api_key: str | None = Field(default=None, description="Letta API key for authentication")


class CloudConfig(BaseSettings):
    """yaucca cloud server connection settings."""

    model_config = SettingsConfigDict(env_prefix="YAUCCA_", env_file=".env", extra="ignore")

    url: str = Field(default="http://localhost:8283", alias="YAUCCA_URL", description="yaucca cloud server URL")
    auth_token: str | None = Field(default=None, alias="YAUCCA_AUTH_TOKEN", description="Bearer token for cloud API")


class AgentConfig(BaseSettings):
    """yaucca agent-specific settings."""

    model_config = SettingsConfigDict(env_prefix="YAUCCA_", env_file=".env", extra="ignore")

    agent_id: str | None = Field(default=None, description="Letta agent ID for yaucca (used for migration)")


class SummarizationConfig(BaseSettings):
    """Settings for LLM-based session summarization."""

    model_config = SettingsConfigDict(env_prefix="YAUCCA_SUMMARY_", env_file=".env", extra="ignore")

    enabled: bool = Field(default=True, description="Toggle summarization on/off")
    model: str = Field(default="", description="Model for claude -p --model (empty = default)")
    min_exchanges: int = Field(default=3, description="New exchanges threshold to trigger summarization")
    min_chars: int = Field(default=2000, description="New chars threshold to trigger summarization")
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

    letta: LettaConfig = Field(default_factory=LettaConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    summary: SummarizationConfig = Field(default_factory=SummarizationConfig)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
