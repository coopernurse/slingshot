from __future__ import annotations

from slingshot.daemon import _comment_age, _comment_epoch, _newest_claim_comment


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
