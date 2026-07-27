"""Prompt rendering for the implement and review agents."""

from __future__ import annotations

import json

from slingshot.review_items import ReviewItem

REVIEW_FAIL_MARKER = "<!-- slingshot:review-fail -->"
CI_FAIL_MARKER = "<!-- slingshot:ci-fail -->"

IMPLEMENT_SYSTEM = """You are an expert software engineer. Your task is to implement
a specification that will be provided below. Follow it faithfully.
- Write production-quality code.
- Run relevant tests before you finish — fix any failures.
- Do NOT commit, push, or open pull requests. The automation handles that.
- When you are done, report a brief summary of what you changed."""


REVIEW_SYSTEM = """You are an expert code reviewer. You will be given a specification
and a diff (run: git diff origin/{default_branch}...HEAD). Review the implementation
in five dimensions:

1. **Spec fidelity** — does the implementation correctly and completely satisfy
   the spec?
2. **Security** — are there any security vulnerabilities or risky patterns?
3. **Regression risk** — could this change break existing behavior?
4. **Naming & style** — does the code follow the project's conventions?
5. **Test quality** — are tests deterministic and hermetic? Watch for hidden
   assumptions about timezone, locale, clock, network, working directory,
   or execution order — tests must pass in any environment, including CI.

For each dimension, assign a pass/fail status and provide specific notes.
Do NOT modify any files.

Your output MUST end with a fenced JSON block (```json ... ```) in this exact format:

```json
{{
  "verdict": "pass" | "fail",
  "sections": {{
    "spec_fidelity":   {{"status": "pass" | "fail", "notes": "..."}},
    "security":        {{"status": "pass" | "fail", "notes": "..."}},
    "regression_risk": {{"status": "pass" | "fail", "notes": "..."}},
    "naming_style":    {{"status": "pass" | "fail", "notes": "..."}},
    "test_quality":    {{"status": "pass" | "fail", "notes": "..."}}
  }},
  "summary": "..."
}}
```

The overall verdict must be "pass" only if ALL sections pass.  Any section
failure means the overall verdict is "fail"."""


HUMAN_ITEMS_DISPOSITION = """You MUST end your output with a fenced JSON block
(```json ... ```) in this format:

```json
{{
  "items": [
    {{"id": "S1", "action": "fixed|wontfix|unclear", "note": "..."}}
  ]
}}
```

Every assigned item (S1, S2, ...) MUST have an entry.  Missing or invalid
JSON when items are assigned means the run will be treated as a failure."""


REVIEW_HUMAN_ITEMS_EXTENSION = """, "human_items": {{"status": "pass"|"fail",
  "unsolved": [{{"id": "S2", "note": "why"}}]}}"""


def _format_item_block(item: ReviewItem) -> str:
    """Render a single review item for the prompt."""
    lines = [
        f"- **{item.alias}** ({item.kind}, author: {item.author})",
    ]
    if item.path:
        loc = f"`{item.path}:{item.line}`"
        if item.original_line is not None:
            loc += f" (original line: {item.original_line})"
        if item.is_outdated:
            loc += " [OUTDATED]"
        lines.append(f"  Location: {loc}")
    if item.diff_hunk:
        lines.append(f"  Diff hunk:\n```diff\n{item.diff_hunk}\n```")
    lines.append(f"  Body:\n> {item.body}")
    if item.addressed_reply_body:
        # Strip hidden markers for cleaner prompt rendering
        clean_reply = item.addressed_reply_body
        for marker in ("<!-- slingshot:addressed", "<!-- slingshot:disputed"):
            idx = clean_reply.find(marker)
            if idx != -1:
                clean_reply = clean_reply[:idx].strip()
        if clean_reply:
            lines.append(f"  Implementer's reply:\n> {clean_reply}")
    lines.append(f"  URL: {item.url}")
    return "\n".join(lines) + "\n"


def _render_items_section(items: list[ReviewItem], title: str) -> str:
    """Render a section listing review items."""
    if not items:
        return ""
    parts = [f"## {title}", ""]
    for item in items:
        parts.append(_format_item_block(item))
    return "\n".join(parts)


def render_implement_prompt(
    spec: str,
    scenario: str,
    feedback: str | None = None,
    worktree_path: str | None = None,
    items: list[ReviewItem] | None = None,
    is_conflicting: bool = False,
) -> str:
    """Render the implement prompt for *scenario* (fresh/resume/rework).

    *items* are unaddressed /slingshot items to be addressed.
    *is_conflicting* adds merge-conflict instructions at the top.
    """
    if scenario == "fresh":
        instruction = (
            "Implement the specification below from scratch. This is a "
            "greenfield implementation — there is no prior code to continue "
            "or fix."
        )
    elif scenario == "resume":
        instruction = (
            "Continue implementing the specification below. The branch "
            "already contains partial work from a prior run. Pick up "
            "where it left off and complete the remaining work."
        )
    elif scenario == "rework":
        instruction = (
            "Fix the issues identified in the code review below. The branch "
            "contains an implementation that was reviewed and found to have "
            "problems. Address every issue raised in the feedback."
        )
    else:
        raise ValueError(f"Unknown scenario: {scenario}")

    parts = [IMPLEMENT_SYSTEM, "", "## Instructions", "", instruction, ""]

    if worktree_path:
        parts.extend([
            "## Working directory",
            "",
            f"You are working in a git worktree of the repository at "
            f"`{worktree_path}`.",
            "Your current directory IS the repository — all file paths in the "
            "spec are relative to it.",
            "Do NOT create, rename, or switch branches, and do NOT run git "
            "commands against any other checkout of this repository.",
            "",
        ])

    if is_conflicting:
        parts.extend([
            "## Merge Conflicts",
            "",
            "This branch has merge conflicts with the default branch. "
            "Before addressing any other items, merge the latest default "
            "branch into your branch and resolve all conflicts.",
            "",
        ])

    if feedback and not is_conflicting:
        parts.extend(["## Reviewer Feedback", "", feedback, ""])

    if items:
        parts.append(_render_items_section(items, "Human Review Items"))
        parts.extend(["", HUMAN_ITEMS_DISPOSITION, ""])

    parts.extend([
        "## Specification",
        "",
        spec,
        "",
        "## Output",
        "",
        "Summarise the changes you made."])
    return "\n".join(parts)


def render_review_prompt(
    spec: str,
    default_branch: str,
    worktree_path: str | None = None,
    addressed_unresolved: list[ReviewItem] | None = None,
    resolved: list[ReviewItem] | None = None,
) -> str:
    """Render the review prompt.

    The agent is expected to run: git diff origin/{default_branch}...HEAD

    *addressed_unresolved* are items the implementer claimed to have fixed
    but the human hasn't resolved yet (verification needed).
    *resolved* are items the human already resolved (informational only).
    """
    system = REVIEW_SYSTEM.format(default_branch=default_branch)
    parts = [
        system, "",
        "## Instructions",
        "",
        f"1. Run `git diff origin/{default_branch}...HEAD` to see the full diff.",
        "2. Evaluate the diff against the specification below in the four dimensions.",
        "3. Provide specific, actionable feedback for each failing dimension.",
        "4. End your output with the JSON verdict block as described above.",
        "",
    ]

    if worktree_path:
        parts.extend([
            "## Working directory",
            "",
            f"You are working in a git worktree at `{worktree_path}`. Do NOT "
            "run git commands against any other checkout of this repository.",
            "",
        ])

    if addressed_unresolved:
        parts.append(
            _render_items_section(
                addressed_unresolved,
                "Human Review Items — Verification Needed",
            ),
        )
        parts.append(
            "For each item above, verify the implementer's claimed fix "
            "against the diff.  If any item was NOT actually fixed, set "
            "`human_items.status` to `\"fail\"` and list the unsolved items "
            "in `human_items.unsolved`.\n",
        )

    if resolved:
        parts.append(
            _render_items_section(
                resolved, "Human Review Items — Already Resolved (informational)",
            ),
        )

    parts.extend([
        "## Specification",
        "",
        spec,
    ])

    # Append the updated verdict format with human_items extension
    verdict_format = (
        "\n\nYour output MUST end with a fenced JSON block "
        "(```json ... ```) in this exact format:\n\n"
        "```json\n"
        "{{\n"
        "  \"verdict\": \"pass\" | \"fail\",\n"
        '  "sections": {{\n'
        '    "spec_fidelity":   {{"status": "pass" | "fail", "notes": "..."}},\n'
        '    "security":        {{"status": "pass" | "fail", "notes": "..."}},\n'
        '    "regression_risk": {{"status": "pass" | "fail", "notes": "..."}},\n'
        '    "naming_style":    {{"status": "pass" | "fail", "notes": "..."}},\n'
        '    "test_quality":    {{"status": "pass" | "fail", "notes": "..."}}\n'
        "  }},\n"
        '  "human_items": {{"status": "pass" | "fail", '
        '"unsolved": [{{"id": "S2", "note": "why"}}]}},\n'
        '  "summary": "..."\n'
        "}}\n"
        "```"
    )
    parts.append(verdict_format)
    return "\n".join(parts)


def parse_verdict(output: str) -> dict | None:
    """Extract and parse the last fenced JSON block from agent output.

    Returns None if no valid JSON block is found.
    """
    in_fence = False
    json_lines: list[str] = []
    fences: list[list[str]] = []
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```json"):
            in_fence = True
            json_lines = []
            continue
        if stripped == "```" and in_fence:
            in_fence = False
            fences.append(json_lines)
            json_lines = []
            continue
        if in_fence:
            json_lines.append(line)
    # Drain any unclosed fence
    if in_fence and json_lines:
        fences.append(json_lines)

    if not fences:
        return None

    # Take the last one
    text = "\n".join(fences[-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def compute_effective_verdict(verdict_data: dict) -> str:
    """Return 'pass' or 'fail' based on verdict + sections + human_items.

    Effective = "pass" iff verdict=="pass" AND every section passes AND
    human_items.status is not "fail".
    """
    verdict = verdict_data.get("verdict", "fail")
    if verdict != "pass":
        return "fail"
    sections = verdict_data.get("sections", {})
    for _name, sec in sections.items():
        if isinstance(sec, dict) and sec.get("status") != "pass":
            return "fail"
    human_items = verdict_data.get("human_items")
    if isinstance(human_items, dict) and human_items.get("status") == "fail":
        return "fail"
    return "pass"


def format_pass_summary(verdict_data: dict) -> str:
    """Format a review-pass summary comment."""
    sections = verdict_data.get("sections", {})
    lines = ["## Slingshot Review: PASSED", ""]
    for label, display in [
        ("spec_fidelity", "Spec Fidelity"),
        ("security", "Security"),
        ("regression_risk", "Regression Risk"),
        ("naming_style", "Naming & Style"),
    ]:
        sec = sections.get(label, {})
        lines.append(f"**{display}:** {sec.get('status', '?')}")
        notes = sec.get("notes", "")
        if notes:
            lines.append(f"> {notes}")
        lines.append("")
    summary = verdict_data.get("summary", "")
    if summary:
        lines.append(summary)
    return "\n".join(lines)


def format_fail_summary(verdict_data: dict) -> str:
    """Format a review-fail summary comment with hidden marker."""
    sections = verdict_data.get("sections", {})
    lines = [
        REVIEW_FAIL_MARKER,
        "## Slingshot Review: FAILED",
        "",
    ]
    for label, display in [
        ("spec_fidelity", "Spec Fidelity"),
        ("security", "Security"),
        ("regression_risk", "Regression Risk"),
        ("naming_style", "Naming & Style"),
    ]:
        sec = sections.get(label, {})
        status = sec.get("status", "?")
        icon = ":x:" if status == "fail" else ":white_check_mark:"
        lines.append(f"{icon} **{display}:** {status}")
        notes = sec.get("notes", "")
        if notes:
            lines.append(f"> {notes}")
        lines.append("")

    human_items = verdict_data.get("human_items")
    if isinstance(human_items, dict) and human_items.get("status") == "fail":
        unsolved = human_items.get("unsolved", [])
        if unsolved:
            aliases = [it.get("id", "?") for it in unsolved
                       if isinstance(it, dict)]
            lines.append(
                f":x: **Human Review Items (unsolved):** {', '.join(aliases)}",
            )
            for it in unsolved:
                if isinstance(it, dict):
                    note = it.get("note", "")
                    if note:
                        lines.append(f"> {it.get('id', '?')}: {note}")
            lines.append("")

    summary = verdict_data.get("summary", "")
    if summary:
        lines.append(summary)
    return "\n".join(lines)
