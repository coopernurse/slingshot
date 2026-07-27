from __future__ import annotations

import json
import sys
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from slingshot.config import Config, RepoConfig
from slingshot.daemon import (
    Daemon,
    WorkerState,
    _comment_age,
    _comment_epoch,
    _newest_claim_comment,
    _run_agent,
)
from slingshot.prompts import CI_FAIL_MARKER, REVIEW_FAIL_MARKER


def _make_repo():
    return RepoConfig(name="test/repo", path=Path("/tmp/slingshot-test-repo"))


class TestCommentAge:
    def test_valid_iso_timestamp(self):
        age = _comment_age("2025-01-01T00:00:00Z")
        assert age is not None
        assert age > 0

    def test_valid_iso_with_milliseconds(self):
        age = _comment_age("2025-01-01T00:00:00.000Z")
        assert age is not None
        assert age > 0

    def test_malformed_timestamp_returns_none(self):
        assert _comment_age("not-a-date") is None

    def test_empty_string_returns_none(self):
        assert _comment_age("") is None


class TestCommentEpoch:
    def test_valid_iso_timestamp(self):
        epoch = _comment_epoch("2025-01-01T00:00:00Z")
        assert epoch is not None
        # 2025-01-01T00:00:00Z in epoch seconds
        assert epoch == 1735689600

    def test_valid_iso_with_milliseconds(self):
        epoch = _comment_epoch("2025-01-01T00:00:00.000Z")
        assert epoch is not None
        assert epoch == 1735689600

    def test_malformed_timestamp_returns_none(self):
        assert _comment_epoch("garbage") is None

    def test_empty_string_returns_none(self):
        assert _comment_epoch("") is None


class TestNewestClaimComment:
    def test_picks_newest_matching_comment(self):
        comments = [
            {"body": "slingshot-claim: abc slingshot:implementing",
             "createdAt": "2025-01-01T00:00:00Z"},
            {"body": "slingshot-claim: def slingshot:implementing",
             "createdAt": "2025-01-02T00:00:00Z"},
            {"body": "slingshot-claim: xyz slingshot:implementing",
             "createdAt": "2025-01-01T12:00:00Z"},
        ]
        result = _newest_claim_comment(comments, "slingshot:implementing")
        assert result is not None
        assert "def" in result["body"]

    def test_returns_none_on_empty_input(self):
        result = _newest_claim_comment([], "slingshot:implementing")
        assert result is None

    def test_returns_none_when_no_matching_claim(self):
        comments = [
            {"body": "some other comment", "createdAt": "2025-01-01T00:00:00Z"},
        ]
        result = _newest_claim_comment(comments, "slingshot:implementing")
        assert result is None

    def test_ignores_claims_for_different_flight_state(self):
        comments = [
            {"body": "slingshot-claim: abc slingshot:implementing",
             "createdAt": "2025-01-01T00:00:00Z"},
        ]
        result = _newest_claim_comment(comments, "slingshot:reviewing")
        assert result is None

    def test_handles_comments_without_created_at(self):
        comments = [
            {"body": "slingshot-claim: abc slingshot:implementing"},
            {"body": "slingshot-claim: def slingshot:implementing",
             "createdAt": "2025-01-01T00:00:00Z"},
        ]
        result = _newest_claim_comment(comments, "slingshot:implementing")
        assert result is not None
        assert "def" in result["body"]


# ---------------------------------------------------------------------------
# CI check tests
# ---------------------------------------------------------------------------


class TestCiCheckFailing:
    def test_failing_check_posts_comment_and_transitions_to_implement(self):
        repo = _make_repo()
        cfg = Config(repos=[repo], review_fail_threshold=5)
        daemon = Daemon(cfg)

        issue = {"number": 1, "title": "test", "body": "spec"}

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_list", return_value=[issue]), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[
                 {"number": 10, "state": "OPEN", "mergedAt": None},
             ]), \
             patch("slingshot.daemon.gh.pr_check_status", return_value={
                 "sha": "abc1234567890",
                 "checks": [
                     {"name": "test", "completed": True, "failed": True, "url": "https://ex.com/1"},
                 ],
             }), \
             patch("slingshot.daemon.gh.pr_comments", return_value=[]), \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment:
            daemon._ci_check(repo)

        mock_comment.assert_called_once()
        body = mock_comment.call_args[0][2]
        assert CI_FAIL_MARKER in body
        assert "abc1234" in body
        assert "test" in body
        mock_tx.assert_called_once_with(
            repo, 1, "slingshot:approved", "slingshot:implement",
        )

    def test_fail_marker_total_at_threshold_transitions_to_blocked(self):
        repo = _make_repo()
        cfg = Config(repos=[repo], review_fail_threshold=1)
        daemon = Daemon(cfg)

        issue = {"number": 1, "title": "test", "body": "spec"}

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_list", return_value=[issue]), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[
                 {"number": 10, "state": "OPEN", "mergedAt": None},
             ]), \
             patch("slingshot.daemon.gh.pr_check_status", return_value={
                 "sha": "abc1234567890",
                 "checks": [
                     {"name": "test", "completed": True, "failed": True, "url": ""},
                 ],
             }), \
             patch("slingshot.daemon.gh.pr_comments", return_value=[]), \
             patch("slingshot.daemon.gh.pr_comment_create"):
            daemon._ci_check(repo)

        mock_tx.assert_called_once_with(
            repo, 1, "slingshot:approved", "slingshot:blocked",
        )


class TestCiCheckPending:
    def test_pending_checks_no_comment_no_label_change(self):
        repo = _make_repo()
        cfg = Config(repos=[repo])
        daemon = Daemon(cfg)

        issue = {"number": 1, "title": "test", "body": "spec"}

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_list", return_value=[issue]), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[
                 {"number": 10, "state": "OPEN", "mergedAt": None},
             ]), \
             patch("slingshot.daemon.gh.pr_check_status", return_value={
                 "sha": "abc",
                 "checks": [
                     {
                         "name": "waiting",
                         "completed": False,
                         "failed": False,
                         "url": "",
                     },
                 ],
             }), \
             patch("slingshot.daemon.gh.pr_comments") as mock_comments, \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment:
            daemon._ci_check(repo)

        mock_comment.assert_not_called()
        mock_comments.assert_not_called()
        mock_tx.assert_not_called()

    def test_all_completed_none_failing_no_action(self):
        repo = _make_repo()
        cfg = Config(repos=[repo])
        daemon = Daemon(cfg)

        issue = {"number": 1, "title": "test", "body": "spec"}

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_list", return_value=[issue]), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[
                 {"number": 10, "state": "OPEN", "mergedAt": None},
             ]), \
             patch("slingshot.daemon.gh.pr_check_status", return_value={
                 "sha": "abc",
                 "checks": [
                     {"name": "lint", "completed": True, "failed": False, "url": ""},
                     {"name": "test", "completed": True, "failed": False, "url": ""},
                 ],
             }), \
             patch("slingshot.daemon.gh.pr_comments") as mock_comments, \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment:
            daemon._ci_check(repo)

        mock_comment.assert_not_called()
        mock_comments.assert_not_called()
        mock_tx.assert_not_called()

    def test_no_checks_reported_no_action(self):
        repo = _make_repo()
        cfg = Config(repos=[repo])
        daemon = Daemon(cfg)

        issue = {"number": 1, "title": "test", "body": "spec"}

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_list", return_value=[issue]), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[
                 {"number": 10, "state": "OPEN", "mergedAt": None},
             ]), \
             patch("slingshot.daemon.gh.pr_check_status", return_value={
                 "sha": "abc",
                 "checks": [],
             }), \
             patch("slingshot.daemon.gh.pr_comments") as mock_comments, \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment:
            daemon._ci_check(repo)

        mock_comment.assert_not_called()
        mock_comments.assert_not_called()
        mock_tx.assert_not_called()


class TestCiCheckDedup:
    def test_same_sha_skips_duplicate_comment_but_still_transitions(self):
        repo = _make_repo()
        cfg = Config(repos=[repo], review_fail_threshold=5)
        daemon = Daemon(cfg)

        issue = {"number": 1, "title": "test", "body": "spec"}
        existing_comments = [
            {"body": f"{CI_FAIL_MARKER}\n**CI Failure** on `abc1234`\n\nstuff",
             "createdAt": "2025-01-01T00:00:00Z"},
        ]

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_list", return_value=[issue]), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[
                 {"number": 10, "state": "OPEN", "mergedAt": None},
             ]), \
             patch("slingshot.daemon.gh.pr_check_status", return_value={
                 "sha": "abc1234567890",
                 "checks": [
                     {"name": "test", "completed": True, "failed": True, "url": ""},
                 ],
             }), \
             patch("slingshot.daemon.gh.pr_comments", return_value=existing_comments), \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment:
            daemon._ci_check(repo)

        mock_comment.assert_not_called()
        mock_tx.assert_called_once_with(
            repo, 1, "slingshot:approved", "slingshot:implement",
        )


class TestCiCheckNoPr:
    def test_no_open_pr_skips_issue(self):
        repo = _make_repo()
        cfg = Config(repos=[repo])
        daemon = Daemon(cfg)

        issue = {"number": 1, "title": "test", "body": "spec"}

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_list", return_value=[issue]), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[]), \
             patch("slingshot.daemon.gh.pr_check_status") as mock_check, \
             patch("slingshot.daemon.gh.pr_comments") as mock_comments, \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment:
            daemon._ci_check(repo)

        mock_check.assert_not_called()
        mock_comments.assert_not_called()
        mock_comment.assert_not_called()
        mock_tx.assert_not_called()

    def test_merged_pr_skips_issue(self):
        repo = _make_repo()
        cfg = Config(repos=[repo])
        daemon = Daemon(cfg)

        issue = {"number": 1, "title": "test", "body": "spec"}

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_list", return_value=[issue]), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[
                 {"number": 10, "state": "OPEN", "mergedAt": "2025-01-01T00:00:00Z"},
             ]), \
             patch("slingshot.daemon.gh.pr_check_status") as mock_check, \
             patch("slingshot.daemon.gh.pr_comments") as mock_comments, \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment:
            daemon._ci_check(repo)

        mock_check.assert_not_called()
        mock_comments.assert_not_called()
        mock_comment.assert_not_called()
        mock_tx.assert_not_called()


class TestFindPrAndFailCount:
    def test_counts_both_review_and_ci_markers(self):
        repo = _make_repo()
        cfg = Config(repos=[repo])
        daemon = Daemon(cfg)

        comments = [
            {"body": f"{REVIEW_FAIL_MARKER}\nsome review feedback",
             "createdAt": "2025-01-01T00:00:00Z"},
            {"body": f"{CI_FAIL_MARKER}\nsome ci feedback",
             "createdAt": "2025-01-02T00:00:00Z"},
            {"body": "normal comment"},
        ]

        with patch("slingshot.daemon.gh.pr_list_by_head", return_value=[
            {"number": 10, "state": "OPEN"},
        ]), \
             patch("slingshot.daemon.gh.pr_comments", return_value=comments):
            pr_num, fail_count = daemon._find_pr_and_fail_count(repo, "slingshot/1")

        assert pr_num == 10
        assert fail_count == 2

    def test_returns_zero_when_no_pr(self):
        repo = _make_repo()
        cfg = Config(repos=[repo])
        daemon = Daemon(cfg)

        with patch("slingshot.daemon.gh.pr_list_by_head", return_value=[]):
            pr_num, fail_count = daemon._find_pr_and_fail_count(repo, "slingshot/1")

        assert pr_num is None
        assert fail_count == 0


class TestHandleCiFailureTransitions:
    def test_combined_fail_count_below_threshold_to_implement(self):
        repo = _make_repo()
        cfg = Config(repos=[repo], review_fail_threshold=5)
        daemon = Daemon(cfg)

        pr_comments = [
            {"body": f"{REVIEW_FAIL_MARKER}\nfeedback",
             "createdAt": "2025-01-01T00:00:00Z"},
            {"body": f"{CI_FAIL_MARKER}\nCI fail on `xyz1111`",
             "createdAt": "2025-01-02T00:00:00Z"},
        ]

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.pr_comments", return_value=pr_comments), \
             patch("slingshot.daemon.gh.pr_comment_create"):
            daemon._handle_ci_failure(
                repo, 1, 10, "abc1234567890",
                [{"name": "test", "completed": True, "failed": True, "url": ""}],
            )

        mock_tx.assert_called_once_with(
            repo, 1, "slingshot:approved", "slingshot:implement",
        )

    def test_combined_fail_count_at_threshold_to_blocked(self):
        repo = _make_repo()
        cfg = Config(repos=[repo], review_fail_threshold=2)
        daemon = Daemon(cfg)

        pr_comments = [
            {"body": f"{REVIEW_FAIL_MARKER}\nfeedback",
             "createdAt": "2025-01-01T00:00:00Z"},
        ]

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.pr_comments", return_value=pr_comments), \
             patch("slingshot.daemon.gh.pr_comment_create"):
            daemon._handle_ci_failure(
                repo, 1, 10, "abc1234567890",
                [{"name": "test", "completed": True, "failed": True, "url": ""}],
            )

        mock_tx.assert_called_once_with(
            repo, 1, "slingshot:approved", "slingshot:blocked",
        )
class TestCountConsecutiveErrors:
    ERROR = "<!-- slingshot:agent-error -->\n**Slingshot implement agent failed:** x"
    CLAIM = "slingshot-claim: daemon-1 slingshot:implementing 2025-01-01T00:00:00Z"

    def _count(self, comments):
        daemon = Daemon(Config(repos=[_make_repo()]))
        with patch("slingshot.daemon.gh.issue_comments", return_value=comments):
            return daemon._count_consecutive_errors("test/repo", 1)

    def test_ignores_claim_comments_between_errors(self):
        comments = [
            {"body": self.ERROR, "createdAt": "2025-01-01T00:00:00Z"},
            {"body": self.CLAIM, "createdAt": "2025-01-01T00:01:00Z"},
            {"body": self.ERROR, "createdAt": "2025-01-01T00:02:00Z"},
            {"body": self.CLAIM, "createdAt": "2025-01-01T00:03:00Z"},
            {"body": self.ERROR, "createdAt": "2025-01-01T00:04:00Z"},
        ]
        assert self._count(comments) == 3

    def test_human_comment_breaks_streak(self):
        comments = [
            {"body": self.ERROR, "createdAt": "2025-01-01T00:00:00Z"},
            {"body": "looks good, let me take over",
             "createdAt": "2025-01-01T00:01:00Z"},
            {"body": self.ERROR, "createdAt": "2025-01-01T00:02:00Z"},
        ]
        assert self._count(comments) == 1

    def test_no_error_comments_returns_zero(self):
        comments = [
            {"body": self.CLAIM, "createdAt": "2025-01-01T00:00:00Z"},
            {"body": "normal comment", "createdAt": "2025-01-01T00:01:00Z"},
        ]
        assert self._count(comments) == 0


class TestHandleAgentFailureBlocking:
    def test_repeated_failures_with_interleaved_claims_eventually_block(self):
        repo = _make_repo()
        cfg = Config(repos=[repo], agent_failure_threshold=3)
        daemon = Daemon(cfg)
        ws = WorkerState(repo, {"number": 8, "title": "t", "body": "s"},
                         "slingshot:implement")

        # Thread state after the third error comment was posted
        # (issue_comments is re-fetched after posting).
        comments = [
            {"body": TestCountConsecutiveErrors.ERROR,
             "createdAt": "2025-01-01T00:00:00Z"},
            {"body": TestCountConsecutiveErrors.CLAIM,
             "createdAt": "2025-01-01T00:01:00Z"},
            {"body": TestCountConsecutiveErrors.ERROR,
             "createdAt": "2025-01-01T00:02:00Z"},
            {"body": TestCountConsecutiveErrors.CLAIM,
             "createdAt": "2025-01-01T00:03:00Z"},
            {"body": TestCountConsecutiveErrors.ERROR,
             "createdAt": "2025-01-01T00:04:00Z"},
        ]

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.issue_comment_create"), \
             patch("slingshot.daemon.gh.issue_comments", return_value=comments):
            daemon._handle_agent_failure(ws, "slingshot:implementing", "empty-diff")

        # The third consecutive error (2 prior + this one) must block.
        assert mock_tx.call_args_list[-1][0] == (
            repo, 8, "slingshot:implement", "slingshot:blocked",
        )


class TestDoImplementReworkRouting:
    def _ws(self, repo):
        issue = {"number": 8, "title": "t", "body": "spec"}
        return WorkerState(repo, issue, "slingshot:implement")

    def _success_mocks(self, tmp_path):
        """Common patches for a successful agent run in tmp_path worktree."""
        worktree = tmp_path / "wt"
        return {
            "worktree": worktree,
            "has_changes": lambda p: Path(p) != tmp_path,
        }

    def test_stale_fail_comments_skip_agent_and_advance(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        ws = self._ws(repo)

        stale = {
            "body": f"{CI_FAIL_MARKER}\n**CI Failure** on `abc1234`",
            "createdAt": "2025-01-01T00:00:00Z",  # epoch 1735689600
        }

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.git.remote_branch_exists", return_value=True), \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.gh.pr_comments", return_value=[stale]), \
             patch("slingshot.daemon.git.branch_last_commit_epoch",
                   return_value=1735689600 + 3600), \
             patch("slingshot.daemon.git.create_worktree_from_remote") as m_wt, \
             patch("slingshot.daemon._run_agent") as m_agent:
            daemon._do_implement(ws)

        m_wt.assert_not_called()
        m_agent.assert_not_called()
        mock_tx.assert_called_once_with(
            repo, 8, "slingshot:implementing", "slingshot:review",
        )

    def test_fresh_fail_comment_runs_rework_and_skips_existing_pr_create(
        self, tmp_path,
    ):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        ws = self._ws(repo)
        mocks = self._success_mocks(tmp_path)

        fresh = {
            "body": f"{CI_FAIL_MARKER}\n**CI Failure** on `abc1234`",
            "createdAt": "2025-01-01T00:00:00Z",  # epoch 1735689600
        }

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.git.remote_branch_exists", return_value=True), \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.gh.pr_comments", return_value=[fresh]), \
             patch("slingshot.daemon.git.branch_last_commit_epoch",
                   return_value=1735689600 - 3600), \
             patch("slingshot.daemon.git.create_worktree_from_remote",
                   return_value=mocks["worktree"]), \
             patch("slingshot.daemon.git.has_changes",
                   side_effect=mocks["has_changes"]), \
             patch("slingshot.daemon._run_agent", return_value=(0, "")), \
             patch("slingshot.daemon.git.commit_changes"), \
             patch("slingshot.daemon.git.push_branch"), \
             patch("slingshot.daemon.gh.pr_create") as m_pr_create:
            daemon._do_implement(ws)

        m_pr_create.assert_not_called()
        mock_tx.assert_called_once_with(
            repo, 8, "slingshot:implementing", "slingshot:review",
        )

    def test_fresh_scenario_creates_pr_when_none_exists(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        ws = self._ws(repo)
        mocks = self._success_mocks(tmp_path)

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.git.remote_branch_exists",
                   return_value=False), \
             patch("slingshot.daemon.gh.pr_list_by_head", return_value=[]), \
             patch("slingshot.daemon.git.create_worktree",
                   return_value=mocks["worktree"]), \
             patch("slingshot.daemon.git.has_changes",
                   side_effect=mocks["has_changes"]), \
             patch("slingshot.daemon._run_agent", return_value=(0, "")), \
             patch("slingshot.daemon.git.commit_changes"), \
             patch("slingshot.daemon.git.push_branch"), \
             patch("slingshot.daemon.gh.repo_default_branch",
                   return_value="main"), \
             patch("slingshot.daemon.gh.pr_create") as m_pr_create:
            daemon._do_implement(ws)

        m_pr_create.assert_called_once()
        mock_tx.assert_called_once_with(
            repo, 8, "slingshot:implementing", "slingshot:review",
        )


def _ws():
    return SimpleNamespace(proc=None)


class TestRunAgent:
    def test_captures_output_and_exit_code(self, tmp_path):
        code, output = _run_agent(
            f"{sys.executable} -c \"print('hello')\"",
            "prompt.md", cwd=str(tmp_path), timeout=60, ws=_ws(),
        )
        assert code == 0
        assert "hello" in output

    def test_injects_opencode_config_content(self, tmp_path):
        code, output = _run_agent(
            f'{sys.executable} -c "import os; '
            "print(os.environ['OPENCODE_CONFIG_CONTENT'])\"",
            "prompt.md", cwd=str(tmp_path), timeout=60, ws=_ws(),
        )
        assert code == 0
        cfg = json.loads(output)
        assert cfg["permission"]["external_directory"] == "allow"

    def test_command_not_found(self, tmp_path):
        code, output = _run_agent(
            "no-such-binary-xyz {prompt_file}",
            "prompt.md", cwd=str(tmp_path), timeout=60, ws=_ws(),
        )
        assert code == -2
        assert output == "agent command not found"

    def test_wall_clock_timeout_kills_process(self, tmp_path):
        start = time.time()
        code, output = _run_agent(
            f'{sys.executable} -c "import time; time.sleep(30)"',
            "prompt.md", cwd=str(tmp_path), timeout=1, ws=_ws(),
        )
        assert code == -1
        assert output == "agent timed out"
        assert time.time() - start < 15


# ---------------------------------------------------------------------------
# Multi-model review tests
# ---------------------------------------------------------------------------


def _make_future(exit_code: int, output: str) -> Future:
    f: Future = Future()
    f.set_result((exit_code, output))
    return f


_PASS_OUTPUT = '```json\n{"verdict": "pass", "sections": {}}\n```'


class TestDoReviewMultiSuccess:
    def test_all_models_succeed_synthesis_runs_and_transitions(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        issue = {"number": 8, "title": "t", "body": "spec"}
        ws = WorkerState(repo, issue, "slingshot:review")

        worktree = tmp_path / "wt"
        worktree.mkdir()
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True)
        prompt_file = prompt_dir / "8-review.md"
        prompt_file.write_text("prompt")

        # Create futures that all succeed
        f1 = _make_future(0, _PASS_OUTPUT)
        f2 = _make_future(0, _PASS_OUTPUT)
        f3 = _make_future(0, _PASS_OUTPUT)
        futures = [f1, f2, f3]

        mock_exec_class = MagicMock()
        mock_exec_instance = MagicMock()
        mock_exec_class.return_value.__enter__.return_value = mock_exec_instance
        mock_exec_instance.submit.side_effect = list(futures)

        mock_as_completed = MagicMock()
        mock_as_completed.return_value = futures

        syn_output = '```json\n{"verdict": "pass", "voters": {"pass": 3, "fail": 0}, "sections": {}, "summary": "all good"}\n```'

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.git.fetch_origin"), \
             patch("slingshot.daemon.git.worktree_path",
                   return_value=worktree), \
             patch("slingshot.daemon.git.has_changes", return_value=False), \
             patch("slingshot.daemon.ThreadPoolExecutor", mock_exec_class), \
             patch("slingshot.daemon.as_completed", mock_as_completed), \
             patch("slingshot.daemon._run_agent",
                   return_value=(0, syn_output)), \
             patch("slingshot.daemon._write_agent_log") as mock_log, \
             patch("slingshot.daemon._copy_file") as mock_copy, \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment:
            daemon._do_review_multi(
                ws, ["cmd1", "cmd2", "cmd3"], 10, worktree,
                prompt_file, "main", False, "spec",
            )

        # All 3 model outputs + synthesis prompt + synthesis log should be written
        assert mock_log.call_count == 4  # 3 models + 1 synthesis
        # Prompt file + 3 model outputs + synthesis prompt should be archived
        assert mock_copy.call_count >= 4  # prompt + 3 model outputs + synthesis

        mock_comment.assert_called_once()
        body = mock_comment.call_args[0][2]
        assert "PASSED (3/3)" in body

        mock_tx.assert_called_once_with(
            repo, 8, "slingshot:reviewing", "slingshot:approved",
        )

    def test_synthesis_fail_to_implement_label(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        issue = {"number": 8, "title": "t", "body": "spec"}
        ws = WorkerState(repo, issue, "slingshot:review")

        worktree = tmp_path / "wt"
        worktree.mkdir()
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True)
        prompt_file = prompt_dir / "8-review.md"
        prompt_file.write_text("prompt")

        f1 = _make_future(0, _PASS_OUTPUT)
        f2 = _make_future(0, _PASS_OUTPUT)
        futures = [f1, f2]

        mock_exec_class = MagicMock()
        mock_exec_instance = MagicMock()
        mock_exec_class.return_value.__enter__.return_value = mock_exec_instance
        mock_exec_instance.submit.side_effect = list(futures)

        mock_as_completed = MagicMock()
        mock_as_completed.return_value = futures

        syn_output = '```json\n{"verdict": "fail", "voters": {"pass": 1, "fail": 1}, "sections": {"spec_fidelity": {"status": "fail", "notes": "broken"}}, "summary": "needs work"}\n```'

        with patch.object(daemon, "_transition_label") as mock_tx, \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.git.fetch_origin"), \
             patch("slingshot.daemon.git.worktree_path",
                   return_value=worktree), \
             patch("slingshot.daemon.git.has_changes", return_value=False), \
             patch("slingshot.daemon.ThreadPoolExecutor", mock_exec_class), \
             patch("slingshot.daemon.as_completed", mock_as_completed), \
             patch("slingshot.daemon._run_agent",
                   return_value=(0, syn_output)), \
             patch("slingshot.daemon._write_agent_log"), \
             patch("slingshot.daemon._copy_file"), \
             patch("slingshot.daemon.gh.pr_comment_create") as mock_comment, \
             patch("slingshot.daemon.gh.pr_comments", return_value=[{"body": ""}]):
            daemon._do_review_multi(
                ws, ["cmd1", "cmd2"], 10, worktree,
                prompt_file, "main", False, "spec",
            )

        mock_comment.assert_called_once()
        body = mock_comment.call_args[0][2]
        assert "FAILED (1/2)" in body
        mock_tx.assert_called_once_with(
            repo, 8, "slingshot:reviewing", "slingshot:implement",
        )


class TestDoReviewMultiFailure:
    def test_nonzero_exit_kills_siblings_and_reports_exit_code(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        issue = {"number": 8, "title": "t", "body": "spec"}
        ws = WorkerState(repo, issue, "slingshot:review")

        worktree = tmp_path / "wt"
        worktree.mkdir()
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True)
        prompt_file = prompt_dir / "8-review.md"
        prompt_file.write_text("prompt")

        # Model 1 fails with exit code 2
        f1 = _make_future(2, "error output")
        f2 = _make_future(-1, "killed")
        futures = [f1, f2]

        mock_exec_class = MagicMock()
        mock_exec_instance = MagicMock()
        mock_exec_class.return_value.__enter__.return_value = mock_exec_instance
        mock_exec_instance.submit.side_effect = list(futures)

        mock_as_completed = MagicMock()
        mock_as_completed.return_value = futures

        with patch.object(daemon, "_handle_agent_failure") as mock_fail, \
             patch.object(daemon, "_transition_label"), \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.git.fetch_origin"), \
             patch("slingshot.daemon.git.worktree_path",
                   return_value=worktree), \
             patch("slingshot.daemon.git.has_changes", return_value=False), \
             patch("slingshot.daemon.ThreadPoolExecutor", mock_exec_class), \
             patch("slingshot.daemon.as_completed", mock_as_completed), \
             patch("slingshot.daemon._write_agent_log") as mock_log, \
             patch("slingshot.daemon._copy_file") as mock_copy:
            daemon._do_review_multi(
                ws, ["cmd1", "cmd2"], 10, worktree,
                prompt_file, "main", False, "spec",
            )

        # Both model outputs should be saved and archived
        assert mock_log.call_count == 2
        assert mock_copy.call_count >= 3  # prompt + 2 model outputs
        mock_fail.assert_called_once()
        reason = mock_fail.call_args[0][2]
        assert reason == "exit=2"

    def test_no_verdict_reports_no_verdict_reason(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        issue = {"number": 8, "title": "t", "body": "spec"}
        ws = WorkerState(repo, issue, "slingshot:review")

        worktree = tmp_path / "wt"
        worktree.mkdir()
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True)
        prompt_file = prompt_dir / "8-review.md"
        prompt_file.write_text("prompt")

        # Model 1 exits 0 but returns no valid JSON
        f1 = _make_future(0, "garbage output with no json block")
        f2 = _make_future(-1, "killed")
        futures = [f1, f2]

        mock_exec_class = MagicMock()
        mock_exec_instance = MagicMock()
        mock_exec_class.return_value.__enter__.return_value = mock_exec_instance
        mock_exec_instance.submit.side_effect = list(futures)

        mock_as_completed = MagicMock()
        mock_as_completed.return_value = futures

        with patch.object(daemon, "_handle_agent_failure") as mock_fail, \
             patch.object(daemon, "_transition_label"), \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.git.fetch_origin"), \
             patch("slingshot.daemon.git.worktree_path",
                   return_value=worktree), \
             patch("slingshot.daemon.git.has_changes", return_value=False), \
             patch("slingshot.daemon.ThreadPoolExecutor", mock_exec_class), \
             patch("slingshot.daemon.as_completed", mock_as_completed), \
             patch("slingshot.daemon._write_agent_log"), \
             patch("slingshot.daemon._copy_file"):
            daemon._do_review_multi(
                ws, ["cmd1", "cmd2"], 10, worktree,
                prompt_file, "main", False, "spec",
            )

        # Should report "no-verdict" not "exit=-1"
        mock_fail.assert_called_once()
        reason = mock_fail.call_args[0][2]
        assert reason == "no-verdict"

    def test_aborted_worker_returns_early(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        issue = {"number": 8, "title": "t", "body": "spec"}
        ws = WorkerState(repo, issue, "slingshot:review")
        ws.aborted = True

        worktree = tmp_path / "wt"
        worktree.mkdir()
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True)
        prompt_file = prompt_dir / "8-review.md"
        prompt_file.write_text("prompt")

        kill_output = "killed"

        f1 = _make_future(-1, kill_output)
        f2 = _make_future(-1, kill_output)
        futures = [f1, f2]

        mock_exec_class = MagicMock()
        mock_exec_instance = MagicMock()
        mock_exec_class.return_value.__enter__.return_value = mock_exec_instance
        mock_exec_instance.submit.side_effect = list(futures)

        mock_as_completed = MagicMock()
        mock_as_completed.return_value = futures

        with patch.object(daemon, "_handle_agent_failure") as mock_fail, \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.git.fetch_origin"), \
             patch("slingshot.daemon.git.worktree_path",
                   return_value=worktree), \
             patch("slingshot.daemon.git.has_changes", return_value=False), \
             patch("concurrent.futures.ThreadPoolExecutor", mock_exec_class), \
             patch("concurrent.futures.as_completed", mock_as_completed):
            daemon._do_review_multi(
                ws, ["cmd1", "cmd2"], 10, worktree,
                prompt_file, "main", False, "spec",
            )

        mock_fail.assert_not_called()

    def test_synthesis_exit_nonzero_reports_synthesis_exit(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        issue = {"number": 8, "title": "t", "body": "spec"}
        ws = WorkerState(repo, issue, "slingshot:review")

        worktree = tmp_path / "wt"
        worktree.mkdir()
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True)
        prompt_file = prompt_dir / "8-review.md"
        prompt_file.write_text("prompt")

        f1 = _make_future(0, _PASS_OUTPUT)
        futures = [f1]

        mock_exec_class = MagicMock()
        mock_exec_instance = MagicMock()
        mock_exec_class.return_value.__enter__.return_value = mock_exec_instance
        mock_exec_instance.submit.side_effect = list(futures)

        mock_as_completed = MagicMock()
        mock_as_completed.return_value = futures

        with patch.object(daemon, "_handle_agent_failure") as mock_fail, \
             patch.object(daemon, "_transition_label"), \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.git.fetch_origin"), \
             patch("slingshot.daemon.git.worktree_path",
                   return_value=worktree), \
             patch("slingshot.daemon.git.has_changes", return_value=False), \
             patch("slingshot.daemon.ThreadPoolExecutor", mock_exec_class), \
             patch("slingshot.daemon.as_completed", mock_as_completed), \
             patch("slingshot.daemon._run_agent",
                   return_value=(1, "synthesis failed")), \
             patch("slingshot.daemon._write_agent_log"), \
             patch("slingshot.daemon._copy_file"):
            daemon._do_review_multi(
                ws, ["cmd1"], 10, worktree,
                prompt_file, "main", False, "spec",
            )

        mock_fail.assert_called_once()
        reason = mock_fail.call_args[0][2]
        assert reason == "synthesis-exit=1"

    def test_synthesis_no_verdict_reports_no_synthesis_verdict(self, tmp_path):
        repo = RepoConfig(name="test/repo", path=tmp_path)
        daemon = Daemon(Config(repos=[repo]))
        issue = {"number": 8, "title": "t", "body": "spec"}
        ws = WorkerState(repo, issue, "slingshot:review")

        worktree = tmp_path / "wt"
        worktree.mkdir()
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True)
        prompt_file = prompt_dir / "8-review.md"
        prompt_file.write_text("prompt")

        f1 = _make_future(0, _PASS_OUTPUT)
        futures = [f1]

        mock_exec_class = MagicMock()
        mock_exec_instance = MagicMock()
        mock_exec_class.return_value.__enter__.return_value = mock_exec_instance
        mock_exec_instance.submit.side_effect = list(futures)

        mock_as_completed = MagicMock()
        mock_as_completed.return_value = futures

        with patch.object(daemon, "_handle_agent_failure") as mock_fail, \
             patch.object(daemon, "_transition_label"), \
             patch("slingshot.daemon.gh.pr_list_by_head",
                   return_value=[{"number": 10}]), \
             patch("slingshot.daemon.git.fetch_origin"), \
             patch("slingshot.daemon.git.worktree_path",
                   return_value=worktree), \
             patch("slingshot.daemon.git.has_changes", return_value=False), \
             patch("slingshot.daemon.ThreadPoolExecutor", mock_exec_class), \
             patch("slingshot.daemon.as_completed", mock_as_completed), \
             patch("slingshot.daemon._run_agent",
                   return_value=(0, "no json here")), \
             patch("slingshot.daemon._write_agent_log"), \
             patch("slingshot.daemon._copy_file"):
            daemon._do_review_multi(
                ws, ["cmd1"], 10, worktree,
                prompt_file, "main", False, "spec",
            )

        mock_fail.assert_called_once()
        reason = mock_fail.call_args[0][2]
        assert reason == "no-synthesis-verdict"
