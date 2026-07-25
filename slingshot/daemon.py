"""The `slingshot daemon` — polling loop, claim management, and worker orchestration."""

from __future__ import annotations

import argparse
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from slingshot.config import Config, RepoConfig
from slingshot import gh
from slingshot import git_utils as git
from slingshot import logging as log
from slingshot import prompts
from slingshot import state


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
        """The in-flight label currently on the issue (e.g. 'slingshot:implementing')."""
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

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, *, once: bool = False) -> None:
        log.log(f"event=start daemon={self.daemon_id}")

        for repo in self.config.repos:
            git.prune_orphan_worktrees(repo.path, self._protected_issue_nums(repo))
            git.ensure_exclude(repo.path)

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
            if ws.phase == "implement":
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
                    gh.issue_edit_labels(ws.repo.name, ws.issue_num, remove_labels=sl_labels)
        except Exception:
            pass
        git.worktree_remove(ws.repo.path, ws.issue_num)
        with self._lock:
            self._workers.pop(ws.key(), None)

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

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        claim_body = f"slingshot-claim: {self.daemon_id} {flight} {ts}"
        try:
            gh.issue_comment_create(repo.name, issue_num, claim_body)
        except Exception:
            return False

        time.sleep(10)

        comments = gh.issue_comments(repo.name, issue_num)
        sorted_comments = sorted(comments, key=lambda c: c.get("createdAt", ""), reverse=True)
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
            git.ensure_exclude(ws.repo.path)

            if ws.phase == "implement":
                elapsed = self._do_implement(ws)
            elif ws.phase == "review":
                elapsed = self._do_review(ws)

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

        if branch_exists:
            if fail_markers > 0 and pr_num is not None:
                scenario = "rework"
                last_push = git.branch_last_commit_epoch(ws.repo.path, branch)
                comments = gh.pr_comments(ws.repo.name, pr_num)
                fail_comments: list[str] = []
                for c in comments:
                    body = c.get("body", "")
                    if "<!-- slingshot:review-fail -->" not in body:
                        continue
                    if last_push is not None:
                        epoch = _comment_epoch(c.get("createdAt", ""))
                        if epoch is not None and epoch < last_push:
                            continue
                    fail_comments.append(body)
                feedback = "\n\n---\n\n".join(fail_comments)
            else:
                scenario = "resume"
        else:
            scenario = "fresh"

        spec = ws.issue_body or ""

        if scenario == "fresh":
            worktree = git.create_worktree(
                ws.repo.path, issue_num, self._default_branch_for(ws.repo),
            )
        else:
            worktree = git.create_worktree_from_remote(ws.repo.path, issue_num)

        prompt = prompts.render_implement_prompt(spec, scenario, feedback)
        prompt_dir = ws.repo.path / ".slingshot" / "prompts"
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

        if exit_code != 0 or not git.has_changes(worktree):
            reason = f"exit={exit_code}" if exit_code != 0 else "empty-diff"
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

        default_branch = self._default_branch_for(ws.repo)
        pr_title = ws.issue_title or f"Slingshot #{issue_num}"
        pr_body = f"Closes #{issue_num}\n\nAutomated implementation by slingshot."
        try:
            gh.pr_create(
                ws.repo.name, title=pr_title, body=pr_body,
                head=branch, base=default_branch,
            )
        except subprocess.CalledProcessError:
            self._handle_agent_failure(ws, ws.flight_label, "pr-create-failed")
            return elapsed

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

        default_branch = self._default_branch_for(ws.repo)
        spec = ws.issue_body or ""

        prompt = prompts.render_review_prompt(spec, default_branch)
        prompt_dir = ws.repo.path / ".slingshot" / "prompts"
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

        verdict_data = prompts.parse_verdict(output)
        if exit_code != 0 or verdict_data is None:
            reason = f"exit={exit_code}" if exit_code != 0 else "no-verdict"
            log.log_agent_failure(ws.repo.name, issue_num, "review", reason)
            self._handle_agent_failure(ws, ws.flight_label, reason)
            return elapsed

        effective = prompts.compute_effective_verdict(verdict_data)
        if effective == "pass":
            summary = prompts.format_pass_summary(verdict_data)
            gh.pr_comment_create(ws.repo.name, pr_num, summary)
            successor = state.WORK_TO_SUCCESSOR[ws.phase]
            self._transition_label(ws.repo, issue_num, ws.flight_label, successor)
        else:
            summary = prompts.format_fail_summary(verdict_data)
            gh.pr_comment_create(ws.repo.name, pr_num, summary)

            comments = gh.pr_comments(ws.repo.name, pr_num)
            fail_count = sum(
                1 for c in comments
                if "<!-- slingshot:review-fail -->" in c.get("body", "")
            )
            if fail_count >= self.config.review_fail_threshold:
                self._transition_label(ws.repo, issue_num, ws.flight_label,
                                       "slingshot:blocked")
            else:
                self._transition_label(ws.repo, issue_num, ws.flight_label,
                                       "slingshot:implement")

        return elapsed

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def _handle_agent_failure(self, ws: WorkerState, current_label: str, reason: str) -> None:
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
            gh.issue_edit_labels(repo.name, issue_num, add_labels=add, remove_labels=remove)
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
            if "<!-- slingshot:review-fail -->" in c.get("body", "")
        )
        return pr_num, fail_count

    def _handle_signal(self, signum, frame) -> None:
        log.log(f"event=signal signal={signal.Signals(signum).name}")
        self._stop.set()


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def _run_agent(
    command_template: str, prompt_file: str, *,
    cwd: str, timeout: int, ws: WorkerState,
) -> tuple[int, str]:
    try:
        parts = shlex.split(command_template)
    except ValueError:
        parts = command_template.split()

    cmd = [prompt_file if p == "{prompt_file}" else p for p in parts]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd,
        )
    except FileNotFoundError:
        return -2, "agent command not found"

    ws.proc = proc
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, (out or "") + (err or "")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return -1, "agent timed out"
    finally:
        ws.proc = None


# ---------------------------------------------------------------------------
# Comment helpers
# ---------------------------------------------------------------------------

def _newest_claim_comment(comments: list[dict], flight_state: str) -> dict | None:
    sorted_comments = sorted(comments, key=lambda c: c.get("createdAt", ""), reverse=True)
    for c in sorted_comments:
        body = c.get("body", "")
        if body.startswith("slingshot-claim:") and flight_state in body:
            return c
    return None


def _comment_age(created_at: str) -> int | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(created_at, fmt).replace(tzinfo=timezone.utc)
            return int((datetime.now(timezone.utc) - dt).total_seconds())
        except (ValueError, TypeError):
            pass
    return None


def _comment_epoch(created_at: str) -> int | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(created_at, fmt).replace(tzinfo=timezone.utc)
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
    p.add_argument("--log-file", help="Path to daemon log file")
    p.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    p.set_defaults(func=cmd_daemon)


def cmd_daemon(args: argparse.Namespace) -> None:
    from slingshot.config import load_config

    cfg = load_config(args.config)
    if not cfg.repos:
        print("error: no repos configured. Edit ~/.config/slingshot/config.toml",
              file=sys.stderr)
        sys.exit(1)

    log.setup_log_file(args.log_file)
    daemon = Daemon(cfg)
    daemon.run(once=args.once)
