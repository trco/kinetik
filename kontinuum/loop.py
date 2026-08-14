"""The poll loop: poll ready -> claim -> execute, honoring the human `hold` guard (§7, §10).

`handle_issue` is the tested decision; `poll_ready`/`issue_labels`/`main` are thin `gh` glue.
`execute` is a stub — the real sandbox -> gate -> reviewer -> PR pipeline is steps 3-6.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from kontinuum.github import GitHubIssueQueue, gh, init_labels
from kontinuum.protocol import attempt_claim, release_if_mine

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
    # ponytail: real pipeline (sandbox -> gate -> reviewer -> PR) is steps 3-6. Stub for now.
    print(f"  claimed {queue.repo}#{queue.number} — would run pipeline")


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
        "repos": args.repos, "lease_min": args.lease_min, "poll_sec": args.poll_sec,
    })
    lease = timedelta(minutes=cfg.lease_min)
    while True:  # ponytail: bare daemon; heartbeats/signals/recovery come when execute() is real
        now = datetime.utcnow()
        for repo in cfg.repos:                             # one K, several repos
            did = run_once(repo, cfg.instance_id, now, lease, cfg.bot_login, cfg.assignee)
            print(f"[{now:%H:%M:%S}] {repo}: " + (f"worked #{did}" if did else "nothing ready"))
        time.sleep(cfg.poll_sec)


if __name__ == "__main__":
    main()
