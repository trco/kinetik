"""The poll loop: poll ready -> claim -> execute, honoring the human `hold` guard (§7, §10).

`handle_issue` is the tested decision; `poll_ready`/`issue_labels`/`main` are thin `gh` glue.
`execute` is a stub — the real sandbox -> gate -> reviewer -> PR pipeline is steps 3-6.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta

from kontinuum.agent import ClaudeAgentRunner, ClaudeReviewer
from kontinuum.ci import await_ci
from kontinuum.effects import open_pr
from kontinuum.github import GitHubIssueQueue, gh, init_labels
from kontinuum.gitcmd import git
from kontinuum.pipeline import Task, propose
from kontinuum.protocol import attempt_claim, release_if_mine
from kontinuum.recipe import load_recipe
from kontinuum.sandbox import Sandbox

CACHE_DIR = os.path.expanduser("~/.kontinuum/cache")

HOLD_LABEL = "kontinuum:hold"


def handle_issue(queue, me: str, now: datetime, lease: timedelta, labels: list[str], execute) -> str:
    """Decide and act on one issue. Returns 'hold' | 'executed' | 'skip'."""
    if HOLD_LABEL in labels:
        release_if_mine(queue, me, now)              # human forcibly reclaimed -> back off
        return "hold"
    if attempt_claim(queue, me, now, lease):
        execute(queue)
        return "executed"
    return "skip"


def execute(queue) -> None:
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


class DemoAgent:
    """Wiring/demo agent: drops a marker file so the pipeline has a diff. Not real work."""

    def run(self, sandbox, task: Task, feedback: str = "") -> str:
        with open(os.path.join(sandbox.workdir, "KONTINUUM.md"), "w") as f:
            f.write(f"Kontinuum touched #{task.number}\n")
        return f"Demo change for #{task.number}."


def make_executor(agent, reviewer=None):
    """Build the real execute(): clone -> read recipe -> sandbox -> propose -> open PR (or block)."""
    def execute(queue):
        base = _default_branch(queue.repo)
        workdir, cache = _worktree(queue.repo, base)
        try:
            recipe = load_recipe(workdir)                # per-repo gate command + image
            info = json.loads(gh("issue", "view", str(queue.number), "--repo", queue.repo, "--json", "title,body"))
            task = Task(queue.number, info.get("title", ""), info.get("body", ""))
            sandbox = Sandbox(recipe.image, workdir, recipe.network)
            proposal = propose(sandbox, task, recipe.gate, agent, reviewer)
            if proposal is None:
                queue.set_label("blocked")
                return None
            url = open_pr(workdir, queue.repo, queue.number, proposal.pr_body, base)
            queue.comment(f"Kontinuum opened {url}")
            queue.set_label("pr-open")
            if await_ci(url) == "fail":                   # green/none = human merges; red = needs a human
                queue.set_label("blocked")
                queue.comment("CI is failing on the PR — needs a human.")
            return url
        finally:
            _remove_worktree(cache, workdir)
    return execute


def _ready_args(repo: str, assignee: str | None) -> list[str]:
    args = ["issue", "list", "--repo", repo, "--label", "kontinuum:ready",
            "--state", "open", "--json", "number", "--jq", ".[].number"]
    if assignee:                                 # personal queue: only issues assigned to this user
        args += ["--assignee", assignee]
    return args


def poll_ready(repo: str, assignee: str | None = None) -> list[int]:
    out = gh(*_ready_args(repo, assignee))
    return [int(line) for line in out.splitlines() if line.strip()]


def issue_labels(repo: str, number: int) -> list[str]:
    out = gh("issue", "view", str(number), "--repo", repo, "--json", "labels", "--jq", ".labels[].name")
    return [line for line in out.splitlines() if line.strip()]


def run_once(repo: str, me: str, now: datetime, lease: timedelta, bot_login: str,
             assignee: str | None = None, execute=execute):
    """One poll pass. Serial: claim and work at most one issue (§11), then return its number."""
    for n in poll_ready(repo, assignee):
        q = GitHubIssueQueue(repo, n, bot_login)
        if handle_issue(q, me, now, lease, issue_labels(repo, n), execute) == "executed":
            return n
    return None


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
    args = p.parse_args(argv)

    if args.cmd == "init-labels":
        init_labels(args.repo)
        return

    cfg = load_config(args.config, {
        "instance_id": args.instance_id, "bot_login": args.bot_login, "assignee": args.assignee,
        "agent": args.agent, "repos": args.repos, "lease_min": args.lease_min, "poll_sec": args.poll_sec,
    })
    # ponytail: default stays the stub so nothing opens PRs until a real agent is chosen.
    if cfg.agent == "claude":
        executor = make_executor(ClaudeAgentRunner(), ClaudeReviewer())
    elif cfg.agent == "demo":
        executor = make_executor(DemoAgent())
    else:
        executor = execute
    lease = timedelta(minutes=cfg.lease_min)
    while True:  # ponytail: bare daemon; heartbeats/signals/recovery come when execute() is real
        now = datetime.utcnow()
        for repo in cfg.repos:                             # one K, several repos
            did = run_once(repo, cfg.instance_id, now, lease, cfg.bot_login, cfg.assignee, executor)
            print(f"[{now:%H:%M:%S}] {repo}: " + (f"worked #{did}" if did else "nothing ready"))
        time.sleep(cfg.poll_sec)


if __name__ == "__main__":
    main()
