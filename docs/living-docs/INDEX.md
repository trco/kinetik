# Living Docs Index

<!-- Generated from page frontmatter by /docs-seed and /docs-update. Do not edit by hand. -->

## Subsystems

- [Agent Seam](subsystems/agent-seam.md) — The two duck-typed ports (AgentRunner, Reviewer), the Claude CLI adapter that edits on the host but runs commands through the sandbox, and the bundled `.claude/` plugin injection that stays out of the diff.
- [Claim Protocol](subsystems/claim-protocol.md) — Epoch-leased, append-only claim log in GitHub issue comments — how a K instance takes, holds, and gives up sole ownership of an issue.
- [Daemon Loop](subsystems/daemon-loop.md) — The `kontinuum run` poll pass — one issue per pass, label-driven, lease-heartbeated, with guards re-checked before every effect.
- [Execution Pipeline](subsystems/execution-pipeline.md) — The attempt loop that turns an issue into a reviewed, gate-green diff — and the credentialed boundary that turns that diff into a PR.
- [Sandbox](subsystems/sandbox.md) — Ephemeral, credential-free, no-network container that every agent and gate command runs in — plus the one-tool MCP server that is the agent's only way to reach it.

## Flows

- [Issue to PR](flows/issue-to-pr.md) — End-to-end path from a `kontinuum:ready` GitHub issue to an open pull request — poll, claim, worktree, sandbox, gate, docs, PR, CI reconciliation.
- [Living-Docs Maintenance](flows/living-docs-maintenance.md) — How the repo's living docs get seeded once and then refreshed in the same PR as the code change — Kontinuum computes the diff, the agent drafts, Kontinuum commits.

## Decisions (ADR)

- [ADR 0001: Claim log in GitHub issue comments](adr/0001-claim-log-in-github-issue-comments.md) — Mutual exclusion between Kontinuum instances is an append-only, epoch-leased claim log built from GitHub issue comments — no database, no lock service, no local state.
- [ADR 0002: Agent on the host, its commands in the sandbox](adr/0002-host-agent-sandboxed-commands.md) — Why the agent reasons and edits files on the host while every command it runs is docker-exec'd into a credential-free box via an MCP tool — and why credentialed effects stay in the orchestrator.
