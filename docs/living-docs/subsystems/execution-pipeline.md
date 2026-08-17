---
title: Execution Pipeline
type: subsystem
summary: The attempt loop that turns an issue into a reviewed, gate-green diff — and the credentialed boundary that turns that diff into a PR.
sources:
  - kinetik/pipeline.py
  - kinetik/effects.py
  - kinetik/recipe.py
  - kinetik/ci.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/sandbox, subsystems/agent-seam, flows/issue-to-pr]
---

> `sources:` lists modules, not a directory glob: this repo is a flat single-package layout
> (`kinetik/*.py`), so `kinetik/**` would flag every page on every change.

## What it does

`propose()` is the whole execution core: run the agent against a worktree, judge the result
mechanically, feed the failure back, retry. It returns a `Proposal` (a diff + PR body) or `None`
("blocked"). It performs **no outward effect** — no push, no PR, no comment. Everything
credentialed lives in `effects.py`, called by the orchestrator *after* `propose()` returns.

Per attempt (`kinetik/pipeline.py:44`):

1. agent edits the worktree → 2. gate runs in the sandbox → 3. stage + secret-scan the diff →
4. optional reviewer (only if 1–3 are clean).

An attempt is **clean** when all three hold at `kinetik/pipeline.py:51`: non-empty diff, gate
exit 0, no secret findings. Empty diff is a *failure*, not a no-op success — otherwise an agent
that did nothing would ship an empty PR.

## Key entry points

- `propose()` — `kinetik/pipeline.py:36`; the failure→feedback ladder at `:57-64`, the
  oscillation guard at `:65-70`.
- `scan_diff()` — `kinetik/effects.py:24`; patterns at `:15`, added-lines-only filter at `:28`.
- `open_pr()` — `kinetik/effects.py:37`; the detached-HEAD push at `:47`.
- `load_recipe()` / `Recipe` — `kinetik/recipe.py:27` / `:14`.
- `summarize_checks()` — `kinetik/ci.py:6`.
- Caller wiring it together: `build_executor()` — `kinetik/loop.py:104` (recipe at `:124`,
  online setup at `:125`, sandbox + `propose` at `:140`, `open_pr` at `:152`).
- Recipe example: `.kinetik/verify.yaml` (this repo's own gate is a deliberate placeholder —
  `compileall`, because the sandbox has no registry egress yet).

## Gotchas / non-obvious

**Feedback is the only channel between attempts.** The worktree is hard-reset
(`kinetik/pipeline.py:67-68`) after every failure, so attempt N+1 starts from a pristine tree.
Reset because a half-fixed tree makes the next diff a mix of two attempts — the gate would be
judging work the agent no longer intends. Cost: the agent must redo the whole change from the
feedback string, so that string carries the last 1500 chars of gate output (`:60`) rather than a
summary.

**The oscillation guard compares diffs, not attempt count.** `recent` keeps the last three diffs
(`:66`), so it catches A/B/A cycles, not just A/A. An identical diff means the feedback taught the
agent nothing; more attempts burn tokens for the same rejection. Note `break` happens *after* the
reset, so a stuck loop still leaves the tree clean. See
`tests/test_pipeline.py:105` (stops at 2 calls) versus `:46` (varying diffs run the full cap).

**The reviewer is deliberately last and conditional** (`:53`). A model review is the expensive
check; spending it on a change that doesn't compile is waste. It is also *not* the trust anchor —
the gate is. A reviewer rejection blocks, but a reviewer approval cannot rescue a red gate.

**The recipe is the per-repo trust anchor.** `image`/`gate` are required and a missing one raises
`ValueError`, not `SystemExit` (`kinetik/recipe.py:31-34`) — the daemon must survive one bad repo.
The split that matters: `setup` runs **with** network in its own container before the agent exists
(`kinetik/loop.py:127`) because it is repo-authored and deterministic; the agent and gate then run
with `network: none`. So the gate is offline by construction — its verdict can't depend on a flaky
registry, and untrusted code has nowhere to send what it read. Opening `network: bridge` moves the
agent outside that guarantee; prefer baking deps into `image`.

**Untracked files count as the diff** (`git add -A` then `--cached`, `:48-49`). That is why the
caller writes injected plugins and `setup`-installed deps into `.git/info/exclude`
(`kinetik/loop.py:133-137`) — otherwise `node_modules` lands in the PR.

**Why `open_pr` is outside the pipeline.** The pipeline handles agent-produced content; the effect
boundary holds the credentials. Keeping them apart means the only thing that can reach GitHub is a
diff that already passed the gate and the secret scan. It also lets the orchestrator re-check the
issue lease and stop-labels *between* proposal and push (`kinetik/loop.py:145-148`) — a human
saying "stop" during a long attempt still prevents the PR.

**CI rollups are advisory, not part of the attempt loop.** K never waits for CI before opening the
PR; `summarize_checks` is consumed only by the non-blocking reconcile pass
(`kinetik/loop.py:201`). Precedence is fail > pending > pass, and anything unrecognised maps to
`pending` (`kinetik/ci.py:20`) so the next pass re-checks instead of blocking on a state we don't
know. `none` (no checks configured) is not a failure. The two branches at `:12-13` exist because
`gh`'s rollup mixes CheckRun (`conclusion`) and StatusContext (`state`) shapes.

## See also

- `subsystems/sandbox` — what `sandbox.run()` actually enforces.
- `subsystems/agent-seam` — the `AgentRunner`/`Reviewer` contracts `propose()` duck-types against
  (`kinetik/agent.py:54`, `:65`); fakes in `tests/fakes.py` satisfy them.
- `flows/issue-to-pr` — the end-to-end path this subsystem sits in the middle of.
