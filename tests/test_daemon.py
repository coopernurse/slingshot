from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from slingshot.config import Config, RepoConfig
from slingshot.daemon import (
    Daemon,
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
