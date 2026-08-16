# Kontinuum

Kontinuum is a persistent orchestrator you run on your own machine: it polls human-curated GitHub
issues, claims one via an epoch-leased append-only claim log so no other instance double-works it,
runs a real Claude coding agent whose *commands* are confined to a credential-free, no-network
sandbox, verifies the result with a fast local gate plus the repo's existing CI, and opens a pull
request for a human to merge. GitHub issues are the only queue; you keep the merge button.

![How Kontinuum works](docs/kontinuum-overview.svg)

Depth lives in the design docs: [`kontinuum-spec.md`](docs/kontinuum-spec.md) (the vision) and
[`kontinuum-mvp.md`](docs/kontinuum-mvp.md) (the as-built system).

## Prerequisites

- `gh` installed and logged in (`gh auth status`) — Kontinuum makes all GitHub writes through it.
- Docker running — one ephemeral sandbox container per task.
- `claude` CLI installed and logged in (or `ANTHROPIC_API_KEY` set for headless servers).
- Python ≥ 3.9.

## Install

```bash
pip install -e '.[dev]'
```

## Config

Per machine, at `~/.kontinuum/config.yaml` (not in a repo):

```yaml
instance_id: kontinuum/uros@laptop   # stamped in every claim-log entry
bot_login:   uros                    # only this author's claim markers are trusted
agent:       claude                  # or: demo (dry-runs the pipeline). Required for `run`.
repos:
  - trco/kontinuum                   # plain string = all defaults
  - repo:    trco/other-project      # or a mapping, for per-repo settings:
    plugins: [living-docs]           #   optional — universal K plugins on here (default: all)
    agent:   demo                    #   optional — overrides the machine-level `agent:` above
assignee:    uros                    # optional — personal queue; omit for the shared queue
lease_min:   60                      # optional
poll_sec:    300                     # optional
```

`plugins:` lists only the **universal, K-bundled** plugins (today: `living-docs`); `[]` turns them
all off. A repo's own `.claude/` is auto-loaded and needs no entry — and always wins per file.

## Per-repo recipe

Each target repo declares how it is verified, at `.kontinuum/verify.yaml`:

```yaml
image: python:3.11-slim              # sandbox image with the toolchain
gate:  python -m pytest -q           # runs OFFLINE in the box; must pass before a PR opens
setup: pip install --target /work/.deps -r requirements.txt   # optional, runs WITH network first
network: none                        # optional; 'bridge' only if the agent itself needs the net
```

This file is the trust anchor — a human confirms it. `kontinuum onboard` proposes one via a PR.

## Commands

```bash
kontinuum init-labels --repo owner/name   # create the kontinuum:* labels (run automatically by `run`)
kontinuum onboard     --repo owner/name   # agent proposes .kontinuum/verify.yaml via a PR
kontinuum run                             # the daemon: poll -> claim -> execute -> open PR
kontinuum status                          # read-only: what K owns / is working / has blocked
```

Label an issue `kontinuum:ready` to queue it. `kontinuum:hold` on an issue, or an open issue
labelled `kontinuum:paused`, stops Kontinuum on that issue or repo. `kontinuum:plan-first` makes
Kontinuum open a plan-only PR and wait: merge it to approve the plan, then set `kontinuum:ready`
again and it implements against the merged plan. Without that label it plans and implements in one
PR (and skips the plan file entirely when the issue is trivial).
