"""Command line entry point: argparse + the daemon loop over the configured repos."""

from __future__ import annotations

import logging
from datetime import timedelta

from kontinuum.agent import ClaudeAgentRunner, ClaudeReviewer
from kontinuum.github import init_labels
from kontinuum.loop import (
    DemoAgent,
    build_executor,
    is_paused,
    logger,
    onboard,
    poll_once,
    reconcile_open_prs,
    status,
)


def main(argv=None):
    import argparse
    import time

    from kontinuum.config import load_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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
        executor = build_executor(ClaudeAgentRunner(), ClaudeReviewer())
    elif cfg.agent == "demo":
        executor = build_executor(DemoAgent())
    else:                                                 # the stub never advances an issue -> would re-claim forever
        raise SystemExit("kontinuum run: set `agent: claude` in the config (or `demo` to exercise the pipeline)")
    for repo in cfg.repos:                                # ensure kontinuum:* labels exist before we set them
        init_labels(repo)
    lease = timedelta(minutes=cfg.lease_min)
    errors: dict = {}                                      # per-issue consecutive-error counts, across passes
    while True:  # ponytail: bare daemon; heartbeats/signals/recovery come when execute() is real
        for repo in cfg.repos:                             # one K, several repos
            try:
                if is_paused(repo):                        # kill switch: kontinuum:paused halts this repo
                    logger.info("%s: paused", repo)
                    continue
                reconcile_open_prs(repo, cfg.bot_login)          # non-blocking: red CI on open PRs -> blocked
                did = poll_once(repo, cfg.instance_id, lease, cfg.bot_login, executor, cfg.assignee, errors)
                logger.info("%s: %s", repo, f"worked #{did}" if did else "nothing ready")
            except Exception as e:                         # a whole-repo failure skips the pass, never the daemon
                logger.error("%s: pass error: %s", repo, e)
        time.sleep(cfg.poll_sec)


if __name__ == "__main__":
    main()
