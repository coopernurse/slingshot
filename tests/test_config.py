from __future__ import annotations

from pathlib import Path

from slingshot.config import (
    Config,
    load_config,
    resolve_prompt_path,
    validate_prompt_paths,
)


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

    def test_comment_debounce_seconds_default(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.comment_debounce_seconds == 180

    def test_comment_debounce_seconds_custom(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
comment_debounce_seconds = 300
[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.comment_debounce_seconds == 300

    def test_agent_prompt_fields_default_none(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.agent.implement_prompt is None
        assert cfg.agent.review_prompt is None

    def test_agent_prompt_fields_parsed(self, tmp_path: Path):
        impl = tmp_path / "impl.md"
        impl.write_text("custom")
        rev = tmp_path / "rev.md"
        rev.write_text("custom")

        config_file = tmp_path / "config.toml"
        config_file.write_text(f"""\
[agent]
implement_prompt = "{impl}"
review_prompt = "{rev}"

[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.agent.implement_prompt == str(impl)
        assert cfg.agent.review_prompt == str(rev)

    def test_repo_prompt_fields_default_none(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.repos[0].implement_prompt is None
        assert cfg.repos[0].review_prompt is None

    def test_repo_prompt_fields_parsed(self, tmp_path: Path):
        impl = tmp_path / "impl.md"
        impl.write_text("custom")
        rev = tmp_path / "rev.md"
        rev.write_text("custom")

        config_file = tmp_path / "config.toml"
        config_file.write_text(f"""\
[[repo]]
name = "owner/repo"
path = "/tmp/test"
implement_prompt = "{impl}"
review_prompt = "{rev}"
""")
        cfg = load_config(str(config_file))
        assert cfg.repos[0].implement_prompt == str(impl)
        assert cfg.repos[0].review_prompt == str(rev)

    def test_config_dir_set_to_config_parent(self, tmp_path: Path):
        config_file = tmp_path / "sub" / "config.toml"
        config_file.parent.mkdir()
        config_file.write_text("""
[[repo]]
name = "owner/repo"
path = "/tmp/test"
""")
        cfg = load_config(str(config_file))
        assert cfg.config_dir == config_file.parent


class TestResolvePromptPath:
    def test_absolute_path_passes_through(self):
        result = resolve_prompt_path("/foo/bar.md", Path("/config/dir"))
        assert result == Path("/foo/bar.md")

    def test_relative_path_resolved_against_config_dir(self):
        result = resolve_prompt_path("prompts/impl.md", Path("/config/dir"))
        assert result == Path("/config/dir/prompts/impl.md").resolve()


class TestValidatePromptPaths:
    def test_all_valid_paths_returns_empty(self, tmp_path: Path):
        impl = tmp_path / "impl.md"
        impl.write_text("prompt")
        rev = tmp_path / "rev.md"
        rev.write_text("prompt")

        cfg = Config()
        cfg.config_dir = tmp_path
        cfg.agent.implement_prompt = str(impl)
        cfg.agent.review_prompt = str(rev)

        errors = validate_prompt_paths(cfg)
        assert errors == []

    def test_missing_file_reported(self, tmp_path: Path):
        cfg = Config()
        cfg.config_dir = tmp_path
        cfg.agent.implement_prompt = "nonexistent.md"

        errors = validate_prompt_paths(cfg)
        assert len(errors) == 1
        assert "nonexistent.md" in errors[0]
        assert "implement_prompt" in errors[0]

    def test_unreadable_file_reported(self, tmp_path: Path):
        bad = tmp_path / "bad.md"
        bad.write_text("prompt")
        bad.chmod(0o000)

        cfg = Config()
        cfg.config_dir = tmp_path
        cfg.agent.implement_prompt = str(bad)

        errors = validate_prompt_paths(cfg)
        bad.chmod(0o644)  # cleanup
        assert len(errors) == 1
        assert "not readable" in errors[0]

    def test_collects_all_failures(self, tmp_path: Path):
        cfg = Config()
        cfg.config_dir = tmp_path
        cfg.agent.implement_prompt = "a.md"
        cfg.agent.review_prompt = "b.md"
        repo = MockRepoConfig(
            name="o/r", path=Path("/tmp/o"),
            implement_prompt="c.md", review_prompt="d.md",
        )
        cfg.repos = [repo]  # type: ignore[assignment]

        errors = validate_prompt_paths(cfg)
        assert len(errors) == 4

    def test_none_prompts_skipped(self, tmp_path: Path):
        cfg = Config()
        cfg.config_dir = tmp_path
        errors = validate_prompt_paths(cfg)
        assert errors == []

    def test_repo_prompt_error(self, tmp_path: Path):
        cfg = Config()
        cfg.config_dir = tmp_path
        repo = MockRepoConfig(
            name="owner/name", path=Path("/tmp/o"),
            implement_prompt="missing.md", review_prompt=None,
        )
        cfg.repos = [repo]  # type: ignore[assignment]

        errors = validate_prompt_paths(cfg)
        assert len(errors) == 1
        assert "owner/name" in errors[0]

    def test_config_dir_default(self):
        cfg = Config()
        assert cfg.config_dir == Path.cwd()


class MockRepoConfig:
    def __init__(self, name, path, implement_prompt, review_prompt):
        self.name = name
        self.path = path
        self.implement_prompt = implement_prompt
        self.review_prompt = review_prompt
