---
description: Use when reading, writing, updating, or initializing the repo living docs under docs/living-docs/. Defines initialization, the page format, frontmatter schema, authoring rules, and how INDEX.md is rebuilt. Read this before authoring/editing any page or running /docs-update or /docs-seed.
---

The living docs live in `docs/living-docs/` and are the single home for "how this system works" documentation. Always read `docs/living-docs/INDEX.md` first, then open only the pages a task needs.

## Initialization (first run — no manual scaffold)

If `docs/living-docs/` does not exist, initialize it BEFORE anything else:

1. Infer `sourceRoots`: prefer `src/` if present; otherwise the top-level dir(s) holding most of the source. 
2. Infer `baseBranch`: `git symbolic-ref --short refs/remotes/origin/HEAD` (strip the `origin/`); fall back to `main`.
3. Under Kinetik you run **headless** — infer the config and proceed; the human reviews it in the resulting PR (no interactive confirmation).
4. Create with the inferred values:
   - `docs/living-docs/config.json`
   - `docs/living-docs/README.md` (one-paragraph orientation pointing at INDEX.md; note INDEX is generated and updates ride in the same PR)
   - `docs/living-docs/INDEX.md` (just the `# Living Docs Index` heading + a "rebuilt from frontmatter, do not edit" note + "no pages yet")
   - the `subsystems/`, `flows/`, `adr/` folders (created when the first page in each is written)
5. Do NOT commit — Kinetik commits your drafts into the PR the human reviews.

## Layout

- `subsystems/` — "what is this part" (one per module).
- `flows/` — "how this happens end-to-end".
- `adr/` — "why" — numbered architecture decision records (`NNNN-title.md`).
- `INDEX.md` — rebuilt from frontmatter by the commands. Never hand-edit.
- `config.json` — `livingDocsRoot`, `sourceRoots`, `baseBranch`.

## Page format

Every page starts with frontmatter, then a short body:

```markdown
---
title: Dataset Fetcher
type: subsystem            # subsystem | flow | adr
summary: One line shown in INDEX.md.
sources:                   # repo-relative globs this page describes — used to detect staleness
  - src/dataset-fetcher/**
last_verified:
  date: 2026-06-26         # YYYY-MM-DD; bumped only when content is confirmed against sources
  sha: 1032d612            # short commit the content was confirmed against
related: [flows/dataset-preparation]
---

## What it does
## Key entry points        # pointers to code as file:line — do NOT restate code
## Gotchas / non-obvious
## See also
```

## Authoring rules

- Capture what code cannot show: why, cross-cutting flow, gotchas. Point to code (`path:line`), do not paraphrase it.
- Keep pages short and high-signal. A smaller, accurate base beats an exhaustive, stale one.
- `sources:` entries are **directory globs** (`<dir>/**`), never individual files — so new files added to a relevant directory still flag the page. For a **subsystem** page use the module root (`src/<module>/**`). For a **flow** or **adr** page list the directory globs covering the code it depends on (e.g. `src/dataset-fetcher/services/**`), deduped — coarse enough to catch new files, specific enough not to flag unrelated code.
- `last_verified.date` and `last_verified.sha` are ALWAYS stamped together, only by an agent that has just confirmed the page against its sources (`sha` = `git rev-parse --short HEAD`). Never hand-edit one without the other.

## Rebuilding INDEX.md

INDEX.md is generated content. To rebuild it: read the frontmatter of every page under `subsystems/`, `flows/`, `adr/`, and write:

- an `# Living Docs Index` heading and the "do not edit by hand" note,
- a `## Subsystems`, `## Flows`, `## Decisions (ADR)` section (omit empty sections),
- under each, one bullet per page sorted by file path: `- [<title>](<relative-path>) — <summary>`.
