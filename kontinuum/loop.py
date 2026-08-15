"""The poll loop: poll ready -> claim -> execute, honoring the human `hold` guard (§7, §10).

`claim_and_run` is the tested decision; `build_executor` builds the real clone -> sandbox -> propose
-> PR executor; `poll_workable`/`issue_labels` are thin `gh` glue.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from datetime import datetime, timedelta

from kontinuum.agent import ClaudeAgentRunner
from kontinuum.ci import summarize_checks
from kontinuum.effects import open_pr
from kontinuum.github import GitHubIssueQueue, gh
from kontinuum.livingdocs import has_living_docs, maintain as maintain_living_docs, seed as seed_living_docs
from kontinuum.pipeline import Task, propose
from kontinuum.plugins import inject, injected
from kontinuum.protocol import still_owns, attempt_claim, heartbeat, release_if_mine
from kontinuum.recipe import load_recipe
from kontinuum.sandbox import Sandbox
from kontinuum.worktree import (
    _git_exclude_path,
    _new_worktree,
    _remove_worktree,
    _untracked_paths,
)

logger = logging.getLogger("kontinuum")


class LeaseHeartbeat:
    """Refreshes the claim lease in the background while a task runs (§7), so a long task can't lose it."""

    def __init__(self, queue, me: str, epoch: int, lease: timedelta, interval: float | None = None):
        self.queue, self.me, self.epoch, self.lease = queue, me, epoch, lease
        self._interval = interval if interval is not None else max(30.0, lease.total_seconds() / 3)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop.wait(self._interval):
            try:
                heartbeat(self.queue, self.me, self.epoch, datetime.utcnow(), self.lease)
            except Exception:
                pass                                          # transient GitHub error -> try next beat

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)

HOLD_LABEL = "kontinuum:hold"
STOP_LABELS = ("kontinuum:hold", "kontinuum:blocked")   # a human/other pass told us to stop


def claim_and_run(queue, me: str, now: datetime, lease: timedelta, labels: list[str], execute) -> str:
    """Decide and act on one issue. Returns 'hold' | 'executed' | 'skip'."""
    if HOLD_LABEL in labels:
        release_if_mine(queue, me, now)              # human forcibly reclaimed -> back off
        return "hold"
    epoch = attempt_claim(queue, me, now, lease)     # the exact epoch we won, or None
    if epoch is not None:
        with LeaseHeartbeat(queue, me, epoch, lease):   # keep the lease alive for the whole task
            result = execute(queue, me)
        return "executed" if result else "bailed"    # bailed (hold/lost/blocked) -> pass moves on
    return "skip"


def _default_branch(repo: str) -> str:
    return gh("repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name").strip()


class DemoAgent:
    """Wiring/demo agent: drops a marker file so the pipeline has a diff. Not real work."""

    def run(self, sandbox, task: Task, feedback: str = "") -> str:
        with open(os.path.join(sandbox.workdir, "KONTINUUM.md"), "w") as f:
            f.write(f"Kontinuum touched #{task.number}\n")
        return f"Demo change for #{task.number}."


def _find_open_pr(repo: str, branch: str) -> str | None:
    out = gh("pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "url", "--jq", ".[].url")
    lines = [line for line in out.splitlines() if line.strip()]
    return lines[0] if lines else None


def pr_body(number: int, proposal, reviewed: bool) -> str:
    """Structured PR body. `Closes #N` stays so merging still closes the issue."""
    checks = "gate ✓ · secret-scan ✓" + (" · reviewer ✓" if reviewed else "")   # only what actually ran
    return (f"## Summary\n{proposal.pr_body.strip()}\n\n"
            f"Closes #{number}\n\n"
            f"---\n🤖 Kontinuum — {checks} (attempt {proposal.attempts})")


def build_executor(agent, reviewer=None):
    """Build the real execute(): clone -> read recipe -> sandbox -> propose -> open PR (or block)."""
    def execute(queue, me):
        branch = f"kontinuum/issue-{queue.number}"
        existing = _find_open_pr(queue.repo, branch)      # crash-retry: PR already open -> idempotent, don't redo
        if existing:
            queue.set_label("pr-open")
            return existing
        now = datetime.utcnow()                          # early §7 guard: bail before any expensive work
        if any(l in queue.labels() for l in STOP_LABELS) or not still_owns(queue, me, now):
            release_if_mine(queue, me, now)
            return None
        base = _default_branch(queue.repo)
        workdir, cache = _new_worktree(queue.repo, base)
        exclude_path = exclude_backup = None
        try:
            excluded = set(inject(workdir))              # bundled K plugins into .claude/ (repo-native wins)
            recipe = load_recipe(workdir)                # per-repo setup/gate/image
            if recipe.setup:                             # trusted install WITH network, before the isolated box
                before = _untracked_paths(workdir)
                r = Sandbox(recipe.image, workdir, "bridge").run("sh", "-c", recipe.setup)
                if r.returncode != 0:                    # don't waste the agent on a broken environment
                    queue.set_label("blocked")
                    queue.comment(f"Kontinuum setup step failed (exit {r.returncode}):\n{(r.stdout + r.stderr)[-800:]}")
                    return None
                excluded |= _untracked_paths(workdir) - before   # keep installed deps out of the diff/PR too
            if excluded:                                 # injected plugins + installed deps stay out of the diff/PR
                exclude_path = _git_exclude_path(workdir)
                exclude_backup = open(exclude_path).read() if os.path.exists(exclude_path) else ""
                with open(exclude_path, "a") as f:              # restored in finally -> no leak to other issues
                    f.write("\n".join(sorted(excluded)) + "\n")
            info = json.loads(gh("issue", "view", str(queue.number), "--repo", queue.repo, "--json", "title,body"))
            task = Task(queue.number, info.get("title", ""), info.get("body", ""))
            sandbox = Sandbox(recipe.image, workdir, recipe.network)   # agent + gate: offline by default
            proposal = propose(sandbox, task, recipe.gate, agent, reviewer)
            if proposal is None:
                queue.set_label("blocked")
                return None
            now = datetime.utcnow()                      # §7 guard: still ours + not stopped, before any effect
            if any(l in queue.labels() for l in STOP_LABELS) or not still_owns(queue, me, now):
                release_if_mine(queue, me, now)          # hold/blocked or lost lease -> back off, open no PR
                return None
            maintain_living_docs(agent, workdir)         # best-effort: doc updates ride in this same PR
            body = pr_body(queue.number, proposal, reviewed=reviewer is not None)
            url = open_pr(workdir, queue.repo, branch, f"{task.title} (#{queue.number})", body, base)
            queue.comment(f"Kontinuum opened {url}")
            queue.set_label("pr-open")                    # CI is checked non-blocking in reconcile_open_prs()
            return url
        finally:
            if exclude_path is not None:                 # shared exclude is serial-safe -> restore, no cross-issue leak
                with open(exclude_path, "w") as f:
                    f.write(exclude_backup)
            _remove_worktree(cache, workdir)
    return execute


def _issue_list_args(repo: str, label: str, assignee: str | None) -> list[str]:
    args = ["issue", "list", "--repo", repo, "--label", label,
            "--state", "open", "--json", "number", "--jq", ".[].number"]
    if assignee:                                 # personal queue: only issues assigned to this user
        args += ["--assignee", assignee]
    return args


def poll_workable(repo: str, assignee: str | None = None) -> list[int]:
    """Issues to consider: ready (new work) + claimed (possibly-orphaned; attempt_claim reclaims if free)."""
    seen: set[int] = set()
    out: list[int] = []
    for label in ("kontinuum:ready", "kontinuum:claimed"):
        for line in gh(*_issue_list_args(repo, label, assignee)).splitlines():
            if line.strip():
                n = int(line)
                if n not in seen:
                    seen.add(n)
                    out.append(n)
    return out


def reconcile_open_prs(repo: str, bot_login: str) -> None:
    """Non-blocking pass: block a pr-open issue whose PR went red on CI or was closed unmerged."""
    out = gh("issue", "list", "--repo", repo, "--label", "kontinuum:pr-open",
             "--state", "open", "--json", "number", "--jq", ".[].number")
    for line in out.splitlines():
        if not line.strip():
            continue
        n = int(line)
        try:
            info = json.loads(gh("pr", "view", f"kontinuum/issue-{n}", "--repo", repo,
                                 "--json", "state,statusCheckRollup"))
        except Exception:
            continue                                      # lingering label / no PR — skip, don't kill the pass
        if info.get("state") == "CLOSED":                 # human closed it without merging
            reason = "The PR was closed without merging — needs a human."
        elif summarize_checks(info.get("statusCheckRollup") or []) == "fail":
            reason = "CI is failing on the PR — needs a human."
        else:
            continue
        try:
            q = GitHubIssueQueue(repo, n, bot_login)
            q.set_label("blocked")
            q.comment(reason)
            logger.info("#%d: blocked — %s", n, reason)
        except Exception as e:
            logger.warning("#%d: could not block: %s", n, e)


MAX_ERRORS = 3   # consecutive infra errors on one issue before we give up and block it


def poll_once(repo: str, me: str, lease: timedelta, bot_login: str, execute,
             assignee: str | None = None, errors: dict | None = None):
    """One poll pass. Serial: claim and work at most one issue (§11), then return its number.

    A per-issue failure never kills the daemon. A transient error releases the claim and retries
    next pass; only after MAX_ERRORS consecutive failures is the issue blocked for a human. `errors`
    (owned by the daemon) tracks the consecutive-failure count per issue across passes.
    """
    errors = errors if errors is not None else {}
    for n in poll_workable(repo, assignee):
        q = GitHubIssueQueue(repo, n, bot_login)
        now = datetime.utcnow()                          # fresh clock per issue, not stale per pass
        try:
            status = claim_and_run(q, me, now, lease, q.labels(), execute)
            if status == "executed":                     # real work -> reset the error count and end the pass
                errors.pop((repo, n), None)
                return n
        except Exception as e:
            errors[(repo, n)] = errors.get((repo, n), 0) + 1
            attempts = errors[(repo, n)]
            logger.warning("#%d: error %d/%d: %s", n, attempts, MAX_ERRORS, e)
            try:
                release_if_mine(q, me, datetime.utcnow())   # give up ownership FIRST so it can retry
            except Exception:
                pass
            if attempts >= MAX_ERRORS:                   # persistent failure -> block for a human
                try:
                    q.set_label("blocked")
                    q.comment(f"Kontinuum failed {attempts}x and stopped on this issue: {e}")
                except Exception:
                    pass
    return None


def is_paused(repo: str) -> bool:
    """Kill switch (§4): any open issue labelled kontinuum:paused halts K on this repo."""
    out = gh("issue", "list", "--repo", repo, "--label", "kontinuum:paused",
             "--state", "open", "--json", "number", "--jq", ".[].number")
    return bool(out.strip())


def status(repo: str) -> None:
    """Read-only: what K owns / is working / blocked on this repo (from GitHub, no local state)."""
    print(repo + (" [PAUSED]" if is_paused(repo) else ""))
    for label in ("ready", "claimed", "pr-open", "blocked", "hold", "needs-triage"):
        out = gh("issue", "list", "--repo", repo, "--label", f"kontinuum:{label}", "--state", "open",
                 "--json", "number,title", "--jq", '.[] | "    #\\(.number) \\(.title)"')
        lines = [line for line in out.splitlines() if line.strip()]
        if lines:
            print(f"  {label} ({len(lines)}):")
            print("\n".join(lines))


_ONBOARD_PROMPT = (
    "Create the file .kontinuum/verify.yaml for this repository — Kontinuum's test recipe.\n"
    "Inspect the project (language, package manifest, how its tests run) and write YAML with:\n"
    "  image: a Docker image with the toolchain (e.g. python:3.11, node:20)\n"
    "  gate:  a command that builds/lints/tests, runnable OFFLINE (no network)\n"
    "  setup: (optional) an install command run WITH network before the gate, e.g. 'npm ci' or\n"
    "         'pip install --target /work/.deps -r requirements.txt' — deps must land under /work\n"
    "Keep the gate minimal but real. Create ONLY that one file."
)


def onboard(repo: str) -> None:
    """Propose a verify.yaml + an initial living-docs set for a repo via one PR the human confirms (§4)."""
    base = _default_branch(repo)
    workdir, cache = _new_worktree(repo, base)
    try:
        vpath = os.path.join(workdir, ".kontinuum", "verify.yaml")
        if os.path.exists(vpath):
            logger.info("%s: already onboarded (.kontinuum/verify.yaml exists)", repo)
            return
        subprocess.run(
            ["claude", "-p", _ONBOARD_PROMPT, "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep",
             "--disallowedTools", "Bash", "--permission-mode", "acceptEdits"],
            cwd=workdir, capture_output=True, text=True, timeout=600)
        if not os.path.exists(vpath):
            logger.error("%s: agent did not produce verify.yaml — rerun or write it by hand", repo)
            return
        with injected(workdir):                          # plugin available for /docs-seed; excluded from the PR
            seed_living_docs(ClaudeAgentRunner(), workdir)   # also draft an initial living-docs set (best-effort)
            url = open_pr(workdir, repo, "kontinuum/onboarding",
                          "Kontinuum: onboarding — verify.yaml + living docs",
                          "Proposed test recipe (image + gate) and an initial living-docs survey. "
                          "Review and prune, then merge to enable Kontinuum here.",
                          base)
        logger.info("%s: onboarding PR %s", repo, url)
    finally:
        _remove_worktree(cache, workdir)


def seed_docs_pr(repo: str, agent) -> str | None:
    """Survey a repo and open a PR adding its initial living docs (§16.1). Skips if they already exist."""
    base = _default_branch(repo)
    workdir, cache = _new_worktree(repo, base)
    try:
        if has_living_docs(workdir):
            logger.info("%s: living docs already exist — nothing to seed", repo)
            return None
        with injected(workdir):                          # plugin available for /docs-seed; excluded from the PR
            if not seed_living_docs(agent, workdir):
                logger.error("%s: agent produced no living docs — rerun", repo)
                return None
            url = open_pr(workdir, repo, "kontinuum/living-docs-seed",
                          "Kontinuum: seed living docs",
                          "Initial living-docs survey (subsystems / flows / adr). Review and prune, then merge.",
                          base)
        logger.info("%s: living-docs seed PR %s", repo, url)
        return url
    finally:
        _remove_worktree(cache, workdir)
