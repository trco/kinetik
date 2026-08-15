---
title: Daemon Loop
type: subsystem
summary: The `kontinuum run` poll pass — one issue per pass, label-driven, lease-heartbeated, with guards re-checked before every effect.
sources:
  - kontinuum/loop.py
  - kontinuum/cli.py
  - kontinuum/config.py
  - kontinuum/worktree.py
  - kontinuum/gitcmd.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/claim-protocol, subsystems/execution-pipeline, flows/issue-to-pr]
---

## What it does

`kontinuum run` is a bare `while True` over the configured repos (`kontinuum/cli.py:80`). Per repo, per pass: kill-switch check → non-blocking PR reconcile → one poll pass → sleep `poll_sec`. GitHub is the only state; the daemon keeps nothing on disk but the clone cache and an in-memory error counter.

**A pass works at most one issue** (`kontinuum/loop.py:213`). It iterates candidates and returns the moment one actually executes. This is deliberate: one live lease, one sandbox, one host at a time. Serialization is also what makes the `.git/info/exclude` trick below safe. Multi-issue concurrency is deferred, not designed out.

Note the pass may *touch* several issues — a "bailed" or "skip" outcome falls through to the next candidate; only "executed" ends the pass.

### Labels

| Label | Who sets it | Meaning |
|---|---|---|
| `ready` | human | queue this issue |
| `hold` | human | stop on this issue; the owner releases its claim (`kontinuum/loop.py:66`) |
| `paused` | human (any open issue carrying it) | repo-wide kill switch (`kontinuum/loop.py:247`) |
| `claimed` | machine | lease held; also re-polled so an orphaned claim can be reclaimed |
| `pr-open` | machine | PR opened; CI watched non-blocking |
| `blocked` | machine | needs a human — clear it by hand to requeue |

Human controls are `ready` / `hold` / `paused`; the rest are machine state projections of the claim log. `set_label` enforces mutual exclusion (`kontinuum/github.py:105`) — without it `ready` would never clear and the issue would be re-selected forever.

Candidates are `ready` + `claimed` (`kontinuum/loop.py:168`); `claimed` is included on purpose so a crashed instance's issue gets picked back up once its lease expires. `assignee` in config narrows this to a personal queue (`kontinuum/loop.py:160`).

### Lease heartbeat

`LeaseHeartbeat` (`kontinuum/loop.py:36`) is a daemon thread wrapping the whole `execute()` call, beating at `lease/3` (min 30s). A long agent run therefore cannot lose its lease to expiry. Beats swallow transient GitHub errors and retry next tick — a flaky network shouldn't kill a task mid-flight.

### Guards before every effect

Ownership and stop-labels are re-read from GitHub, never cached: once early to bail before expensive work (`kontinuum/loop.py:113`) and again immediately before the PR is opened (`kontinuum/loop.py:142`). Between those two points minutes of agent work elapse, so the second check is the one that matters — a human who applied `hold` mid-run gets no PR.

### Error budget

Per-issue consecutive failures are counted in an `errors` dict owned by the daemon (`kontinuum/cli.py:79`), keyed `(repo, number)` so it survives across passes. On a failure the claim is released *first* (so the issue can retry) and only at `MAX_ERRORS = 3` is it labelled `blocked` with a comment (`kontinuum/loop.py:230`). A success resets the count. Nothing here can kill the daemon: per-issue errors are caught in `poll_once`, whole-repo errors in `cli.py:89`.

### `reconcile_open_prs`

Runs before the poll pass and never blocks it (`kontinuum/loop.py:182`). For each `pr-open` issue it looks up `kontinuum/issue-N` and blocks the issue if the PR was closed unmerged or CI went red. K does not wait on CI inside `execute()` — the PR opens, the next pass judges it. Missing PRs and per-issue failures are swallowed so one stale label can't stop the sweep.

### Worktrees

Every task gets a throwaway detached worktree off `origin/<default>`, cut from a per-repo cached clone under `~/.kontinuum/cache` (`kontinuum/worktree.py:15`). The user's own checkout is never touched, and the clone is fetched rather than re-downloaded. Cleanup is in a `finally` (`kontinuum/loop.py:152`).

### Config

Two separate things: **machine config** at `~/.kontinuum/config.yaml` (`kontinuum/config.py:28`) — identity, repos, lease/poll timing, which agent — never in a target repo; and the **per-repo recipe** `.kontinuum/verify.yaml` inside each repo (image / gate / setup), which is the human-confirmed trust anchor. CLI flags override the file only when non-`None` (`kontinuum/config.py:35`).

## Key entry points

- `kontinuum/cli.py:23` — argparse: `run`, `init-labels`, `onboard`, `seed-docs`, `status`. `kontinuum/__main__.py` just calls it.
- `kontinuum/loop.py:213` — `poll_once`, the pass.
- `kontinuum/loop.py:64` — `claim_and_run`, the tested per-issue decision (`hold` / `executed` / `bailed` / `skip`).
- `kontinuum/loop.py:104` — `build_executor`, the clone → recipe → sandbox → propose → PR body.
- `kontinuum/loop.py:277`, `kontinuum/loop.py:305` — `onboard` and `seed_docs_pr`, one-shot PR-producing commands.
- `tests/test_loop.py:75`, `tests/test_loop.py:85` — the pass-level behaviours worth not regressing.

## Gotchas / non-obvious

- **The `.git/info/exclude` dance** (`kontinuum/loop.py:130`). Injected plugins and `setup`-installed deps must stay out of the diff, so their paths are appended to the exclude file and the original content is restored in `finally`. But `_git_exclude_path` (`kontinuum/worktree.py:45`) deliberately asks git for the *real* path — in a linked worktree `info/exclude` lives in the shared common dir of the cached clone, so it is shared by every worktree of that repo. This is only safe because passes are serial. Add concurrency and two tasks on the same repo will clobber each other's exclude file.
- **Crash-retry idempotency** (`kontinuum/loop.py:108`). Branch names are deterministic (`kontinuum/issue-N`), so before doing anything the executor asks whether an open PR already exists on that branch. If K died after opening the PR but before labelling, the retry just sets `pr-open` and returns — no duplicate work, no duplicate PR.
- **`setup` runs with network, the gate does not** (`kontinuum/loop.py:122`). Failing setup blocks the issue immediately rather than wasting an agent run on a broken environment.
- **No agent configured is a hard error** (`kontinuum/cli.py:74`). The stub never advances an issue, so it would re-claim the same one forever. Fail fast instead.
- **All timestamps are naive UTC** (`datetime.utcnow()` throughout). Every instance sharing a repo must agree on that; see the ponytail note at `kontinuum/github.py:28`.
- **`status` reads GitHub only** (`kontinuum/loop.py:254`) — there is no local state to be stale.

## See also

- `subsystems/claim-protocol` — what `attempt_claim` / `still_owns` / `release_if_mine` actually guarantee.
- `subsystems/execution-pipeline` — what happens inside `propose()`.
- `flows/issue-to-pr` — the end-to-end walk.
