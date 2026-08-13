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


def poll_ready(repo: str) -> list[int]:
    out = gh("issue", "list", "--repo", repo, "--label", "kontinuum:ready",
             "--state", "open", "--json", "number", "--jq", ".[].number")
    return [int(line) for line in out.splitlines() if line.strip()]


def issue_labels(repo: str, number: int) -> list[str]:
    out = gh("issue", "view", str(number), "--repo", repo, "--json", "labels", "--jq", ".labels[].name")
    return [line for line in out.splitlines() if line.strip()]


def run_once(repo: str, me: str, now: datetime, lease: timedelta, bot_login: str, execute=execute):
    """One poll pass. Serial: claim and work at most one issue (§11), then return its number."""
    for n in poll_ready(repo):
        q = GitHubIssueQueue(repo, n, bot_login)
        if handle_issue(q, me, now, lease, issue_labels(repo, n), execute) == "executed":
            return n
    return None


def main(argv=None):
    import argparse
    import time

    p = argparse.ArgumentParser(prog="kontinuum")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="poll the queue and work ready issues")
    r.add_argument("--repo", required=True)
    r.add_argument("--instance-id", required=True)          # e.g. kontinuum/uros@laptop
    r.add_argument("--bot-login", required=True)            # comment author to trust
    r.add_argument("--lease-min", type=int, default=60)
    r.add_argument("--poll-sec", type=int, default=300)
    il = sub.add_parser("init-labels", help="create the kontinuum:* labels on a repo")
    il.add_argument("--repo", required=True)
    args = p.parse_args(argv)

    if args.cmd == "init-labels":
        init_labels(args.repo)
        return

    lease = timedelta(minutes=args.lease_min)
    while True:  # ponytail: bare daemon; heartbeats/signals/recovery come when execute() is real
        now = datetime.utcnow()
        did = run_once(args.repo, args.instance_id, now, lease, args.bot_login)
        print(f"[{now:%H:%M:%S}] " + (f"worked #{did}" if did else "nothing ready"))
        time.sleep(args.poll_sec)


if __name__ == "__main__":
    main()
