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
related: [subsystems/sandbox, subsystems/execution-pipeline, flows/living-docs-maintenance]
---

## What it does

The seam between Kontinuum's orchestration and whatever model actually writes code. Two ports:
`AgentRunner.run(sandbox, task, feedback) -> pr_body` edits the worktree; `Reviewer.review(diff, task) -> Verdict`
gives a read-only opinion. `ClaudeAgentRunner` / `ClaudeReviewer` are the only shipped adapters; `cli.py:70`
maps the config's `agent:` name to them.

**Why Protocols, not base classes** (`kontinuum/agent.py:53`, `:65`): the rest of K is duck-typed (queues,
sandboxes), and the fakes in `tests/fakes.py:37`, `:49` implement the ports without importing or subclassing
anything. `@runtime_checkable` buys one thing — `tests/test_agent.py:11` asserts real adapters *and* fakes
satisfy the same `isinstance` check, so the fake can't silently drift from the contract. It checks method
names only; the semantics (return even on failure, never touch anything outward) live in the docstrings and
are enforced by the pipeline, not the type.

**Split trust in the Claude adapter** (`kontinuum/agent.py:96`): reasoning and file edits run on the *host*
worktree — that's where the login lives and edits are cheap. Commands do not: `--disallowedTools Bash` plus
`--allowedTools Read Edit Write mcp__kontinuum-sandbox__run` (`agent.py:110`) leaves the sandbox MCP `run`
tool as the only way to execute anything, and that tool `docker exec`s into the credential-free, offline box
(`kontinuum/sandbox_mcp.py:35`). `--strict-mcp-config` keeps the operator's own global MCP servers out of the
run. Read the containment caveat in the module docstring (`agent.py:14-28`): the flags keep a well-behaved
CLI on the contract, the **sandbox** is the boundary.

`run_command(workdir, command)` (`agent.py:121`) is an optional extra capability, deliberately *not* on the
Protocol — callers probe it with `getattr` (`kontinuum/livingdocs.py:35`, `:72`) so an adapter without it just
skips docs work. It runs an injected slash command headless (`/docs-seed`, `/docs-update`); the prompt lives in
`kontinuum/bundled_plugins/commands/`, not in Python. It grants `Task` (for the `living-docs-maintainer`
fan-out) but no Bash and no sandbox — a docs pass runs no commands, and K owns git.

`plugins.inject()` (`kontinuum/plugins.py:23`) copies the bundle into `<worktree>/.claude/`, mirroring the
bundle layout, and returns the injected relative paths so the caller can git-exclude them. `execute()` folds
them into its own exclude alongside installed deps (`kontinuum/loop.py:120`, `:130`); the edit-only flows use
the `injected()` context manager instead (`plugins.py:46`, used at `loop.py:293`, `:313`). Net effect: the
plugin ships with K (same version everywhere), is available to the agent for the run, and never reaches the PR.

## Key entry points

- Ports: `kontinuum/agent.py:53` (`AgentRunner`), `kontinuum/agent.py:65` (`Reviewer`)
- Runner + tool flags: `kontinuum/agent.py:96`; injected-command path: `kontinuum/agent.py:121`
- Reviewer: `kontinuum/agent.py:145`; denied-tool list `kontinuum/agent.py:50`
- Task prompt (PR-body contract): `kontinuum/agent.py:72`
- Injection: `kontinuum/plugins.py:23`; scoped variant `kontinuum/plugins.py:46`
- Callers: `kontinuum/pipeline.py:45` (run/gate loop), `kontinuum/loop.py:120` (injection + exclude), `kontinuum/cli.py:70` (adapter selection)

## Gotchas / non-obvious

- **The runner must not raise.** A timeout returns a string (`agent.py:115`) and empty output falls back to
  `"Implements #N"` (`agent.py:117`). `propose()` judges the *worktree*, not the return value — a raise aborts
  the whole task instead of costing one attempt.
- **The reviewer must not touch the worktree.** It gets the diff as text, runs with no `cwd` and every file
  tool denied (`agent.py:154`). This is load-bearing, not stylistic: review happens *after* the diff is staged
  and secret-scanned (`pipeline.py:48-53`), and `open_pr` re-runs `git add -A` (`effects.py:44`) — so anything
  a reviewer wrote would ride into the PR unreviewed and unscanned.
- **Repo-native `.claude/` wins per file, not per tree** (`plugins.py:37`, `tests/test_plugins.py:20`). A repo
  shipping its own `.claude/skills/living-docs.md` keeps it, and that path is *not* returned — correctly, since
  it is tracked and belongs in the repo's own diff. The flip side: a stale same-named repo file silently
  shadows K's newer bundled one, so "same version on every machine" only holds for files the repo doesn't own.
- **The git exclude is shared across worktrees of the cached clone** (`worktree.py:45` asks git for the real
  path). Both injection paths restore it in a `finally` (`plugins.py:63`, `loop.py:153`); a leaked entry would
  hide a target repo's *own* `.claude/` files from later commits on unrelated issues.
- **Docs work is best-effort by design.** `run_command` swallows timeouts (`agent.py:135`) and `livingdocs`
  swallows exceptions — a failed docs pass never blocks the code PR.

## See also

- `subsystems/sandbox` — the actual containment boundary the `run` tool crosses into
- `subsystems/execution-pipeline` — the run → gate → scan → review loop that consumes these ports
- `flows/living-docs-maintenance` — what the injected `/docs-update` command does with `run_command`
