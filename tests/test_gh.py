from __future__ import annotations

import json
from unittest.mock import patch

from slingshot.gh import pr_check_status


def _run_mock(return_data):
    """Create a mock for subprocess.run that returns the given JSON data."""
    stdout_val = json.dumps(return_data) if return_data is not None else ""

    def _mock_run(args, **kwargs):
        class Result:
            returncode = 0
            stdout = stdout_val
            stderr = ""
        return Result()

    return _mock_run


class TestPrCheckStatus:
    def test_checkrun_completed_success(self):
        data = {
            "headRefOid": "abc1234def5678",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "lint",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "detailsUrl": "https://example.com/run/1",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("owner/repo", 42)
        assert result["sha"] == "abc1234def5678"
        assert len(result["checks"]) == 1
        c = result["checks"][0]
        assert c["name"] == "lint"
        assert c["completed"] is True
        assert c["failed"] is False
        assert c["url"] == "https://example.com/run/1"

    def test_checkrun_completed_failure(self):
        data = {
            "headRefOid": "abc1234def5678",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "test",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://example.com/run/2",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("owner/repo", 42)
        c = result["checks"][0]
        assert c["failed"] is True

    def test_checkrun_completed_timed_out(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "slow",
                    "status": "COMPLETED",
                    "conclusion": "TIMED_OUT",
                    "detailsUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["failed"] is True

    def test_checkrun_completed_action_required(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "needs",
                    "status": "COMPLETED",
                    "conclusion": "ACTION_REQUIRED",
                    "detailsUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["failed"] is True

    def test_checkrun_completed_neutral(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "neutral-check",
                    "status": "COMPLETED",
                    "conclusion": "NEUTRAL",
                    "detailsUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["completed"] is True
        assert c["failed"] is False

    def test_checkrun_completed_skipped(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "skip-me",
                    "status": "COMPLETED",
                    "conclusion": "SKIPPED",
                    "detailsUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["completed"] is True
        assert c["failed"] is False

    def test_checkrun_completed_cancelled(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "old-run",
                    "status": "COMPLETED",
                    "conclusion": "CANCELLED",
                    "detailsUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["completed"] is True
        assert c["failed"] is False

    def test_checkrun_pending(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "waiting",
                    "status": "IN_PROGRESS",
                    "conclusion": "",
                    "detailsUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["completed"] is False
        assert c["failed"] is False

    def test_checkrun_queued(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "queued-check",
                    "status": "QUEUED",
                    "conclusion": "",
                    "detailsUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["completed"] is False

    def test_statuscontext_success(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "StatusContext",
                    "context": "ci/circleci",
                    "state": "SUCCESS",
                    "targetUrl": "https://circleci.com/gh/owner/repo/1",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["name"] == "ci/circleci"
        assert c["completed"] is True
        assert c["failed"] is False
        assert c["url"] == "https://circleci.com/gh/owner/repo/1"

    def test_statuscontext_failure(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "StatusContext",
                    "context": "ci/travis",
                    "state": "FAILURE",
                    "targetUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["failed"] is True

    def test_statuscontext_error(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "StatusContext",
                    "context": "ci/jenkins",
                    "state": "ERROR",
                    "targetUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["failed"] is True

    def test_statuscontext_pending(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "StatusContext",
                    "context": "ci/pending",
                    "state": "PENDING",
                    "targetUrl": "",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        c = result["checks"][0]
        assert c["completed"] is False

    def test_no_rollup(self):
        data = {"headRefOid": "abc1234"}
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        assert result["sha"] == "abc1234"
        assert result["checks"] == []

    def test_empty_response(self):
        with patch("slingshot.gh.subprocess.run", _run_mock(None)):
            result = pr_check_status("r", 1)
        assert result["sha"] == ""
        assert result["checks"] == []

    def test_mixed_checkrun_and_statuscontext(self):
        data = {
            "headRefOid": "mixed12",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "lint",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "detailsUrl": "https://ex.com/lint",
                },
                {
                    "__typename": "StatusContext",
                    "context": "ci/travis",
                    "state": "FAILURE",
                    "targetUrl": "https://travis-ci.com/owner/repo/1",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        assert result["sha"] == "mixed12"
        assert len(result["checks"]) == 2
        assert result["checks"][0]["name"] == "lint"
        assert result["checks"][0]["failed"] is False
        assert result["checks"][1]["name"] == "ci/travis"
        assert result["checks"][1]["failed"] is True

    def test_unknown_typename_skipped(self):
        data = {
            "headRefOid": "abc",
            "statusCheckRollup": [
                {
                    "__typename": "CheckSuite",
                    "name": "suite",
                },
            ],
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_check_status("r", 1)
        assert result["checks"] == []
