---
title: Issue to PR
type: flow
summary: End-to-end path from a `kinetik:ready` GitHub issue to an open pull request — poll, claim, worktree, sandbox, gate, docs, PR, CI reconciliation.
sources:
  - kinetik/loop.py
  - kinetik/pipeline.py
  - kinetik/worktree.py
  - kinetik/effects.py
  - kinetik/protocol.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/daemon-loop, subsystems/execution-pipeline, subsystems/claim-protocol, subsystems/sandbox]
---

## What it does

One labelled issue becomes one open PR, serially: the daemon works **at most one issue per repo per
pass** (`poll_once` returns as soon as one issue reports `executed`, `kinetik/loop.py:217`). Everything
outward-facing happens at the end, behind two ownership guards, so a human can stop Kinetik at any
point before the push and nothing escapes.

> Note: `sources:` lists modules, not `<dir>/**` globs — this repo is a flat single package, so a
> directory glob would match the whole codebase.

## The ordered walk

Ordering is load-bearing; each step assumes the previous one.

1. **Pause check** — an open `kinetik:paused` issue skips the whole repo for the pass (`kinetik/cli.py:91`).
2. **CI reconciliation first** — `reconcile_open_prs` runs *before* new work, so a red PR gets blocked
   even on a pass that then claims nothing (`kinetik/loop.py:186`).
3. **Poll** — `kinetik:ready` plus `kinetik:claimed`; the second label is what lets an orphaned
   claim (dead instance, expired lease) be reclaimed rather than stranded (`kinetik/loop.py:172`).
4. **Hold, then claim** — `claim_and_run` checks `kinetik:hold` *before* claiming, and an epoch lease is
   won with a read-back tie-break (`kinetik/loop.py:64`, `kinetik/protocol.py:15`). A background
   `LeaseHeartbeat` renews at ~lease/3 for the whole task (`kinetik/loop.py:36`).
5. **Idempotent re-entry** — if a PR already exists on `kinetik/issue-N`, relabel `pr-open` and stop.
   This is the crash-retry path; it must precede all work (`kinetik/loop.py:111`).
6. **Early guard** — stop-labels + `still_owns` *before* spending a clone or a container (`kinetik/loop.py:115`).
7. **Worktree** — fresh detached worktree off `origin/<default>` in a cached clone; never the user's
   checkout (`kinetik/worktree.py:26`).
8. **Inject plugins** — the repo's selected bundles into `.claude/`; repo-native files always win
   (`kinetik/plugins.py:33`).
9. **Setup, with network** — the repo-authored `setup` runs in a `bridge` sandbox *before* isolation;
   a non-zero exit blocks the issue rather than handing the agent a broken environment (`kinetik/loop.py:125`).
10. **Exclude** — injected plugins + everything `setup` created are appended to the shared
    `info/exclude` so they never enter the diff or the PR (`kinetik/loop.py:133`).
11. **Attempt loop, offline** — agent edits → gate → secret scan → optional reviewer, up to 3 tries with
    failure fed back; identical-diff oscillation guard stops early (`kinetik/pipeline.py:36`,
    `kinetik/effects.py:24`). No proposal ⇒ `blocked`.
12. **Living docs** — `/docs-update` on the changed paths, best-effort, so doc updates ride in the same
    PR (`kinetik/livingdocs.py:64`). Skipped if the repo has the `living-docs` plugin off.
13. **Second guard** — stop-labels + `still_owns` again, immediately before the first outward effect
    (`kinetik/loop.py:145`).
14. **PR** — commit + force-push `HEAD:refs/heads/kinetik/issue-N` + `gh pr create`, all inside the
    credentialed boundary (`kinetik/effects.py:37`), then comment and set `pr-open` (`kinetik/loop.py:152`).

## Bail-out points

| Where | Trigger | Effect |
|---|---|---|
| before claim | `kinetik:hold` | `release_if_mine`, returns `hold` (`kinetik/loop.py:66`) |
| early guard | hold/blocked, or lease lost | release, no clone, no container (`kinetik/loop.py:116`) |
| setup | non-zero exit | `blocked` + truncated log comment, agent never runs (`kinetik/loop.py:128`) |
| propose | 3 failed attempts or stuck diff | `blocked`, no PR (`kinetik/loop.py:142`) |
| second guard | hold/blocked, or lease lost | release, **no PR opened** (`kinetik/loop.py:146`) |
| poll pass | 3 consecutive infra errors on one issue | claim released first, then `blocked` (`kinetik/loop.py:234`) |
| after merge window | PR red or closed unmerged | `blocked` on the next pass (`kinetik/loop.py:200`) |

## Gotchas / non-obvious

- **Two guards, not one.** The early one saves work; the late one (`kinetik/loop.py:145`) is the
  correctness-critical one — it is the last moment a human `hold` or a lost lease can prevent a push.
- **`finally` restores the exclude file, not just the worktree** (`kinetik/loop.py:156`). `info/exclude`
  is *shared across worktrees of the cached clone*, so a leaked entry would silently hide a target
  repo's own `.claude/` file from a later issue's commit. Serial execution is what makes this safe.
- **Network is inverted from intuition**: the trusted `setup` gets the network; the untrusted agent and
  gate do not (`kinetik/recipe.py:20`, `kinetik/loop.py:127` vs `:140`).
- **Blocking vs best-effort**: setup, gate, secret scan and both guards block the PR. Living-docs
  maintenance, the issue comment and CI reconciliation do not — a docs failure is swallowed
  (`kinetik/livingdocs.py:80`).
- **`bailed` is not `executed`.** A guard bail lets `poll_once` continue to the next issue in the same
  pass; only a real `executed` ends the pass and resets the error counter (`kinetik/loop.py:231`).
- **`pr-open` does not mean green.** The local gate ran offline; real CI is checked later and
  non-blocking by `reconcile_open_prs` (`kinetik/loop.py:154`, `kinetik/ci.py:6`).
- **State labels are mutually exclusive** — `set_label` removes the other `kinetik:*` state labels,
  which is what stops a finished issue from being re-polled forever (`kinetik/github.py:105`).
- **The agent never pushes.** It only proposes a diff; every credentialed write lives in
  `kinetik/effects.py` and `kinetik/github.py`, orchestrator-side.

## See also

- `docs/kinetik-mvp.md` — the as-built system (§7 guards, §10 pipeline, §16.1 living docs).
- `README.md` — labels, per-repo `.kinetik/verify.yaml`, daemon commands.
