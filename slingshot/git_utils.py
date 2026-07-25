"""Git operations — worktrees, branching, committing."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(
    args: list[str], *,
    cwd: str | Path | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        capture_output=capture,
        text=True,
        cwd=str(cwd) if cwd else None,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout or "", stderr=stderr
        )
    return result


def _check(args: list[str], *, cwd: str | Path | None = None) -> bool:
    """Run a command; return True if exit 0, False otherwise."""
    try:
        _run(args, cwd=cwd, capture=True)
        return True
    except subprocess.CalledProcessError:
        return False


def fetch_origin(checkout: Path) -> None:
    """Fetch updates from origin in the bare checkout."""
    _run(["git", "fetch", "origin"], cwd=checkout, capture=False)


def ensure_exclude(checkout: Path) -> None:
    """Append '.slingshot/' to .git/info/exclude if not already present."""
    exclude = checkout / ".git" / "info" / "exclude"
    entry = ".slingshot/"
    if exclude.exists():
        content = exclude.read_text()
        if entry in content:
            return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a") as fh:
        fh.write(f"\n{entry}\n")


def remote_branch_exists(checkout: Path, branch: str) -> bool:
    """Check whether 'origin/<branch>' exists."""
    return _check(["git", "rev-parse", "--verify", f"origin/{branch}"], cwd=checkout)


def branch_last_commit_epoch(checkout: Path, branch: str) -> int | None:
    """Return committer-date (epoch seconds) of origin/<branch> tip, or None."""
    try:
        result = _run(["git", "log", "-1", "--format=%ct", f"origin/{branch}"],
                      cwd=checkout)
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def default_branch(checkout: Path) -> str:
    """Detect default branch from origin/HEAD, fall back to 'main'."""
    try:
        result = _run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=checkout,
        )
        ref = result.stdout.strip()  # refs/remotes/origin/main
        return ref.split("/")[-1]
    except subprocess.CalledProcessError:
        return "main"


def create_worktree(checkout: Path, issue_num: int, base: str) -> Path:
    """Create a new worktree + branch from origin/<base>."""
    branch = f"slingshot/{issue_num}"
    wt_path = checkout / ".slingshot" / "worktrees" / str(issue_num)
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale worktree if present, then create
    worktree_remove(checkout, issue_num)
    _run([
        "git", "worktree", "add", str(wt_path),
        "-b", branch, f"origin/{base}",
    ], cwd=checkout, capture=False)
    return wt_path


def create_worktree_from_remote(checkout: Path, issue_num: int) -> Path:
    """Create a worktree tracking an existing remote branch."""
    branch = f"slingshot/{issue_num}"
    wt_path = checkout / ".slingshot" / "worktrees" / str(issue_num)
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    worktree_remove(checkout, issue_num)
    _run([
        "git", "worktree", "add", str(wt_path),
        f"origin/{branch}",
    ], cwd=checkout, capture=False)
    return wt_path


def checkout_in_worktree(worktree: Path, branch: str) -> None:
    """In an existing worktree, checkout a branch (tracking origin)."""
    _run(["git", "checkout", "-B", branch], cwd=worktree, capture=False)


def worktree_path(checkout: Path, issue_num: int) -> Path:
    return checkout / ".slingshot" / "worktrees" / str(issue_num)


def has_changes(worktree: Path) -> bool:
    """Return True if there are uncommitted changes."""
    try:
        result = _run(["git", "status", "--porcelain"], cwd=worktree)
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def commit_changes(worktree: Path, message: str) -> None:
    """Add all changes and commit."""
    _run(["git", "add", "-A"], cwd=worktree, capture=False)
    _run(["git", "commit", "-m", message], cwd=worktree, capture=False)


def push_branch(worktree: Path) -> None:
    """Push the current branch to origin and set upstream."""
    branch_name = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree,
    ).stdout.strip()
    _run(["git", "push", "-u", "origin", branch_name], cwd=worktree, capture=False)


def worktree_remove(checkout: Path, issue_num: int) -> None:
    """Remove a specific worktree if it exists."""
    wt_path = checkout / ".slingshot" / "worktrees" / str(issue_num)
    if not wt_path.exists():
        return
    try:
        _run([
            "git", "worktree", "remove", str(wt_path), "--force",
        ], cwd=checkout, capture=False)
    except subprocess.CalledProcessError:
        pass


def worktree_list(checkout: Path) -> list[Path]:
    """Return paths of all worktrees for this checkout."""
    try:
        result = _run(["git", "worktree", "list", "--porcelain"], cwd=checkout)
        paths: list[Path] = []
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                p = Path(line.split(" ", 1)[1])
                if ".slingshot/worktrees" in str(p):
                    paths.append(p)
        return paths
    except subprocess.CalledProcessError:
        return []


def prune_orphan_worktrees(checkout: Path, active_issue_nums: set[int]) -> None:
    """Remove worktrees for issues that are no longer active."""
    for wt_path in worktree_list(checkout):
        issue_num_str = wt_path.name
        if issue_num_str.isdigit() and int(issue_num_str) not in active_issue_nums:
            worktree_remove(checkout, int(issue_num_str))
