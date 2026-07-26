"""Wrappers around the `gh` CLI for GitHub operations.

Every function shells out via subprocess and returns parsed results.
All functions may raise CalledProcessError on `gh` failures.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from slingshot.logging import log_cmd_output


def _run(
    args: list[str], *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=60,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        raise subprocess.CalledProcessError(
            result.returncode, args, output=stdout, stderr=stderr
        )
    log_cmd_output(args, result.stdout, result.stderr)
    return result


def _json(args: list[str], *, input_text: str | None = None) -> Any:
    result = _run(args, input_text=input_text)
    return json.loads(result.stdout) if result.stdout.strip() else None


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


def issue_list(repo: str, label: str) -> list[dict]:
    """Return open issues with *label*.

    Returns a list of dicts: number, title, labels (list of str), body, state.
    """
    return _json([
        "gh", "issue", "list", "--repo", repo,
        "--label", label, "--state", "open",
        "--json", "number,title,labels,body,state",
    ]) or []


def issue_get(repo: str, issue_num: int) -> dict | None:
    """Return a single issue dict or None (e.g. 404)."""
    try:
        return _json([
            "gh", "issue", "view", "--repo", repo, str(issue_num),
            "--json", "number,title,labels,body,state",
        ])
    except subprocess.CalledProcessError:
        return None


def issue_create(repo: str, title: str, body: str, labels: list[str]) -> dict:
    """Create an issue. Returns dict with keys: number, url, title."""
    args = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
    for lb in labels:
        args.extend(["--label", lb])
    result = _run(args)
    # gh issue create prints the URL. Parse it and fetch the issue JSON.
    url = result.stdout.strip()
    # Extract issue number from URL: .../issues/42
    num = int(url.rstrip("/").rsplit("/", 1)[-1])
    return {"number": num, "url": url, "title": title}


def issue_reopen(repo: str, issue_num: int) -> None:
    _run(["gh", "issue", "reopen", "--repo", repo, str(issue_num)])


def issue_edit_labels(
    repo: str,
    issue_num: int,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> None:
    """Add and/or remove labels on an issue. All slingshot labels are
    replaced atomically by the caller — this is a low-level wrapper."""
    args = ["gh", "issue", "edit", "--repo", repo, str(issue_num)]
    for lb in (add_labels or []):
        args.extend(["--add-label", lb])
    for lb in (remove_labels or []):
        args.extend(["--remove-label", lb])
    _run(args)


def issue_comment_create(repo: str, issue_num: int, body: str) -> dict:
    """Post a comment on an issue. Returns the REST API comment dict (id, url, ...)."""
    return _json([
        "gh", "api", "-X", "POST",
        f"repos/{repo}/issues/{issue_num}/comments",
        "-f", f"body={body}",
    ]) or {}


def issue_comments(repo: str, issue_num: int) -> list[dict]:
    """Return all comments on an issue (or PR) from every page."""
    try:
        pages = _json([
            "gh", "api", "--paginate", "--slurp",
            f"repos/{repo}/issues/{issue_num}/comments",
        ]) or []
        comments = [c for page in pages for c in page]
    except subprocess.CalledProcessError:
        return []
    # The REST API returns snake_case; normalize to the camelCase fields
    # the rest of the codebase expects.
    for c in comments:
        if "createdAt" not in c and "created_at" in c:
            c["createdAt"] = c["created_at"]
    return comments


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------


def pr_list_by_head(repo: str, head_branch: str, state: str = "open") -> list[dict]:
    """Find PRs whose head branch matches."""
    return _json([
        "gh", "pr", "list", "--repo", repo,
        "--head", head_branch, "--state", state,
        "--json", "number,title,url,headRefName,baseRefName,state,mergedAt",
    ]) or []


def pr_get(repo: str, pr_num: int) -> dict | None:
    try:
        return _json([
            "gh", "pr", "view", "--repo", repo, str(pr_num),
            "--json", "number,title,url,headRefName,baseRefName,state,mergedAt",
        ])
    except subprocess.CalledProcessError:
        return None


def pr_create(
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> dict:
    """Create a PR. Returns dict with number, url."""
    result = _run([
        "gh", "pr", "create", "--repo", repo,
        "--title", title, "--body", body,
        "--head", head, "--base", base,
    ])
    url = result.stdout.strip()
    num = int(url.rstrip("/").rsplit("/", 1)[-1])
    return {"number": num, "url": url}


def pr_comment_create(repo: str, pr_num: int, body: str) -> dict:
    """Post a comment on a PR. Returns the REST API comment dict."""
    return issue_comment_create(repo, pr_num, body)


def pr_merged(repo: str, pr_num: int) -> bool:
    pr = pr_get(repo, pr_num)
    return bool(pr and pr.get("mergedAt"))


def pr_comments(repo: str, pr_num: int) -> list[dict]:
    """Return plain comments on a PR (not inline review comments)."""
    return issue_comments(repo, pr_num)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def label_list(repo: str) -> list[str]:
    """Return list of existing label names."""
    data = _json(["gh", "label", "list", "--repo", repo, "--json", "name"]) or []
    return [item["name"] for item in data]


def label_create(repo: str, name: str, color: str = "0366d6") -> None:
    try:
        _run([
            "gh", "label", "create", "--repo", repo, name, "--color", color,
        ])
    except subprocess.CalledProcessError:
        pass  # already exists or permission denied


# ---------------------------------------------------------------------------
# Repo metadata
# ---------------------------------------------------------------------------


def repo_default_branch(repo: str) -> str:
    """Return the default branch name for *repo*."""
    data = _json(["gh", "repo", "view", "--repo", repo, "--json", "defaultBranchRef"])
    if data and data.get("defaultBranchRef"):
        return data["defaultBranchRef"]["name"]
    return "main"
