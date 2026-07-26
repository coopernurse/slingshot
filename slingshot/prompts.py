"""Prompt rendering for the implement and review agents."""

from __future__ import annotations

import json

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


SYNTHESIS_SYSTEM = """You are an expert code reviewer serving as a synthesis /
tie-breaking agent.
You will be given a specification, a diff, and the raw outputs from N
independent review agents. Your job is to synthesize their findings into a
single unified verdict.

- If all N models agree on the verdict, rubber-stamp the consensus.
- If there is dissent, act as the **tiebreaker**: produce a final verdict,
  explain the minority position in the "dissent" field, and report the
  vote counts in the "voters" field. Every model gets equal weight (one
  vote).

Your output MUST end with a fenced JSON block (```json ... ```) in this
exact format:

```json
{{
  "verdict": "pass" | "fail",
  "voters": {{"pass": 2, "fail": 1}},
  "sections": {{
    "spec_fidelity":   {{"status": "pass" | "fail", "notes": "..."}},
    "security":        {{"status": "pass" | "fail", "notes": "..."}},
    "regression_risk": {{"status": "pass" | "fail", "notes": "..."}},
    "naming_style":    {{"status": "pass" | "fail", "notes": "..."}},
    "test_quality":    {{"status": "pass" | "fail", "notes": "..."}}
  }},
  "dissent": "Model 2 flagged regression_risk: <specific concern about...>",
  "summary": "..."
}}
```

The overall verdict must be "pass" only if ALL sections pass. Any section
failure means the overall verdict is "fail"."""


def render_implement_prompt(
    spec: str,
    scenario: str,
    feedback: str | None = None,
    worktree_path: str | None = None,
) -> str:
    """Render the implement prompt for *scenario* (fresh/resume/rework).

    *feedback* is non-None only for the rework scenario.
    *worktree_path* is the absolute path the agent must work inside.
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

    if feedback:
        parts.extend(["## Reviewer Feedback", "", feedback, ""])

    parts.extend([
        "## Specification",
        "",
        spec,
        "",
        "## Output",
        "",
        "Summarise the changes you made."])
    return "\n".join(parts)


def render_review_prompt(spec: str, default_branch: str,
                        worktree_path: str | None = None) -> str:
    """Render the review prompt.

    The agent is expected to run: git diff origin/{default_branch}...HEAD
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

    parts.extend([
        "## Specification",
        "",
        spec,
    ])
    return "\n".join(parts)


def render_synthesis_prompt(
    spec: str, default_branch: str, review_outputs: list[str],
    worktree_path: str | None = None,
) -> str:
    system = SYNTHESIS_SYSTEM
    parts = [
        system, "",
        "## Instructions",
        "",
        f"1. Run `git diff origin/{default_branch}...HEAD` to see the full diff.",
        "2. Below are the raw outputs from N independent review agents.",
        "3. Synthesize their findings into a single unified verdict.",
        "4. If all N agree, rubber-stamp the consensus.",
        "5. If there is dissent, act as tiebreaker — produce a final verdict,",
        "   explain the minority position in the \"dissent\" field, and report",
        "   vote counts in \"voters\".",
        "6. End your output with the JSON verdict block as described above.",
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

    parts.append("## Specification")
    parts.append("")
    parts.append(spec)
    parts.append("")

    for i, output in enumerate(review_outputs, 1):
        parts.append(f"## Review Agent {i} Output")
        parts.append("")
        parts.append(output)
        parts.append("")

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
    """Return 'pass' or 'fail' based on verdict + sections.

    Effective = "pass" iff verdict=="pass" AND every section passes.
    """
    verdict = verdict_data.get("verdict", "fail")
    sections = verdict_data.get("sections", {})
    if verdict != "pass":
        return "fail"
    for _name, sec in sections.items():
        if isinstance(sec, dict) and sec.get("status") != "pass":
            return "fail"
    return "pass"


def format_pass_summary(verdict_data: dict, voters: dict | None = None,
                       dissent: str | None = None) -> str:
    """Format a review-pass summary comment."""
    sections = verdict_data.get("sections", {})
    header = "## Slingshot Review: PASSED"
    if voters:
        pass_votes = voters.get("pass", 0)
        fail_votes = voters.get("fail", 0)
        total = pass_votes + fail_votes
        if total > 0:
            header = f"## Slingshot Review: PASSED ({pass_votes}/{total})"
    lines = [header, ""]
    for label, display in [
        ("spec_fidelity", "Spec Fidelity"),
        ("security", "Security"),
        ("regression_risk", "Regression Risk"),
        ("naming_style", "Naming & Style"),
        ("test_quality", "Test Quality"),
    ]:
        sec = sections.get(label, {})
        lines.append(f"**{display}:** {sec.get('status', '?')}")
        notes = sec.get("notes", "")
        if notes:
            lines.append(f"> {notes}")
        lines.append("")
    if dissent:
        lines.append("### Dissent")
        lines.append(dissent)
        lines.append("")
    summary = verdict_data.get("summary", "")
    if summary:
        lines.append(summary)
    return "\n".join(lines)


def format_fail_summary(verdict_data: dict, voters: dict | None = None,
                       dissent: str | None = None) -> str:
    """Format a review-fail summary comment with hidden marker."""
    sections = verdict_data.get("sections", {})
    header = "## Slingshot Review: FAILED"
    if voters:
        pass_votes = voters.get("pass", 0)
        fail_votes = voters.get("fail", 0)
        total = pass_votes + fail_votes
        if total > 0:
            header = f"## Slingshot Review: FAILED ({pass_votes}/{total})"
    lines = [
        REVIEW_FAIL_MARKER,
        header,
        "",
    ]
    for label, display in [
        ("spec_fidelity", "Spec Fidelity"),
        ("security", "Security"),
        ("regression_risk", "Regression Risk"),
        ("naming_style", "Naming & Style"),
        ("test_quality", "Test Quality"),
    ]:
        sec = sections.get(label, {})
        status = sec.get("status", "?")
        icon = ":x:" if status == "fail" else ":white_check_mark:"
        lines.append(f"{icon} **{display}:** {status}")
        notes = sec.get("notes", "")
        if notes:
            lines.append(f"> {notes}")
        lines.append("")
    if dissent:
        lines.append("### Dissent")
        lines.append(dissent)
        lines.append("")
    summary = verdict_data.get("summary", "")
    if summary:
        lines.append(summary)
    return "\n".join(lines)
