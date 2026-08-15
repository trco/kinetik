---
title: Issue to PR
type: flow
summary: End-to-end path from a `kontinuum:ready` GitHub issue to an open pull request — poll, claim, worktree, sandbox, gate, docs, PR, CI reconciliation.
sources:
  - kontinuum/loop.py
  - kontinuum/pipeline.py
  - kontinuum/worktree.py
  - kontinuum/effects.py
  - kontinuum/protocol.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/daemon-loop, subsystems/execution-pipeline, subsystems/claim-protocol, subsystems/sandbox]
---

## What it does

One labelled issue becomes one open PR, serially: the daemon works **at most one issue per repo per
pass** (`poll_once` returns as soon as one issue reports `executed`, `kontinuum/loop.py:213`). Everything
outward-facing happens at the end, behind two ownership guards, so a human can stop Kontinuum at any
point before the push and nothing escapes.

> Note: `sources:` lists modules, not `<dir>/**` globs — this repo is a flat single package, so a
> directory glob would match the whole codebase.

## The ordered walk

Ordering is load-bearing; each step assumes the previous one.

1. **Pause check** — an open `kontinuum:paused` issue skips the whole repo for the pass (`kontinuum/cli.py:83`).
2. **CI reconciliation first** — `reconcile_open_prs` runs *before* new work, so a red PR gets blocked
   even on a pass that then claims nothing (`kontinuum/loop.py:182`).
3. **Poll** — `kontinuum:ready` plus `kontinuum:claimed`; the second label is what lets an orphaned
   claim (dead instance, expired lease) be reclaimed rather than stranded (`kontinuum/loop.py:168`).
4. **Hold, then claim** — `claim_and_run` checks `kontinuum:hold` *before* claiming, and an epoch lease is
   won with a read-back tie-break (`kontinuum/loop.py:64`, `kontinuum/protocol.py:15`). A background
   `LeaseHeartbeat` renews at ~lease/3 for the whole task (`kontinuum/loop.py:36`).
5. **Idempotent re-entry** — if a PR already exists on `kontinuum/issue-N`, relabel `pr-open` and stop.
   This is the crash-retry path; it must precede all work (`kontinuum/loop.py:108`).
6. **Early guard** — stop-labels + `still_owns` *before* spending a clone or a container (`kontinuum/loop.py:112`).
7. **Worktree** — fresh detached worktree off `origin/<default>` in a cached clone; never the user's
   checkout (`kontinuum/worktree.py:26`).
8. **Inject plugins** — bundled `.claude/` files; repo-native files always win (`kontinuum/plugins.py:23`).
9. **Setup, with network** — the repo-authored `setup` runs in a `bridge` sandbox *before* isolation;
   a non-zero exit blocks the issue rather than handing the agent a broken environment (`kontinuum/loop.py:122`).
10. **Exclude** — injected plugins + everything `setup` created are appended to the shared
    `info/exclude` so they never enter the diff or the PR (`kontinuum/loop.py:130`).
11. **Attempt loop, offline** — agent edits → gate → secret scan → optional reviewer, up to 3 tries with
    failure fed back; identical-diff oscillation guard stops early (`kontinuum/pipeline.py:36`,
    `kontinuum/effects.py:24`). No proposal ⇒ `blocked`.
12. **Living docs** — `/docs-update` on the changed paths, best-effort, so doc updates ride in the same
    PR (`kontinuum/livingdocs.py:64`).
13. **Second guard** — stop-labels + `still_owns` again, immediately before the first outward effect
    (`kontinuum/loop.py:142`).
14. **PR** — commit + force-push `HEAD:refs/heads/kontinuum/issue-N` + `gh pr create`, all inside the
    credentialed boundary (`kontinuum/effects.py:37`), then comment and set `pr-open` (`kontinuum/loop.py:148`).

## Bail-out points

| Where | Trigger | Effect |
|---|---|---|
| before claim | `kontinuum:hold` | `release_if_mine`, returns `hold` (`kontinuum/loop.py:66`) |
| early guard | hold/blocked, or lease lost | release, no clone, no container (`kontinuum/loop.py:113`) |
| setup | non-zero exit | `blocked` + truncated log comment, agent never runs (`kontinuum/loop.py:125`) |
| propose | 3 failed attempts or stuck diff | `blocked`, no PR (`kontinuum/loop.py:139`) |
| second guard | hold/blocked, or lease lost | release, **no PR opened** (`kontinuum/loop.py:143`) |
| poll pass | 3 consecutive infra errors on one issue | claim released first, then `blocked` (`kontinuum/loop.py:230`) |
| after merge window | PR red or closed unmerged | `blocked` on the next pass (`kontinuum/loop.py:196`) |

## Gotchas / non-obvious

- **Two guards, not one.** The early one saves work; the late one (`kontinuum/loop.py:142`) is the
  correctness-critical one — it is the last moment a human `hold` or a lost lease can prevent a push.
- **`finally` restores the exclude file, not just the worktree** (`kontinuum/loop.py:152`). `info/exclude`
  is *shared across worktrees of the cached clone*, so a leaked entry would silently hide a target
  repo's own `.claude/` file from a later issue's commit. Serial execution is what makes this safe.
- **Network is inverted from intuition**: the trusted `setup` gets the network; the untrusted agent and
  gate do not (`kontinuum/recipe.py:20`, `kontinuum/loop.py:124` vs `:137`).
- **Blocking vs best-effort**: setup, gate, secret scan and both guards block the PR. Living-docs
  maintenance, the issue comment and CI reconciliation do not — a docs failure is swallowed
  (`kontinuum/livingdocs.py:80`).
- **`bailed` is not `executed`.** A guard bail lets `poll_once` continue to the next issue in the same
  pass; only a real `executed` ends the pass and resets the error counter (`kontinuum/loop.py:227`).
- **`pr-open` does not mean green.** The local gate ran offline; real CI is checked later and
  non-blocking by `reconcile_open_prs` (`kontinuum/loop.py:150`, `kontinuum/ci.py:6`).
- **State labels are mutually exclusive** — `set_label` removes the other `kontinuum:*` state labels,
  which is what stops a finished issue from being re-polled forever (`kontinuum/github.py:105`).
- **The agent never pushes.** It only proposes a diff; every credentialed write lives in
  `kontinuum/effects.py` and `kontinuum/github.py`, orchestrator-side.

## See also

- `docs/kontinuum-mvp.md` — the as-built system (§7 guards, §10 pipeline, §16.1 living docs).
- `README.md` — labels, per-repo `.kontinuum/verify.yaml`, daemon commands.
