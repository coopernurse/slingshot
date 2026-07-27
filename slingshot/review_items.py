"""Review items — fetch, qualify, and partition /slingshot PR comments."""

from __future__ import annotations

import json
from dataclasses import dataclass

from slingshot import gh
from slingshot import logging as log

ADDRESSED_MARKER = "<!-- slingshot:addressed "
DISPUTED_MARKER = "<!-- slingshot:disputed "

_TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


@dataclass
class ReviewItem:
    alias: str = ""  # "S1", "S2", ... assigned sequentially
    kind: str = ""  # "inline" | "conversation"

    # Identity
    thread_node_id: str = ""  # GraphQL node ID for inline
    comment_id: str = ""  # REST review-comment id (for inline replies)
    conversation_comment_id: str = ""  # REST issue-comment id (for conv)

    # Content
    body: str = ""
    author: str = ""
    author_association: str = ""
    created_at: str = ""
    updated_at: str = ""
    url: str = ""

    # Inline-only fields
    path: str = ""
    line: int | None = None
    original_line: int | None = None
    is_outdated: bool = False
    is_resolved: bool = False

    # Markers found in replies or daemon summary comment
    addressed_epoch: int = 0
    disputed_epoch: int = 0

    # Body of the most recent addressed reply from the daemon
    addressed_reply_body: str = ""

    def is_qualifying(self, daemon_login: str) -> bool:
        return (
            self.body.strip().startswith("/slingshot")
            and self.author_association.upper() in _TRUSTED_ASSOCIATIONS
            and self.author != daemon_login
        )

    @property
    def updated_epoch(self) -> int:
        return _parse_iso_epoch(self.updated_at)

    @property
    def created_epoch(self) -> int:
        return _parse_iso_epoch(self.created_at)


def _parse_iso_epoch(s: str) -> int:
    if not s:
        return 0
    import datetime

    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.datetime.strptime(s, fmt).replace(
                tzinfo=datetime.UTC,
            )
            return int(dt.timestamp())
        except (ValueError, TypeError):
            pass
    return 0


def _reply_markers(
    replies: list[dict], thread_node_id: str
) -> tuple[int, int, str, str]:
    """Return (addressed_epoch, disputed_epoch, addressed_body, disputed_body)."""
    addr = 0
    disp = 0
    addr_body = ""
    disp_body = ""
    for reply in replies:
        body = reply.get("body", "")
        epoch = _parse_iso_epoch(reply.get("created_at", ""))
        if ADDRESSED_MARKER in body and thread_node_id in body:
            if epoch > addr:
                addr = epoch
                addr_body = body
        if DISPUTED_MARKER in body and thread_node_id in body:
            if epoch > disp:
                disp = epoch
                disp_body = body
    return addr, disp, addr_body, disp_body


def fetch_items(repo: str, pr_num: int) -> tuple[list[ReviewItem], list[ReviewItem]]:
    """Fetch all potential review items from a PR.

    Returns (all_items, conversation_items).  all_items is the union of
    inline and conversation items with aliases assigned sequentially.
    """
    all_items: list[ReviewItem] = []
    alias_counter = [0]

    def _next_alias() -> str:
        alias_counter[0] += 1
        return f"S{alias_counter[0]}"

    # ---- inline review threads ------------------------------------------

    rest_comments = gh.pr_review_comments(repo, pr_num)
    rest_by_key: dict[tuple[str, int], dict] = {}
    for rc in rest_comments:
        path = rc.get("path", "")
        line = rc.get("line")
        if path and line is not None:
            key = (path, int(line))
            if key not in rest_by_key:
                rest_by_key[key] = rc

    threads, thread_total = gh.pr_review_threads(repo, pr_num)
    if thread_total >= 100:
        log.log(
            f"repo={repo} pr={pr_num} "
            f"event=review-threads-truncated "
            f"total_threads={thread_total}"
        )

    for thread in threads:
        tpath = thread.get("path", "") or ""
        tline = thread.get("line")
        if not tpath or tline is None:
            continue

        comments = thread.get("comments", [])
        if not comments:
            continue

        first = comments[0]
        thread_node_id = thread.get("id", "")
        is_resolved = thread.get("isResolved", False)

        # Match REST comment for the review-comment databaseId
        key = (tpath, int(tline) if tline is not None else -1)
        rest_match = rest_by_key.get(key)
        comment_id = rest_match.get("id", "") if rest_match else ""

        alias = _next_alias()
        url = f"https://github.com/{repo}/pull/{pr_num}#discussion_r{comment_id}"

        addr_epoch, disp_epoch, addr_body, _ = _reply_markers(
            comments[1:],
            thread_node_id,
        )
        # Also check for markers in REST replies (daemon replies via REST)
        all_replies = rest_comments_replies(rest_comments, comment_id)
        rest_addr, rest_disp, rest_addr_body, _ = _reply_markers(
            all_replies,
            thread_node_id,
        )
        if rest_addr > addr_epoch:
            addr_epoch = rest_addr
            addr_body = rest_addr_body
        if rest_disp > disp_epoch:
            disp_epoch = rest_disp

        item = ReviewItem(
            alias=alias,
            kind="inline",
            thread_node_id=thread_node_id,
            comment_id=comment_id,
            body=first.get("body", ""),
            author=first.get("author", ""),
            author_association=first.get("authorAssociation", ""),
            created_at=first.get("createdAt", ""),
            updated_at=first.get("updatedAt", ""),
            url=url,
            path=tpath,
            line=tline,
            original_line=thread.get("originalLine"),
            is_outdated=thread.get("isOutdated", False),
            is_resolved=is_resolved,
            addressed_epoch=addr_epoch,
            disputed_epoch=disp_epoch,
            addressed_reply_body=addr_body,
        )
        all_items.append(item)

    # ---- conversation comments ------------------------------------------

    conv_items: list[ReviewItem] = []
    pr_conversation_comments = gh.pr_comments(repo, pr_num)

    # First pass: create items for comments starting with /slingshot
    conv_ids_seen: set[str] = set()
    for c in pr_conversation_comments:
        body = c.get("body", "")
        if not body.strip().startswith("/slingshot"):
            continue
        cid = str(c.get("id", c.get("databaseId", "")))
        conv_ids_seen.add(cid)
        alias = _next_alias()
        conv = ReviewItem(
            alias=alias,
            kind="conversation",
            conversation_comment_id=cid,
            body=body,
            author=c.get("author", ""),
            author_association=c.get("authorAssociation", ""),
            created_at=c.get("createdAt", ""),
            updated_at=c.get("updatedAt", ""),
            url=c.get("html_url", c.get("url", "")),
        )
        all_items.append(conv)
        conv_items.append(conv)

    # For conversation items, scan all PR comments for daemon summary reply
    for conv_item in conv_items:
        cid = conv_item.conversation_comment_id
        if not cid:
            continue
        marker_str = f"conv:{cid}"
        for c in pr_conversation_comments:
            body = c.get("body", "")
            epoch = _parse_iso_epoch(c.get("createdAt", ""))
            if ADDRESSED_MARKER in body and marker_str in body:
                if epoch > conv_item.addressed_epoch:
                    conv_item.addressed_epoch = epoch
                    conv_item.addressed_reply_body = body
            if DISPUTED_MARKER in body and marker_str in body:
                if epoch > conv_item.disputed_epoch:
                    conv_item.disputed_epoch = epoch

    # Second pass: find retracted conversation comments referenced by
    # markers but not yet in the items list.  Retracted means the body
    # no longer starts with /slingshot but a daemon marker exists.
    retracted_ids: set[str] = set()
    for c in pr_conversation_comments:
        body = c.get("body", "")
        for marker_prefix in (ADDRESSED_MARKER, DISPUTED_MARKER):
            idx = 0
            while True:
                idx = body.find(marker_prefix, idx)
                if idx == -1:
                    break
                rest = body[idx + len(marker_prefix) :]
                space_idx = rest.find(" ")
                end_idx = space_idx if space_idx != -1 else None
                ref = rest[:end_idx].strip()
                if ref.startswith("conv:"):
                    ref_id = ref[5:]
                    if ref_id and ref_id not in conv_ids_seen:
                        conv_ids_seen.add(ref_id)
                        retracted_ids.add(ref_id)
                        orig = None
                        for orig_c in pr_conversation_comments:
                            orig_cid = str(
                                orig_c.get("id", orig_c.get("databaseId", "")),
                            )
                            if orig_cid == ref_id:
                                orig = orig_c
                                break
                        if orig is not None:
                            alias = _next_alias()
                            retracted = ReviewItem(
                                alias=alias,
                                kind="conversation",
                                conversation_comment_id=ref_id,
                                body=orig.get("body", ""),
                                author=orig.get("author", ""),
                                author_association=orig.get(
                                    "authorAssociation",
                                    "",
                                ),
                                created_at=orig.get("createdAt", ""),
                                updated_at=orig.get("updatedAt", ""),
                                url=orig.get("html_url", orig.get("url", "")),
                            )
                            all_items.append(retracted)
                            conv_items.append(retracted)
                idx += len(marker_prefix)
                if space_idx != -1:
                    idx = idx - len(marker_prefix) + space_idx

    # Re-scan markers for newly added retracted items
    for conv_item in conv_items:
        cid = conv_item.conversation_comment_id
        if not cid or cid not in retracted_ids:
            continue
        marker_str = f"conv:{cid}"
        for c in pr_conversation_comments:
            body = c.get("body", "")
            epoch = _parse_iso_epoch(c.get("createdAt", ""))
            if ADDRESSED_MARKER in body and marker_str in body:
                if epoch > conv_item.addressed_epoch:
                    conv_item.addressed_epoch = epoch
                    conv_item.addressed_reply_body = body
            if DISPUTED_MARKER in body and marker_str in body:
                if epoch > conv_item.disputed_epoch:
                    conv_item.disputed_epoch = epoch

    return all_items, conv_items


def rest_comments_replies(
    rest_comments: list[dict],
    parent_id: str,
) -> list[dict]:
    """Return REST review comments that are replies to *parent_id*."""
    if not parent_id:
        return []
    result: list[dict] = []
    for c in rest_comments:
        in_reply = c.get("in_reply_to_id")
        if in_reply is not None and str(in_reply) == parent_id:
            result.append(c)
    return result


def qualifying(items: list[ReviewItem], daemon_login: str) -> list[ReviewItem]:
    """Filter *items* to those that are qualifying /slingshot comments."""
    return [it for it in items if it.is_qualifying(daemon_login)]


def get_newest_item_epoch(items: list[ReviewItem]) -> int:
    """Return the maximum created/updated epoch among *items*, or 0."""
    best = 0
    for it in items:
        best = max(best, it.created_epoch, it.updated_epoch)
    return best


def partition(
    items: list[ReviewItem],
) -> tuple[
    list[ReviewItem],
    list[ReviewItem],
    list[ReviewItem],
]:
    """Partition qualified items into (unaddressed, addressed_unresolved, resolved).

    Resolved:
    - Inline: thread is resolved (human clicked "Resolve conversation").
    - Conversation: body no longer starts with /slingshot (retraction).

    Addressed-unresolved:
    - Addressed marker exists, newer than item's updated_at,
      not overridden by a newer disputed marker, thread NOT resolved.

    Unaddressed:
    - No addressed marker, or disputed marker is newest, or
      item was edited after the addressed marker.
    """
    unaddressed: list[ReviewItem] = []
    addressed_unresolved: list[ReviewItem] = []
    resolved: list[ReviewItem] = []

    for item in items:
        if item.kind == "inline" and item.is_resolved:
            resolved.append(item)
            continue
        if item.kind == "conversation" and not item.body.strip().startswith(
            "/slingshot"
        ):
            if item.addressed_epoch > 0 or item.disputed_epoch > 0:
                resolved.append(item)
            continue

        newest_marker_epoch = max(item.addressed_epoch, item.disputed_epoch)
        if newest_marker_epoch == 0:
            unaddressed.append(item)
            continue

        if item.updated_epoch > newest_marker_epoch:
            unaddressed.append(item)
            continue

        if item.disputed_epoch > item.addressed_epoch:
            unaddressed.append(item)
            continue

        if item.addressed_epoch > 0:
            addressed_unresolved.append(item)
        else:
            unaddressed.append(item)

    return unaddressed, addressed_unresolved, resolved


def parse_dispositions(output: str) -> dict | None:
    """Extract and parse the JSON items block from agent output.

    Looks for a fenced JSON block containing an ``"items"`` key and
    returns the parsed dict, or None if not found / invalid.
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
    if in_fence and json_lines:
        fences.append(json_lines)

    for block_lines in reversed(fences):
        text = "\n".join(block_lines)
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "items" in data:
                return data
        except json.JSONDecodeError:
            pass
    return None
