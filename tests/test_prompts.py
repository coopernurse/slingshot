from __future__ import annotations

from slingshot import prompts
from slingshot.prompts import IMPLEMENT_SYSTEM, REVIEW_SYSTEM
from slingshot.review_items import ReviewItem


class TestParseVerdict:
    def test_well_formed_pass(self):
        output = """
        some preamble
        ```json
        {"verdict": "pass",
         "sections": {"spec_fidelity": {"status": "pass", "notes": "ok"}}}
        ```
        """
        result = prompts.parse_verdict(output)
        assert result is not None
        assert result["verdict"] == "pass"

    def test_well_formed_fail(self):
        output = """
        ```json
        {"verdict": "fail", "sections": {}}
        ```
        """
        result = prompts.parse_verdict(output)
        assert result is not None
        assert result["verdict"] == "fail"

    def test_malformed_json_returns_none(self):
        output = """
        ```json
        {not valid json}
        ```
        """
        result = prompts.parse_verdict(output)
        assert result is None

    def test_garbage_input_returns_none(self):
        result = prompts.parse_verdict("just some random text")
        assert result is None

    def test_no_json_fence_returns_none(self):
        result = prompts.parse_verdict("no fence here")
        assert result is None

    def test_last_fence_wins(self):
        output = """
        ```json
        {"verdict": "fail"}
        ```
        ```json
        {"verdict": "pass"}
        ```
        """
        result = prompts.parse_verdict(output)
        assert result is not None
        assert result["verdict"] == "pass"

    def test_missing_verdict_field(self):
        output = """
        ```json
        {"sections": {}}
        ```
        """
        result = prompts.parse_verdict(output)
        assert result is not None
        assert "verdict" not in result

    def test_empty_sections(self):
        output = """
        ```json
        {"verdict": "pass", "sections": {}}
        ```
        """
        result = prompts.parse_verdict(output)
        assert result is not None
        assert result["sections"] == {}

    def test_unclosed_fence(self):
        output = """
        ```json
        {"verdict": "fail"}
        """
        result = prompts.parse_verdict(output)
        assert result is not None
        assert result["verdict"] == "fail"


class TestComputeEffectiveVerdict:
    def test_pass_with_all_sections_passing(self):
        data = {
            "verdict": "pass",
            "sections": {
                "spec_fidelity": {"status": "pass"},
                "security": {"status": "pass"},
            },
        }
        assert prompts.compute_effective_verdict(data) == "pass"

    def test_fail_when_top_level_is_fail(self):
        data = {"verdict": "fail", "sections": {}}
        assert prompts.compute_effective_verdict(data) == "fail"

    def test_fail_when_section_fails(self):
        data = {
            "verdict": "pass",
            "sections": {
                "spec_fidelity": {"status": "fail"},
                "security": {"status": "pass"},
            },
        }
        assert prompts.compute_effective_verdict(data) == "fail"

    def test_fail_when_verdict_missing_defaults_to_fail(self):
        data = {"sections": {}}
        assert prompts.compute_effective_verdict(data) == "fail"

    def test_non_dict_section_does_not_block_pass(self):
        data = {
            "verdict": "pass",
            "sections": {"spec_fidelity": "not a dict"},
        }
        assert prompts.compute_effective_verdict(data) == "pass"


class TestFormatPassSummary:
    def test_contains_verdict_content(self):
        data = {
            "verdict": "pass",
            "sections": {
                "spec_fidelity": {"status": "pass", "notes": "looks good"},
            },
            "summary": "All good",
        }
        result = prompts.format_pass_summary(data)
        assert "PASSED" in result
        assert "looks good" in result
        assert "All good" in result

    def test_handles_missing_fields(self):
        data = {"verdict": "pass", "sections": {}}
        result = prompts.format_pass_summary(data)
        assert "PASSED" in result
        assert "?" in result


class TestFormatFailSummary:
    def test_contains_verdict_content(self):
        data = {
            "verdict": "fail",
            "sections": {
                "spec_fidelity": {"status": "fail", "notes": "incomplete"},
            },
            "summary": "Fix it",
        }
        result = prompts.format_fail_summary(data)
        assert "FAILED" in result
        assert "slingshot:review-fail" in result
        assert "incomplete" in result
        assert "Fix it" in result

    def test_handles_passing_sections_with_checkmark(self):
        data = {
            "verdict": "fail",
            "sections": {
                "spec_fidelity": {"status": "pass"},
                "security": {"status": "fail"},
            },
        }
        result = prompts.format_fail_summary(data)
        assert ":white_check_mark:" in result
        assert ":x:" in result


class TestImplementPromptAnchoring:
    def test_no_branch_creation_in_fresh(self):
        result = prompts.render_implement_prompt("spec", "fresh")
        assert "Create a new branch" not in result

    def test_worktree_path_in_output(self):
        result = prompts.render_implement_prompt(
            "spec",
            "fresh",
            worktree_path="/tmp/wt",
        )
        assert "/tmp/wt" in result
        assert "Do NOT create" in result

    def test_no_slingshot_dir_in_output(self):
        result = prompts.render_implement_prompt("spec", "fresh")
        assert ".slingshot/" not in result

    def test_no_worktree_section_when_path_none(self):
        result = prompts.render_implement_prompt("spec", "fresh")
        assert "Working directory" not in result


class TestReviewPromptAnchoring:
    def test_worktree_path_in_output(self):
        result = prompts.render_review_prompt("spec", "main", worktree_path="/tmp/wt")
        assert "/tmp/wt" in result
        assert "Do NOT" in result

    def test_no_worktree_section_when_path_none(self):
        result = prompts.render_review_prompt("spec", "main")
        assert "Working directory" not in result


class TestSynthesisPrompt:
    def test_renders_with_multiple_outputs(self):
        result = prompts.render_synthesis_prompt(
            "spec",
            "main",
            ["agent 1 output", "agent 2 output", "agent 3 output"],
        )
        assert "synthesis" in result.lower() or "tie-breaking" in result.lower()
        assert "spec" in result
        assert "agent 1 output" in result
        assert "agent 2 output" in result
        assert "agent 3 output" in result

    def test_includes_diff_instruction(self):
        result = prompts.render_synthesis_prompt(
            "spec",
            "main",
            ["output"],
        )
        assert "git diff" in result
        assert "origin/main...HEAD" in result

    def test_worktree_path_in_output(self):
        result = prompts.render_synthesis_prompt(
            "spec",
            "main",
            ["output"],
            worktree_path="/tmp/wt",
        )
        assert "/tmp/wt" in result
        assert "Do NOT" in result

    def test_no_worktree_section_when_path_none(self):
        result = prompts.render_synthesis_prompt("spec", "main", ["output"])
        assert "Working directory" not in result


class TestFormatPassSummaryWithVoters:
    def test_single_model_no_voters(self):
        data = {
            "verdict": "pass",
            "sections": {
                "spec_fidelity": {"status": "pass", "notes": "ok"},
            },
            "summary": "all good",
        }
        result = prompts.format_pass_summary(data)
        assert "PASSED" in result
        assert "(" not in result  # no voter count

    def test_with_voters(self):
        data = {
            "verdict": "pass",
            "sections": {},
            "summary": "consensus",
        }
        result = prompts.format_pass_summary(data, voters={"pass": 2, "fail": 1})
        assert "PASSED (2/3)" in result

    def test_with_dissent(self):
        data = {
            "verdict": "pass",
            "sections": {},
        }
        result = prompts.format_pass_summary(data, dissent="Model 2 disagreed")
        assert "### Dissent" in result
        assert "Model 2 disagreed" in result

    def test_includes_test_quality(self):
        data = {
            "verdict": "pass",
            "sections": {
                "test_quality": {"status": "pass", "notes": "good tests"},
            },
        }
        result = prompts.format_pass_summary(data)
        assert "Test Quality" in result
        assert "good tests" in result


class TestFormatFailSummaryWithVoters:
    def test_with_voters(self):
        data = {
            "verdict": "fail",
            "sections": {},
        }
        result = prompts.format_fail_summary(data, voters={"pass": 1, "fail": 2})
        assert "FAILED (1/3)" in result
        assert "slingshot:review-fail" in result

    def test_with_dissent(self):
        data = {
            "verdict": "fail",
            "sections": {},
        }
        result = prompts.format_fail_summary(data, dissent="Minority disagrees")
        assert "### Dissent" in result
        assert "Minority disagrees" in result

    def test_includes_test_quality(self):
        data = {
            "verdict": "fail",
            "sections": {
                "test_quality": {"status": "fail", "notes": "no tests"},
            },
        }
        result = prompts.format_fail_summary(data)
        assert "Test Quality" in result
        assert "no tests" in result


class TestImplementPromptWithItems:
    def test_items_section_in_prompt(self):
        items = [
            ReviewItem(
                alias="S1",
                kind="inline",
                author="test-user",
                body="/slingshot fix this",
                path="src/foo.py",
                line=42,
                url="https://github.com/o/r/pull/1#discussion_r1",
            ),
        ]
        result = prompts.render_implement_prompt(
            "spec",
            "rework",
            items=items,
        )
        assert "Human Review Items" in result
        assert "S1" in result
        assert "src/foo.py:42" in result
        assert "fix this" in result
        assert "items" in result.lower()

    def test_no_items_section_when_empty(self):
        result = prompts.render_implement_prompt("spec", "rework")
        assert "Human Review Items" not in result

    def test_conflict_wording(self):
        result = prompts.render_implement_prompt(
            "spec",
            "rework",
            is_conflicting=True,
        )
        assert "Merge Conflicts" in result
        assert "merge" in result.lower()

    def test_conflict_suppresses_feedback(self):
        result = prompts.render_implement_prompt(
            "spec",
            "rework",
            feedback="some feedback",
            is_conflicting=True,
        )
        assert "Merge Conflicts" in result
        assert "some feedback" not in result

    def test_disposition_contract_in_prompt(self):
        items = [
            ReviewItem(
                alias="S1",
                kind="inline",
                author="u",
                body="/slingshot x",
                url="https://github.com/o/r/pull/1",
            ),
        ]
        result = prompts.render_implement_prompt(
            "spec",
            "rework",
            items=items,
        )
        assert '"items"' in result
        assert '"action"' in result
        assert "fixed|wontfix|unclear" in result


class TestReviewPromptWithItems:
    def test_addressed_unresolved_section(self):
        items = [
            ReviewItem(
                alias="S1",
                kind="inline",
                author="user",
                body="/slingshot fix",
                path="src/bar.py",
                line=10,
                url="https://github.com/o/r/pull/1",
            ),
        ]
        result = prompts.render_review_prompt(
            "spec",
            "main",
            addressed_unresolved=items,
        )
        assert "Verification Needed" in result
        assert "S1" in result
        assert "human_items" in result

    def test_resolved_section(self):
        items = [
            ReviewItem(
                alias="S2",
                kind="inline",
                author="user",
                body="/slingshot fixed this",
                path="src/baz.py",
                line=5,
                url="https://github.com/o/r/pull/1",
            ),
        ]
        result = prompts.render_review_prompt(
            "spec",
            "main",
            resolved=items,
        )
        assert "Already Resolved" in result
        assert "S2" in result

    def test_human_items_in_verdict_format(self):
        result = prompts.render_review_prompt("spec", "main")
        assert '"human_items"' in result
        assert '"unsolved"' in result

    def test_addressed_reply_body_in_section(self):
        items = [
            ReviewItem(
                alias="S1",
                kind="inline",
                author="user",
                body="/slingshot fix",
                path="src/bar.py",
                line=10,
                url="https://github.com/o/r/pull/1",
                addressed_reply_body=(
                    "**Fixed** in `abc1234`: changed the null check\n"
                    "<!-- slingshot:addressed node1 -->"
                ),
            ),
        ]
        result = prompts.render_review_prompt(
            "spec",
            "main",
            addressed_unresolved=items,
        )
        assert "Implementer's reply" in result
        assert "changed the null check" in result
        # Hidden markers should be stripped from rendering
        assert "slingshot:addressed" not in result.split("Implementer's reply")[1]


class TestComputeEffectiveVerdictWithHumanItems:
    def test_human_items_fail_defeats_pass(self):
        data = {
            "verdict": "pass",
            "sections": {
                "spec_fidelity": {"status": "pass"},
                "security": {"status": "pass"},
            },
            "human_items": {
                "status": "fail",
                "unsolved": [{"id": "S1", "note": "not fixed"}],
            },
        }
        assert prompts.compute_effective_verdict(data) == "fail"

    def test_human_items_pass_with_all_pass(self):
        data = {
            "verdict": "pass",
            "sections": {
                "spec_fidelity": {"status": "pass"},
                "security": {"status": "pass"},
            },
            "human_items": {"status": "pass", "unsolved": []},
        }
        assert prompts.compute_effective_verdict(data) == "pass"

    def test_human_items_missing_does_not_block(self):
        data = {
            "verdict": "pass",
            "sections": {
                "spec_fidelity": {"status": "pass"},
            },
        }
        assert prompts.compute_effective_verdict(data) == "pass"


class TestFormatFailSummaryWithHumanItems:
    def test_unsolved_items_in_summary(self):
        data = {
            "verdict": "pass",
            "sections": {
                "spec_fidelity": {"status": "pass"},
            },
            "human_items": {
                "status": "fail",
                "unsolved": [
                    {"id": "S1", "note": "still broken"},
                    {"id": "S3", "note": "regression"},
                ],
            },
        }
        result = prompts.format_fail_summary(data)
        assert "S1" in result
        assert "S3" in result
        assert "still broken" in result
        assert "Human Review Items" in result


class TestCustomSystemPrompt:
    CUSTOM = "You are a custom agent. Repo: {repo_name}, issue: {issue_number}."

    def test_implement_uses_custom_prompt(self):
        result = prompts.render_implement_prompt(
            "spec body", "fresh",
            custom_system_prompt=self.CUSTOM.format(
                repo_name="o/r", issue_number=42,
            ),
        )
        assert "You are a custom agent. Repo: o/r, issue: 42." in result
        assert IMPLEMENT_SYSTEM.splitlines()[0] not in result

    def test_implement_falls_back_to_default(self):
        result = prompts.render_implement_prompt("spec body", "fresh")
        assert IMPLEMENT_SYSTEM.splitlines()[0] in result
        assert "custom agent" not in result

    def test_review_uses_custom_prompt(self):
        result = prompts.render_review_prompt(
            "spec body", "main",
            custom_system_prompt=self.CUSTOM.format(
                repo_name="o/r", issue_number=42,
            ),
        )
        assert "You are a custom agent. Repo: o/r, issue: 42." in result
        assert REVIEW_SYSTEM.splitlines()[0] not in result

    def test_review_falls_back_to_default(self):
        result = prompts.render_review_prompt("spec body", "main")
        assert REVIEW_SYSTEM.splitlines()[0] in result
        assert "custom agent" not in result

    def test_custom_prompt_still_appends_dynamic_sections(self):
        result = prompts.render_implement_prompt(
            "spec body", "fresh",
            custom_system_prompt="Custom header.",
            worktree_path="/tmp/wt",
        )
        assert "Custom header." in result
        assert "## Instructions" in result
        assert "## Specification" in result
        assert "## Output" in result
        assert "spec body" in result
        assert "/tmp/wt" in result

    def test_custom_review_still_appends_dynamic_sections(self):
        result = prompts.render_review_prompt(
            "spec body", "main",
            custom_system_prompt="Custom header.",
            worktree_path="/tmp/wt",
        )
        assert "Custom header." in result
        assert "## Instructions" in result
        assert "## Specification" in result
        assert "spec body" in result
        assert "/tmp/wt" in result

    def test_custom_none_same_as_default(self):
        default_result = prompts.render_implement_prompt("spec body", "fresh")
        none_result = prompts.render_implement_prompt(
            "spec body", "fresh", custom_system_prompt=None,
        )
        assert default_result == none_result

    def test_custom_implement_with_items(self):
        items = [
            ReviewItem(
                alias="S1", kind="inline", author="test-user",
                body="/slingshot fix this", path="src/foo.py", line=42,
                url="https://github.com/o/r/pull/1#discussion_r1",
            ),
        ]
        result = prompts.render_implement_prompt(
            "spec body", "rework", items=items,
            custom_system_prompt="Custom system prompt.",
        )
        assert "Custom system prompt." in result
        assert "Human Review Items" in result
        assert "S1" in result

    def test_custom_review_with_items(self):
        items = [
            ReviewItem(
                alias="S1", kind="inline", author="user",
                body="/slingshot fix", path="src/bar.py", line=10,
                url="https://github.com/o/r/pull/1",
            ),
        ]
        result = prompts.render_review_prompt(
            "spec body", "main",
            custom_system_prompt="Custom system prompt.",
            addressed_unresolved=items,
        )
        assert "Custom system prompt." in result
        assert "Verification Needed" in result
        assert "S1" in result
