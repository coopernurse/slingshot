"""Git operations — worktrees, branching, committing."""

from __future__ import annotations

import subprocess
from pathlib import Path

from slingshot.logging import log_cmd_output


def _run(
    args: list[str], *,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout or "", stderr=stderr
        )
    log_cmd_output(args, result.stdout, result.stderr)
    return result


def _check(args: list[str], *, cwd: str | Path | None = None) -> bool:
    """Run a command; return True if exit 0, False otherwise."""
    try:
        _run(args, cwd=cwd)
        return True
    except subprocess.CalledProcessError:
        return False


def fetch_origin(checkout: Path) -> None:
    """Fetch updates from origin in the bare checkout."""
    _run(["git", "fetch", "origin"], cwd=checkout)


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


def _repo_slug(path: Path) -> str:
    """Return a filesystem-safe slug from a repo path.

    ``/Users/james/src/slingshot`` → ``Users-james-src-slingshot``.
    """
    parts = [p for p in path.parts if p not in ("", "/")]
    return "-".join(parts)


def worktree_root() -> Path:
    """Return the directory where slingshot worktrees are stored.

    Layout: ``~/.local/share/slingshot/worktrees/<repo-slug>/<issue>/``.
    """
    return Path.home() / ".local" / "share" / "slingshot" / "worktrees"


def create_worktree(checkout: Path, issue_num: int, base: str) -> Path:
    """Create a new worktree + branch from origin/<base>."""
    branch = f"slingshot/{issue_num}"
    wt_path = worktree_root() / _repo_slug(checkout) / str(issue_num)
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale worktree if present, then create
    worktree_remove(checkout, issue_num)
    # Remove stale local branch from a previous failed attempt that
    # never pushed (e.g. empty-diff).  If the branch had been pushed,
    # remote_branch_exists would have routed us to resume/rework.
    _delete_branch_if_exists(checkout, branch)
    _run([
        "git", "worktree", "add", str(wt_path),
        "-b", branch, f"origin/{base}",
    ], cwd=checkout)
    return wt_path


def create_worktree_from_remote(checkout: Path, issue_num: int) -> Path:
    """Create a worktree tracking an existing remote branch."""
    branch = f"slingshot/{issue_num}"
    wt_path = worktree_root() / _repo_slug(checkout) / str(issue_num)
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    worktree_remove(checkout, issue_num)
    # Remove stale local branch from a previous run; the remote branch
    # is the source of truth.  Without this, `worktree add -b` fails
    # with "branch already exists".
    _delete_branch_if_exists(checkout, branch)
    # Create a local branch tracking the remote one.  Passing
    # `origin/<branch>` alone would leave the worktree on a detached
    # HEAD, and `push_branch` would have no branch name to push.
    _run([
        "git", "worktree", "add", str(wt_path),
        "--track", "-b", branch, f"origin/{branch}",
    ], cwd=checkout)
    return wt_path


def checkout_in_worktree(worktree: Path, branch: str) -> None:
    """In an existing worktree, checkout a branch (tracking origin)."""
    _run(["git", "checkout", "-B", branch], cwd=worktree)


def _delete_branch_if_exists(checkout: Path, branch: str) -> None:
    """Delete a local branch if it exists (no-op otherwise)."""
    try:
        _run(["git", "branch", "-D", branch], cwd=checkout)
    except subprocess.CalledProcessError:
        pass


def worktree_path(checkout: Path, issue_num: int) -> Path:
    return worktree_root() / _repo_slug(checkout) / str(issue_num)


def has_changes(worktree: Path) -> bool:
    """Return True if there are uncommitted changes."""
    try:
        result = _run(["git", "status", "--porcelain"], cwd=worktree)
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def has_unpushed_commits(worktree: Path) -> bool:
    """Return True if the current branch has commits not yet pushed to remote."""
    try:
        result = _run(
            ["git", "log", "--oneline", "@{u}..HEAD"],
            cwd=worktree,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def worktree_status(worktree: Path) -> str:
    """Return a diagnostic snapshot of the worktree: status, log, branch info."""
    lines: list[str] = []
    try:
        r = _run(["git", "status", "--porcelain"], cwd=worktree)
        lines.append(f"git status --porcelain: {r.stdout.strip() or '(clean)'}")
    except Exception as exc:
        lines.append(f"git status --porcelain: ERROR {exc}")
    try:
        r = _run(["git", "log", "--oneline", "-5"], cwd=worktree)
        lines.append(f"git log --oneline -5:{chr(10)}{r.stdout.strip()}")
    except Exception as exc:
        lines.append(f"git log --oneline -5: ERROR {exc}")
    try:
        r = _run(["git", "branch", "-v"], cwd=worktree)
        lines.append(f"git branch -v:{chr(10)}{r.stdout.strip()}")
    except Exception as exc:
        lines.append(f"git branch -v: ERROR {exc}")
    return "\n".join(lines)


def commit_changes(worktree: Path, message: str) -> None:
    """Add all changes and commit."""
    _run(["git", "add", "-A"], cwd=worktree)
    _run(["git", "commit", "-m", message], cwd=worktree)


def push_branch(worktree: Path) -> None:
    """Push the current branch to origin and set upstream."""
    branch_name = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree,
    ).stdout.strip()
    _run(["git", "push", "-u", "origin", branch_name], cwd=worktree)


def worktree_remove(checkout: Path, issue_num: int) -> None:
    """Remove a specific worktree if it exists."""
    wt_path = worktree_root() / _repo_slug(checkout) / str(issue_num)
    if not wt_path.exists():
        return
    try:
        _run([
            "git", "worktree", "remove", str(wt_path), "--force",
        ], cwd=checkout)
    except subprocess.CalledProcessError:
        pass


def worktree_list(checkout: Path) -> list[Path]:
    """Return paths of all slingshot worktrees for this checkout."""
    root = worktree_root() / _repo_slug(checkout)
    if not root.exists():
        return []
    try:
        result = _run(["git", "worktree", "list", "--porcelain"], cwd=checkout)
        known: set[str] = set()
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                known.add(line.split(" ", 1)[1])
        return [p for p in root.iterdir() if p.is_dir() and str(p) in known]
    except subprocess.CalledProcessError:
        return []


def prune_orphan_worktrees(checkout: Path, active_issue_nums: set[int]) -> None:
    """Remove worktrees for issues that are no longer active."""
    for wt_path in worktree_list(checkout):
        issue_num_str = wt_path.name
        if issue_num_str.isdigit() and int(issue_num_str) not in active_issue_nums:
            worktree_remove(checkout, int(issue_num_str))
