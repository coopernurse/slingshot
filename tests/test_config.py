from __future__ import annotations

from pathlib import Path

from slingshot.config import Config, load_config


class TestLoadConfig:
    def test_loads_valid_toml(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
poll_interval_seconds = 120
claim_timeout_minutes = 45

[agent]
implement_command = "echo {prompt_file}"
review_command = "echo {prompt_file}"

[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.poll_interval_seconds == 120
        assert cfg.claim_timeout_minutes == 45
        assert len(cfg.repos) == 1
        assert cfg.repos[0].name == "owner/repo"
        assert cfg.repos[0].path == Path("/tmp/test")

    def test_applies_defaults_when_fields_absent(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.poll_interval_seconds == 60
        assert cfg.claim_timeout_minutes == 30
        assert cfg.agent_timeout_minutes == 30
        assert cfg.review_fail_threshold == 5
        assert cfg.agent_failure_threshold == 3
        assert cfg.max_concurrent == 2

    def test_default_agent_commands(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.agent.implement_command == "opencode run --auto {prompt_file}"
        assert cfg.agent.review_command == "opencode run --auto {prompt_file}"

    def test_missing_file_returns_default_config(self, tmp_path: Path):
        cfg = load_config(str(tmp_path / "nonexistent.toml"))
        assert isinstance(cfg, Config)
        assert cfg.poll_interval_seconds == 60
        assert cfg.repos == []
        assert cfg.agent.implement_command == "opencode run --auto {prompt_file}"

    def test_empty_repos_when_none_configured(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("")
        cfg = load_config(str(config_file))
        assert cfg.repos == []

    def test_multiple_repos(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[[repo]]
name = "a/b"
path = "/tmp/a"

[[repo]]
name = "c/d"
path = "/tmp/c"
""")
        cfg = load_config(str(config_file))
        assert len(cfg.repos) == 2
        assert cfg.repos[0].name == "a/b"
        assert cfg.repos[1].name == "c/d"

    def test_repo_by_name(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[[repo]]
name = "a/b"
path = "/tmp/a"

[[repo]]
name = "c/d"
path = "/tmp/c"
""")
        cfg = load_config(str(config_file))
        found = cfg.repo_by_name("a/b")
        assert found is not None
        assert found.name == "a/b"
        assert cfg.repo_by_name("nonexistent") is None
