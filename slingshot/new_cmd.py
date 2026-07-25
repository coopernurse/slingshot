"""Implementation of the `slingshot new` subcommand."""

from __future__ import annotations

import sys
from pathlib import Path

from slingshot import gh, state


def cmd_new(*, spec: str, repo: str, title: str | None) -> None:
    """`slingshot new --spec <file> --repo <owner/name> [--title <title>]`"""

    spec_path = Path(spec)
    if not spec_path.exists():
        print(f"error: spec file not found: {spec}", file=sys.stderr)
        sys.exit(1)

    spec_text = spec_path.read_text()

    if title:
        issue_title = title
    else:
        issue_title = _extract_title(spec_text)
        if not issue_title:
            print(
                "error: no markdown H1 found in spec. Use --title to provide one.",
                file=sys.stderr,
            )
            sys.exit(1)

    _ensure_slingshot_labels(repo)

    issue = gh.issue_create(
        repo=repo,
        title=issue_title,
        body=spec_text,
        labels=[state.SLINGSHOT_LABELS[0]],  # "slingshot:implement"
    )
    print(issue["url"])


def _extract_title(text: str) -> str | None:
    """Extract the first markdown H1 line (stripping leading # and whitespace)."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") or stripped == "#":
            return stripped.lstrip("#").strip()
    return None


def _ensure_slingshot_labels(repo: str) -> None:
    """Create any slingshot:* labels that don't exist in the repo."""
    existing = set(gh.label_list(repo))
    for label in state.SLINGSHOT_LABELS:
        if label not in existing:
            gh.label_create(repo, label)
