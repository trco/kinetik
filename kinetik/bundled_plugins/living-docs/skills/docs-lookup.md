---
description: Use BEFORE exploring, grepping, reading, or modifying the source to understand any part of this system — whether to answer how it works ("how does X work", "where is X handled", "what does X do"), or to ground yourself before implementing a feature, fixing a bug, or refactoring/improving code in an area. Start from the maintained living docs instead of re-deriving understanding from code.
---

Any task that requires understanding part of this codebase starts here — consult the living docs FIRST, before reading or grepping `src/`. This applies whether you are:

- **researching** how something works (a question, a subsystem, an end-to-end flow, or why a decision was made), OR
- **about to change code** — implementing a feature, fixing a bug, or refactoring/improving an area — and need to understand it first.

Steps:

1. Read `docs/living-docs/INDEX.md`. It is a short, always-cheap table of contents — one line per page, grouped into Subsystems / Flows / Decisions (ADR).
2. From the index, open ONLY the 1–2 pages relevant to the area you are about to work in or research. Do not read the whole living docs.
3. Use each page's `path:line` pointers to jump straight to the relevant code, instead of searching from scratch. Note any "Gotchas / non-obvious" before editing.
4. If no page covers the area, fall back to exploring the code — and consider running `/docs-update` afterward to capture what you learned.
5. After changing code in a covered area, run `/docs-update` so the page stays current (drafts only — you review).

This is just-in-time retrieval: the index is the lightweight identifier layer; load page detail on demand. It exists so you start a task from accumulated understanding rather than re-analyzing the codebase every time.
