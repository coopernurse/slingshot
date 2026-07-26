from __future__ import annotations

from unittest.mock import patch

from slingshot.review_items import (
    ReviewItem,
    _parse_iso_epoch,
    fetch_items,
    get_newest_item_epoch,
    parse_dispositions,
    partition,
    qualifying,
)


class TestReviewItemQualifying:
    def test_qualifies_valid_slash_slingshot(self):
        item = ReviewItem(
            body="/slingshot fix this",
            author="trusted-user",
            author_association="MEMBER",
        )
        assert item.is_qualifying("daemon-bot")

    def test_qualifies_with_leading_whitespace(self):
        item = ReviewItem(
            body="  /slingshot fix this",
            author="trusted-user",
            author_association="MEMBER",
        )
        assert item.is_qualifying("daemon-bot")

    def test_rejects_wrong_prefix(self):
        item = ReviewItem(
            body="please /slingshot fix this",  # doesn't start with /slingshot
            author="trusted-user",
            author_association="MEMBER",
        )
        assert not item.is_qualifying("daemon-bot")

    def test_rejects_non_trusted_association(self):
        item = ReviewItem(
            body="/slingshot fix this",
            author="random-user",
            author_association="CONTRIBUTOR",
        )
        assert not item.is_qualifying("daemon-bot")

    def test_rejects_daemon_own_user(self):
        item = ReviewItem(
            body="/slingshot fix this",
            author="daemon-bot",
            author_association="MEMBER",
        )
        assert not item.is_qualifying("daemon-bot")

    def test_rejects_body_not_starting_with_slash_slingshot(self):
        item = ReviewItem(
            body="please /slingshot fix this",
            author="trusted-user",
            author_association="OWNER",
        )
        assert not item.is_qualifying("daemon-bot")

    def test_trusted_associations_include_owner(self):
        item = ReviewItem(
            body="/slingshot fix this",
            author="owner-user",
            author_association="OWNER",
        )
        assert item.is_qualifying("daemon-bot")

    def test_trusted_associations_include_collaborator(self):
        item = ReviewItem(
            body="/slingshot fix this",
            author="collab-user",
            author_association="COLLABORATOR",
        )
        assert item.is_qualifying("daemon-bot")


class TestQualifyingFunction:
    def test_filters_non_qualifying_items(self):
        items = [
            ReviewItem(body="/slingshot a", author="good",
                       author_association="MEMBER", alias="S1"),
            ReviewItem(body="not slingshot", author="good",
                       author_association="MEMBER", alias="S2"),
            ReviewItem(body="/slingshot b", author="daemon-bot",
                       author_association="MEMBER", alias="S3"),
        ]
        result = qualifying(items, "daemon-bot")
        assert len(result) == 1
        assert result[0].alias == "S1"


class TestPartition:
    def test_unaddressed_no_markers(self):
        item = ReviewItem(
            alias="S1", kind="inline", thread_node_id="node1",
            body="/slingshot fix this", updated_at="2025-01-01T00:00:00Z",
        )
        unaddr, addr, res = partition([item])
        assert len(unaddr) == 1
        assert unaddr[0].alias == "S1"
        assert addr == []
        assert res == []

    def test_addressed_unresolved(self):
        item = ReviewItem(
            alias="S1", kind="inline", thread_node_id="node1",
            body="/slingshot fix this", updated_at="2025-01-01T00:00:00Z",
            addressed_epoch=1735689601,  # after updated
        )
        unaddr, addr, res = partition([item])
        assert unaddr == []
        assert len(addr) == 1
        assert addr[0].alias == "S1"
        assert res == []

    def test_resolved_inline(self):
        item = ReviewItem(
            alias="S1", kind="inline", thread_node_id="node1",
            body="/slingshot fix this", updated_at="2025-01-01T00:00:00Z",
            is_resolved=True,
        )
        unaddr, addr, res = partition([item])
        assert unaddr == []
        assert addr == []
        assert len(res) == 1
        assert res[0].alias == "S1"

    def test_resolved_conversation_retraction(self):
        item = ReviewItem(
            alias="S1", kind="conversation",
            body="thanks, I removed the prefix",
            updated_at="2025-01-01T00:00:00Z",
        )
        unaddr, addr, res = partition([item])
        assert unaddr == []
        assert addr == []
        assert len(res) == 1

    def test_disputed_beats_addressed(self):
        item = ReviewItem(
            alias="S1", kind="inline", thread_node_id="node1",
            body="/slingshot fix this", updated_at="2025-01-01T00:00:00Z",
            addressed_epoch=1735689601,
            disputed_epoch=1735689610,
        )
        unaddr, addr, res = partition([item])
        assert len(unaddr) == 1
        assert addr == []

    def test_edit_after_addressed_unaddressed(self):
        item = ReviewItem(
            alias="S1", kind="inline", thread_node_id="node1",
            body="/slingshot fix this",
            updated_at="2025-01-02T00:00:00Z",  # epoch > 1735689600
            addressed_epoch=1735689600,  # Jan 1 2025
        )
        unaddr, addr, res = partition([item])
        assert len(unaddr) == 1
        assert addr == []


class TestParseDispositions:
    def test_valid_dispositions(self):
        output = """
        Done with changes.
        ```json
        {"items": [{"id": "S1", "action": "fixed", "note": "done"}]}
        ```
        """
        result = parse_dispositions(output)
        assert result is not None
        assert result["items"][0]["id"] == "S1"
        assert result["items"][0]["action"] == "fixed"

    def test_no_items_block_returns_none(self):
        output = """
        ```json
        {"verdict": "pass"}
        ```
        """
        result = parse_dispositions(output)
        assert result is None

    def test_malformed_json_returns_none(self):
        output = """
        ```json
        {not json}
        ```
        """
        result = parse_dispositions(output)
        assert result is None

    def test_no_json_fence_returns_none(self):
        result = parse_dispositions("no fence here")
        assert result is None

    def test_last_block_with_items_wins(self):
        output = """
        ```json
        {"items": [{"id": "S1", "action": "fixed"}]}
        ```
        ```json
        {"items": [{"id": "S2", "action": "wontfix", "note": "nope"}]}
        ```
        """
        result = parse_dispositions(output)
        assert result is not None
        assert result["items"][0]["id"] == "S2"

    def test_unclosed_fence(self):
        output = """
        ```json
        {"items": [{"id": "S1", "action": "fixed"}]}
        """
        result = parse_dispositions(output)
        assert result is not None
        assert result["items"][0]["id"] == "S1"


class TestGetNewestItemEpoch:
    def test_returns_max_epoch(self):
        items = [
            ReviewItem(created_at="2025-01-01T00:00:00Z",
                       updated_at="2025-01-01T00:00:00Z"),
            ReviewItem(created_at="2025-01-02T00:00:00Z",
                       updated_at="2025-01-02T00:00:00Z"),
        ]
        result = get_newest_item_epoch(items)
        assert result == 1735776000  # Jan 2 2025

    def test_returns_zero_for_empty(self):
        assert get_newest_item_epoch([]) == 0


class TestParseIsoEpoch:
    def test_valid_iso(self):
        assert _parse_iso_epoch("2025-01-01T00:00:00Z") == 1735689600

    def test_with_milliseconds(self):
        assert _parse_iso_epoch("2025-01-01T00:00:00.000Z") == 1735689600

    def test_empty_string(self):
        assert _parse_iso_epoch("") == 0

    def test_invalid_string(self):
        assert _parse_iso_epoch("not-a-date") == 0


class TestFetchItems:
    def test_inline_items_from_graphql(self):
        thread_data = [{
            "id": "node_abc",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/foo.py",
            "line": 42,
            "originalLine": None,
            "diffHunk": "@@ -40,6 +40,8 @@",
            "comments": [{
                "body": "/slingshot fix this",
                "author": "trusted",
                "authorAssociation": "MEMBER",
                "createdAt": "2025-01-01T00:00:00Z",
                "updatedAt": "2025-01-01T00:00:00Z",
            }],
        }]
        rest_review = [{
            "id": "1001",
            "body": "/slingshot fix this",
            "path": "src/foo.py",
            "line": 42,
            "original_line": None,
            "diff_hunk": "@@ -40,6 +40,8 @@",
            "author": "trusted",
            "author_association": "MEMBER",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "in_reply_to_id": None,
            "html_url": "https://github.com/o/r/pull/1#discussion_r1001",
        }]
        with patch("slingshot.review_items.gh.pr_review_threads",
                   return_value=thread_data), \
             patch("slingshot.review_items.gh.pr_review_comments",
                   return_value=rest_review), \
             patch("slingshot.review_items.gh.pr_comments", return_value=[]):
            all_items, conv_items = fetch_items("o/r", 1)
            assert len(all_items) == 1
            assert all_items[0].kind == "inline"
            assert all_items[0].path == "src/foo.py"
            assert all_items[0].line == 42
            assert all_items[0].comment_id == "1001"
            assert conv_items == []

    def test_conversation_items_from_rest(self):
        conv_comments = [{
            "id": "2001",
            "body": "/slingshot add tests",
            "author": "trusted",
            "authorAssociation": "MEMBER",
            "createdAt": "2025-01-01T00:00:00Z",
            "updatedAt": "2025-01-01T00:00:00Z",
            "html_url": "https://github.com/o/r/pull/1#issuecomment-2001",
        }]
        with patch("slingshot.review_items.gh.pr_review_threads",
                   return_value=[]), \
             patch("slingshot.review_items.gh.pr_review_comments",
                   return_value=[]), \
             patch("slingshot.review_items.gh.pr_comments",
                   return_value=conv_comments):
            all_items, conv_items = fetch_items("o/r", 1)
            assert len(all_items) == 1
            assert all_items[0].kind == "conversation"
            assert all_items[0].body == "/slingshot add tests"
            assert len(conv_items) == 1

    def test_conv_markers_from_summary_comment(self):
        conv_comments = [
            {
                "id": "2001",
                "body": "/slingshot add tests",
                "author": "trusted",
                "authorAssociation": "MEMBER",
                "createdAt": "2025-01-01T00:00:00Z",
                "updatedAt": "2025-01-01T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/1#issuecomment-2001",
            },
            {
                "id": "2002",
                "body": "summary\n<!-- slingshot:addressed conv:2001 -->",
                "author": "daemon",
                "authorAssociation": "MEMBER",
                "createdAt": "2025-01-02T00:00:00Z",
                "updatedAt": "2025-01-02T00:00:00Z",
            },
        ]
        with patch("slingshot.review_items.gh.pr_review_threads",
                   return_value=[]), \
             patch("slingshot.review_items.gh.pr_review_comments",
                   return_value=[]), \
             patch("slingshot.review_items.gh.pr_comments",
                   return_value=conv_comments):
            all_items, conv_items = fetch_items("o/r", 1)
            assert len(all_items) == 1
            assert all_items[0].addressed_epoch > 0
