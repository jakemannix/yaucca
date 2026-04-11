"""Configuration management for yaucca.

Loads settings from environment variables, then falls back to .env files.
Search order for .env:
  1. YAUCCA_ENV_FILE environment variable (set by hooks when pip-installed)
  2. ~/.config/yaucca/.env (standard config location)
  3. ./.env (current directory, for development)
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str | None:
    """Find the .env file to use."""
    # Explicit override (set by hooks in ~/.claude/settings.json)
    explicit = os.environ.get("YAUCCA_ENV_FILE")
    if explicit and Path(explicit).exists():
        return explicit
    # Standard config location
    config_env = Path.home() / ".config" / "yaucca" / ".env"
    if config_env.exists():
        return str(config_env)
    # CWD (development)
    if Path(".env").exists():
        return ".env"
    return None


_env_file = _find_env_file()


class CloudConfig(BaseSettings):
    """yaucca cloud server connection settings."""

    model_config = SettingsConfigDict(env_prefix="YAUCCA_", env_file=_env_file, extra="ignore")

    url: str = Field(default="http://YAUCCA_URL_env_var_is_unset:0", alias="YAUCCA_URL", description="yaucca cloud server URL")
    auth_token: str | None = Field(default=None, alias="YAUCCA_AUTH_TOKEN", description="Bearer token for cloud API")
    required: bool = Field(default=False, alias="YAUCCA_REQUIRED", description="If true, hooks fail hard (exit 1) when cloud is unreachable")
    default_exclude_tags: str = Field(default="", alias="YAUCCA_DEFAULT_EXCLUDE_TAGS", description="Comma-separated tags to exclude from queries by default (e.g. '@done')")


class SummarizationConfig(BaseSettings):
    """Settings for LLM-based session summarization."""

    model_config = SettingsConfigDict(env_prefix="YAUCCA_SUMMARY_", env_file=_env_file, extra="ignore")

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
        env_file=_env_file,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cloud: CloudConfig = Field(default_factory=CloudConfig)
    summary: SummarizationConfig = Field(default_factory=SummarizationConfig)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
