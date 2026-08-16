---
title: Agent Seam
type: subsystem
summary: The two duck-typed ports (AgentRunner, Reviewer), the Claude CLI adapter that edits on the host but runs commands through the sandbox, and the bundled `.claude/` plugin injection that stays out of the diff.
sources:
  - kontinuum/agent.py
  - kontinuum/plugins.py
  - kontinuum/bundled_plugins/**
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/sandbox, subsystems/execution-pipeline, subsystems/planning, flows/living-docs-maintenance]
---

## What it does

The seam between Kontinuum's orchestration and whatever model actually writes code. Two ports:
`AgentRunner.run(sandbox, task, feedback) -> pr_body` edits the worktree; `Reviewer.review(diff, task) -> Verdict`
gives a read-only opinion. `ClaudeAgentRunner` / `ClaudeReviewer` are the only shipped adapters; `cli.py:70`
maps the config's `agent:` name to them.

**Why Protocols, not base classes** (`kontinuum/agent.py:54`, `:66`): the rest of K is duck-typed (queues,
sandboxes), and the fakes in `tests/fakes.py:37`, `:49` implement the ports without importing or subclassing
anything. `@runtime_checkable` buys one thing — `tests/test_agent.py:13` asserts real adapters *and* fakes
satisfy the same `isinstance` check, so the fake can't silently drift from the contract. It checks method
names only; the semantics (return even on failure, never touch anything outward) live in the docstrings and
are enforced by the pipeline, not the type.

**Split trust in the Claude adapter** (`kontinuum/agent.py:87`): reasoning and file edits run on the *host*
worktree — that's where the login lives and edits are cheap. Commands do not: `--disallowedTools Bash` plus
`--allowedTools Read Edit Write mcp__kontinuum-sandbox__run` (`agent.py:113`) leaves the sandbox MCP `run`
tool as the only way to execute anything, and that tool `docker exec`s into the credential-free, offline box
(`kontinuum/sandbox_mcp.py:35`). `--strict-mcp-config` keeps the operator's own global MCP servers out of the
run. Read the containment caveat in the module docstring (`agent.py:13-28`): the flags keep a well-behaved
CLI on the contract, the **sandbox** is the boundary.

**The implement prompt has two shapes** (`agent.py:73`, chosen at `:108`). `_prompt(task, plan)` takes an
optional plan path, and `run` computes it with `existing_plan(sandbox.workdir, task.number)`: with a
human-approved plan already in the worktree (a merged `plan-first` PR, so it's on the base branch) the agent
is told to read it, implement it and write no new one; with none, it is told to judge whether the issue
warrants a plan at all and, if so, write it to the path K assigns before implementing. Both strings live in
`kontinuum/plan.py:64`, `:78` — the seam owns tool flags and the PR-body contract, planning policy is one
import away, shared verbatim with the plan-only pass so the two paths can't drift.

`run_command(workdir, command)` (`agent.py:124`) is an optional extra capability, deliberately *not* on the
Protocol — callers probe it with `getattr` (`kontinuum/livingdocs.py:35`, `:72`, `kontinuum/plan.py:94`).
Three callers, two shapes: the docs passes send an injected slash command (`/docs-seed`, `/docs-update`)
whose prompt lives in `kontinuum/bundled_plugins/commands/`; `plan.draft` sends a plain prompt (`plan.py:98`),
so the plan-first pass needs no plugin injected at all. One allowlist serves both, for the same reason — an
edit-only pass runs no commands, so no sandbox and no MCP; `Task` is granted for the `living-docs-maintainer`
fan-out, Bash is not, since K owns git.

`plugins.inject()` (`kontinuum/plugins.py:23`) copies the bundle into `<worktree>/.claude/`, mirroring the
bundle layout, and returns the injected relative paths so the caller can git-exclude them. `execute()` folds
them into its own exclude alongside installed deps (`kontinuum/loop.py:163`, `:172`); the edit-only flows use
the `injected()` context manager instead (`plugins.py:46`, used at `loop.py:341`, `:361`). Net effect: the
plugin ships with K (same version everywhere), is available to the agent for the run, and never reaches the PR.

## Key entry points

- Ports: `kontinuum/agent.py:54` (`AgentRunner`), `kontinuum/agent.py:66` (`Reviewer`)
- Runner + tool flags: `kontinuum/agent.py:87`, `:113`; injected-command path: `kontinuum/agent.py:124`
- Reviewer: `kontinuum/agent.py:143`; denied-tool list `kontinuum/agent.py:51`
- Implement prompt (plan shape + PR-body contract): `kontinuum/agent.py:73`; its planning half `kontinuum/plan.py:64`, `:78`
- Injection: `kontinuum/plugins.py:23`; scoped variant `kontinuum/plugins.py:46`
- Callers: `kontinuum/pipeline.py:45` (run/gate loop), `kontinuum/loop.py:163` (injection + exclude), `kontinuum/cli.py:70` (adapter selection)

## Gotchas / non-obvious

- **The runner must not raise.** A timeout returns a string (`agent.py:119`) and empty output falls back to
  `"Implements #N"` (`agent.py:120`). `propose()` judges the *worktree*, not the return value — a raise aborts
  the whole task instead of costing one attempt.
- **The reviewer must not touch the worktree.** It gets the diff as text, runs with no `cwd` and every file
  tool denied (`agent.py:158`). This is load-bearing, not stylistic: review happens *after* the diff is staged
  and secret-scanned (`pipeline.py:48-53`), and `open_pr` re-runs `git add -A` (`effects.py:54`) — so anything
  a reviewer wrote would ride into the PR unreviewed and unscanned.
- **Repo-native `.claude/` wins per file, not per tree** (`plugins.py:37`, `tests/test_plugins.py:20`). A repo
  shipping its own `.claude/skills/living-docs.md` keeps it, and that path is *not* returned — correctly, since
  it is tracked and belongs in the repo's own diff. The flip side: a stale same-named repo file silently
  shadows K's newer bundled one, so "same version on every machine" only holds for files the repo doesn't own.
- **The git exclude is shared across worktrees of the cached clone** (`worktree.py:45` asks git for the real
  path). Both injection paths restore it in a `finally` (`plugins.py:63`, `loop.py:201`); a leaked entry would
  hide a target repo's *own* `.claude/` files from later commits on unrelated issues.
- **Same capability, opposite failure modes.** `run_command` swallows timeouts (`agent.py:139`) and returns
  nothing, so its callers decide what a failure means. Docs shrug it off (`livingdocs.py:41`, `:81`) — a failed
  docs pass never blocks the code PR. Planning does not: a missing `run_command`, a raise, or a swallowed
  timeout that produced no file all leave `plan.draft` returning `None` (`plan.py:94`, `:108`, `:111`), and
  `plan_first_pr` then labels the issue `blocked` and comments (`loop.py:126-129`). Intended — missing docs are
  a gap, but silently skipping a `plan-first` plan would implement code the human never got to approve.

## See also

- `subsystems/planning` — the plan artifact, the two paths, and the prompts this seam imports
- `subsystems/sandbox` — the actual containment boundary the `run` tool crosses into
- `subsystems/execution-pipeline` — the run → gate → scan → review loop that consumes these ports
- `flows/living-docs-maintenance` — what the injected `/docs-update` command does with `run_command`
