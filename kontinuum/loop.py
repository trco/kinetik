"""The poll loop: poll ready -> claim -> execute, honoring the human `hold` guard (§7, §10).

`handle_issue` is the tested decision; `poll_ready`/`issue_labels`/`main` are thin `gh` glue.
`execute` is a stub — the real sandbox -> gate -> reviewer -> PR pipeline is steps 3-6.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta

from kontinuum.agent import ClaudeAgentRunner, ClaudeReviewer
from kontinuum.ci import summarize_checks
from kontinuum.effects import open_pr, pr_body_text
from kontinuum.github import GitHubIssueQueue, gh, init_labels
from kontinuum.gitcmd import git
from kontinuum.pipeline import Task, propose
from kontinuum.protocol import assert_owner, attempt_claim, heartbeat, release_if_mine
from kontinuum.recipe import load_recipe
from kontinuum.sandbox import Sandbox

CACHE_DIR = os.path.expanduser("~/.kontinuum/cache")


class Heartbeater:
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


def handle_issue(queue, me: str, now: datetime, lease: timedelta, labels: list[str], execute) -> str:
    """Decide and act on one issue. Returns 'hold' | 'executed' | 'skip'."""
    if HOLD_LABEL in labels:
        release_if_mine(queue, me, now)              # human forcibly reclaimed -> back off
        return "hold"
    epoch = attempt_claim(queue, me, now, lease)     # the exact epoch we won, or None
    if epoch is not None:
        with Heartbeater(queue, me, epoch, lease):   # keep the lease alive for the whole task
            result = execute(queue, me)
        return "executed" if result else "bailed"    # bailed (hold/lost/blocked) -> pass moves on
    return "skip"


def execute(queue, me: str) -> None:
    # ponytail: default stub for the daemon until a real agent + recipe are configured.
    # The end-to-end executor is make_executor() below (clone -> sandbox -> propose -> PR).
    print(f"  claimed {queue.repo}#{queue.number} — would run pipeline")


def _default_branch(repo: str) -> str:
    return gh("repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name").strip()


def _cache(repo: str) -> str:
    """One local clone per repo, kept up to date — reused across tasks instead of re-cloning."""
    path = os.path.join(CACHE_DIR, repo.replace("/", "__"))
    if os.path.isdir(path):
        git(path, "fetch", "--quiet", "origin")
    else:
        os.makedirs(CACHE_DIR, exist_ok=True)
        gh("repo", "clone", repo, path, "--", "-q")
    return path


def _worktree(repo: str, base: str) -> tuple[str, str]:
    """A fresh, isolated worktree off the latest base — no re-download, never touches your checkout."""
    cache = _cache(repo)
    workdir = os.path.join(tempfile.mkdtemp(prefix="kontinuum-wt-"), "wt")
    git(cache, "worktree", "add", "--quiet", "--force", "--detach", workdir, f"origin/{base}")
    return workdir, cache


def _remove_worktree(cache: str, workdir: str) -> None:
    git(cache, "worktree", "remove", "--force", workdir)
    shutil.rmtree(os.path.dirname(workdir), ignore_errors=True)


def _untracked(workdir: str) -> set[str]:
    # -z = NUL-separated, unquoted -> handles paths with spaces/special chars
    out = git(workdir, "status", "--porcelain", "-z", "--untracked-files=normal")
    return {e[3:] for e in out.split("\0") if e.startswith("?? ")}


def _exclude_file(workdir: str) -> str:
    # in a worktree, workdir/.git is a FILE; ask git for the real (shared) exclude path
    p = git(workdir, "rev-parse", "--git-path", "info/exclude").strip()
    return p if os.path.isabs(p) else os.path.join(workdir, p)


def _read_file(path: str) -> str:
    return open(path).read() if os.path.exists(path) else ""


class DemoAgent:
    """Wiring/demo agent: drops a marker file so the pipeline has a diff. Not real work."""

    def run(self, sandbox, task: Task, feedback: str = "") -> str:
        with open(os.path.join(sandbox.workdir, "KONTINUUM.md"), "w") as f:
            f.write(f"Kontinuum touched #{task.number}\n")
        return f"Demo change for #{task.number}."


def _existing_pr(repo: str, branch: str) -> str | None:
    out = gh("pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "url", "--jq", ".[].url")
    lines = [line for line in out.splitlines() if line.strip()]
    return lines[0] if lines else None


def make_executor(agent, reviewer=None):
    """Build the real execute(): clone -> read recipe -> sandbox -> propose -> open PR (or block)."""
    def execute(queue, me):
        branch = f"kontinuum/issue-{queue.number}"
        existing = _existing_pr(queue.repo, branch)      # crash-retry: PR already open -> idempotent, don't redo
        if existing:
            queue.set_label("pr-open")
            return existing
        now = datetime.utcnow()                          # early §7 guard: bail before any expensive work
        if any(l in queue.labels() for l in STOP_LABELS) or not assert_owner(queue, me, now):
            release_if_mine(queue, me, now)
            return None
        base = _default_branch(queue.repo)
        workdir, cache = _worktree(queue.repo, base)
        exclude_path = exclude_backup = None
        try:
            recipe = load_recipe(workdir)                # per-repo setup/gate/image
            if recipe.setup:                             # trusted install WITH network, before the isolated box
                before = _untracked(workdir)
                r = Sandbox(recipe.image, workdir, "bridge").run("sh", "-c", recipe.setup)
                if r.returncode != 0:                    # don't waste the agent on a broken environment
                    queue.set_label("blocked")
                    queue.comment(f"Kontinuum setup step failed (exit {r.returncode}):\n{(r.stdout + r.stderr)[-800:]}")
                    return None
                new = _untracked(workdir) - before       # keep installed deps out of the diff/PR
                if new:
                    exclude_path = _exclude_file(workdir)
                    exclude_backup = _read_file(exclude_path)   # restored in finally so it can't leak to other issues
                    with open(exclude_path, "a") as f:
                        f.write("\n".join(sorted(new)) + "\n")
            info = json.loads(gh("issue", "view", str(queue.number), "--repo", queue.repo, "--json", "title,body"))
            task = Task(queue.number, info.get("title", ""), info.get("body", ""))
            sandbox = Sandbox(recipe.image, workdir, recipe.network)   # agent + gate: offline by default
            proposal = propose(sandbox, task, recipe.gate, agent, reviewer)
            if proposal is None:
                queue.set_label("blocked")
                return None
            now = datetime.utcnow()                      # §7 guard: still ours + not stopped, before any effect
            if any(l in issue_labels(queue.repo, queue.number) for l in STOP_LABELS) or not assert_owner(queue, me, now):
                release_if_mine(queue, me, now)          # hold/blocked or lost lease -> back off, open no PR
                return None
            url = open_pr(workdir, queue.repo, branch, f"Kontinuum: address #{queue.number}",
                          pr_body_text(queue.number, proposal.pr_body), base)
            queue.comment(f"Kontinuum opened {url}")
            queue.set_label("pr-open")                    # CI is checked non-blocking in maintain_prs()
            return url
        finally:
            if exclude_path is not None:                 # shared exclude is serial-safe -> restore, no cross-issue leak
                with open(exclude_path, "w") as f:
                    f.write(exclude_backup)
            _remove_worktree(cache, workdir)
    return execute


def _list_args(repo: str, label: str, assignee: str | None) -> list[str]:
    args = ["issue", "list", "--repo", repo, "--label", label,
            "--state", "open", "--json", "number", "--jq", ".[].number"]
    if assignee:                                 # personal queue: only issues assigned to this user
        args += ["--assignee", assignee]
    return args


def poll_ready(repo: str, assignee: str | None = None) -> list[int]:
    """Issues to consider: ready (new work) + claimed (possibly-orphaned; attempt_claim reclaims if free)."""
    seen: set[int] = set()
    out: list[int] = []
    for label in ("kontinuum:ready", "kontinuum:claimed"):
        for line in gh(*_list_args(repo, label, assignee)).splitlines():
            if line.strip():
                n = int(line)
                if n not in seen:
                    seen.add(n)
                    out.append(n)
    return out


def maintain_prs(repo: str, bot_login: str) -> None:
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
        except Exception:
            pass


def issue_labels(repo: str, number: int) -> list[str]:
    out = gh("issue", "view", str(number), "--repo", repo, "--json", "labels", "--jq", ".labels[].name")
    return [line for line in out.splitlines() if line.strip()]


MAX_ERRORS = 3   # consecutive infra errors on one issue before we give up and block it


def run_once(repo: str, me: str, lease: timedelta, bot_login: str,
             assignee: str | None = None, execute=execute, errors: dict | None = None):
    """One poll pass. Serial: claim and work at most one issue (§11), then return its number.

    A per-issue failure never kills the daemon. A transient error releases the claim and retries
    next pass; only after MAX_ERRORS consecutive failures is the issue blocked for a human. `errors`
    (owned by the daemon) tracks the consecutive-failure count per issue across passes.
    """
    errors = errors if errors is not None else {}
    for n in poll_ready(repo, assignee):
        q = GitHubIssueQueue(repo, n, bot_login)
        now = datetime.utcnow()                          # fresh clock per issue, not stale per pass
        try:
            status = handle_issue(q, me, now, lease, issue_labels(repo, n), execute)
            if status == "executed":                     # real work -> reset the error count and end the pass
                errors.pop((repo, n), None)
                return n
        except Exception as e:
            errors[(repo, n)] = errors.get((repo, n), 0) + 1
            attempts = errors[(repo, n)]
            print(f"  #{n}: error {attempts}/{MAX_ERRORS}: {e}")
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
    """Propose a verify.yaml for a repo via a PR the human confirms (§4). No sandbox — edit-only."""
    base = _default_branch(repo)
    workdir, cache = _worktree(repo, base)
    try:
        vpath = os.path.join(workdir, ".kontinuum", "verify.yaml")
        if os.path.exists(vpath):
            print(f"{repo}: already onboarded (.kontinuum/verify.yaml exists)")
            return
        subprocess.run(
            ["claude", "-p", _ONBOARD_PROMPT, "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep",
             "--disallowedTools", "Bash", "--permission-mode", "acceptEdits"],
            cwd=workdir, capture_output=True, text=True, timeout=600)
        if not os.path.exists(vpath):
            print(f"{repo}: agent did not produce verify.yaml — rerun or write it by hand")
            return
        url = open_pr(workdir, repo, "kontinuum/onboarding",
                      "Kontinuum: onboarding — add verify.yaml",
                      "Proposed Kontinuum test recipe (image + gate). Review it, then merge to enable Kontinuum here.",
                      base)
        print(f"{repo}: onboarding PR {url}")
    finally:
        _remove_worktree(cache, workdir)


def main(argv=None):
    import argparse
    import time

    from kontinuum.config import load_config

    p = argparse.ArgumentParser(prog="kontinuum")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="poll the queue and work ready issues")
    r.add_argument("--config", default=None, help="config file (default ~/.kontinuum/config.yaml)")
    r.add_argument("--repo", action="append", dest="repos", help="repo(s); overrides config, repeatable")
    r.add_argument("--instance-id")                        # all of these override the config file
    r.add_argument("--bot-login")
    r.add_argument("--assignee")                           # personal queue: only issues assigned to this user
    r.add_argument("--agent")                              # "demo" runs the pipeline; unset = stub
    r.add_argument("--lease-min", type=int)
    r.add_argument("--poll-sec", type=int)
    il = sub.add_parser("init-labels", help="create the kontinuum:* labels on a repo")
    il.add_argument("--repo", required=True)
    ob = sub.add_parser("onboard", help="propose a verify.yaml for a repo via a PR")
    ob.add_argument("--repo", required=True)
    stt = sub.add_parser("status", help="show what K owns / is working / blocked (read-only)")
    stt.add_argument("--config", default=None)
    args = p.parse_args(argv)

    if args.cmd == "init-labels":
        init_labels(args.repo)
        return
    if args.cmd == "onboard":
        onboard(args.repo)
        return
    if args.cmd == "status":
        for repo in load_config(args.config).repos:
            status(repo)
        return

    cfg = load_config(args.config, {
        "instance_id": args.instance_id, "bot_login": args.bot_login, "assignee": args.assignee,
        "agent": args.agent, "repos": args.repos, "lease_min": args.lease_min, "poll_sec": args.poll_sec,
    })
    if cfg.agent == "claude":
        executor = make_executor(ClaudeAgentRunner(), ClaudeReviewer())
    elif cfg.agent == "demo":
        executor = make_executor(DemoAgent())
    else:                                                 # the stub never advances an issue -> would re-claim forever
        raise SystemExit("kontinuum run: set `agent: claude` in the config (or `demo` to exercise the pipeline)")
    for repo in cfg.repos:                                # ensure kontinuum:* labels exist before we set them
        init_labels(repo)
    lease = timedelta(minutes=cfg.lease_min)
    errors: dict = {}                                      # per-issue consecutive-error counts, across passes
    while True:  # ponytail: bare daemon; heartbeats/signals/recovery come when execute() is real
        for repo in cfg.repos:                             # one K, several repos
            stamp = datetime.utcnow().strftime("%H:%M:%S")
            try:
                if is_paused(repo):                        # kill switch: kontinuum:paused halts this repo
                    print(f"[{stamp}] {repo}: paused")
                    continue
                maintain_prs(repo, cfg.bot_login)          # non-blocking: red CI on open PRs -> blocked
                did = run_once(repo, cfg.instance_id, lease, cfg.bot_login, cfg.assignee, executor, errors)
                print(f"[{stamp}] {repo}: " + (f"worked #{did}" if did else "nothing ready"))
            except Exception as e:                         # a whole-repo failure skips the pass, never the daemon
                print(f"[{stamp}] {repo}: pass error: {e}")
        time.sleep(cfg.poll_sec)


if __name__ == "__main__":
    main()
