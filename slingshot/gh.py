"""Wrappers around the `gh` CLI for GitHub operations.

Every function shells out via subprocess and returns parsed results.
All functions may raise CalledProcessError on `gh` failures.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


def _run(
    args: list[str],
    *,
    input_text: str | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        capture_output=capture,
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
    return result


def _json(args: list[str], *, input_text: str | None = None) -> Any:
    result = _run(args, input_text=input_text, capture=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


def issue_list(repo: str, label: str) -> list[dict]:
    """Return open issues with *label*.

    Returns a list of dicts: number, title, labels (list of str), body, state.
    """
    return (
        _json(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--label",
                label,
                "--state",
                "open",
                "--json",
                "number,title,labels,body,state",
            ]
        )
        or []
    )


def issue_get(repo: str, issue_num: int) -> dict | None:
    """Return a single issue dict or None (e.g. 404)."""
    try:
        return _json(
            [
                "gh",
                "issue",
                "view",
                "--repo",
                repo,
                str(issue_num),
                "--json",
                "number,title,labels,body,state",
            ]
        )
    except subprocess.CalledProcessError:
        return None


def issue_create(repo: str, title: str, body: str, labels: list[str]) -> dict:
    """Create an issue. Returns dict with keys: number, url, title."""
    args = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
    for lb in labels:
        args.extend(["--label", lb])
    result = _run(args, capture=True)
    # gh issue create prints the URL. Parse it and fetch the issue JSON.
    url = result.stdout.strip()
    # Extract issue number from URL: .../issues/42
    num = int(url.rstrip("/").rsplit("/", 1)[-1])
    return {"number": num, "url": url, "title": title}


def issue_reopen(repo: str, issue_num: int) -> None:
    _run(["gh", "issue", "reopen", "--repo", repo, str(issue_num)], capture=False)


def issue_edit_labels(
    repo: str,
    issue_num: int,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> None:
    """Add and/or remove labels on an issue. All slingshot labels are
    replaced atomically by the caller — this is a low-level wrapper."""
    args = ["gh", "issue", "edit", "--repo", repo, str(issue_num)]
    for lb in add_labels or []:
        args.extend(["--add-label", lb])
    for lb in remove_labels or []:
        args.extend(["--remove-label", lb])
    _run(args, capture=False)


def issue_comment_create(repo: str, issue_num: int, body: str) -> dict:
    """Post a comment on an issue. Returns the REST API comment dict (id, url, ...)."""
    return (
        _json(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{repo}/issues/{issue_num}/comments",
                "-f",
                f"body={body}",
            ]
        )
        or {}
    )


def issue_comments(repo: str, issue_num: int) -> list[dict]:
    """Return all comments on an issue (or PR) from every page."""
    try:
        pages = (
            _json(
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{repo}/issues/{issue_num}/comments",
                ]
            )
            or []
        )
        comments = [c for page in pages for c in page]
    except subprocess.CalledProcessError:
        return []
    # The REST API returns snake_case; normalize to the camelCase fields
    # the rest of the codebase expects.
    for c in comments:
        if "createdAt" not in c and "created_at" in c:
            c["createdAt"] = c["created_at"]
        if "updatedAt" not in c and "updated_at" in c:
            c["updatedAt"] = c["updated_at"]
        if "authorAssociation" not in c and "author_association" in c:
            c["authorAssociation"] = c["author_association"]
        if "author" not in c:
            user = c.get("user", {})
            if isinstance(user, dict) and user.get("login"):
                c["author"] = user["login"]
    return comments


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------


def pr_list_by_head(repo: str, head_branch: str, state: str = "open") -> list[dict]:
    """Find PRs whose head branch matches."""
    return (
        _json(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                head_branch,
                "--state",
                state,
                "--json",
                "number,title,url,headRefName,baseRefName,state,mergedAt",
            ]
        )
        or []
    )


def pr_get(repo: str, pr_num: int) -> dict | None:
    try:
        return _json(
            [
                "gh",
                "pr",
                "view",
                "--repo",
                repo,
                str(pr_num),
                "--json",
                "number,title,url,headRefName,baseRefName,state,mergedAt",
            ]
        )
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
    result = _run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--body",
            body,
            "--head",
            head,
            "--base",
            base,
        ],
        capture=True,
    )
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
        _run(
            [
                "gh",
                "label",
                "create",
                "--repo",
                repo,
                name,
                "--color",
                color,
            ],
            capture=False,
        )
    except subprocess.CalledProcessError:
        pass  # already exists or permission denied


def ensure_labels(repo: str, labels: list[str]) -> None:
    existing = set(label_list(repo))
    for label in labels:
        if label not in existing:
            label_create(repo, label)


# ---------------------------------------------------------------------------
# Review threads (GraphQL) and mergeable check
# ---------------------------------------------------------------------------


def graphql(query: str, variables: dict | None = None) -> dict | None:
    """Run a GraphQL query via ``gh api graphql``.

    Returns the ``data`` portion of the GraphQL response, or None on error.
    """
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    if variables:
        for key, value in variables.items():
            if isinstance(value, bool):
                args.extend(["-F", f"{key}={json.dumps(value)}"])
            else:
                args.extend(["-F", f"{key}={value}"])
    raw = _json(args)
    if raw is None:
        return None
    return raw.get("data")


_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        totalCount
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          originalLine
          comments(first: 10) {
            nodes {
              body
              author { login }
              authorAssociation
              createdAt
              updatedAt
            }
          }
        }
      }
    }
  }
}
"""


def pr_review_threads(repo: str, pr_num: int) -> tuple[list[dict], int]:
    """Return review threads for a PR via GraphQL.

    Returns (threads, total_count).  *threads* is a list of thread dicts
    with keys: id, isResolved, isOutdated, path, line, originalLine,
    comments (list of comment dicts).  Each comment has: body,
    author, authorAssociation, createdAt, updatedAt.
    """
    owner, _, name = repo.partition("/")
    data = graphql(
        _REVIEW_THREADS_QUERY,
        {
            "owner": owner,
            "name": name,
            "number": pr_num,
        },
    )
    if not data:
        return [], 0
    repo_node = data.get("repository", {}) or {}
    pr_node = repo_node.get("pullRequest", {}) or {}
    threads = pr_node.get("reviewThreads", {}) or {}
    total_count = threads.get("totalCount", 0)
    nodes = threads.get("nodes", []) or []
    result: list[dict] = []
    for thread in nodes:
        if not thread:
            continue
        comments = []
        for c in (thread.get("comments") or {}).get("nodes", []) or []:
            if not c:
                continue
            author_node = c.get("author") or {}
            comments.append(
                {
                    "body": c.get("body", ""),
                    "author": author_node.get("login", ""),
                    "authorAssociation": c.get("authorAssociation", ""),
                    "createdAt": c.get("createdAt", ""),
                    "updatedAt": c.get("updatedAt", ""),
                }
            )
        result.append(
            {
                "id": thread.get("id", ""),
                "isResolved": thread.get("isResolved", False),
                "isOutdated": thread.get("isOutdated", False),
                "path": thread.get("path", ""),
                "line": thread.get("line"),
                "originalLine": thread.get("originalLine"),
                "comments": comments,
            }
        )
    return result, total_count


def pr_review_reply(repo: str, pr_num: int, comment_id: str, body: str) -> dict | None:
    """Reply to a review-thread comment via REST.

    *comment_id* is the REST API comment id (a numeric string from the
    ``id`` field on a review comment — not the GraphQL node id).
    """
    return _json(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{repo}/pulls/{pr_num}/comments/{comment_id}/replies",
            "-f",
            f"body={body}",
        ]
    )


def pr_review_comments(repo: str, pr_num: int) -> list[dict]:
    """Return all review comments on a PR via REST.

    Returns a list of comment dicts with fields: id, body, path, line,
    original_line, diff_hunk, user (login), author_association,
    created_at, updated_at, in_reply_to_id, html_url.
    """
    try:
        pages = (
            _json(
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{repo}/pulls/{pr_num}/comments",
                ]
            )
            or []
        )
        raw_comments = [c for page in pages for c in page]
    except subprocess.CalledProcessError:
        return []
    result: list[dict] = []
    for c in raw_comments:
        user = c.get("user", {}) or {}
        result.append(
            {
                "id": str(c.get("id", "")),
                "body": c.get("body", ""),
                "path": c.get("path", ""),
                "line": c.get("line"),
                "original_line": c.get("original_line"),
                "diff_hunk": c.get("diff_hunk", ""),
                "author": user.get("login", ""),
                "author_association": c.get("author_association", ""),
                "created_at": c.get("created_at", ""),
                "updated_at": c.get("updated_at", ""),
                "in_reply_to_id": c.get("in_reply_to_id"),
                "html_url": c.get("html_url", ""),
            }
        )
    return result


_MERGEABLE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) { mergeable }
  }
}
"""


def pr_mergeable(repo: str, pr_num: int) -> str | None:
    """Return the mergeability of a PR: MERGEABLE, CONFLICTING,
    UNKNOWN, or None on error.
    """
    owner, _, name = repo.partition("/")
    data = graphql(
        _MERGEABLE_QUERY,
        {
            "owner": owner,
            "name": name,
            "number": pr_num,
        },
    )
    try:
        return (
            (data or {}).get("repository", {}).get("pullRequest", {}).get("mergeable")
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Check status
# ---------------------------------------------------------------------------


def pr_check_status(repo: str, pr_num: int) -> dict:
    """Return {"sha": <head SHA>, "checks": [{"name", "completed", "failed", "url"}]}.

    Uses ``gh pr view`` with ``--json headRefOid,statusCheckRollup`` (not
    ``gh pr checks``, which exits non-zero on failure).
    """
    raw = (
        _json(
            [
                "gh",
                "pr",
                "view",
                "--repo",
                repo,
                str(pr_num),
                "--json",
                "headRefOid,statusCheckRollup",
            ]
        )
        or {}
    )
    sha = raw.get("headRefOid") or ""
    checks: list[dict] = []
    rollup = raw.get("statusCheckRollup") or []
    for item in rollup:
        ctx = item.get("__typename") or ""
        # --- CheckRun shape ---
        if ctx == "CheckRun" or ctx == "":
            name = item.get("name", "")
            status = item.get("status", "")
            conclusion = item.get("conclusion", "")
            url = item.get("detailsUrl", "")
            completed = status == "COMPLETED"
            failed = completed and conclusion in (
                "FAILURE",
                "TIMED_OUT",
                "ACTION_REQUIRED",
            )
        # --- StatusContext shape ---
        elif ctx == "StatusContext":
            name = item.get("context", "")
            state = item.get("state", "")
            url = item.get("targetUrl", "")
            completed = state in ("SUCCESS", "FAILURE", "ERROR")
            failed = state in ("FAILURE", "ERROR")
        else:
            continue
        checks.append(
            {
                "name": name,
                "completed": completed,
                "failed": failed,
                "url": url,
            }
        )
    return {"sha": sha, "checks": checks}


# ---------------------------------------------------------------------------
# Repo metadata
# ---------------------------------------------------------------------------


def repo_default_branch(repo: str) -> str:
    """Return the default branch name for *repo*."""
    data = _json(["gh", "repo", "view", "--repo", repo, "--json", "defaultBranchRef"])
    if data and data.get("defaultBranchRef"):
        return data["defaultBranchRef"]["name"]
    return "main"
