"""Configuration loading from ~/.config/slingshot/config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "slingshot" / "config.toml"


@dataclass
class RepoConfig:
    name: str  # "owner/name"
    path: Path  # local checkout


@dataclass
class AgentConfig:
    implement_command: str = "opencode run --auto {prompt_file}"
    review_command: str = "opencode run --auto {prompt_file}"


@dataclass
class Config:
    poll_interval_seconds: int = 60
    claim_timeout_minutes: int = 30
    agent_timeout_minutes: int = 30
    review_fail_threshold: int = 5
    agent_failure_threshold: int = 3
    max_concurrent: int = 2
    agent: AgentConfig = field(default_factory=AgentConfig)
    repos: list[RepoConfig] = field(default_factory=list)

    def repo_by_name(self, name: str) -> RepoConfig | None:
        for r in self.repos:
            if r.name == name:
                return r
        return None


def load_config(path: str | None = None) -> Config:
    """Load configuration from *path* (or the default location)."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return Config()

    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)

    cfg = Config()
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

    if "agent" in raw and isinstance(raw["agent"], dict):
        agent_raw = raw["agent"]
        if "implement_command" in agent_raw:
            cfg.agent.implement_command = str(agent_raw["implement_command"])
        if "review_command" in agent_raw:
            cfg.agent.review_command = str(agent_raw["review_command"])

    if "repo" in raw and isinstance(raw["repo"], list):
        for entry in raw["repo"]:
            if isinstance(entry, dict) and "name" in entry and "path" in entry:
                cfg.repos.append(
                    RepoConfig(
                        name=str(entry["name"]),
                        path=Path(entry["path"]),
                    )
                )
    return cfg
