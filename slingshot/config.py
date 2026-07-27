"""Configuration loading from ~/.config/slingshot/config.toml."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "slingshot" / "config.toml"


@dataclass
class RepoConfig:
    name: str  # "owner/name"
    path: Path  # local checkout
    implement_prompt: str | None = None
    review_prompt: str | None = None


@dataclass
class AgentConfig:
    implement_command: str = "opencode run --auto {prompt_file}"
    review_command: str = "opencode run --auto {prompt_file}"
    implement_prompt: str | None = None
    review_prompt: str | None = None


@dataclass
class Config:
    poll_interval_seconds: int = 60
    claim_timeout_minutes: int = 30
    agent_timeout_minutes: int = 30
    review_fail_threshold: int = 5
    agent_failure_threshold: int = 3
    max_concurrent: int = 2
    comment_debounce_seconds: int = 180
    agent: AgentConfig = field(default_factory=AgentConfig)
    repos: list[RepoConfig] = field(default_factory=list)
    config_dir: Path = field(default_factory=Path.cwd)

    def repo_by_name(self, name: str) -> RepoConfig | None:
        for r in self.repos:
            if r.name == name:
                return r
        return None


def resolve_prompt_path(path: str, config_dir: Path) -> Path:
    """Resolve *path* relative to *config_dir*; absolute paths pass through."""
    p = Path(path)
    if p.is_absolute():
        return p
    return (config_dir / p).resolve()


def validate_prompt_paths(config: Config) -> list[str]:
    """Validate every custom prompt path in the config.  Returns a list of
    error messages (empty if all valid)."""
    errors: list[str] = []

    if config.agent.implement_prompt:
        resolved = resolve_prompt_path(
            config.agent.implement_prompt, config.config_dir,
        )
        if not resolved.is_file():
            errors.append(
                f"implement_prompt [agent]: file not found: {resolved}",
            )
        elif not _is_readable(resolved):
            errors.append(
                f"implement_prompt [agent]: file not readable: {resolved}",
            )

    if config.agent.review_prompt:
        resolved = resolve_prompt_path(
            config.agent.review_prompt, config.config_dir,
        )
        if not resolved.is_file():
            errors.append(
                f"review_prompt [agent]: file not found: {resolved}",
            )
        elif not _is_readable(resolved):
            errors.append(
                f"review_prompt [agent]: file not readable: {resolved}",
            )

    for repo in config.repos:
        if repo.implement_prompt:
            resolved = resolve_prompt_path(
                repo.implement_prompt, config.config_dir,
            )
            if not resolved.is_file():
                errors.append(
                    f"implement_prompt [{repo.name}]: file not found: "
                    f"{resolved}",
                )
            elif not _is_readable(resolved):
                errors.append(
                    f"implement_prompt [{repo.name}]: file not readable: "
                    f"{resolved}",
                )

        if repo.review_prompt:
            resolved = resolve_prompt_path(
                repo.review_prompt, config.config_dir,
            )
            if not resolved.is_file():
                errors.append(
                    f"review_prompt [{repo.name}]: file not found: "
                    f"{resolved}",
                )
            elif not _is_readable(resolved):
                errors.append(
                    f"review_prompt [{repo.name}]: file not readable: "
                    f"{resolved}",
                )

    return errors


def _is_readable(path: Path) -> bool:
    try:
        with open(path, "rb"):
            pass
        return True
    except OSError:
        return False


def load_config(path: str | None = None) -> Config:
    """Load configuration from *path* (or the default location)."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return Config()

    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)

    cfg = Config()
    cfg.config_dir = config_path.parent

    if "poll_interval_seconds" in raw:
        cfg.poll_interval_seconds = int(raw["poll_interval_seconds"])
    if "claim_timeout_minutes" in raw:
        cfg.claim_timeout_minutes = int(raw["claim_timeout_minutes"])
    if "agent_timeout_minutes" in raw:
        cfg.agent_timeout_minutes = int(raw["agent_timeout_minutes"])
    if "review_fail_threshold" in raw:
        cfg.review_fail_threshold = int(raw["review_fail_threshold"])
    if "agent_failure_threshold" in raw:
        cfg.agent_failure_threshold = int(raw["agent_failure_threshold"])
    if "max_concurrent" in raw:
        cfg.max_concurrent = int(raw["max_concurrent"])
    if "comment_debounce_seconds" in raw:
        cfg.comment_debounce_seconds = int(raw["comment_debounce_seconds"])

    if "agent" in raw and isinstance(raw["agent"], dict):
        agent_raw = raw["agent"]
        if "implement_command" in agent_raw:
            cfg.agent.implement_command = str(agent_raw["implement_command"])
        if "review_command" in agent_raw:
            cfg.agent.review_command = str(agent_raw["review_command"])
        if "implement_prompt" in agent_raw:
            cfg.agent.implement_prompt = str(agent_raw["implement_prompt"])
        if "review_prompt" in agent_raw:
            cfg.agent.review_prompt = str(agent_raw["review_prompt"])

    if "repo" in raw and isinstance(raw["repo"], list):
        for entry in raw["repo"]:
            if isinstance(entry, dict) and "name" in entry and "path" in entry:
                repo = RepoConfig(
                    name=str(entry["name"]),
                    path=Path(entry["path"]),
                )
                if "implement_prompt" in entry:
                    repo.implement_prompt = str(entry["implement_prompt"])
                if "review_prompt" in entry:
                    repo.review_prompt = str(entry["review_prompt"])
                cfg.repos.append(repo)

    errors = validate_prompt_paths(cfg)
    if errors:
        for err in errors:
            print(f"slingshot: {err}", file=sys.stderr)
        sys.exit(1)

    return cfg
