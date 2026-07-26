from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from slingshot import git_utils
from slingshot.git_utils import _repo_slug, worktree_path, worktree_root


class TestRepoSlug:
    def test_full_path(self):
        slug = _repo_slug(Path("/Users/james/src/slingshot"))
        assert slug == "Users-james-src-slingshot"

    def test_single_level(self):
        assert _repo_slug(Path("/repo")) == "repo"

    def test_long_path(self):
        assert _repo_slug(Path("/a/b/c/d/e")) == "a-b-c-d-e"

    def test_home_relative(self):
        home = Path.home()
        slug = _repo_slug(home / "src" / "slingshot")
        assert slug.endswith("slingshot")
        assert slug.startswith("Users") or slug.startswith("home")


class TestWorktreeRoot:
    def test_returns_home_local_share(self):
        root = worktree_root()
        assert root == Path.home() / ".local" / "share" / "slingshot" / "worktrees"

    def test_is_absolute(self):
        assert worktree_root().is_absolute()


class TestWorktreePath:
    def test_paths_are_outside_checkout(self):
        checkout = Path("/Users/james/src/slingshot")
        wt = worktree_path(checkout, 42)
        assert str(checkout) not in str(wt)
        assert wt.name == "42"
        assert "slingshot" in wt.parts

    def test_path_is_absolute(self):
        wt = worktree_path(Path("/some/repo"), 1)
        assert wt.is_absolute()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    )


@pytest.fixture
def checkout_with_remote_branch(tmp_path, monkeypatch):
    """A clone with only origin/slingshot/42 existing (no local branch).

    Worktrees are redirected under tmp_path so the real
    ~/.local/share/slingshot is untouched.
    """
    monkeypatch.setattr(git_utils, "worktree_root", lambda: tmp_path / "wt")

    origin = tmp_path / "origin.git"
    _git(["init", "--bare", str(origin)], cwd=tmp_path)
    checkout = tmp_path / "checkout"
    _git(["clone", str(origin), str(checkout)], cwd=tmp_path)
    _git(["config", "user.email", "test@example.com"], cwd=checkout)
    _git(["config", "user.name", "Test"], cwd=checkout)

    (checkout / "README.md").write_text("hi\n")
    _git(["add", "."], cwd=checkout)
    _git(["commit", "-m", "init"], cwd=checkout)
    _git(["push", "origin", "HEAD:main"], cwd=checkout)

    _git(["checkout", "-b", "slingshot/42"], cwd=checkout)
    (checkout / "feature.txt").write_text("work\n")
    _git(["add", "."], cwd=checkout)
    _git(["commit", "-m", "feature"], cwd=checkout)
    _git(["push", "origin", "slingshot/42"], cwd=checkout)
    _git(["checkout", "-"], cwd=checkout)
    _git(["branch", "-D", "slingshot/42"], cwd=checkout)
    _git(["fetch", "origin"], cwd=checkout)

    return checkout, origin


class TestCreateWorktreeFromRemote:
    def test_worktree_is_on_local_branch_not_detached(
        self, checkout_with_remote_branch,
    ):
        checkout, _origin = checkout_with_remote_branch
        wt = git_utils.create_worktree_from_remote(checkout, 42)

        branch = _git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt,
        ).stdout.strip()
        assert branch == "slingshot/42"
        assert (wt / "feature.txt").exists()

    def test_branch_tracks_remote(self, checkout_with_remote_branch):
        checkout, _origin = checkout_with_remote_branch
        wt = git_utils.create_worktree_from_remote(checkout, 42)

        upstream = _git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=wt,
        ).stdout.strip()
        assert upstream == "origin/slingshot/42"

    def test_commit_and_push_roundtrip(self, checkout_with_remote_branch):
        checkout, origin = checkout_with_remote_branch
        wt = git_utils.create_worktree_from_remote(checkout, 42)

        (wt / "more.txt").write_text("more\n")
        git_utils.commit_changes(wt, "more")
        git_utils.push_branch(wt)

        local_sha = _git(["rev-parse", "HEAD"], cwd=wt).stdout.strip()
        remote_sha = _git(
            ["rev-parse", "slingshot/42"], cwd=origin,
        ).stdout.strip()
        assert local_sha == remote_sha

    def test_recreate_with_stale_local_branch(self, checkout_with_remote_branch):
        checkout, _origin = checkout_with_remote_branch
        wt = git_utils.create_worktree_from_remote(checkout, 42)
        (wt / "more.txt").write_text("more\n")
        git_utils.commit_changes(wt, "more")
        git_utils.push_branch(wt)

        # Second run: stale local branch exists and must be replaced
        # with the remote tip, not fail with "branch already exists".
        wt2 = git_utils.create_worktree_from_remote(checkout, 42)
        assert (wt2 / "more.txt").exists()
        branch = _git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt2,
        ).stdout.strip()
        assert branch == "slingshot/42"
