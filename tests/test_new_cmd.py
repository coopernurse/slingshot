from __future__ import annotations

from slingshot.new_cmd import _extract_title


class TestExtractTitle:
    def test_extracts_first_h1(self):
        text = "# Hello World\n\nsome body text"
        assert _extract_title(text) == "Hello World"

    def test_extracts_h1_with_extra_whitespace(self):
        text = "  #    Padded Title   \nbody"
        assert _extract_title(text) == "Padded Title"

    def test_returns_none_when_no_h1(self):
        text = "## H2 heading\n\ncontent"
        assert _extract_title(text) is None

    def test_returns_none_for_empty_text(self):
        assert _extract_title("") is None

    def test_ignores_non_h1_headings(self):
        text = "## First H2\n\n### H3\n\n# Real H1\n\n## Another H2"
        assert _extract_title(text) == "Real H1"

    def test_matches_bare_hash(self):
        text = "#\n\nnext line"
        assert _extract_title(text) == ""

    def test_no_hash_prefix_is_not_a_heading(self):
        text = "regular text\n# heading"
        assert _extract_title(text) == "heading"
