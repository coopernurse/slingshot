"""Structured, line-based logging for the daemon."""

from __future__ import annotations

from datetime import UTC, datetime


def log(msg: str) -> None:
    """Write a timestamped log line to stdout."""
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    line = f"{ts} {msg}"
    print(line, flush=True)


def log_poll(repo: str, candidates: int, active: int) -> None:
    log(f"repo={repo} event=poll candidates={candidates} active={active}")


def log_transition(repo: str, issue: int, from_state: str, to_state: str) -> None:
    log(f"repo={repo} issue={issue} event=transition from={from_state} to={to_state}")


def log_agent_launch(repo: str, issue: int, phase: str) -> None:
    log(f"repo={repo} issue={issue} event=agent-launch phase={phase}")


def log_agent_complete(repo: str, issue: int, phase: str, elapsed_s: float) -> None:
    log(
        f"repo={repo} issue={issue} event=agent-complete "
        f"phase={phase} elapsed_s={elapsed_s:.0f}"
    )


def log_agent_failure(repo: str, issue: int, phase: str, reason: str) -> None:
    log(f"repo={repo} issue={issue} event=agent-failure phase={phase} reason={reason}")


def log_reap(repo: str, issue: int, from_state: str, to_state: str) -> None:
    log(f"repo={repo} issue={issue} event=reap from={from_state} to={to_state}")


def log_abort(repo: str, issue: int, reason: str) -> None:
    log(f"repo={repo} issue={issue} event=abort reason={reason}")


def log_ci_failure(repo: str, issue: int, pr_num: int, failed_count: int) -> None:
    log(
        f"repo={repo} issue={issue} pr={pr_num} event=ci-failure "
        f"failing_checks={failed_count}"
    )
