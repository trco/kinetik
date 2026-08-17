Survey this repository and author an INITIAL set of living docs. $ARGUMENTS

You have NO shell; infer the structure by reading the code. Do not run git.

## Rules
- First read `.claude/skills/living-docs.md` for the page format, frontmatter schema, and authoring rules.
- Edit files directly under `docs/living-docs/`. Do NOT touch code, and do NOT run git or commit — Kinetik commits your drafts into the pull request the human reviews.
- Start SMALL and high-signal: aim for ~6-8 trusted pages, not exhaustive coverage.

## Steps
1. Initialize `docs/living-docs/` per the skill: `config.json` (infer `sourceRoots` from the layout, `baseBranch` from origin/HEAD else `main`), `README.md`, an empty `INDEX.md`, and the subsystems/flows/adr folders.
2. Survey each top-level module under the source roots and decide the page list:
   - core **subsystem** pages (one per major module),
   - main **flow** pages (end-to-end),
   - **adr** pages only for genuinely non-obvious decisions.
3. For each page, dispatch the `living-docs-maintainer` sub-agent (subagent_type: living-docs-maintainer), one per page — run independent pages in parallel. Pass it the page path, type, and relevant source globs. Write each page it returns to its file.
4. Rebuild `docs/living-docs/INDEX.md` from all page frontmatter per the skill.
