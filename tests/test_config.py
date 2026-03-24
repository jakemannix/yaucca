"""Tests for yaucca.config module."""

from unittest.mock import patch

from yaucca.config import CloudConfig, Settings, SummarizationConfig


class TestCloudConfig:
    def test_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = CloudConfig(_env_file=None)
            assert config.url == "http://YAUCCA_URL_env_var_is_unset:0"
            assert config.auth_token is None

    def test_env_override(self) -> None:
        with patch.dict("os.environ", {"YAUCCA_URL": "https://yaucca.modal.run", "YAUCCA_AUTH_TOKEN": "secret"}):
            config = CloudConfig()
            assert config.url == "https://yaucca.modal.run"
            assert config.auth_token == "secret"


class TestSummarizationConfig:
    def test_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = SummarizationConfig(_env_file=None)
            assert config.enabled is True
            assert config.model == ""
            assert config.min_exchanges == 8
            assert config.min_chars == 10000
            assert config.timeout == 90
            assert config.max_transcript_chars == 100_000
            assert config.claude_command == "claude"

    def test_env_overrides(self) -> None:
        env = {
            "YAUCCA_SUMMARY_ENABLED": "false",
            "YAUCCA_SUMMARY_MODEL": "haiku",
            "YAUCCA_SUMMARY_MIN_EXCHANGES": "5",
            "YAUCCA_SUMMARY_MIN_CHARS": "5000",
            "YAUCCA_SUMMARY_TIMEOUT": "120",
            "YAUCCA_SUMMARY_MAX_TRANSCRIPT_CHARS": "50000",
            "YAUCCA_SUMMARY_CLAUDE_COMMAND": "/usr/local/bin/claude",
        }
        with patch.dict("os.environ", env):
            config = SummarizationConfig()
            assert config.enabled is False
            assert config.model == "haiku"
            assert config.min_exchanges == 5
            assert config.min_chars == 5000
            assert config.timeout == 120
            assert config.max_transcript_chars == 50000
            assert config.claude_command == "/usr/local/bin/claude"


class TestSettings:
    def test_includes_cloud(self) -> None:
        with patch.dict("os.environ", {"YAUCCA_URL": "https://test.modal.run"}):
            settings = Settings()
            assert settings.cloud.url == "https://test.modal.run"

    def test_includes_summary(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings(_env_file=None)
            assert settings.summary.enabled is True
            assert settings.summary.min_exchanges == 8
