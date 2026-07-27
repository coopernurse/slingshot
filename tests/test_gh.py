from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from slingshot.gh import (
    graphql,
    issue_comments,
    pr_check_status,
    pr_mergeable,
    pr_review_comments,
    pr_review_reply,
    pr_review_threads,
)


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


class TestGraphql:
    def test_basic_query(self):
        data = {"data": {"repository": {"name": "test"}}}
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = graphql("query { repository { name } }")
        assert result == data

    def test_with_variables(self):
        data = {"data": {"repository": {"id": "R_123"}}}
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = graphql(
                "query($id: ID!) { node(id: $id) { id } }",
                {"id": "R_123"},
            )
        assert result == data

    def test_empty_response(self):
        with patch("slingshot.gh.subprocess.run", _run_mock(None)):
            result = graphql("query { field }")
        assert result == {}


class TestPrReviewThreads:
    def test_full_response(self):
        data = {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "id": "TR_123",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "src/main.py",
                                "line": 42,
                                "originalLine": 42,
                                "diffHunk": "@@ -40,6 +40,7 @@",
                                "comments": {
                                    "nodes": [
                                        {
                                            "body": "/slingshot fix this",
                                            "author": {"login": "reviewer"},
                                            "authorAssociation": "COLLABORATOR",
                                            "createdAt": "2024-01-01T00:00:00Z",
                                            "updatedAt": "2024-01-02T00:00:00Z",
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            threads, total = pr_review_threads("owner/repo", 42)
        assert total == 1
        assert len(threads) == 1
        t = threads[0]
        assert t["id"] == "TR_123"
        assert t["isResolved"] is False
        assert t["isOutdated"] is False
        assert t["path"] == "src/main.py"
        assert t["line"] == 42
        assert t["originalLine"] == 42
        assert t["diffHunk"] == "@@ -40,6 +40,7 @@"
        assert len(t["comments"]) == 1
        c = t["comments"][0]
        assert c["body"] == "/slingshot fix this"
        assert c["author"] == "reviewer"
        assert c["authorAssociation"] == "COLLABORATOR"
        assert c["createdAt"] == "2024-01-01T00:00:00Z"
        assert c["updatedAt"] == "2024-01-02T00:00:00Z"

    def test_empty_graphql_response(self):
        with patch("slingshot.gh.subprocess.run", _run_mock(None)):
            threads, total = pr_review_threads("owner/repo", 42)
        assert threads == []
        assert total == 0

    def test_no_pull_request(self):
        data = {"repository": {}}
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            threads, total = pr_review_threads("owner/repo", 42)
        assert threads == []
        assert total == 0

    def test_no_review_threads(self):
        data = {"repository": {"pullRequest": {}}}
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            threads, total = pr_review_threads("owner/repo", 42)
        assert threads == []
        assert total == 0

    def test_thread_with_no_comments(self):
        data = {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "id": "TR_456",
                                "isResolved": True,
                                "isOutdated": True,
                                "path": "src/util.py",
                                "line": 10,
                                "originalLine": None,
                                "diffHunk": "",
                                "comments": {"nodes": []},
                            }
                        ],
                    }
                }
            }
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            threads, total = pr_review_threads("owner/repo", 42)
        assert len(threads) == 1
        assert threads[0]["id"] == "TR_456"
        assert threads[0]["isResolved"] is True
        assert threads[0]["isOutdated"] is True
        assert threads[0]["comments"] == []

    def test_nil_thread_node(self):
        data = {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "totalCount": 2,
                        "nodes": [None, None],
                    }
                }
            }
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            threads, total = pr_review_threads("owner/repo", 42)
        assert threads == []
        assert total == 2

    def test_nil_comment_node(self):
        data = {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "id": "TR_789",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "file.py",
                                "line": 5,
                                "originalLine": 5,
                                "diffHunk": "",
                                "comments": {
                                    "nodes": [
                                        None,
                                        {
                                            "body": "ok",
                                            "author": {"login": "user1"},
                                            "authorAssociation": "OWNER",
                                            "createdAt": "2024-01-01T00:00:00Z",
                                            "updatedAt": "2024-01-01T00:00:00Z",
                                        },
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            threads, total = pr_review_threads("owner/repo", 42)
        assert len(threads) == 1
        assert len(threads[0]["comments"]) == 1
        assert threads[0]["comments"][0]["body"] == "ok"

    def test_comment_author_is_none(self):
        data = {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "id": "TR_N1",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "file.py",
                                "line": 1,
                                "originalLine": 1,
                                "diffHunk": "",
                                "comments": {
                                    "nodes": [
                                        {
                                            "body": "comment",
                                            "author": None,
                                            "authorAssociation": "",
                                            "createdAt": "",
                                            "updatedAt": "",
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            threads, total = pr_review_threads("owner/repo", 42)
        assert len(threads) == 1
        c = threads[0]["comments"][0]
        assert c["body"] == "comment"
        assert c["author"] == ""


class TestPrReviewReply:
    def test_success(self):
        data = {"id": 12345, "body": "Addressed", "url": "https://..."}
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_review_reply("owner/repo", 42, "12345", "Addressed")
        assert result == data

    def test_empty_response(self):
        with patch("slingshot.gh.subprocess.run", _run_mock(None)):
            result = pr_review_reply("owner/repo", 42, "12345", "body")
        assert result is None


class TestPrReviewComments:
    def test_full_response(self):
        data = [
            [
                {
                    "id": 101,
                    "body": "/slingshot fix",
                    "path": "src/app.py",
                    "line": 50,
                    "original_line": 50,
                    "diff_hunk": "@@ -50 +50 @@",
                    "user": {"login": "reviewer1"},
                    "author_association": "COLLABORATOR",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                    "in_reply_to_id": None,
                    "html_url": "https://github.com/owner/repo/pull/42#discussion_r101",
                }
            ]
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_review_comments("owner/repo", 42)
        assert len(result) == 1
        c = result[0]
        assert c["id"] == "101"
        assert c["body"] == "/slingshot fix"
        assert c["path"] == "src/app.py"
        assert c["line"] == 50
        assert c["original_line"] == 50
        assert c["diff_hunk"] == "@@ -50 +50 @@"
        assert c["author"] == "reviewer1"
        assert c["author_association"] == "COLLABORATOR"
        assert c["created_at"] == "2024-01-01T00:00:00Z"
        assert c["updated_at"] == "2024-01-02T00:00:00Z"
        assert c["in_reply_to_id"] is None
        assert c["html_url"] == "https://github.com/owner/repo/pull/42#discussion_r101"

    def test_empty_response(self):
        with patch("slingshot.gh.subprocess.run", _run_mock(None)):
            result = pr_review_comments("owner/repo", 42)
        assert result == []

    def test_error_returns_empty_list(self):
        def _failing_run(args, **kwargs):
            raise subprocess.CalledProcessError(1, args, output="", stderr="error")

        with patch("slingshot.gh.subprocess.run", _failing_run):
            result = pr_review_comments("owner/repo", 42)
        assert result == []

    def test_missing_user(self):
        data = [
            [
                {
                    "id": 1,
                    "body": "text",
                    "path": "f.py",
                    "line": 1,
                    "original_line": None,
                    "diff_hunk": "",
                    "user": None,
                    "author_association": "",
                    "created_at": "",
                    "updated_at": "",
                    "in_reply_to_id": None,
                    "html_url": "",
                }
            ]
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_review_comments("owner/repo", 42)
        assert result[0]["author"] == ""

    def test_multiple_pages(self):
        data = [
            [
                {
                    "id": 1,
                    "body": "first",
                    "path": "x.py",
                    "line": 1,
                    "original_line": 1,
                    "diff_hunk": "",
                    "user": {"login": "u1"},
                    "author_association": "OWNER",
                    "created_at": "",
                    "updated_at": "",
                    "in_reply_to_id": None,
                    "html_url": "",
                }
            ],
            [
                {
                    "id": 2,
                    "body": "second",
                    "path": "y.py",
                    "line": 2,
                    "original_line": 2,
                    "diff_hunk": "",
                    "user": {"login": "u2"},
                    "author_association": "MEMBER",
                    "created_at": "",
                    "updated_at": "",
                    "in_reply_to_id": None,
                    "html_url": "",
                }
            ],
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_review_comments("owner/repo", 42)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[0]["author"] == "u1"
        assert result[1]["id"] == "2"
        assert result[1]["author"] == "u2"


class TestIssueCommentsNormalization:
    def test_normalizes_snake_to_camel(self):
        data = [
            [
                {
                    "id": 1,
                    "body": "test comment",
                    "user": {"login": "author1"},
                    "author_association": "OWNER",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                }
            ]
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = issue_comments("owner/repo", 42)
        assert len(result) == 1
        c = result[0]
        assert c["createdAt"] == "2024-01-01T00:00:00Z"
        assert c["updatedAt"] == "2024-01-02T00:00:00Z"
        assert c["authorAssociation"] == "OWNER"
        assert c["author"] == "author1"

    def test_preserves_existing_camelcase(self):
        data = [
            [
                {
                    "id": 1,
                    "body": "test",
                    "user": {"login": "author2"},
                    "authorAssociation": "MEMBER",
                    "createdAt": "2024-01-01T00:00:00Z",
                    "updatedAt": "2024-01-02T00:00:00Z",
                }
            ]
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = issue_comments("owner/repo", 42)
        c = result[0]
        assert c["createdAt"] == "2024-01-01T00:00:00Z"
        assert c["updatedAt"] == "2024-01-02T00:00:00Z"
        assert c["authorAssociation"] == "MEMBER"

    def test_existing_author_not_overwritten(self):
        data = [
            [
                {
                    "id": 1,
                    "body": "test",
                    "author": "existing-author",
                    "user": {"login": "different-login"},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                }
            ]
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = issue_comments("owner/repo", 42)
        c = result[0]
        assert c["author"] == "existing-author"

    def test_user_none_does_not_set_author(self):
        data = [
            [
                {
                    "id": 1,
                    "body": "test",
                    "user": None,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = issue_comments("owner/repo", 42)
        c = result[0]
        assert "author" not in c

    def test_user_empty_dict_does_not_set_author(self):
        data = [
            [
                {
                    "id": 1,
                    "body": "test",
                    "user": {},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = issue_comments("owner/repo", 42)
        c = result[0]
        assert "author" not in c

    def test_multiple_comments(self):
        data = [
            [
                {
                    "id": 1,
                    "body": "first",
                    "user": {"login": "u1"},
                    "author_association": "OWNER",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                },
                {
                    "id": 2,
                    "body": "second",
                    "user": {"login": "u2"},
                    "author_association": "MEMBER",
                    "created_at": "2024-01-03T00:00:00Z",
                    "updated_at": "2024-01-04T00:00:00Z",
                },
            ]
        ]
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = issue_comments("owner/repo", 42)
        assert len(result) == 2
        assert result[0]["author"] == "u1"
        assert result[0]["createdAt"] == "2024-01-01T00:00:00Z"
        assert result[1]["author"] == "u2"
        assert result[1]["authorAssociation"] == "MEMBER"

    def test_error_returns_empty_list(self):
        def _failing_run(args, **kwargs):
            raise subprocess.CalledProcessError(1, args, output="", stderr="error")

        with patch("slingshot.gh.subprocess.run", _failing_run):
            result = issue_comments("owner/repo", 42)
        assert result == []


class TestPrMergeable:
    def test_mergeable(self):
        data = {
            "repository": {
                "pullRequest": {
                    "mergeable": "MERGEABLE",
                }
            }
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_mergeable("owner/repo", 42)
        assert result == "MERGEABLE"

    def test_conflicting(self):
        data = {
            "repository": {
                "pullRequest": {
                    "mergeable": "CONFLICTING",
                }
            }
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_mergeable("owner/repo", 42)
        assert result == "CONFLICTING"

    def test_unknown(self):
        data = {
            "repository": {
                "pullRequest": {
                    "mergeable": "UNKNOWN",
                }
            }
        }
        with patch("slingshot.gh.subprocess.run", _run_mock(data)):
            result = pr_mergeable("owner/repo", 42)
        assert result == "UNKNOWN"

    def test_empty_response(self):
        with patch("slingshot.gh.subprocess.run", _run_mock(None)):
            result = pr_mergeable("owner/repo", 42)
        assert result is None
