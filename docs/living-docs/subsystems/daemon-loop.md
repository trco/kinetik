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
related: [subsystems/claim-protocol, subsystems/execution-pipeline, subsystems/planning, flows/issue-to-pr]
---

## What it does

`kontinuum run` is a bare `while True` over the configured repos (`kontinuum/cli.py:80`). Per repo, per pass: kill-switch check → non-blocking PR reconcile → one poll pass → sleep `poll_sec`. GitHub is the only state; the daemon keeps nothing on disk but the clone cache and an in-memory error counter.

**A pass works at most one issue** (`kontinuum/loop.py:261`). It iterates candidates and returns the moment one actually executes. This is deliberate: one live lease, one sandbox, one host at a time. Serialization is also what makes the `.git/info/exclude` trick below safe. Multi-issue concurrency is deferred, not designed out.

Note the pass may *touch* several issues — a "bailed" or "skip" outcome falls through to the next candidate; only "executed" ends the pass.

### Labels

| Label | Who sets it | Meaning |
|---|---|---|
| `ready` | human | queue this issue |
| `hold` | human | stop on this issue; the owner releases its claim (`kontinuum/loop.py:68`) |
| `paused` | human (any open issue carrying it) | repo-wide kill switch (`kontinuum/loop.py:295`) |
| `plan-first` | human | propose a plan and get it merged before any code (see below) |
| `claimed` | machine | lease held; also re-polled so an orphaned claim can be reclaimed |
| `pr-open` | machine | PR opened; CI watched non-blocking |
| `blocked` | machine | needs a human — clear it by hand to requeue |
| `needs-triage` | machine here (also a human/producer signal) | a human promotes this — set after a plan PR, cleared by re-labelling `ready` |

Human controls are `ready` / `hold` / `paused` / `plan-first`; the rest are machine state projections of the claim log. `set_label` enforces mutual exclusion over `STATE_LABELS` (`kontinuum/github.py:105`) — without it `ready` would never clear and the issue would be re-selected forever.

Candidates are `ready` + `claimed` (`kontinuum/loop.py:216`); `claimed` is included on purpose so a crashed instance's issue gets picked back up once its lease expires. `assignee` in config narrows this to a personal queue (`kontinuum/loop.py:211`).

### The `plan-first` detour

When `plan-first` is set and no plan for the issue is already in the worktree, `execute()` opens a plan-only PR on `kontinuum/plan-N` and returns (`kontinuum/loop.py:183`, `kontinuum/loop.py:114`) — the agent sandbox, `propose()` and the code PR never run. It returns a URL, so the pass counts it as *executed* and ends there. The plan PR carries no `Closes #N` (`kontinuum/loop.py:105`): merging it approves the plan, it does not finish the issue.

Three things worth knowing:

- **Where the gate sits.** After the worktree, plugin injection, recipe `setup` and the exclude dance, but before the sandbox. So a plan pass pays for a `setup` it doesn't need — the price of keeping injected plugins and installed deps out of the plan PR's diff. See the `ponytail:` note at `kontinuum/loop.py:182`.
- **How it stops the polling.** The issue ends on `needs-triage`, which *is* in `STATE_LABELS` (`kontinuum/github.py:17`), so setting it evicts `claimed`/`ready` and the issue drops out of `poll_workable` entirely. K will not touch it again until a human merges the plan and re-labels `ready`.
- **What actually ends the detour.** `plan-first` is *not* a state label (it is only in `LABEL_COLORS`, `kontinuum/github.py:56`), so it survives the re-label and the gate would fire forever on the label alone. The exit is `existing_plan` (`kontinuum/plan.py:54`): once the plan PR is merged the plan file is on the base branch the worktree is cut from, the gate sees it, and the agent implements against it. The label is a standing preference, not a one-shot flag.

Idempotent like the main path: an already-open plan PR is reused rather than redrafted, and the §7 ownership + stop-label guard re-runs before the push (`kontinuum/loop.py:135`).

### Lease heartbeat

`LeaseHeartbeat` (`kontinuum/loop.py:37`) is a daemon thread wrapping the whole `execute()` call, beating at `lease/3` (min 30s). A long agent run therefore cannot lose its lease to expiry. Beats swallow transient GitHub errors and retry next tick — a flaky network shouldn't kill a task mid-flight.

### Guards before every effect

Ownership and stop-labels are re-read from GitHub, never cached: once early to bail before expensive work (`kontinuum/loop.py:155`) and again immediately before the PR is opened (`kontinuum/loop.py:190`, and `kontinuum/loop.py:135` on the plan-first branch). Between those two points minutes of agent work elapse, so the second check is the one that matters — a human who applied `hold` mid-run gets no PR.

### Error budget

Per-issue consecutive failures are counted in an `errors` dict owned by the daemon (`kontinuum/cli.py:79`), keyed `(repo, number)` so it survives across passes. On a failure the claim is released *first* (so the issue can retry) and only at `MAX_ERRORS = 3` is it labelled `blocked` with a comment (`kontinuum/loop.py:286`). A success resets the count. Nothing here can kill the daemon: per-issue errors are caught in `poll_once`, whole-repo errors in `cli.py:89`.

### `reconcile_open_prs`

Runs before the poll pass and never blocks it (`kontinuum/loop.py:230`). For each `pr-open` issue it looks up `kontinuum/issue-N` and blocks the issue if the PR was closed unmerged or CI went red. K does not wait on CI inside `execute()` — the PR opens, the next pass judges it. Missing PRs and per-issue failures are swallowed so one stale label can't stop the sweep. Note it only sweeps `pr-open`; a `kontinuum/plan-N` PR is a human's problem by construction.

### Worktrees

Every task gets a throwaway detached worktree off `origin/<default>`, cut from a per-repo cached clone under `~/.kontinuum/cache` (`kontinuum/worktree.py:15`). The user's own checkout is never touched, and the clone is fetched rather than re-downloaded. Cleanup is in a `finally` (`kontinuum/loop.py:204`).

### Config

Two separate things: **machine config** at `~/.kontinuum/config.yaml` (`kontinuum/config.py:28`) — identity, repos, lease/poll timing, which agent — never in a target repo; and the **per-repo recipe** `.kontinuum/verify.yaml` inside each repo (image / gate / setup), which is the human-confirmed trust anchor. CLI flags override the file only when non-`None` (`kontinuum/config.py:35`).

## Key entry points

- `kontinuum/cli.py:23` — argparse: `run`, `init-labels`, `onboard`, `seed-docs`, `status`. `kontinuum/__main__.py` just calls it.
- `kontinuum/loop.py:261` — `poll_once`, the pass.
- `kontinuum/loop.py:65` — `claim_and_run`, the tested per-issue decision (`hold` / `executed` / `bailed` / `skip`).
- `kontinuum/loop.py:147` — `build_executor`, the clone → recipe → plan gate → sandbox → propose → PR body.
- `kontinuum/loop.py:114` — `plan_first_pr`, the plan-only detour.
- `kontinuum/loop.py:325`, `kontinuum/loop.py:353` — `onboard` and `seed_docs_pr`, one-shot PR-producing commands.
- `tests/test_loop.py:75`, `tests/test_loop.py:85` — the pass-level behaviours worth not regressing.
- `tests/test_plan.py:130` — the plan-first detour's hand-back to a human.

## Gotchas / non-obvious

- **The `.git/info/exclude` dance** (`kontinuum/loop.py:173`). Injected plugins and `setup`-installed deps must stay out of the diff, so their paths are appended to the exclude file and the original content is restored in `finally`. But `_git_exclude_path` (`kontinuum/worktree.py:45`) deliberately asks git for the *real* path — in a linked worktree `info/exclude` lives in the shared common dir of the cached clone, so it is shared by every worktree of that repo. This is only safe because passes are serial. Add concurrency and two tasks on the same repo will clobber each other's exclude file.
- **Crash-retry idempotency** (`kontinuum/loop.py:151`). Branch names are deterministic (`kontinuum/issue-N`, `kontinuum/plan-N`), so before doing anything the executor asks whether an open PR already exists on that branch. If K died after opening the PR but before labelling, the retry just sets the label and returns — no duplicate work, no duplicate PR.
- **`setup` runs with network, the gate does not** (`kontinuum/loop.py:165`). Failing setup blocks the issue immediately rather than wasting an agent run on a broken environment.
- **No agent configured is a hard error** (`kontinuum/cli.py:74`). The stub never advances an issue, so it would re-claim the same one forever. Fail fast instead.
- **All timestamps are naive UTC** (`datetime.utcnow()` throughout). Every instance sharing a repo must agree on that; see the ponytail note at `kontinuum/github.py:28`.
- **`status` reads GitHub only** (`kontinuum/loop.py:302`) — there is no local state to be stale. It lists `needs-triage` too, so plan-pending issues are visible.

## See also

- `subsystems/claim-protocol` — what `attempt_claim` / `still_owns` / `release_if_mine` actually guarantee.
- `subsystems/planning` — the two plan paths and what the agent is told to write.
- `subsystems/execution-pipeline` — what happens inside `propose()`.
- `flows/issue-to-pr` — the end-to-end walk.
