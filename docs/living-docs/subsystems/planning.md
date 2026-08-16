---
title: Per-Issue Planning
type: subsystem
summary: One plan artifact per issue (`docs/plans/<date>-issue-<N>-<slug>.md`) reachable two ways — riding in the code PR by default, or as a plan-only PR a human merges to approve when `kontinuum:plan-first` is set.
sources:
  - kontinuum/plan.py
  - kontinuum/loop.py
  - kontinuum/agent.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/agent-seam, subsystems/daemon-loop, flows/issue-to-pr]
---

## What it does

MVP §16.1 item #4: the agent writes down what it intends to do before it does it, grounded in the
living docs. **One artifact, two paths to it** — `docs/plans/<YYYY-MM-DD>-issue-<N>-<slug>.md`
(`kontinuum/plan.py:48`).

**Default — autonomous until the PR.** The planning instruction (`plan_prompt`, `plan.py:64`) is
appended to the agent's own implement prompt (`kontinuum/agent.py:73`). So the plan file is written
in the *same edit pass* as the code, and ships in the *same PR*: it is gate-checked, secret-scanned
and reviewed with everything else, at no extra agent run. Nothing in the daemon branches for it.

**`kontinuum:plan-first` — a human approval checkpoint.** The label (`plan.py:32`) makes `execute()`
run a plan-only agent pass, open a plan-only PR on `kontinuum/plan-N`, set `needs-triage` and stop
(`kontinuum/loop.py:114`). **Approval is the merge.** Once merged the plan is on the base branch, so
the next run's worktree (cut from base) already contains it, `existing_plan` finds it
(`agent.py:108`), and the agent implements against it via `follow_prompt` (`plan.py:78`) instead of
writing a new one.

**Triviality is the agent's call.** The prompt says a one-liner, a typo or a rename warrants no plan
file — ceremony on trivial issues is worse than no plan (`plan.py:66`, locked by
`tests/test_plan.py:48`).

## Key entry points

- `kontinuum/plan.py:48` — `plan_path`: K assigns the filename. `_slug` (`:44`) caps at 40 chars and
  degrades to `plan` for a punctuation-only title (`tests/test_plan.py:29`).
- `kontinuum/plan.py:54` — `existing_plan`: date-independent glob over `docs/plans/`, newest wins.
- `kontinuum/plan.py:64` / `:78` — the two instructions: write one vs follow the approved one.
- `kontinuum/plan.py:87` — `draft`: the plan-only agent pass (needs `run_command`, `agent.py:124`).
- `kontinuum/loop.py:183` — the gate inside `execute()`: `plan_first(labels) and not existing_plan(...)`.
- `kontinuum/loop.py:114` — `plan_first_pr`: reuse-or-draft → scan → §7 guard → PR → `needs-triage`.
- `kontinuum/loop.py:105` — `plan_pr_body`.
- `kontinuum/effects.py:37` — `scan_worktree`, the secret scan for this path.
- `kontinuum/plan.py:33` — `LIVING_DOCS_HINT`, shared by both prompts: read INDEX.md and the one or
  two pages covering the area, as context to verify, not gospel.

## Gotchas / non-obvious

- **K owns the filename, the agent only writes the file.** Discovery is by filename — K has no index
  and the agent has no shell — so an agent-invented name would strand the plan where nothing finds it
  (`plan.py:14`). The prompt embeds the exact path (`tests/test_plan.py:51`).
- **The trailing `-` after the issue number is load-bearing.** `*issue-{n}-*.md` is what keeps #7 from
  matching #71 (`plan.py:60`, `tests/test_plan.py:38`).
- **`draft` prompts the agent directly, not via a bundled slash command** (`plan.py:98`) — unlike the
  living-docs flows. Both paths then share one instruction source and cannot drift.
- **The plan PR deliberately omits `Closes #N`** — only `Refs #N` (`loop.py:105`,
  `tests/test_plan.py:176`). Merging the plan approves it; it does not finish the issue.
- **`scan_worktree` exists only because this push bypasses `propose()`.** Plan-first content never
  reaches the pipeline's `scan_diff`, and no push may leave unscanned; it stages exactly as `open_pr`
  does so it sees what would be pushed (`effects.py:37`, `tests/test_plan.py:162`).
- **`plan_first_pr` is idempotent and re-guards before the push.** An already-open plan PR is reused,
  never redrafted (`loop.py:122`, `tests/test_plan.py:139`), and the §7 ownership + stop-label check
  is re-read immediately before `open_pr` (`loop.py:136`), so a mid-run `hold` yields no plan PR.
- **The plan-first branch still pays for the recipe `setup` step it does not need.** Marked
  `ponytail:` at `kontinuum/loop.py:182`: setup runs before the branch point so its installed deps
  land in `.git/info/exclude` and stay out of the plan PR — a plan PR containing `node_modules` is a
  worse trade than one wasted install.
- **The label does not need removing after approval.** `set_label("needs-triage")` only clears
  `STATE_LABELS` (`kontinuum/github.py:17`, `:112`), and `plan-first` is not one — it is a human
  override with its own colour (`github.py:55`). What disarms the detour on the next run is the
  merged plan satisfying `existing_plan`, not the label going away (`loop.py:183`).
- **A retry just rewrites the plan.** `propose()` does `reset --hard` + `clean -fd` between attempts
  (`kontinuum/pipeline.py:67`), so a default-path plan file from a failed attempt is discarded and
  `existing_plan` is re-evaluated fresh on the next `run()`.
- **`docs/plans/` is stripped from the living-docs changed-path list** (`kontinuum/livingdocs.py:20`)
  — a plan describes a change, it is not part of it, and it must not trigger doc updates of its own.
- **A plan PR consumes the pass.** `plan_first_pr` returning a URL makes `claim_and_run` report
  `executed`, so the pass ends there; returning `None` (blocked/lost lease) falls through to the next
  candidate.
- **`sources:` here are module files, not directory globs** — `kontinuum/` is a flat single package,
  so `kontinuum/**` would flag every page on every change. See `docs/living-docs/README.md:10`.

## See also

- [subsystems/daemon-loop](daemon-loop.md) — the `execute()` ordering, labels and §7 guards this sits inside.
- [subsystems/agent-seam](agent-seam.md) — `run_command`, the optional capability the plan pass needs.
- [flows/issue-to-pr](../flows/issue-to-pr.md) — the end-to-end walk.
