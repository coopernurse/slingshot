from __future__ import annotations

from pathlib import Path

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
