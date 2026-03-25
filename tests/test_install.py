"""Tests for yaucca install/uninstall lifecycle.

Verifies that install() and uninstall() correctly manage:
- ~/.claude/settings.json (hooks)
- ~/.claude/rules/yaucca-memory.md (memory rules template)
- .env file resolution by app name
- MCP server commands (mocked)
- User block seeding (mocked)

All file operations use a temporary directory — no real config is touched.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """Create a fake home directory with .claude structure."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    # Minimal existing settings
    (claude_dir / "settings.json").write_text(json.dumps({
        "some_existing_setting": True,
    }))
    # Rules dir
    (claude_dir / "rules").mkdir()
    # Config dir
    (tmp_path / ".config" / "yaucca").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def prod_env(fake_home: Path) -> Path:
    """Create a production .env file."""
    env_file = fake_home / ".config" / "yaucca" / ".env"
    env_file.write_text(
        "YAUCCA_URL=https://testuser--yaucca-serve.modal.run\n"
        "YAUCCA_AUTH_TOKEN=prod-token-abc123\n"
    )
    return env_file


@pytest.fixture
def test_env(fake_home: Path) -> Path:
    """Create a test app .env file."""
    env_dir = fake_home / ".config" / "yaucca" / "yaucca-test"
    env_dir.mkdir(parents=True)
    env_file = env_dir / ".env"
    env_file.write_text(
        "YAUCCA_URL=https://testuser--yaucca-test-serve.modal.run\n"
        "YAUCCA_AUTH_TOKEN=test-token-xyz789\n"
    )
    return env_file


def _patch_paths(fake_home: Path):
    """Return a dict of patches to redirect all file paths to fake_home."""
    return {
        "SETTINGS_PATH": fake_home / ".claude" / "settings.json",
        "ENV_FILE": fake_home / ".config" / "yaucca" / ".env",
        "CONFIG_DIR": fake_home / ".config" / "yaucca",
    }


def _patch_home(fake_home: Path):
    """Patch Path.home() to return fake_home."""
    return patch.object(Path, "home", return_value=fake_home)


class TestEnvFileResolution:
    """Test that _load_env reads from the correct file based on app name."""

    def test_default_app_reads_prod_env(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        with _patch_home(fake_home), \
             patch.object(inst, "ENV_FILE", prod_env), \
             patch.object(inst, "CONFIG_DIR", fake_home / ".config" / "yaucca"):
            inst._active_env_file = prod_env
            env = inst._load_env()
            assert env["YAUCCA_URL"] == "https://testuser--yaucca-serve.modal.run"
            assert env["YAUCCA_AUTH_TOKEN"] == "prod-token-abc123"

    def test_custom_app_reads_app_env(self, fake_home: Path, test_env: Path) -> None:
        import yaucca.install as inst

        with _patch_home(fake_home):
            inst._active_env_file = test_env
            env = inst._load_env()
            assert env["YAUCCA_URL"] == "https://testuser--yaucca-test-serve.modal.run"
            assert env["YAUCCA_AUTH_TOKEN"] == "test-token-xyz789"

    def test_first_occurrence_wins(self, fake_home: Path) -> None:
        """Duplicate keys: first value is kept, not last."""
        import yaucca.install as inst

        env_file = fake_home / "dupes.env"
        env_file.write_text(
            "YAUCCA_URL=https://first.modal.run\n"
            "YAUCCA_URL=https://second.modal.run\n"
        )
        inst._active_env_file = env_file
        env = inst._load_env()
        assert env["YAUCCA_URL"] == "https://first.modal.run"

    def test_comments_and_blanks_ignored(self, fake_home: Path) -> None:
        import yaucca.install as inst

        env_file = fake_home / "commented.env"
        env_file.write_text(
            "# This is a comment\n"
            "\n"
            "YAUCCA_URL=https://real.modal.run\n"
            "# YAUCCA_URL=https://commented-out.modal.run\n"
        )
        inst._active_env_file = env_file
        env = inst._load_env()
        assert env["YAUCCA_URL"] == "https://real.modal.run"
        assert len(env) == 1


class TestHooksInstallUninstall:
    """Test that hooks are correctly added to and removed from settings.json."""

    def test_install_adds_hooks(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        settings_path = fake_home / ".claude" / "settings.json"
        patches = _patch_paths(fake_home)

        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        assert "SessionStart" in settings["hooks"]
        assert "Stop" in settings["hooks"]
        assert "SessionEnd" in settings["hooks"]

        # Verify hooks contain yaucca marker
        for event in ("SessionStart", "Stop", "SessionEnd"):
            hook_groups = settings["hooks"][event]
            commands = [h["command"] for g in hook_groups for h in g["hooks"]]
            assert any("yaucca.hooks" in cmd for cmd in commands), f"No yaucca hook in {event}"

        # Verify MCP tools are auto-approved
        assert "permissions" in settings
        assert "mcp__yaucca" in settings["permissions"]["allow"]

    def test_install_preserves_existing_settings(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        settings_path = fake_home / ".claude" / "settings.json"
        patches = _patch_paths(fake_home)

        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        settings = json.loads(settings_path.read_text())
        assert settings["some_existing_setting"] is True

    def test_uninstall_removes_hooks(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        settings_path = fake_home / ".claude" / "settings.json"
        patches = _patch_paths(fake_home)

        # Install first
        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        # Verify hooks exist
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings

        # Uninstall
        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            inst.uninstall(app_name="yaucca")

        # Hooks should be gone, other settings preserved
        settings = json.loads(settings_path.read_text())
        assert "hooks" not in settings
        assert settings["some_existing_setting"] is True

    def test_install_creates_backup(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        patches = _patch_paths(fake_home)
        backup_path = fake_home / ".claude" / "settings.json.bak"

        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        assert backup_path.exists()
        backup = json.loads(backup_path.read_text())
        assert backup["some_existing_setting"] is True
        assert "hooks" not in backup  # backup is from before install


class TestMemoryRulesTemplate:
    """Test that the memory rules template is installed and removed correctly."""

    def test_install_creates_rules(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        rules_path = fake_home / ".claude" / "rules" / "yaucca-memory.md"
        patches = _patch_paths(fake_home)

        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        assert rules_path.exists()
        content = rules_path.read_text()
        assert "yaucca Memory System" in content
        assert "Core Memory" in content

    def test_uninstall_removes_rules(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        rules_path = fake_home / ".claude" / "rules" / "yaucca-memory.md"
        patches = _patch_paths(fake_home)

        # Install
        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        assert rules_path.exists()

        # Uninstall
        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            inst.uninstall(app_name="yaucca")

        assert not rules_path.exists()

    def test_install_skips_existing_rules(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        rules_path = fake_home / ".claude" / "rules" / "yaucca-memory.md"
        rules_path.write_text("My custom rules — should not be overwritten")
        patches = _patch_paths(fake_home)

        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        assert rules_path.read_text() == "My custom rules — should not be overwritten"


class TestHookPythonPath:
    """Test that hooks use the correct Python interpreter path."""

    def test_hooks_use_absolute_python(self, fake_home: Path, prod_env: Path) -> None:
        import sys

        import yaucca.install as inst

        settings_path = fake_home / ".claude" / "settings.json"
        patches = _patch_paths(fake_home)

        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        settings = json.loads(settings_path.read_text())
        cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert sys.executable in cmd
        assert "python -m yaucca.hooks" not in cmd or sys.executable in cmd


class TestAppNameIsolation:
    """Test that --app-name keeps environments fully isolated."""

    def test_different_app_names_use_different_env_files(
        self, fake_home: Path, prod_env: Path, test_env: Path
    ) -> None:
        import yaucca.install as inst

        settings_path = fake_home / ".claude" / "settings.json"
        patches = _patch_paths(fake_home)

        # Install with default app name
        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        settings = json.loads(settings_path.read_text())
        prod_cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert ".config/yaucca/.env" in prod_cmd
        assert "yaucca-test" not in prod_cmd

        # Now install with test app name (replaces hooks)
        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca-test")

        settings = json.loads(settings_path.read_text())
        test_cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert "yaucca-test" in test_cmd

    def test_get_env_reads_active_file_not_os_environ(
        self, fake_home: Path, test_env: Path
    ) -> None:
        """_get_env must read from the .env file, not os.environ."""
        import os

        import yaucca.install as inst

        inst._active_env_file = test_env

        # Even if os.environ has a different value, _get_env should
        # return the value from the .env file
        original = os.environ.get("YAUCCA_URL")
        try:
            os.environ["YAUCCA_URL"] = "https://SHOULD-NOT-USE-THIS"
            result = inst._get_env("YAUCCA_URL")
            assert result == "https://testuser--yaucca-test-serve.modal.run"
        finally:
            if original is None:
                os.environ.pop("YAUCCA_URL", None)
            else:
                os.environ["YAUCCA_URL"] = original

    def test_mcp_server_uses_app_name(self, fake_home: Path, test_env: Path) -> None:
        """MCP add command should use the app name, not hardcoded 'yaucca'."""
        import yaucca.install as inst

        inst._active_env_file = test_env
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=1, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_run):
            inst._install_mcp_server(app_name="yaucca-test")

        # Find the 'mcp add' call
        add_calls = [c for c in calls if "add" in c]
        assert len(add_calls) == 1
        add_cmd = add_calls[0]
        assert "yaucca-test" in add_cmd
        assert "yaucca-test-serve" in " ".join(add_cmd)


class TestFullRoundTrip:
    """Test install → verify → uninstall → verify round trip."""

    def test_full_cycle(self, fake_home: Path, prod_env: Path) -> None:
        import yaucca.install as inst

        settings_path = fake_home / ".claude" / "settings.json"
        rules_path = fake_home / ".claude" / "rules" / "yaucca-memory.md"
        backup_path = fake_home / ".claude" / "settings.json.bak"
        patches = _patch_paths(fake_home)

        original_settings = json.loads(settings_path.read_text())

        # === Install ===
        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch.object(inst, "_active_env_file", prod_env), \
             patch.object(inst, "_check_prerequisites"), \
             patch.object(inst, "_is_cloud_env", return_value=False), \
             patch.object(inst, "_seed_user_block_interactive", return_value=None), \
             patch.object(inst, "_check_user_block", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            inst.install(app_name="yaucca")

        # Verify install state
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        assert settings["some_existing_setting"] is True
        assert rules_path.exists()
        assert backup_path.exists()

        # === Uninstall ===
        with _patch_home(fake_home), \
             patch.multiple(inst, **patches), \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            inst.uninstall(app_name="yaucca")

        # Verify clean state
        settings = json.loads(settings_path.read_text())
        assert "hooks" not in settings
        assert settings["some_existing_setting"] is True
        assert not rules_path.exists()

        # Original non-hook settings preserved
        for key in original_settings:
            assert settings[key] == original_settings[key]
