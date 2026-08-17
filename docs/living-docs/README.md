# Living Docs

This directory is the single home for "how Kinetik works" documentation: `subsystems/` (what a
part is), `flows/` (how something happens end-to-end), and `adr/` (why a decision was made). Start
at [INDEX.md](INDEX.md) and open only the pages a task needs — pages point at code (`path:line`)
instead of restating it, so the code stays the source of truth. INDEX.md is generated from page
frontmatter by `/docs-seed` and `/docs-update`; never hand-edit it. Doc updates ride in the same
pull request as the change that caused them, so a human reviews code and docs together.

Note on `sources:` globs: `kinetik/` is a flat single-package layout, so pages list the modules
they describe (`kinetik/<module>.py`) rather than sub-directory globs — a directory glob here
would match the whole codebase and flag every page on every change.
