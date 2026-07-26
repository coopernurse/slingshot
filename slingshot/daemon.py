"""The `slingshot daemon` — polling loop, claim management, and worker orchestration."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

from slingshot import gh, prompts, review_items, state
from slingshot import git_utils as git
from slingshot import logging as log
from slingshot.config import Config, RepoConfig
from slingshot.prompts import CI_FAIL_MARKER, REVIEW_FAIL_MARKER
from slingshot.review_items import ADDRESSED_MARKER

# ---------------------------------------------------------------------------
# Worker status tracking
# ---------------------------------------------------------------------------

class WorkerState:
    __slots__ = (
        "repo", "issue_num", "issue_title", "issue_body",
        "phase", "thread", "start_time",
        "proc", "aborted",
    )

    def __init__(
        self, repo: RepoConfig, issue: dict, phase: str
    ) -> None:
        self.repo = repo
        self.issue_num = issue["number"]
        self.issue_title = issue.get("title", "")
        self.issue_body = issue.get("body", "")
        self.phase = phase           # work state: "implement" | "review"
        self.thread: threading.Thread | None = None
        self.start_time: float = 0.0
        self.proc: subprocess.Popen | None = None
        self.aborted = False

    def key(self) -> str:
        return f"{self.repo.name}/{self.issue_num}/{self.phase}"

    @property
    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    @property
    def flight_label(self) -> str:
        """The in-flight label currently on the issue."""
        # e.g. 'slingshot:implementing'
        return state.WORK_TO_FLIGHT[self.phase]

    def branch_name(self) -> str:
        return f"slingshot/{self.issue_num}"


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class Daemon:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.daemon_id = f"{socket.gethostname()}-{uuid.uuid4()}"
        self._workers: dict[str, WorkerState] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._debounce: dict[str, float] = {}

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, *, once: bool = False) -> None:
        log.log(f"event=start daemon={self.daemon_id}")

        for repo in self.config.repos:
            git.prune_orphan_worktrees(repo.path, self._protected_issue_nums(repo))

        while not self._stop.is_set():
            self._poll_cycle()
            if once:
                break
            self._stop.wait(timeout=self.config.poll_interval_seconds)

        self._shutdown()

    def _shutdown(self) -> None:
        log.log("event=shutdown")
        with self._lock:
            workers = [w for w in self._workers.values() if w.is_alive]
        for w in workers:
            log.log(f"repo={w.repo.name} issue={w.issue_num} event=shutdown-abandon")

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def _poll_cycle(self) -> None:
        with self._lock:
            finished = [k for k, w in self._workers.items() if not w.is_alive]
            for k in finished:
                del self._workers[k]

        for repo in self.config.repos:
            self._reap(repo)
            self._abort_check(repo)
        for repo in self.config.repos:
            self._review_items_check(repo)
        for repo in self.config.repos:
            self._ci_check(repo)
        for repo in self.config.repos:
            self._claim_work(repo)

        for repo in self.config.repos:
            git.prune_orphan_worktrees(repo.path, self._protected_issue_nums(repo))

    # ------------------------------------------------------------------
    # Reaper
    # ------------------------------------------------------------------

    def _reap(self, repo: RepoConfig) -> None:
        timeout_seconds = self.config.claim_timeout_minutes * 60
        for flight_state in sorted(state.IN_FLIGHT_STATES):
            try:
                issues = gh.issue_list(repo.name, flight_state)
            except Exception:
                continue
            for issue in issues:
                issue_num = issue["number"]
                if self._local_worker_holds(repo.name, issue_num, flight_state):
                    continue
                comments = gh.issue_comments(repo.name, issue_num)
                newest = _newest_claim_comment(comments, flight_state)
                if newest is None:
                    continue
                age = _comment_age(newest.get("createdAt", ""))
                if age is not None and age >= timeout_seconds:
                    preclaim = state.FLIGHT_TO_PRECLAIM[flight_state]
                    self._transition_label(repo, issue_num, flight_state, preclaim)
                    log.log_reap(repo.name, issue_num, flight_state, preclaim)

    # ------------------------------------------------------------------
    # Abort on external mutation
    # ------------------------------------------------------------------

    def _abort_check(self, repo: RepoConfig) -> None:
        with self._lock:
            workers = [w for w in self._workers.values()
                       if w.is_alive and w.repo.name == repo.name]
        for ws in workers:
            issue = gh.issue_get(repo.name, ws.issue_num)
            if issue is None:
                continue
            if (issue.get("state") or "").upper() != "OPEN":
                self._abort_worker(ws, "issue-closed")
                continue

            # Label-mismatch: if the issue's slingshot label changed beneath
            # a live worker, exit without writing any transition.
            current_labels = {lb["name"] for lb in issue.get("labels", [])
                              if lb["name"].startswith(state.SLINGSHOT_LABEL_PREFIX)}
            expected = {ws.flight_label}
            if current_labels and current_labels != expected:
                self._abort_worker(ws, "label-mismatch")
                continue

            if ws.phase == "slingshot:implement":
                continue
            prs = gh.pr_list_by_head(repo.name, ws.branch_name(), state="all")
            if prs:
                pr = prs[0]
                pr_state = (pr.get("state") or "").upper()
                if pr.get("mergedAt") or pr_state in ("CLOSED", "MERGED"):
                    self._abort_worker(ws, "pr-closed")

    def _abort_worker(self, ws: WorkerState, reason: str) -> None:
        ws.aborted = True
        log.log_abort(ws.repo.name, ws.issue_num, reason)
        if ws.proc is not None and ws.proc.poll() is None:
            ws.proc.terminate()
            try:
                ws.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ws.proc.kill()
                ws.proc.wait()
        try:
            issue = gh.issue_get(ws.repo.name, ws.issue_num)
            if issue:
                sl_labels = [lb["name"] for lb in issue.get("labels", [])
                             if lb["name"].startswith(state.SLINGSHOT_LABEL_PREFIX)]
                if sl_labels:
                    gh.issue_edit_labels(
                        ws.repo.name, ws.issue_num, remove_labels=sl_labels,
                    )
        except Exception:
            pass
        git.worktree_remove(ws.repo.path, ws.issue_num)
        with self._lock:
            self._workers.pop(ws.key(), None)

    # ------------------------------------------------------------------
    # Review-items watcher
    # ------------------------------------------------------------------

    def _review_items_check(self, repo: RepoConfig) -> None:
        for watch_state in sorted(state.WATCHER_STATES):
            try:
                issues = gh.issue_list(repo.name, watch_state)
            except Exception:
                continue
            for issue in issues:
                issue_num = issue["number"]
                prs = gh.pr_list_by_head(
                    repo.name, f"slingshot/{issue_num}", state="all",
                )
                if not prs:
                    continue
                pr = prs[0]
                if pr.get("mergedAt") or (pr.get("state") or "").upper() in (
                    "CLOSED", "MERGED"
                ):
                    continue
                if (pr.get("state") or "").upper() != "OPEN":
                    continue
                pr_num = pr["number"]

                if self._local_worker_holds(repo.name, issue_num):
                    continue

                if watch_state == "slingshot:blocked":
                    self._check_blocked_unblock(repo, issue_num, pr_num)
                    continue

                if watch_state == "slingshot:awaiting-checks":
                    self._check_awaiting_checks(repo, issue_num, pr_num)
                    continue

                # review and approved states
                self._check_items_bounce(repo, issue_num, pr_num, watch_state)

    def _check_items_bounce(
        self, repo: RepoConfig, issue_num: int, pr_num: int,
        current_state: str,
    ) -> None:
        try:
            all_items, _ = review_items.fetch_items(repo.name, pr_num)
        except Exception:
            return

        qualified = review_items.qualifying(all_items, self._daemon_login())
        if not qualified:
            return

        unaddressed, _, _ = review_items.partition(qualified)
        if not unaddressed:
            return

        debounce_key = f"{repo.name}/{issue_num}"
        if time.time() < self._debounce.get(debounce_key, 0):
            return

        debounce_secs = self.config.comment_debounce_seconds
        self._debounce[debounce_key] = time.time() + debounce_secs
        log.log(
            f"repo={repo.name} issue={issue_num} "
            f"event=items-bounce from={current_state} "
            f"count={len(unaddressed)} debounce={debounce_secs}s"
        )
        self._transition_label(repo, issue_num, current_state, "slingshot:implement")

    def _check_awaiting_checks(
        self, repo: RepoConfig, issue_num: int, pr_num: int,
    ) -> None:
        try:
            checks_status = gh.pr_check_status(repo.name, pr_num)
            mergeable = gh.pr_mergeable(repo.name, pr_num)
            all_items, _ = review_items.fetch_items(repo.name, pr_num)
        except Exception:
            return

        qualified = review_items.qualifying(all_items, self._daemon_login())
        unaddressed, addressed_unresolved, _ = review_items.partition(qualified)

        if unaddressed:
            self._transition_label(
                repo, issue_num, state.AWAITING_CHECKS, "slingshot:implement",
            )
            return

        checks = checks_status.get("checks", [])
        pending = any(not c["completed"] for c in checks)
        failing = any(c["failed"] for c in checks)

        if pending or mergeable == "UNKNOWN" or mergeable is None:
            return

        if failing or mergeable == "CONFLICTING":
            self._transition_label(
                repo, issue_num, state.AWAITING_CHECKS, "slingshot:implement",
            )
            return

        # green ∧ mergeable
        self._transition_label(
            repo, issue_num, state.AWAITING_CHECKS, "slingshot:approved",
        )
        if addressed_unresolved:
            self._post_nudge(repo, issue_num, pr_num, addressed_unresolved)

    def _check_blocked_unblock(
        self, repo: RepoConfig, issue_num: int, pr_num: int,
    ) -> None:
        try:
            all_items, _ = review_items.fetch_items(repo.name, pr_num)
            issue_comments = gh.issue_comments(repo.name, issue_num)
        except Exception:
            return

        qualified = review_items.qualifying(all_items, self._daemon_login())
        if not qualified:
            return

        # Blocked watermark: newest non-claim issue comment timestamp
        blocked_watermark = 0
        for c in sorted(issue_comments,
                        key=lambda x: x.get("createdAt", ""), reverse=True):
            body = c.get("body", "")
            if body.startswith("slingshot-claim:"):
                continue
            blocked_watermark = _comment_epoch(c.get("createdAt", ""))
            break

        newest_item = review_items.get_newest_item_epoch(qualified)
        if newest_item <= blocked_watermark:
            return

        log.log(
            f"repo={repo.name} issue={issue_num} "
            f"event=unblock watermark={blocked_watermark} "
            f"newest_item={newest_item}"
        )
        self._transition_label(
            repo, issue_num, "slingshot:blocked", "slingshot:implement",
        )
        # Post a marker comment on the issue to break the consecutive-error streak
        try:
            gh.issue_comment_create(
                repo.name, issue_num, "<!-- slingshot:human-unblock -->",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CI check (extended for merge conflicts + items)
    # ------------------------------------------------------------------

    def _ci_check(self, repo: RepoConfig) -> None:
        try:
            issues = gh.issue_list(repo.name, "slingshot:approved")
        except Exception:
            return

        for issue in issues:
            issue_num = issue["number"]
            try:
                prs = gh.pr_list_by_head(
                    repo.name, f"slingshot/{issue_num}", state="all",
                )
            except Exception:
                continue

            if not prs:
                continue

            pr = prs[0]
            if pr.get("mergedAt") or (pr.get("state") or "").upper() in (
                "CLOSED", "MERGED"
            ):
                continue

            if (pr.get("state") or "").upper() != "OPEN":
                continue

            pr_num = pr["number"]

            # Check for unaddressed items (new /slingshot comments)
            try:
                all_items, _ = review_items.fetch_items(repo.name, pr_num)
                qualified = review_items.qualifying(
                    all_items, self._daemon_login(),
                )
                unaddressed, _, _ = review_items.partition(qualified)
            except Exception:
                unaddressed = []

            if unaddressed:
                self._transition_label(
                    repo, issue_num, "slingshot:approved", "slingshot:implement",
                )
                continue

            # Check for merge conflicts
            try:
                mergeable = gh.pr_mergeable(repo.name, pr_num)
            except Exception:
                mergeable = None
            if mergeable == "CONFLICTING":
                self._transition_label(
                    repo, issue_num, "slingshot:approved", "slingshot:implement",
                )
                continue

            try:
                status = gh.pr_check_status(repo.name, pr_num)
            except Exception:
                continue

            checks = status.get("checks", [])
            sha = status.get("sha", "")

            if not checks:
                continue

            if any(not c["completed"] for c in checks):
                continue

            failing = [c for c in checks if c["failed"]]
            if not failing:
                continue

            self._handle_ci_failure(repo, issue_num, pr_num, sha, failing)

    def _handle_ci_failure(
        self, repo: RepoConfig, issue_num: int, pr_num: int,
        sha: str, failing_checks: list[dict],
    ) -> None:
        short_sha = sha[:7] if sha else "unknown"

        urls = [f"- [{c['name']}]({c['url']})" if c.get("url") else f"- {c['name']}"
                for c in failing_checks]
        check_list = "\n".join(urls)

        body = (
            f"{CI_FAIL_MARKER}\n"
            f"**CI Failure** on `{short_sha}`\n\n"
            f"Failing checks:\n{check_list}\n\n"
            f"Run `gh run view --repo {repo.name} --log-failed`"
            f" to view logs."
        )

        try:
            comments = gh.pr_comments(repo.name, pr_num)
        except Exception:
            comments = []

        newest_ci_fail = None
        for c in sorted(comments, key=lambda x: x.get("createdAt", ""), reverse=True):
            if CI_FAIL_MARKER in c.get("body", ""):
                newest_ci_fail = c
                break

        if newest_ci_fail is None or short_sha not in newest_ci_fail.get("body", ""):
            try:
                gh.pr_comment_create(repo.name, pr_num, body)
            except Exception:
                pass

        fail_count = sum(
            1 for c in comments
            if REVIEW_FAIL_MARKER in c.get("body", "")
            or CI_FAIL_MARKER in c.get("body", "")
        )
        # include the comment we just posted
        if newest_ci_fail is None or short_sha not in newest_ci_fail.get("body", ""):
            fail_count += 1

        log.log_ci_failure(repo.name, issue_num, pr_num, len(failing_checks))

        if fail_count >= self.config.review_fail_threshold:
            self._transition_label(repo, issue_num,
                                   "slingshot:approved", "slingshot:blocked")
        else:
            self._transition_label(repo, issue_num,
                                   "slingshot:approved", "slingshot:implement")

    # ------------------------------------------------------------------
    # Claim and spawn workers
    # ------------------------------------------------------------------

    def _claim_work(self, repo: RepoConfig) -> None:
        candidates: dict[str, list[dict]] = {}
        total = 0
        for work_state in sorted(state.WORK_STATES):
            try:
                issues = gh.issue_list(repo.name, work_state)
            except Exception:
                issues = []
            candidates[work_state] = issues
            total += len(issues)

        with self._lock:
            repo_active = sum(1 for w in self._workers.values()
                              if w.is_alive and w.repo.name == repo.name)
            active = sum(1 for w in self._workers.values() if w.is_alive)
        log.log_poll(repo.name, total, repo_active)

        available = self.config.max_concurrent - active
        if available <= 0:
            return

        for work_state in sorted(state.WORK_STATES):
            for issue in candidates[work_state]:
                if available <= 0:
                    return
                issue_num = issue["number"]
                if self._local_worker_holds(repo.name, issue_num):
                    continue
                if self._try_claim(repo, issue_num, work_state):
                    ws = WorkerState(repo, issue, work_state)
                    self._spawn_worker(ws)
                    available -= 1

    def _try_claim(self, repo: RepoConfig, issue_num: int, work_state: str) -> bool:
        flight = state.WORK_TO_FLIGHT[work_state]
        self._transition_label(repo, issue_num, work_state, flight)

        ts = datetime.now(UTC).isoformat(timespec="seconds")
        claim_body = f"slingshot-claim: {self.daemon_id} {flight} {ts}"
        try:
            gh.issue_comment_create(repo.name, issue_num, claim_body)
        except Exception as exc:
            log.log(f"repo={repo.name} issue={issue_num} event=claim-error error={exc}")
            return False

        time.sleep(10)

        comments = gh.issue_comments(repo.name, issue_num)
        sorted_comments = sorted(
            comments, key=lambda c: c.get("createdAt", ""), reverse=True,
        )
        newest = _newest_claim_comment(sorted_comments, flight)
        if newest is None:
            self._transition_label(repo, issue_num, flight, work_state)
            return False
        if self.daemon_id in newest.get("body", ""):
            return True

        log.log(f"repo={repo.name} issue={issue_num} event=claim-lost")
        return False

    # ------------------------------------------------------------------
    # Worker spawning and orchestration
    # ------------------------------------------------------------------

    def _spawn_worker(self, ws: WorkerState) -> None:
        log.log_agent_launch(ws.repo.name, ws.issue_num, ws.phase)
        ws.start_time = time.time()
        ws.thread = threading.Thread(target=self._run_worker, args=(ws,), daemon=True)
        ws.thread.start()
        with self._lock:
            self._workers[ws.key()] = ws

    def _run_worker(self, ws: WorkerState) -> None:
        elapsed = 0.0
        try:
            git.fetch_origin(ws.repo.path)

            if ws.phase == "slingshot:implement":
                elapsed = self._do_implement(ws)
            elif ws.phase == "slingshot:review":
                elapsed = self._do_review(ws)
            else:
                raise ValueError(f"unknown worker phase {ws.phase!r}")

            log.log_agent_complete(ws.repo.name, ws.issue_num, ws.phase, elapsed)
        except Exception as exc:
            log.log_agent_failure(ws.repo.name, ws.issue_num, ws.phase, str(exc))
            if not ws.aborted:
                try:
                    self._handle_agent_failure(ws, ws.flight_label, str(exc))
                except Exception:
                    pass
        finally:
            with self._lock:
                self._workers.pop(ws.key(), None)

    # ------------------------------------------------------------------
    # Implement phase
    # ------------------------------------------------------------------

    def _do_implement(self, ws: WorkerState) -> float:
        issue_num = ws.issue_num
        branch = ws.branch_name()
        branch_exists = git.remote_branch_exists(ws.repo.path, branch)

        pr_num, fail_markers = self._find_pr_and_fail_count(ws.repo, branch)
        feedback = None

        # Check for /slingshot items on the PR
        implement_items: list[review_items.ReviewItem] = []
        is_conflicting = False
        if pr_num is not None:
            try:
                all_items, _ = review_items.fetch_items(ws.repo.name, pr_num)
                qualified = review_items.qualifying(
                    all_items, self._daemon_login(),
                )
                unaddressed, _, _ = review_items.partition(qualified)
                implement_items = unaddressed
            except Exception:
                pass
            try:
                mergeable = gh.pr_mergeable(ws.repo.name, pr_num)
                if mergeable == "CONFLICTING":
                    is_conflicting = True
            except Exception:
                pass

        if branch_exists:
            if (fail_markers > 0 or implement_items) and pr_num is not None:
                last_push = git.branch_last_commit_epoch(ws.repo.path, branch)
                comments = gh.pr_comments(ws.repo.name, pr_num)
                fail_comments: list[str] = []
                for c in comments:
                    body = c.get("body", "")
                    if REVIEW_FAIL_MARKER not in body and CI_FAIL_MARKER not in body:
                        continue
                    if last_push is not None:
                        epoch = _comment_epoch(c.get("createdAt", ""))
                        if epoch is not None and epoch < last_push:
                            continue
                    fail_comments.append(body)
                if fail_comments or implement_items:
                    scenario = "rework"
                    if fail_comments:
                        feedback = "\n\n---\n\n".join(fail_comments)
                else:
                    # Every fail comment and /slingshot item predates the
                    # branch tip.  No unaddressed items exist.  Skip agent run.
                    # Check items live — not by timestamp.
                    # Unresolved items defeat the "done" shortcut.
                    scenario = "done"
            else:
                scenario = "resume"
        else:
            scenario = "fresh"

        if scenario == "done":
            log.log(
                f"repo={ws.repo.name} issue={issue_num} "
                f"event=rework-skip reason=no-fresh-feedback-or-items"
            )
            successor = state.WORK_TO_SUCCESSOR[ws.phase]
            self._transition_label(ws.repo, issue_num, ws.flight_label, successor)
            return 0.0

        spec = ws.issue_body or ""

        if scenario == "fresh":
            worktree = git.create_worktree(
                ws.repo.path, issue_num, self._default_branch_for(ws.repo),
            )
        else:
            worktree = git.create_worktree_from_remote(ws.repo.path, issue_num)

        main_before = git.has_changes(ws.repo.path)

        prompt = prompts.render_implement_prompt(
            spec, scenario, feedback, worktree_path=str(worktree),
            items=implement_items if implement_items else None,
            is_conflicting=is_conflicting,
        )
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = prompt_dir / f"{issue_num}-implement.md"
        prompt_file.write_text(prompt)

        timeout = self.config.agent_timeout_minutes * 60
        start = time.time()
        exit_code, output = _run_agent(
            self.config.agent.implement_command,
            str(prompt_file),
            cwd=str(worktree),
            timeout=timeout,
            ws=ws,
        )
        elapsed = time.time() - start

        if ws.aborted:
            return elapsed

        main_escaped = git.has_changes(ws.repo.path) and not main_before
        if main_escaped:
            if output:
                _write_agent_log(ws.repo.path, issue_num, ws.phase, output)
                tail = _tail(output, 50)
                log.log(
                    f"repo={ws.repo.name} issue={issue_num} "
                    f"event=agent-output tail=\n{tail}"
                )
            log.log_agent_failure(
                ws.repo.name, issue_num, "implement", "agent-escaped-worktree",
            )
            self._handle_agent_failure(
                ws, ws.flight_label, "agent-escaped-worktree",
            )
            return elapsed

        if output:
            _write_agent_log(ws.repo.path, issue_num, ws.phase, output)

        # Archive prompt file to main repo for debugging
        archive_dir = ws.repo.path / ".slingshot" / "prompts"
        archive_dir.mkdir(parents=True, exist_ok=True)
        _copy_file(prompt_file, archive_dir / prompt_file.name)

        # Check item dispositions — must be valid JSON when items are assigned
        dispositions_data = None
        if implement_items:
            dispositions_data = review_items.parse_dispositions(output)
            if not dispositions_data:
                log.log_agent_failure(
                    ws.repo.name, issue_num, "implement", "no-items-disposition",
                )
                self._handle_agent_failure(
                    ws, ws.flight_label, "no-items-disposition",
                )
                return elapsed

        if exit_code != 0 or not git.has_changes(worktree):
            reason = f"exit={exit_code}" if exit_code != 0 else "empty-diff"
            if output:
                tail = _tail(output, 50)
                log.log(
                    f"repo={ws.repo.name} issue={issue_num} "
                    f"event=agent-output tail=\n{tail}"
                )
            log.log_agent_failure(ws.repo.name, issue_num, "implement", reason)
            self._handle_agent_failure(ws, ws.flight_label, reason)
            return elapsed

        commit_msg = f"slingshot: {ws.issue_title} (#{issue_num})"
        if len(commit_msg) > 72:
            commit_msg = commit_msg[:69] + "..."

        try:
            git.commit_changes(worktree, commit_msg)
        except subprocess.CalledProcessError:
            self._handle_agent_failure(ws, ws.flight_label, "commit-failed")
            return elapsed

        try:
            git.push_branch(worktree)
        except subprocess.CalledProcessError:
            self._handle_agent_failure(ws, ws.flight_label, "push-failed")
            return elapsed

        try:
            # On rework/resume the PR already exists from a prior cycle;
            # only create it when no open PR exists for this branch.
            open_prs = gh.pr_list_by_head(ws.repo.name, branch, state="open")
            if not open_prs:
                default_branch = self._default_branch_for(ws.repo)
                pr_title = ws.issue_title or f"Slingshot #{issue_num}"
                pr_body = (
                    f"Closes #{issue_num}\n\nAutomated implementation by slingshot."
                )
                gh.pr_create(
                    ws.repo.name, title=pr_title, body=pr_body,
                    head=branch, base=default_branch,
                )
        except subprocess.CalledProcessError:
            self._handle_agent_failure(ws, ws.flight_label, "pr-create-failed")
            return elapsed

        # Post item replies after successful push
        if dispositions_data and pr_num is not None:
            try:
                self._post_item_replies(
                    ws.repo, issue_num, pr_num, implement_items,
                    dispositions_data,
                )
            except Exception as exc:
                log.log(
                    f"repo={ws.repo.name} issue={issue_num} "
                    f"event=post-replies-error error={exc}"
                )

        successor = state.WORK_TO_SUCCESSOR[ws.phase]
        self._transition_label(ws.repo, issue_num, ws.flight_label, successor)
        return elapsed

    # ------------------------------------------------------------------
    # Review phase
    # ------------------------------------------------------------------

    def _do_review(self, ws: WorkerState) -> float:
        issue_num = ws.issue_num
        branch = ws.branch_name()
        prs = gh.pr_list_by_head(ws.repo.name, branch, state="open")
        if not prs:
            self._handle_agent_failure(ws, ws.flight_label, "no-pr-found")
            return 0.0
        pr_num = prs[0]["number"]

        git.fetch_origin(ws.repo.path)
        worktree = git.worktree_path(ws.repo.path, issue_num)
        if not worktree.exists():
            worktree = git.create_worktree_from_remote(ws.repo.path, issue_num)

        # --- Review-pass gate: check for unaddressed items ---------------
        try:
            all_items, _ = review_items.fetch_items(ws.repo.name, pr_num)
            qualified = review_items.qualifying(all_items, self._daemon_login())
            unaddressed, addressed_unresolved, resolved = review_items.partition(
                qualified,
            )
        except Exception:
            unaddressed, addressed_unresolved, resolved = [], [], []

        if unaddressed:
            log.log(
                f"repo={ws.repo.name} issue={issue_num} "
                f"event=review-gate-bounce reason=unaddressed-items"
            )
            self._transition_label(
                ws.repo, issue_num, ws.flight_label, "slingshot:implement",
            )
            return 0.0

        try:
            checks_status = gh.pr_check_status(ws.repo.name, pr_num)
        except Exception:
            checks_status = {"sha": "", "checks": []}
        checks = checks_status.get("checks", [])
        checks_pending = any(not c["completed"] for c in checks)
        checks_failing = any(c["failed"] for c in checks)

        try:
            mergeable = gh.pr_mergeable(ws.repo.name, pr_num)
        except Exception:
            mergeable = None

        if checks_pending or mergeable == "UNKNOWN" or mergeable is None:
            if checks:
                self._transition_label(
                    ws.repo, issue_num, ws.flight_label, state.AWAITING_CHECKS,
                )
            else:
                self._transition_label(
                    ws.repo, issue_num, ws.flight_label, state.AWAITING_CHECKS,
                )
            return 0.0

        if checks_failing or mergeable == "CONFLICTING":
            self._transition_label(
                ws.repo, issue_num, ws.flight_label, "slingshot:implement",
            )
            return 0.0

        # --- Run the review agent ---------------------------------------

        default_branch = self._default_branch_for(ws.repo)
        spec = ws.issue_body or ""

        main_before = git.has_changes(ws.repo.path)

        prompt = prompts.render_review_prompt(
            spec, default_branch, worktree_path=str(worktree),
            addressed_unresolved=addressed_unresolved if addressed_unresolved
            else None,
            resolved=resolved if resolved else None,
        )
        prompt_dir = worktree / ".slingshot" / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = prompt_dir / f"{issue_num}-review.md"
        prompt_file.write_text(prompt)

        timeout = self.config.agent_timeout_minutes * 60
        start = time.time()
        exit_code, output = _run_agent(
            self.config.agent.review_command,
            str(prompt_file),
            cwd=str(worktree),
            timeout=timeout,
            ws=ws,
        )
        elapsed = time.time() - start

        if ws.aborted:
            return elapsed

        main_escaped = git.has_changes(ws.repo.path) and not main_before
        if main_escaped:
            if output:
                _write_agent_log(ws.repo.path, issue_num, ws.phase, output)
                tail = _tail(output, 50)
                log.log(
                    f"repo={ws.repo.name} issue={issue_num} "
                    f"event=agent-output tail=\n{tail}"
                )
            log.log_agent_failure(
                ws.repo.name, issue_num, "review", "agent-escaped-worktree",
            )
            self._handle_agent_failure(
                ws, ws.flight_label, "agent-escaped-worktree",
            )
            return elapsed

        if output:
            _write_agent_log(ws.repo.path, issue_num, ws.phase, output)

        # Archive prompt file to main repo for debugging
        archive_dir = ws.repo.path / ".slingshot" / "prompts"
        archive_dir.mkdir(parents=True, exist_ok=True)
        _copy_file(prompt_file, archive_dir / prompt_file.name)

        verdict_data = prompts.parse_verdict(output)
        if exit_code != 0 or verdict_data is None:
            reason = f"exit={exit_code}" if exit_code != 0 else "no-verdict"
            if output:
                tail = _tail(output, 50)
                log.log(
                    f"repo={ws.repo.name} issue={issue_num} "
                    f"event=agent-output tail=\n{tail}"
                )
            log.log_agent_failure(ws.repo.name, issue_num, "review", reason)
            self._handle_agent_failure(ws, ws.flight_label, reason)
            return elapsed

        effective = prompts.compute_effective_verdict(verdict_data)
        if effective == "pass":
            summary = prompts.format_pass_summary(verdict_data)
            gh.pr_comment_create(ws.repo.name, pr_num, summary)

            # Post disputed markers for unsolved human items
            human_items = verdict_data.get("human_items")
            if isinstance(human_items, dict) and human_items.get("status") == "fail":
                unsolved = human_items.get("unsolved", [])
                self._post_disputed_replies(ws.repo.name, pr_num, unsolved,
                                           all_items)

            # Check pass-gate again: unaddressed items may have appeared
            # while review was running
            try:
                all_items2, _ = review_items.fetch_items(ws.repo.name, pr_num)
                qualified2 = review_items.qualifying(
                    all_items2, self._daemon_login(),
                )
                unaddr2, addr2, _ = review_items.partition(qualified2)
            except Exception:
                unaddr2, addr2 = [], []

            if unaddr2:
                self._transition_label(
                    ws.repo, issue_num, ws.flight_label, "slingshot:implement",
                )
                return elapsed

            # Re-check checks and mergeable
            try:
                checks2 = gh.pr_check_status(ws.repo.name, pr_num)
                m2 = gh.pr_mergeable(ws.repo.name, pr_num)
            except Exception:
                checks2 = {"sha": "", "checks": []}
                m2 = None

            cks2 = checks2.get("checks", [])
            if any(not c["completed"] for c in cks2) or m2 == "UNKNOWN" or m2 is None:
                self._transition_label(
                    ws.repo, issue_num, ws.flight_label, state.AWAITING_CHECKS,
                )
                return elapsed

            if any(c["failed"] for c in cks2) or m2 == "CONFLICTING":
                self._transition_label(
                    ws.repo, issue_num, ws.flight_label, "slingshot:implement",
                )
                return elapsed

            self._transition_label(
                ws.repo, issue_num, ws.flight_label, "slingshot:approved",
            )
            if addr2:
                self._post_nudge(ws.repo.name, issue_num, pr_num, addr2)
        else:
            summary = prompts.format_fail_summary(verdict_data)
            gh.pr_comment_create(ws.repo.name, pr_num, summary)

            # Post disputed markers for unsolved human items
            human_items = verdict_data.get("human_items")
            if isinstance(human_items, dict) and human_items.get("status") == "fail":
                unsolved = human_items.get("unsolved", [])
                self._post_disputed_replies(ws.repo.name, pr_num, unsolved,
                                           all_items)

            comments = gh.pr_comments(ws.repo.name, pr_num)
            fail_count = sum(
                1 for c in comments
                if REVIEW_FAIL_MARKER in c.get("body", "")
            )
            if fail_count >= self.config.review_fail_threshold:
                self._transition_label(ws.repo, issue_num, ws.flight_label,
                                       "slingshot:blocked")
            else:
                self._transition_label(ws.repo, issue_num, ws.flight_label,
                                       "slingshot:implement")

        return elapsed

    # ------------------------------------------------------------------
    # Review items: reply posting
    # ------------------------------------------------------------------

    def _daemon_login(self) -> str:
        try:
            result = subprocess.run(
                ["gh", "api", "user", "--jq", ".login"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    def _post_item_replies(
        self,
        repo: RepoConfig,
        issue_num: int,
        pr_num: int,
        items: list[review_items.ReviewItem],
        dispositions: dict,
    ) -> None:
        try:
            sha_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=str(git.worktree_path(repo.path, issue_num)),
            )
            short_sha = (
                sha_result.stdout.strip()[:7]
                if sha_result.returncode == 0 else "unknown"
            )
        except Exception:
            short_sha = "unknown"

        disp_list = dispositions.get("items", [])
        disp_map: dict[str, dict] = {}
        for d in disp_list:
            if isinstance(d, dict) and "id" in d:
                disp_map[d["id"]] = d

        inline_replies: list[tuple[str, str]] = []
        conv_summary_parts: list[str] = []
        conv_markers: list[str] = []

        for item in items:
            disp = disp_map.get(item.alias)
            if disp is None:
                continue
            action = disp.get("action", "unclear")
            note = disp.get("note", "")

            if item.kind == "inline" and item.comment_id:
                reply_body = (
                    f"Addressed in `{short_sha}`: {note}\n"
                    f"{ADDRESSED_MARKER}{item.thread_node_id} -->"
                )
                inline_replies.append((item.comment_id, reply_body))

            elif item.kind == "conversation" and item.conversation_comment_id:
                action_label = {"fixed": "Fixed", "wontfix": "Won't fix",
                                "unclear": "Unclear"}.get(action, action)
                conv_summary_parts.append(
                    f"**{item.alias}** ({action_label}): {note}"
                )
                conv_markers.append(
                    f"{ADDRESSED_MARKER}conv:{item.conversation_comment_id} -->"
                )

        for comment_id, reply_body in inline_replies:
            try:
                gh.pr_review_reply(repo.name, pr_num, comment_id, reply_body)
            except Exception:
                pass

        if conv_summary_parts:
            conv_body = (
                "## Slingshot: Human Review Items — Conversation\n\n"
                + "\n\n".join(conv_summary_parts)
                + "\n\n"
                + "\n".join(conv_markers)
            )
            try:
                gh.pr_comment_create(repo.name, pr_num, conv_body)
            except Exception:
                pass

    def _post_disputed_replies(
        self,
        repo_name: str,
        pr_num: int,
        unsolved: list[dict],
        all_items: list[review_items.ReviewItem],
    ) -> None:
        item_map: dict[str, review_items.ReviewItem] = {}
        for it in all_items:
            item_map[it.alias] = it

        for entry in unsolved:
            alias = entry.get("id", "")
            note = entry.get("note", "")
            item = item_map.get(alias)
            if item is None:
                continue
            if item.kind == "inline" and item.comment_id:
                reply_body = (
                    f"<!-- slingshot:disputed {item.thread_node_id} -->\n"
                    f"**Disputed:** {note}"
                )
                try:
                    gh.pr_review_reply(repo_name, pr_num, item.comment_id,
                                       reply_body)
                except Exception:
                    pass

    def _post_nudge(
        self,
        repo_name: str,
        issue_num: int,
        pr_num: int,
        addressed_unresolved: list[review_items.ReviewItem],
    ) -> None:
        count = len(addressed_unresolved)
        body = (
            f"**{count} thread{'s' if count != 1 else ''} awaiting "
            f"your resolution.** Please resolve the addressed review "
            f"threads when ready."
        )
        try:
            gh.pr_comment_create(repo_name, pr_num, body)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def _handle_agent_failure(
        self, ws: WorkerState, current_label: str, reason: str,
    ) -> None:
        preclaim = state.FLIGHT_TO_PRECLAIM.get(current_label, current_label)
        self._transition_label(ws.repo, ws.issue_num, current_label, preclaim)

        err_body = (
            f"<!-- slingshot:agent-error -->\n"
            f"**Slingshot {ws.phase} agent failed:** {reason}\n"
            f"Reverting to `{preclaim}`."
        )
        try:
            gh.issue_comment_create(ws.repo.name, ws.issue_num, err_body)
        except Exception:
            pass

        strike_count = self._count_consecutive_errors(ws.repo.name, ws.issue_num)
        if strike_count >= self.config.agent_failure_threshold:
            self._transition_label(ws.repo, ws.issue_num, preclaim, "slingshot:blocked")

    def _count_consecutive_errors(self, repo: str, issue_num: int) -> int:
        comments = gh.issue_comments(repo, issue_num)
        sorted_comments = sorted(comments, key=lambda c: c.get("createdAt", ""))
        count = 0
        for c in reversed(sorted_comments):
            body = c.get("body", "")
            if "<!-- slingshot:agent-error -->" in body:
                count += 1
            elif body.startswith("slingshot-claim:"):
                # Claim markers are posted before every attempt; they are
                # daemon noise and must not reset the failure streak.
                continue
            else:
                break
        return count

    def _protected_issue_nums(self, repo: RepoConfig) -> set[int]:
        nums: set[int] = set()
        with self._lock:
            for w in self._workers.values():
                if w.is_alive and w.repo.name == repo.name:
                    nums.add(w.issue_num)
        try:
            for issue in gh.issue_list(repo.name, "slingshot:approved"):
                prs = gh.pr_list_by_head(
                    repo.name, f"slingshot/{issue['number']}", state="all",
                )
                if not any(p.get("mergedAt") for p in prs):
                    nums.add(issue["number"])
        except Exception:
            pass
        return nums

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition_label(
        self, repo: RepoConfig, issue_num: int,
        from_state: str | None, to_state: str | None,
    ) -> None:
        remove = [from_state] if from_state else []
        add = [to_state] if to_state else []
        try:
            gh.issue_edit_labels(
                repo.name, issue_num, add_labels=add, remove_labels=remove,
            )
            if from_state and to_state:
                log.log_transition(repo.name, issue_num, from_state, to_state)
        except subprocess.CalledProcessError as e:
            log.log(f"repo={repo.name} issue={issue_num} event=label-error error={e}")

    def _local_worker_holds(self, repo_name: str, issue_num: int,
                            phase: str | None = None) -> bool:
        with self._lock:
            for ws in self._workers.values():
                if ws.repo.name == repo_name and ws.issue_num == issue_num:
                    if phase is None or phase == ws.flight_label:
                        return ws.is_alive
        return False

    def _default_branch_for(self, repo: RepoConfig) -> str:
        try:
            return gh.repo_default_branch(repo.name)
        except Exception:
            pass
        try:
            return git.default_branch(repo.path)
        except Exception:
            return "main"

    def _find_pr_and_fail_count(self, repo: RepoConfig, branch: str):
        prs = gh.pr_list_by_head(repo.name, branch, state="all")
        if not prs:
            return None, 0
        pr_num = prs[0]["number"]
        comments = gh.pr_comments(repo.name, pr_num)
        fail_count = sum(
            1 for c in comments
            if REVIEW_FAIL_MARKER in c.get("body", "")
            or CI_FAIL_MARKER in c.get("body", "")
        )
        return pr_num, fail_count

    def _handle_signal(self, signum, frame) -> None:
        log.log(f"event=signal signal={signal.Signals(signum).name}")
        self._stop.set()


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def _write_agent_log(
    repo_path, issue_num, phase, output,
):
    log_dir = repo_path / ".slingshot" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    short_phase = phase.split(":")[-1]
    log_path = log_dir / f"{issue_num}-{short_phase}-{ts}.log"
    log_path.write_text(output)
    return log_path


def _copy_file(src, dst):
    shutil.copy2(src, dst)


def _tail(text, n):
    lines = text.splitlines()
    return "\n".join(lines[-n:])


# opencode anchors project config discovery at the repo's common git dir,
# which for a linked worktree is the *main* checkout — so an opencode.json
# written into the worktree is never loaded, and the whole worktree counts
# as an "external directory". Inject the config via the environment instead.
_OPENCODE_CONFIG_CONTENT = json.dumps({
    "$schema": "https://opencode.ai/config.json",
    "permission": {"external_directory": "allow"},
})

# How often to wake up and check the wall-clock deadline while waiting for
# the agent. Kept small so a deadline that passes while the machine is
# asleep is enforced soon after wake.
_TIMEOUT_CHECK_SECONDS = 30


def _run_agent(
    command_template: str, prompt_file: str, *,
    cwd: str, timeout: int, ws: WorkerState,
) -> tuple[int, str]:
    try:
        parts = shlex.split(command_template)
    except ValueError:
        parts = command_template.split()

    cmd = [prompt_file if p == "{prompt_file}" else p for p in parts]

    env = os.environ.copy()
    env["OPENCODE_CONFIG_CONTENT"] = _OPENCODE_CONFIG_CONTENT

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd, env=env,
        )
    except FileNotFoundError:
        return -2, "agent command not found"

    ws.proc = proc
    # Enforce a wall-clock deadline. subprocess timeouts run on a monotonic
    # clock that pauses while the machine is asleep, which can stretch a
    # 30-minute timeout into many hours on a laptop.
    deadline = time.time() + timeout
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                proc.kill()
                proc.wait()
                return -1, "agent timed out"
            try:
                out, err = proc.communicate(
                    timeout=min(remaining, _TIMEOUT_CHECK_SECONDS),
                )
                return proc.returncode, (out or "") + (err or "")
            except subprocess.TimeoutExpired:
                continue
    finally:
        ws.proc = None


# ---------------------------------------------------------------------------
# Comment helpers
# ---------------------------------------------------------------------------

def _newest_claim_comment(comments: list[dict], flight_state: str) -> dict | None:
    sorted_comments = sorted(
        comments, key=lambda c: c.get("createdAt", ""), reverse=True,
    )
    for c in sorted_comments:
        body = c.get("body", "")
        if body.startswith("slingshot-claim:") and flight_state in body:
            return c
    return None


def _comment_age(created_at: str) -> int | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(created_at, fmt).replace(tzinfo=UTC)
            return int((datetime.now(UTC) - dt).total_seconds())
        except (ValueError, TypeError):
            pass
    return None


def _comment_epoch(created_at: str) -> int | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(created_at, fmt).replace(tzinfo=UTC)
            return int(dt.timestamp())
        except (ValueError, TypeError):
            pass
    return None


# ---------------------------------------------------------------------------
# CLI entry for daemon subcommand
# ---------------------------------------------------------------------------

def register_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("daemon", help="Run the slingshot polling daemon")
    p.add_argument("--config", help="Path to config file")
    p.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    p.set_defaults(func=cmd_daemon)


def cmd_daemon(args: argparse.Namespace) -> None:
    from slingshot.config import load_config

    cfg = load_config(args.config)
    if not cfg.repos:
        log.log("event=error msg=\"no repos configured\"")
        sys.exit(1)

    daemon = Daemon(cfg)
    daemon.run(once=args.once)
