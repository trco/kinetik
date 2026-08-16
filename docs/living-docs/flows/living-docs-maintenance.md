---
title: Living-Docs Maintenance
type: flow
summary: How the repo's living docs get seeded once and then refreshed in the same PR as the code change — Kontinuum computes the diff, the agent drafts, Kontinuum commits.
sources:
  - kontinuum/livingdocs.py
  - kontinuum/bundled_plugins/**
  - kontinuum/plugins.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/agent-seam, subsystems/planning, flows/issue-to-pr]
---

## What it does

Keeps `docs/living-docs/` true without a human writing it, through two entry points:

- **Seeding** — a whole-repo survey producing an initial page set, in **its own PR**. Runs from
  `onboard` (bundled with the proposed `verify.yaml`) or the standalone `seed-docs` CLI command.
  Skipped if living docs already exist.
- **Maintenance** — after the agent's change passes the gate but before the PR is opened, the docs
  covering the paths that changed are refreshed and ride in the **same code PR**, so the human
  reviews code and docs together.

The division of labour is what makes it work, and it follows the same containment as the rest of the
system: the agent has **no shell**, so *Kontinuum* computes what changed with git and passes it in;
the agent only **drafts** with Read/Edit/Write; *Kontinuum* commits. Neither the command prompts nor
the page conventions live in Python — they live in the bundled plugin files, which ship with
Kontinuum (same version everywhere) rather than with the target repo.

Both entry points fan out: the slash command decides *which* pages to touch, then dispatches the
`living-docs-maintainer` sub-agent **one per page**, in parallel, each in a clean context. The
sub-agent returns page content as text and never writes; the orchestrating command writes each file
and rebuilds `INDEX.md` from all frontmatter.

## Key entry points

- `kontinuum/livingdocs.py:28` — `seed()`: invokes `/docs-seed`, returns whether docs now exist.
- `kontinuum/livingdocs.py:45` — `changed_paths()`: the git half of the split (`git status --porcelain -z`).
- `kontinuum/livingdocs.py:64` — `maintain()`: invokes `/docs-update <paths>`.
- `kontinuum/loop.py:194` — where maintenance is called inside `build_executor`'s `execute()`; note it
  sits *after* the §7 ownership guard and *before* `open_pr`, so docs land in the same commit.
- `kontinuum/loop.py:325` / `kontinuum/loop.py:353` — `onboard()` and `seed_docs_pr()`, the two seeding callers.
- `kontinuum/cli.py:46` — the `seed-docs` subcommand.
- `kontinuum/agent.py:124` — `run_command()`: the headless slash-command capability. `Task` is allowed
  (for the fan-out), `Bash` is not — and the ban propagates to sub-agents.
- `kontinuum/plugins.py:23` / `kontinuum/plugins.py:46` — `inject()` and the `injected()` context manager.
- `kontinuum/bundled_plugins/commands/docs-update.md:12` — the affected-vs-gap page selection.
- `kontinuum/bundled_plugins/agents/living-docs-maintainer.md:4` — the sub-agent's `tools:` line.

## Gotchas / non-obvious

- **Docs are best-effort and must never block the code PR.** Every failure path is swallowed:
  no `run_command` capability → silent no-op; exception or timeout → logged warning and continue
  (`kontinuum/livingdocs.py:80`, `kontinuum/agent.py:139`). Locked in by
  `tests/test_livingdocs.py:77`.
- **A doc pass must not feed on itself.** `docs/living-docs/`, `docs/plans/` and `.claude/` are
  stripped from the changed-path list (`kontinuum/livingdocs.py:20`, pinned by
  `tests/test_livingdocs.py:29`). Living docs written by one pass would otherwise look like source
  changes to the next; a plan file is the same trap from the other side — a plan **describes** a
  change, it isn't part of it, so leaving it in would have the docs agent document the plan instead
  of the code. Injected plugins are also already invisible via `.git/info/exclude`, so the prefix
  filter is belt-and-braces there.
- **Plans and doc updates now ride in the same PR as the code**, which is exactly why the exclusion
  matters: per-issue planning writes `docs/plans/<date>-issue-<N>-<slug>.md` in the same edit pass
  (see [subsystems/planning](../subsystems/planning.md)), so without the prefix the two narrative
  artifacts would chase each other.
- **The changed-path list is truncated** to the first 100 paths (`kontinuum/livingdocs.py:79`) — the
  arguments go into a prompt, so a huge refactor gives the agent a partial view of what moved.
- **The plugin must be injected before a slash command can resolve.** Claude Code loads `.claude/`
  from its cwd; `/docs-seed` in a worktree with no injection is just unknown text. `execute()` folds
  injection into its own exclude (`kontinuum/loop.py:163`); the edit-only flows use the `injected()`
  context manager instead. Restoring the exclude on exit matters — it is shared across worktrees of a
  cached clone.
- **Repo-native `.claude/` files always win** (`kontinuum/plugins.py:38`): a target repo that has its
  own `living-docs.md` skill keeps its conventions, and Kontinuum's copy is skipped.
- **`sha: seed` on freshly drafted pages** is not a placeholder bug. The skill wants
  `last_verified.sha` = `git rev-parse --short HEAD`, but the drafting sub-agent has no shell; it
  stamps `seed` and Kontinuum's commit supplies the real history.
- **`sources:` globs in *this* repo are module files, not directory globs** — `kontinuum/` is a flat
  single package, so `kontinuum/**` would flag every page on every change. See
  `docs/living-docs/README.md:10`.
- Maintenance only runs when something source-level actually changed; a clean tree issues no command
  (`tests/test_livingdocs.py:65`).

## See also

- [subsystems/agent-seam](../subsystems/agent-seam.md) — the `run_command` capability and the tool allowlist.
- [subsystems/planning](../subsystems/planning.md) — the plan artifact that shares the PR and is excluded here.
- [flows/issue-to-pr](issue-to-pr.md) — the surrounding execute() pipeline this hooks into.
